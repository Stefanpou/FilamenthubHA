"""Two-way sync with Bambu Lab's Filament Manager (the spool list shown in Bambu Studio and Bambu Handy).

Spoolman stays the source of truth for weights (Bambu never deducts manual spools). Spool Sync:
* links Filament Manager (FM) spools to Spoolman spools (marker note, RFID, confident unique match, or by the user)
* pushes the real remaining weight to linked manual FM spools
* imports slot bindings the user makes in Bambu Studio / Handy and exports its own slot assignments as FM bindings
* can create FM spools for Spoolman spools that are really missing there, and remove them again

Rules that keep this safe (see tests/test_fm.py and tests/test_attack_*.py):
* A binding counts as "new" only when its mount (printer, AMS, slot) changes - `updatedAt` also changes on every
  weight edit, including our own, so it is only used as an estimate of when the binding was made. The very first
  sync only records a baseline. Every write we make ourselves is recorded (`by_us`) so it never looks like Studio.
* A Studio/Handy binding is imported only if no *strong* decision (the user in Spool Sync, or RFID) about the slot
  or spool is newer, and only once the printer agrees (tray present, same material, similar colour, no other RFID
  spool). Per slot only the newest binding is imported. Automatic reactions are weak and never override it.
  A binding the printer does not confirm waits with its original time (pending) for at most PENDING_MAX; a binding is
  evaluated again when its row is linked to another spool; it is obsolete once its spool physically left the slot.
* Our exports never overwrite a Studio binding that is waiting to be imported, never act on an empty/partial FM
  list or unknown printer state, and replace a Studio binding only with a newer strong decision.
* All writes (sync, create, import, link, ignore, remove) run under one lock.

Endpoints (unofficial, used by Bambu Studio's network plugin; see README):
  GET/POST/PUT /v1/design-user-service/my/filament/v2, DELETE .../batch, POST .../slot-mappings/sync
"""
from __future__ import annotations

import logging
import re
import threading
import time

from . import config
from .bambu_cloud import AuthExpired, BambuCloud, CloudError
from .catalog import same_product_line  # noqa: F401  (also used by the tests)
from .db import Database
from .printer_state import color_distance, slot_label
from .spoolman import TAG_FIELD, Spoolman, SpoolmanError, extra_value, spool_display
from .sync import PHYSICAL_ENDS, _same_time, parse_time

log = logging.getLogger("spoolsync.fm")

MARKER = "spoolman:"
IMPORT_MARKER = "bambu-fm:"   # in the comment of Spoolman spools imported from FM (makes retries idempotent)
PENDING_MAX = 86400           # a Studio/Handy binding the printer never confirms is given up after a day
# Ends of an assignment that show the spool physically left the slot (a Studio binding of it is then obsolete).
PHYSICAL_END_REASONS = tuple(sorted(PHYSICAL_ENDS))
GENERIC_IDS = {  # Bambu generic filament presets by material (Studio's slot picker needs a filamentId)
    "PLA": "GFL99", "PLA+": "GFL99", "PLA-CF": "GFL98", "PETG": "GFG99", "PETG-CF": "GFG98", "PET-CF": "GFT98",
    "ABS": "GFB99", "ASA": "GFB98", "TPU": "GFU99", "PA": "GFN99", "PA-CF": "GFN98", "PC": "GFC99", "PVA": "GFS99",
    "HIPS": "GFS98", "PCTG": "GFG97",
}
AMS_TYPES = {"AMS": 1, "AMS Lite": 2, "AMS 2 Pro": 3, "AMS HT": 4}
UNMOUNTED = ["", None, "", False, 0]
MISSING_SYNCS_BEFORE_UNLINK = 2


class FmBusy(Exception):
    """Another Filament Manager operation is running."""


class FmShadow(CloudError):
    """Shadow mode (FILAMENTHUB_SHADOW=1): the write was skipped, Bambu's Filament Manager is not touched."""


class FmHttpError(CloudError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _hex(c: str) -> str:
    return (c or "").lstrip("#").upper()[:6]


def _rfid(v: str | None) -> str:
    v = (v or "").strip().upper()
    return "" if not v or set(v) == {"0"} else v


def _mat(m: str | None) -> str:
    """Material for exact comparison ('PLA+' == 'PLA', 'PETG' != 'PETG-CF')."""
    return (m or "").upper().replace("+", "").strip()


def fm_slot_key(fm: dict) -> str | None:
    """Slot key for an FM spool's mount fields, or None when not mounted."""
    if not fm.get("inPrinter"):
        return None
    try:
        ams_id = int(fm.get("amsId") or 0)
        slot = int(str(fm.get("slotId") or "0"))
    except (TypeError, ValueError):
        return None
    if ams_id in (254, 255):
        return f"ext:{ams_id}"
    if int(fm.get("amsType") or 0) == 0 or slot in (254, 255):
        return f"ext:{slot if slot in (254, 255) else 255}"
    if ams_id >= 128:
        return f"ams:{ams_id}:0"
    return f"ams:{ams_id}:{slot}"


def _mount(fm: dict) -> list:
    return [fm.get("devId") or "", fm.get("amsId"), str(fm.get("slotId") or ""), bool(fm.get("inPrinter")),
            int(fm.get("amsType") or 0)]


def _mount_key(mount: list, dev: str) -> str | None:
    """Slot key of a stored mount on our printer, or None."""
    if not mount or len(mount) < 5 or not mount[3] or mount[0] != dev:
        return None
    return fm_slot_key({"inPrinter": True, "amsId": mount[1], "slotId": mount[2], "amsType": mount[4]})


def _base(m: str | None) -> str:
    """Base material ('PLA+' and 'PLA-CF' -> 'PLA')."""
    return _mat(m).split("-")[0]


def match_score(fm: dict, sp: dict, exact_material: bool = False) -> float:
    """How well an FM spool matches a Spoolman spool (display dict), 0..1. >= 0.75 with exact material = confident."""
    fm_mat = (fm.get("filamentType") or "").upper()
    sp_mat = (sp["material"] or "").upper()
    if exact_material and fm_mat != sp_mat:
        return 0.0
    if _base(fm_mat) != _base(sp_mat):
        return 0.0
    dist = color_distance(_hex(fm.get("color")), sp["color"]) if _hex(fm.get("color")) and sp["color"] else 200
    if dist > 60:
        return 0.0
    score = 0.5 + 0.3 * (1 - dist / 60)
    fv = _norm(fm.get("filamentVendor"))
    if fv and fv != "generic" and fv == _norm(sp["vendor"]):
        score += 0.1
    try:
        if abs(float(fm.get("netWeight") or 0) - sp["remaining"]) < 50:
            score += 0.1
    except (TypeError, ValueError):
        pass
    return round(min(score, 1.0), 3)


def _plausible(fm: dict, sp: dict) -> bool:
    """Could this FM row describe this Spoolman spool (display dict)? Used to never create a duplicate."""
    if _rfid(fm.get("RFID")) and _rfid(fm.get("RFID")) == _rfid(sp["tag"]):
        return True
    if match_score(fm, sp) >= 0.5:
        return True
    # A spool without a colour cannot be compared by colour: the same base material is enough.
    return not sp["color"] and bool(sp["material"]) and _base(fm.get("filamentType")) == _base(sp["material"])


class FilamentManager:
    def __init__(self, db: Database, spoolman: Spoolman, engine):
        self.db = db
        self.spoolman = spoolman
        self.engine = engine
        self._lock = threading.RLock()
        self.fm_spools: list[dict] = []
        self.last_sync = 0.0
        self.last_error = ""
        self._config: list[dict] | None = None
        self._missing: dict[int, int] = {}
        self._seen_missing: dict[str, int] = {}      # seen FM rows missing from complete lists (in a row)
        self._had_rows = False
        self._effective: dict[int, float] = {}       # FM rows whose mount changed this sync -> binding time
        self._pending: set[int] = set()              # linked rows whose Studio binding waits for the printer
        self._foreign_pending: dict[str, float] = {}  # slot -> newest unlinked Studio binding waiting for a link
        self._blocked = False                        # printer state or FM list unusable this sync: no writes
        self._wake = threading.Event()
        self._stop = threading.Event()

    # ---------------------------------------------------------------- settings / links
    @property
    def enabled(self) -> bool:
        return bool(self.db.get("fm_enabled", False))

    @property
    def auto_create(self) -> bool:
        return bool(self.db.get("fm_auto_create", False))

    def _expect_rows(self) -> bool:
        """Does Filament Manager have rows we know of (so an empty answer is most likely Bambu's glitch)?"""
        seen = self.db.get("fm_binding_seen", {}) or {}
        return bool(self._had_rows or self.links() or any(k.isdigit() for k in seen)
                    or self.db.query_one("SELECT 1 FROM fm_created LIMIT 1"))

    def links(self) -> dict[int, int]:
        """spool_id -> fm_id"""
        return {r["spool_id"]: r["fm_id"] for r in self.db.query("SELECT spool_id, fm_id FROM fm_links")}

    def _spool_registered(self, spool_id: int) -> str | None:
        try:
            return self.spoolman.spool(spool_id).get("registered")
        except SpoolmanError:
            return None

    def _link(self, spool_id: int, fm_id: int, source: str, registered: str | None = None) -> None:
        registered = registered if registered is not None else self._spool_registered(spool_id)
        pending = self._pending_creates().get(str(spool_id))
        with self.db.lock:
            self.db.execute("DELETE FROM fm_links WHERE spool_id=? OR fm_id=?", (spool_id, fm_id))
            self.db.execute("INSERT INTO fm_links(spool_id, fm_id, linked_ts, source, spool_registered) VALUES(?,?,?,?,?)",
                            (spool_id, fm_id, time.time(), source, registered))
            self.db.execute("DELETE FROM fm_ignored WHERE fm_id=?", (fm_id,))
        self._keep_row(fm_id)
        if pending:
            row = next((h for h in self.fm_spools if int(h["id"]) == fm_id), None)
            if row and self._created_row([row], pending["create"], pending["sent"], set(pending.get("before") or [])):
                # The row our unconfirmed create made is being linked: it is ours, so it can be removed later.
                self.db.execute("INSERT OR IGNORE INTO fm_created(fm_id, spool_id, ts) VALUES(?,?,?)", (fm_id, spool_id, time.time()))
            self._set_pending_create(spool_id, None)

    def link(self, spool_id: int, fm_id: int, source: str = "manuel", force: bool = False) -> None:
        with self._write():
            links = self.links()
            other_row = links.get(spool_id)
            if other_row and other_row != fm_id and not force:
                raise ValueError(f"Spole #{spool_id} er allerede koblet til en anden Bambu-spole")
            owner = {v: k for k, v in links.items()}.get(fm_id)
            if owner and owner != spool_id and not force:
                raise ValueError(f"Bambu-spolen er allerede koblet til spole #{owner}")
            self._link(spool_id, fm_id, source)
        self.trigger()

    def unlink(self, fm_id: int) -> None:
        """Remove a link and keep the pair from being linked automatically again."""
        with self._write():
            self.db.execute("DELETE FROM fm_links WHERE fm_id=?", (fm_id,))
            self.db.execute("INSERT OR IGNORE INTO fm_ignored(fm_id) VALUES(?)", (fm_id,))

    def ignore(self, fm_id: int) -> None:
        with self._write():
            self.db.execute("DELETE FROM fm_links WHERE fm_id=?", (fm_id,))
            self.db.execute("INSERT OR IGNORE INTO fm_ignored(fm_id) VALUES(?)", (fm_id,))

    def unignore(self, fm_id: int) -> None:
        with self._write():
            self.db.execute("DELETE FROM fm_ignored WHERE fm_id=?", (fm_id,))
            self._keep_row(fm_id)

    def _keep_row(self, fm_id: int) -> None:
        """The user takes a row into use again: it is no longer a duplicate waiting to be deleted."""
        retire = self.db.get("fm_retire", []) or []
        if fm_id in retire:
            self.db.set("fm_retire", [i for i in retire if i != fm_id])

    def ignored(self) -> set[int]:
        return {r["fm_id"] for r in self.db.query("SELECT fm_id FROM fm_ignored")}

    # ---------------------------------------------------------------- locking
    class _Held:
        def __init__(self, lock: threading.RLock, timeout: float):
            self.lock, self.timeout = lock, timeout

        def __enter__(self):
            if not self.lock.acquire(timeout=self.timeout):
                raise FmBusy("Synkronisering med Bambu kører – prøv igen om lidt")
            return self

        def __exit__(self, *exc):
            self.lock.release()
            return False

    def _write(self, timeout: float = 60):
        return self._Held(self._lock, timeout)

    # ---------------------------------------------------------------- cloud access
    def _cloud(self) -> BambuCloud:
        if not self.engine.cloud:
            raise CloudError("Ikke logget ind på Bambu")
        return self.engine.cloud

    def _url(self, path: str) -> str:
        return f"{self._cloud().hosts['api']}/v1/design-user-service{path}"

    _shadow_logged: set = set()

    def _shadow_skip(self, what: str) -> None:
        """Log (once per kind of write) that a write to Bambu's Filament Manager was skipped in shadow mode."""
        if what not in FilamentManager._shadow_logged:
            FilamentManager._shadow_logged.add(what)
            log.info("SHADOW mode: Filament Manager write skipped (%s); further skips of this kind are not logged", what)

    def _call(self, method: str, path: str, body=None):
        if config.SHADOW and method.upper() != "GET":
            # Every write (create / update / delete / slot-mapping sync) goes through here: skip it, read-only otherwise.
            self._shadow_skip(f"{method.upper()} {path.split('?')[0]}")
            raise FmShadow("Skyggetilstand: skrivning til Bambu Filament Manager er slået fra")
        try:
            resp = self._cloud()._request(method, self._url(path), json_body=body)
        except CloudError:
            raise
        except Exception as e:  # requests / curl_cffi transport errors
            raise CloudError(f"Bambu svarer ikke: {e}") from e
        if resp.status_code >= 400:
            raise FmHttpError(f"Filament Manager svarede {resp.status_code}: {resp.text[:200]}", resp.status_code)
        try:
            return resp.json()
        except ValueError:
            return {}

    def fetch(self) -> list[dict]:
        """The complete FM spool list. Raises CloudError when the answer is malformed or incomplete."""
        by_id: dict[int, dict] = {}
        offset, total, limit = 0, None, 100
        for _ in range(200):
            data = self._call("GET", f"/my/filament/v2?offset={offset}&limit={limit}")
            if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
                raise CloudError("Uventet svar fra Filament Manager")
            if data.get("total") is not None and not isinstance(data.get("total"), int):
                raise CloudError("Uventet svar fra Filament Manager")
            had = len(by_id)
            for h in data["hits"]:
                if isinstance(h, dict) and str(h.get("id", "")).lstrip("-").isdigit():
                    by_id.setdefault(int(h["id"]), h)   # duplicate rows (repeated pages) count once
            offset += len(data["hits"])
            if isinstance(data.get("total"), int):
                total = data["total"]
                if not data["hits"] or (len(data["hits"]) < limit and len(by_id) >= total):
                    break
            else:
                # Bambu (since 2026-10) answers without "total". A short page is no proof (the server may cap the
                # page size): only an empty page, or one with nothing new (offset ignored), is the end.
                if len(by_id) == had:
                    total = len(by_id)
                    break
        if total is None or len(by_id) < total:
            raise CloudError("Filament Manager returnerede en ufuldstændig liste")
        hits = list(by_id.values())
        self.fm_spools = hits
        return hits

    def filament_config(self) -> list[dict]:
        if self._config is None:
            try:
                cfg = self._call("GET", "/filament/config")
                self._config = (cfg.get("filamentSettings") or []) if isinstance(cfg, dict) else []
            except CloudError:
                return []  # do not cache failures
        return self._config

    def _filament_id(self, sp: dict) -> str:
        """Bambu preset id for a Spoolman spool: exact Bambu product if known, else the generic one for the material."""
        mat = (sp["material"] or "").upper()
        for c in self.filament_config():
            name = _norm(c.get("filamentName"))
            if (_norm(c.get("filamentVendor")) == _norm(sp["vendor"]) and (c.get("filamentType") or "").upper() == mat
                    and name and name in _norm(f"{sp['material']} {sp['name']}")):
                return c.get("filamentId") or ""
        for key in (mat, mat.split("-")[0] + ("-CF" if "CF" in mat else ""), mat.split("-")[0].rstrip("+")):
            if key in GENERIC_IDS:
                return GENERIC_IDS[key]
        return ""

    def _fm_body(self, sp: dict) -> dict:
        colors = sp.get("colors") or []
        color = f"#{sp['color'] or 'FFFFFF'}"
        body = {
            "filamentVendor": sp["vendor"] or "Generic", "filamentType": sp["material"] or "PLA",
            "filamentName": (sp["name"] or sp["material"] or "Filament")[:60], "filamentId": self._filament_id(sp),
            "isSupport": False, "color": color, "colorType": 1 if len(colors) > 1 else 2,
            "netWeight": int(round(max(sp["remaining"], 0))), "totalNetWeight": int(round(sp["initial"] or 1000)),
        }
        if len(colors) > 1:
            body["colors"] = [f"#{c}" for c in colors]
        return body

    # ---------------------------------------------------------------- create / import / remove
    def _pending_creates(self) -> dict:
        return self.db.get("fm_create_pending", {}) or {}

    def _set_pending_create(self, spool_id: int, value: dict | None) -> None:
        p = self._pending_creates()
        if value is None:
            if str(spool_id) not in p:
                return
            p.pop(str(spool_id), None)
        else:
            p[str(spool_id)] = value
        self.db.set("fm_create_pending", p)

    @staticmethod
    def _created_row(fm: list[dict], create: dict, sent: float, exclude: set[int]) -> list[dict]:
        """Rows that can only be the one our create made (a create cannot carry a note, so noted rows are not ours;
        it makes a manual row without RFID, created right after the request)."""
        return [h for h in fm if int(h["id"]) not in exclude and not (h.get("note") or "").strip()
                and not _rfid(h.get("RFID")) and (h.get("createType") or "manual") == "manual"
                and (h.get("filamentType") or "") == create["filamentType"] and _hex(h.get("color")) == _hex(create["color"])
                and (h.get("filamentName") or "") == create["filamentName"]
                and abs(int(h.get("netWeight") or 0) - create["netWeight"]) <= 1
                and sent - 120 <= float(h.get("createdAt") or sent) <= sent + 600]

    def create_for(self, spool_id: int) -> int:
        with self._write():
            if spool_id in self.links():
                return self.links()[spool_id]
            if str(spool_id) in self._pending_creates():
                raise CloudError("Spolen er ved at blive oprettet i Bambu – den kobles ved næste synkronisering")
            sp = spool_display(self.spoolman.spool(spool_id))
            if sp["tag"]:
                raise ValueError("RFID-spoler oprettes af Bambu selv, når spolen sættes i AMS'en")
            fm = self.fetch()
            if not fm and self._expect_rows():
                # Bambu's empty-list glitch: without the real list a similar row cannot be ruled out.
                raise CloudError("Bambu returnerede en tom spoleliste – prøv igen om lidt")
            before = {int(h["id"]) for h in fm}
            linked_rows = set(self.links().values())
            for h in fm:
                if int(h["id"]) in linked_rows:
                    continue
                if _plausible(h, sp):
                    raise ValueError("En lignende spole findes allerede i Bambu – kobl den i stedet for at oprette en ny")
            body = self._fm_body(sp)
            create = {k: v for k, v in body.items() if k != "colors"}  # colors/note/status are rejected on create
            create["createType"] = "manual"
            sent = time.time()
            # Written BEFORE the POST: whatever fails afterwards, the row can be found and adopted later.
            self._set_pending_create(spool_id, {"sent": sent, "create": create, "before": sorted(before)})
            try:
                self._call("POST", "/my/filament/v2", create)
            except FmShadow:
                self._set_pending_create(spool_id, None)   # nothing was sent in shadow mode
                raise
            except FmHttpError as e:
                if 400 <= e.status < 500:
                    self._set_pending_create(spool_id, None)  # definitively rejected: nothing was created
                raise
            new = self._created_row(self.fetch(), create, sent, before)
            if len(new) != 1:
                raise CloudError("Spolen blev oprettet i Bambu, men kunne ikke genfindes entydigt – den kobles ved næste synkronisering")
            fm_id = self._adopt_created(spool_id, int(new[0]["id"]), body)
            self.db.event("info", f"Spole #{spool_id} oprettet i Bambu Filament Manager")
            return fm_id

    def _adopt_created(self, spool_id: int, fm_id: int, body: dict) -> int:
        # Record it before anything else can fail, so it can always be found and removed again.
        self.db.execute("INSERT OR IGNORE INTO fm_created(fm_id, spool_id, ts) VALUES(?,?,?)", (fm_id, spool_id, time.time()))
        self._link(spool_id, fm_id, "oprettet")
        self._set_pending_create(spool_id, None)
        self._finish_created(fm_id, spool_id, body)
        return fm_id

    def _finish_created(self, fm_id: int, spool_id: int, body: dict) -> None:
        patch = {"id": fm_id, "filamentName": body["filamentName"], "note": f"{MARKER}{spool_id}"}
        if "colors" in body:
            patch.update(colors=body["colors"], colorType=body["colorType"])
        try:
            self._call("PUT", "/my/filament/v2", patch)
        except CloudError as e:
            log.warning("marker note for FM %s not set yet: %s", fm_id, e)  # retried by the sync

    def _resolve_pending_creates(self, fm: list[dict], spools: dict, by_fm: dict) -> int:
        """Adopt FM rows created by an earlier create_for whose confirmation failed."""
        n = 0
        for sid_s, p in list(self._pending_creates().items()):
            sid = int(sid_s)
            if sid in set(by_fm.values()):
                self._set_pending_create(sid, None)
                continue
            rows = self._created_row([h for h in fm if int(h["id"]) not in by_fm], p["create"], p["sent"], set(p.get("before") or []))
            if len(rows) == 1 and sid in spools:
                self._adopt_created(sid, int(rows[0]["id"]), self._fm_body(spools[sid]))
                by_fm[int(rows[0]["id"])] = sid
                n += 1
            elif time.time() - p["sent"] > 86400:
                self._set_pending_create(sid, None)  # nothing appeared within a day: the POST failed
        return n

    def import_to_spoolman(self, fm_id: int, catalog_external: list[dict]) -> int:
        with self._write():
            existing = {v: k for k, v in self.links().items()}.get(fm_id)
            if existing:
                return existing
            h = next((x for x in self.fetch() if int(x["id"]) == fm_id), None)
            if not h:
                raise CloudError("Spolen findes ikke i Filament Manager")
            # Never create a second Spoolman spool for a physical spool that already exists there.
            rfid = _rfid(h.get("RFID"))
            note = h.get("note") or ""
            net = float(h.get("netWeight") or 0)
            known = net > 0 and float(h.get("totalNetWeight") or 0) > 0
            linked = self.links()
            for s in self.spoolman.spools(allow_archived=True):
                if (f"{IMPORT_MARKER}{fm_id})" in (s.get("comment") or "") and s["id"] not in linked
                        and not s.get("archived")):
                    # Imported before, but the answer was lost (e.g. a Spoolman timeout): link that spool. (Not an
                    # archived one: the row may have been reused in Handy for a new spool.) Bambu's weight it was
                    # created with is a weighing as of its creation.
                    if known:
                        self.engine.note_weighed(s["id"], ts=parse_time(s.get("registered")))
                    self._link(s["id"], fm_id, "importeret", registered=s.get("registered"))
                    return s["id"]
                if rfid and _rfid(extra_value(s, TAG_FIELD)) == rfid:
                    raise ValueError(f"RFID-spolen findes allerede som spole #{s['id']} – kobl den i stedet")
                if note == f"{MARKER}{s['id']}":
                    raise ValueError(f"Bambu-spolen er markeret som spole #{s['id']} – kobl den i stedet")
            vendor = h.get("filamentVendor") or "Generic"
            material = h.get("filamentType") or "PLA"
            color = _hex(h.get("color")) or "FFFFFF"
            total = float(h.get("totalNetWeight") or 0)
            if total <= 0:
                total = 1000.0   # Bambu leaves the size at 0 until the AMS has read the spool
            # The product line ("PLA Basic", "PLA Matte", "PETG HF") must match too: Bambu has several products
            # in the same colour.
            def same_line(name: str | None) -> bool:
                return same_product_line(h.get("filamentName"), material, name)

            best, best_d = None, 999.0
            for e in catalog_external:
                if (e.get("material") or "").upper() != material.upper() or not same_line(e.get("name")):
                    continue
                if _norm(e.get("manufacturer")) != _norm(vendor):
                    continue  # a row without a real brand ("Generic") never becomes some brand's product
                if abs(float(e.get("weight") or 0) - total) > 1:
                    continue
                d = color_distance(_hex(e.get("color_hex")), color) if e.get("color_hex") else 999
                if d < best_d:
                    best, best_d = e, d
            good = best is not None and best_d <= 20
            vend = self.spoolman.get_or_create_vendor(best["manufacturer"] if good else vendor)
            name = h.get("displayName") or (best["name"] if good else f"{h.get('filamentName') or material} #{color}")
            fil = None
            filaments = self.spoolman.filaments()
            if good:
                fil = next((f for f in filaments if f.get("external_id") == best["id"]), None)
            for f in filaments if fil is None else []:
                if ((f.get("vendor") or {}).get("id") == vend["id"] and (f.get("material") or "").upper() == material.upper()
                        and (f.get("color_hex") or "").upper()[:6] == color and abs(float(f.get("weight") or 0) - total) < 1
                        and same_line(f.get("name"))
                        and (not good or not f.get("external_id") or f.get("external_id") == best["id"])):
                    fil = f
                    break
            if fil is None:
                fields = {"vendor_id": vend["id"], "name": name[:100], "material": material, "color_hex": color, "weight": total,
                          "density": float((best or {}).get("density") or 1.24) if good else 1.24, "diameter": 1.75}
                if good:
                    fields["external_id"] = best["id"]
                    if best.get("spool_weight") is not None:
                        fields["spool_weight"] = best["spool_weight"]
                fil = self.spoolman.create_filament(**fields)
            spool_fields = {"filament_id": fil["id"], "initial_weight": total,
                            "comment": f"Importeret fra Bambu Filament Manager ({IMPORT_MARKER}{fm_id})"}
            if known:
                spool_fields["remaining_weight"] = min(net, total)
            # (netWeight <= 0: Bambu has not read the spool's weight yet (e.g. -10 g) - not an empty spool; Spoolman
            # then counts it as full until it is weighed or used.)
            if rfid:
                spool_fields["extra"] = {TAG_FIELD: f'"{rfid}"'}
            sp = self.spoolman.create_spool(**spool_fields)
            if known:
                # Bambu's weight already contains what was printed from the spool so far: count it as a weighing.
                self.engine.note_weighed(sp["id"])
            self._link(sp["id"], fm_id, "importeret", registered=sp.get("registered"))
            self.db.event("info", f"Spole importeret fra Bambu Filament Manager som #{sp['id']}")
            return sp["id"]

    def remove_created(self) -> int:
        with self._write():
            if self._pending_creates():
                # Adopt rows whose creation was never confirmed, so they are removed too.
                try:
                    fm = self.fetch()
                    spools = {s["id"]: spool_display(s) for s in self.spoolman.spools(allow_archived=True, max_age=0)}
                    self._resolve_pending_creates(fm, spools, {v: k for k, v in self.links().items()})
                except (CloudError, SpoolmanError) as e:
                    log.warning("could not resolve pending creates before removal: %s", e)
            ids = [r["fm_id"] for r in self.db.query("SELECT fm_id FROM fm_created")]
            removed: list[int] = []
            if ids:
                # A fresh list, never one kept from an earlier (possibly glitched) answer.
                before_rows = self.fetch()
                if not before_rows:
                    raise CloudError("Bambu returnerede en tom spoleliste – prøv igen om lidt")
                before = {int(h["id"]) for h in before_rows}
                mounted = any(h.get("inPrinter") for h in before_rows if int(h["id"]) in set(ids))
                self._call("DELETE", "/my/filament/v2/batch", {"ids": ids})
                # Only forget rows Bambu really deleted (it keeps e.g. rows bound to a slot).
                left = {int(h["id"]) for h in self.fetch()}
                if not left and (before - set(ids) or mounted):
                    # An empty answer where rows must remain (other rows, or ours bound to a slot) is Bambu's
                    # known empty-list glitch: ask again, and forget nothing unless the list is believable.
                    left = {int(h["id"]) for h in self.fetch()}
                    if not left:
                        left = set(ids)
                        self.db.event("warning", "Bambu svarede med en tom spoleliste efter sletningen – tjekker igen "
                                                 "ved næste 'Fjern oprettede'")
                removed = [i for i in ids if i not in left]
                for i in removed:
                    self.db.execute("DELETE FROM fm_links WHERE fm_id=?", (i,))
                    self.db.execute("DELETE FROM fm_created WHERE fm_id=?", (i,))
                seen = self.db.get("fm_binding_seen", {}) or {}
                for i in removed:
                    seen.pop(str(i), None)
                if not left and before <= set(ids) and not mounted:
                    # FM is empty because we emptied it (it held only our unbound rows), not because of a glitch:
                    # rows seen earlier are all gone.
                    self._had_rows = False
                    for k in [k for k in seen if k.isdigit()]:
                        seen.pop(k)
                self.db.set("fm_binding_seen", seen)
                if removed:
                    self.db.event("info", f"{len(removed)} spoler fjernet fra Bambu Filament Manager")
                if len(removed) < len(ids):
                    self.db.event("warning", f"{len(ids) - len(removed)} spoler blev ikke fjernet af Bambu (fx bundet "
                                             f"til et slot) – fjern dem i Bambu Studio/Handy")
            self.db.set("fm_auto_create", False)  # otherwise the next sync would create them again
            return len(removed)

    def empty_for_long(self) -> bool:
        """Bambu has answered with an empty list for a while (3 answers, 10 minutes)."""
        e = self.db.get("fm_empty_since")
        return bool(e) and e["count"] >= 3 and time.time() - e["first"] >= 600

    def confirm_empty(self) -> None:
        """The user confirms that every row really was deleted in Bambu Studio/Handy: forget what was linked."""
        with self._write():
            if self.fetch():
                raise ValueError("Bambu viser spoler igen – der er intet at nulstille")
            if not self.empty_for_long():
                raise ValueError("Bambu har ikke vist en tom liste længe nok – prøv igen om lidt")
            self.db.execute("DELETE FROM fm_links")
            self.db.execute("DELETE FROM fm_created")
            self.db.set("fm_retire", [])
            seen = self.db.get("fm_binding_seen", {}) or {}
            self.db.set("fm_binding_seen", {k: v for k, v in seen.items() if k == "_baseline_ts"})
            self.db.delete("fm_empty_since")
            self._had_rows = False
            self._missing.clear()
            self._seen_missing.clear()
            self.last_error = ""
            self.db.event("info", "Bambu Filament Manager er tom: koblingerne er nulstillet")
        self.trigger()

    # ---------------------------------------------------------------- bindings (slot-mappings)
    def bind(self, slot: str, fm_id: int | None) -> bool:
        """Bind (fm_id) or unbind (None) a slot in Filament Manager so Studio/Handy show the right spool.

        Returns False (and does nothing) when the printer or AMS serial is not known yet. Raises CloudError unless
        Bambu confirms the mapping (exactly one result with code 0)."""
        dev = self.engine.device or {}
        if not dev or not slot.startswith("ams:"):
            return False
        if config.SHADOW:
            self._shadow_skip("POST /my/filament/v2/slot-mappings/sync")
            return False
        _, unit, tray = slot.split(":")
        mod = (self.engine.modules or {}).get(unit) or {}
        if not mod.get("serial"):
            log.info("FM bind of %s skipped: AMS serial unknown", slot)
            return False
        mapping = {"amsId": int(unit), "amsSn": mod["serial"], "amsType": AMS_TYPES.get(mod.get("model"), 1),
                   "rfid": None, "slotId": tray, "spoolId": fm_id}
        res = self._call("POST", "/my/filament/v2/slot-mappings/sync", {"devId": dev["serial"], "mappings": [mapping]})
        results = res.get("results") if isinstance(res, dict) else None
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict) or results[0].get("code") != 0:
            raise CloudError(f"Filament Manager bekræftede ikke bindingen ({str(res)[:120]})")
        return True

    def _import_bindings(self, fm: list[dict], spools: dict, by_fm: dict, seen: dict) -> int:
        """Bindings made in Bambu Studio/Handy -> Spool Sync. Fills _effective / _pending / _foreign_pending."""
        dev = (self.engine.device or {}).get("serial")
        slots = {s.key: s for s in self.engine.current_slots()}
        if not dev or not slots:
            self._blocked = True   # printer state unknown: neither import nor export this round
            return 0
        now = time.time()
        initial = "_baseline_ts" not in seen
        if initial:
            seen["_baseline_ts"] = now      # the very first run only records a baseline
        baseline_ts = float(seen["_baseline_ts"])
        ignored = self.ignored()
        # Links the user made (or confirmed by importing). Automatic links never re-open an old binding.
        user_links = {r["fm_id"] for r in self.db.query("SELECT fm_id FROM fm_links WHERE source IN ('manuel', 'importeret')")}
        candidates: dict[str, list[tuple]] = {}
        for h in fm:
            fid = int(h["id"])
            mount = _mount(h)
            sid_now = by_fm.get(fid)
            prev = seen.get(str(fid))
            created = float(h.get("createdAt") or h.get("updatedAt") or now)
            if prev is None and (initial or not mount[3] or created < baseline_ts):
                # (A mounted row first seen later but created before the baseline, e.g. after an empty answer or a
                # pruned record, is just as old.)
                # Baseline, or a new row that is not in a slot: nothing to import. An unmounted row carries no
                # binding decision (its updatedAt moves with every edit, including ours). A baseline binding's time
                # is unknown (updatedAt is the row's last edit): it is marked, so it never pushes a spool out.
                seen[str(fid)] = {"mount": mount, "changed_ts": float(h.get("updatedAt") or now) if mount[3] else 0.0,
                                  "checked_ts": now, "by_us": False, "sid": sid_now, "baseline": bool(mount[3])}
                continue
            pending_since = now
            baseline = False
            if prev is None:
                # A row we have never seen (e.g. created and bound in Studio just now): a change from unmounted.
                prev = {"mount": UNMOUNTED, "checked_ts": 0.0}
                binding_ts = min(now, max(float(h.get("createdAt") or 0), float(h.get("updatedAt") or 0)))
            elif not prev["mount"][3] and not mount[3]:
                # Still in no slot; only its representation may differ (e.g. after our own unbind).
                seen[str(fid)] = {**prev, "mount": mount, "checked_ts": now, "sid": sid_now}
                continue
            elif prev["mount"] == mount:
                relinked = (not prev.get("by_us") and prev.get("sid", sid_now) != sid_now
                            and (sid_now is None or fid in user_links))
                if not prev.get("pending") and not relinked:
                    prev["checked_ts"] = now
                    prev["sid"] = sid_now
                    continue
                # A Studio/Handy binding still waiting for the printer, or the user linked its row to another spool
                # since: evaluate it again, as of when it was made.
                binding_ts = float(prev.get("changed_ts") or 0)
                baseline = bool(prev.get("baseline"))
                if prev.get("pending"):
                    pending_since = float(prev.get("pending_since") or now)
            else:
                # The mount changed after we last saw the old one, so the binding is at least that recent even if
                # Bambu did not bump updatedAt; a later updatedAt is the better estimate of when it was made.
                binding_ts = min(now, max(float(h.get("updatedAt") or 0), float(prev.get("checked_ts") or 0)))
            self._effective[fid] = binding_ts
            # FM (Studio/Handy) cleared the old slot at that time - not when Bambu unbinds an RFID row by itself
            # (the spool was taken out of the AMS).
            old_key = _mount_key(prev["mount"], dev) if prev["mount"] != mount and not _rfid(h.get("RFID")) else None
            if old_key:
                seen[f"slot:{old_key}"] = max(float(seen.get(f"slot:{old_key}") or 0), binding_ts)
            record = {"mount": mount, "changed_ts": binding_ts, "checked_ts": now, "by_us": False, "sid": sid_now,
                      "baseline": baseline}
            key = fm_slot_key(h)
            if not key or h.get("devId") != dev:
                seen[str(fid)] = record
                continue  # unmounted or another printer
            if fid in ignored:
                seen[str(fid)] = record
                continue
            if fid not in by_fm:
                # Not linked (yet): re-evaluated once the user links it. Protects the slot against weak exports.
                self._foreign_pending[key] = max(self._foreign_pending.get(key, 0.0), binding_ts)
                continue
            sid = by_fm[fid]
            sp = spools.get(sid)
            if not sp or sp["archived"] or self.db.latest_strong_decision(key, sid) > binding_ts:
                seen[str(fid)] = record
                continue  # permanent: spool gone, or our own explicit decision is newer
            last = self.db.query_one("SELECT end_ts, end_reason FROM assignments WHERE spool_id=? ORDER BY start_ts DESC "
                                     "LIMIT 1", (sid,))
            ran_out = bool(last and last["end_ts"] and last["end_reason"] == "løb tør")
            if ran_out or (sp.get("weight_known", True) and sp["remaining"] <= 0):
                # An empty spool (it ran out - maybe not deducted yet) is not what sits in the slot now: its row was
                # reused in Handy.
                seen[str(fid)] = record
                self.db.event("warning", f"Bambu Studio/Handy satte den tomme spole #{sid} i {slot_label(key)} – "
                                         f"vælg den nye spole i appen")
                continue
            marks = ",".join("?" * len(PHYSICAL_END_REASONS))
            if self.db.query_one(f"SELECT 1 FROM assignments WHERE slot=? AND spool_id=? AND end_ts > ? "
                                 f"AND end_reason IN ({marks})", (key, sid, binding_ts, *PHYSICAL_END_REASONS)):
                # The spool was in the slot after the binding was made and has physically left it since (taken out,
                # swapped): the binding no longer describes the slot (same rule as obsolete() for exports).
                seen[str(fid)] = record
                continue
            cur = self.db.current_assignment(key)
            if cur and cur["spool_id"] == sid:
                seen[str(fid)] = record
                continue
            if baseline and (cur or any(a["spool_id"] == sid for a in self.db.current_assignments().values())):
                # A binding from before the sync was turned on (its time is unknown) never replaces a spool, and never
                # moves its spool out of the slot it is in: it may only fill an empty slot with a spool from the shelf.
                seen[str(fid)] = record
                continue
            candidates.setdefault(key, []).append((binding_ts, fid, record, sid, sp, pending_since))
        n = 0
        for key, cands in candidates.items():
            cands.sort(key=lambda c: c[0])
            for _, fid, record, _, _, _ in cands[:-1]:
                seen[str(fid)] = record   # superseded by a newer binding of the same slot
            binding_ts, fid, record, sid, sp, pending_since = cands[-1]
            slot = slots.get(key)
            tray_rfids = {x.upper() for x in (slot.tray_uuid, slot.tag_uid) if x} if slot else set()
            transient = (not slot or not slot.present
                         or (slot.material and _mat(slot.material) != _mat(sp["material"]))
                         or (slot.color and sp["color"] and color_distance(slot.color, sp["color"]) > 80)
                         or (tray_rfids and (sp["tag"] or "").upper() not in tray_rfids)
                         or (sp["tag"] and not tray_rfids))   # a Bambu RFID spool is never in a tray without RFID
            if transient:
                if now - pending_since < PENDING_MAX:
                    # Waits for the spool to be in the slot / the printer to agree. Its binding time is kept, so
                    # weight updates (which move updatedAt) can go on meanwhile.
                    self._pending.add(fid)
                    seen[str(fid)] = {**record, "pending": True, "pending_since": pending_since}
                else:
                    seen[str(fid)] = record
                    self.db.event("warning", f"Bambu Studio/Handy satte spole #{sid} i {slot_label(key)}, men printeren "
                                             f"har ikke den spole i slottet – bindingen ignoreres")
                continue
            try:
                done = self.engine.manual_assign(key, sid, link_rfid=False, source="bambu", export=False,
                                                 not_if_strong_after=binding_ts)
            except ValueError:
                done = False
            seen[str(fid)] = record
            if done:
                self.db.event("info", f"Bambu Studio/Handy: spole #{sid} sat i {slot_label(key)}")
                n += 1
        return n

    def _reconcile_exports(self, fm: list[dict], links: dict, seen: dict) -> int:
        """Make FM's slot bindings show what Spool Sync has, where our decision is newer than FM's."""
        dev = (self.engine.device or {}).get("serial")
        if not dev or self._blocked or not fm:
            return 0
        fm_by_id = {int(h["id"]): h for h in fm}
        bound: dict[str, list[int]] = {}
        where: dict[int, str] = {}
        for h in fm:
            key = fm_slot_key(h)
            if key and h.get("devId") == dev and key.startswith("ams:"):
                bound.setdefault(key, []).append(int(h["id"]))
                where[int(h["id"])] = key
        current = self.db.current_assignments()
        by_fm = {v: k for k, v in links.items()}
        link_ts = {r["spool_id"]: r["linked_ts"] for r in self.db.query("SELECT spool_id, linked_ts FROM fm_links")}
        keys = {k for k in set(bound) | set(current) if k.startswith("ams:")}
        n = 0
        now = time.time()

        def changed(fid: int) -> float:
            return float(self._effective.get(fid) or (seen.get(str(fid)) or {}).get("changed_ts") or 0)

        def by_us(fid: int) -> bool:
            return bool((seen.get(str(fid)) or {}).get("by_us")) and fid not in self._effective

        def obsolete(fid: int, key: str) -> bool:
            """The spool of this Studio binding was in the slot and has physically left it since (ran empty,
            swapped): the binding no longer describes the slot."""
            sid = by_fm.get(fid)
            if sid is None:
                return False
            marks = ",".join("?" * len(PHYSICAL_END_REASONS))
            return bool(self.db.query_one(
                f"SELECT 1 FROM assignments WHERE slot=? AND spool_id=? AND end_ts > ? AND end_reason IN ({marks})",
                (key, sid, changed(fid), *PHYSICAL_END_REASONS)))

        for key in sorted(keys):
            rows = bound.get(key, [])
            if any(_rfid(fm_by_id[f].get("RFID")) for f in rows):
                continue  # RFID spools are bound by Studio/Handy through ams/sync
            if any(f in self._pending for f in rows):
                continue  # a Studio/Handy binding for this slot is waiting to be imported - never overwrite it
            strong = self.db.latest_strong_decision(key)
            if key in self._foreign_pending and strong <= self._foreign_pending[key]:
                continue  # an unlinked Studio binding: only a newer explicit decision may replace it
            cur = current.get(key)
            want = links.get(cur["spool_id"]) if cur else None
            if want is not None:
                if want not in fm_by_id:
                    continue  # the wanted row's current mount is unknown
                if want in self._pending or _rfid(fm_by_id[want].get("RFID")):
                    continue  # it was just moved in Studio (import pending), or it is an RFID spool
            have = list(rows)
            if (want and have == [want]) or (not want and not have):
                continue
            foreign = [f for f in have if f != want and not by_us(f) and not obsolete(f, key)]
            if foreign and strong <= max(changed(f) for f in foreign):
                continue  # a Studio binding is only replaced by a newer explicit decision
            if (want is not None and where.get(want) not in (None, key) and not by_us(want)
                    and not (cur["source"] in ("manuel", "rfid")
                             and max(cur["start_ts"], float(cur.get("decided_ts") or 0)) > changed(want))):
                continue  # the wanted row sits in another slot by a Studio binding: only moved by a newer explicit decision
            # Our own earlier export of a row that no longer belongs to the slot's spool (relinked, unlinked,
            # ignored) says nothing about the slot any more.
            stale = {f for f in have if by_us(f) and (cur is None or by_fm.get(f) != cur["spool_id"])}
            fm_changed = max([changed(f) for f in have if f not in foreign and f not in stale]
                             + [float(seen.get(f"slot:{key}") or 0)] + ([changed(want)] if want is not None else []))
            if cur:
                # When the spool went into the slot, or got its current FM row (a relink changes which row belongs
                # there). A row that sits in another slot by a Studio binding is protected above.
                ours = max(cur["start_ts"], float(cur.get("decided_ts") or 0), float(link_ts.get(cur["spool_id"]) or 0))
            else:
                last = self.db.query_one("SELECT MAX(end_ts) t FROM assignments WHERE slot=?", (key,))
                ours = float((last or {}).get("t") or 0)
            if ours <= fm_changed:
                continue  # FM's state is newer (it is imported instead, when it checks out)
            try:
                if not self.bind(key, want):
                    continue
            except CloudError as e:
                log.warning("FM bind of %s failed: %s", key, e)
                continue
            n += 1
            # Record our own write (so it never looks like a Studio/Handy change) and update the in-memory view,
            # so later slots in this loop act on the new state.
            for f in have:
                prev = seen.get(str(f)) or {}
                seen[str(f)] = {"mount": list(UNMOUNTED), "changed_ts": float(prev.get("changed_ts") or 0),
                                "checked_ts": now, "by_us": True}
                where.pop(f, None)
            bound[key] = []
            if want is not None:
                old = where.get(want)
                if old and want in bound.get(old, []):
                    bound[old].remove(want)
                _, unit, tray = key.split(":")
                mod = (self.engine.modules or {}).get(unit) or {}
                seen[str(want)] = {"mount": [dev, int(unit), tray, True, AMS_TYPES.get(mod.get("model"), 1)],
                                   "changed_ts": ours, "checked_ts": now, "by_us": True}
                bound[key] = [want]
                where[want] = key
        return n

    # ---------------------------------------------------------------- sync
    def sync(self) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"skipped": "busy"}
        try:
            return self._sync()
        except AuthExpired as e:
            self.last_error = str(e)
            return {"error": str(e)}
        except (CloudError, SpoolmanError) as e:
            self.last_error = str(e)
            log.warning("FM sync failed: %s", e)
            return {"error": str(e)}
        except Exception as e:  # never let an unexpected answer stop the sync for good
            log.exception("FM sync crashed")
            self.last_error = f"Uventet fejl: {e}"
            return {"error": self.last_error}
        finally:
            self._lock.release()

    def _sync(self) -> dict:
        if not self.enabled:
            return {"skipped": "disabled"}
        self._effective, self._pending, self._foreign_pending, self._blocked = {}, set(), {}, False
        fm = self.fetch()
        raw = self.spoolman.spools(allow_archived=True, max_age=0)
        link_rows = self.db.query("SELECT * FROM fm_links")
        links = {r["spool_id"]: r["fm_id"] for r in link_rows}
        seen = self.db.get("fm_binding_seen", {}) or {}
        if not raw and links:
            # Spoolman answering an empty list while links exist is a glitch, not "everything deleted".
            self.last_error = "Spoolman returnerede ingen spoler – synkronisering sat på pause"
            return {"skipped": "spoolman empty"}
        if not fm and (links or self._had_rows or any(k.isdigit() for k in seen)):
            # Most likely Bambu's empty-list glitch (it can last a quarter of an hour). Nothing is pruned or created
            # from it. When it lasts, the app asks whether every row really was deleted in Studio/Handy
            # (confirm_empty).
            now = time.time()
            empty = self.db.get("fm_empty_since") or {"first": now, "count": 0}
            empty["count"] += 1
            self.db.set("fm_empty_since", empty)
            self.last_error = "Bambu returnerede en tom spoleliste – synkronisering sat på pause"
            return {"skipped": "fm empty"}
        if fm:
            self.db.delete("fm_empty_since")
            self._had_rows = True
            # Rows deleted in Studio/Handy: forget them once they are missing from two complete lists in a row (so
            # an FM that is legitimately empty later is not mistaken for Bambu's empty-list glitch forever).
            ids = {str(h["id"]) for h in fm}
            for k in [k for k in seen if k.isdigit()]:
                if k in ids:
                    self._seen_missing.pop(k, None)
                    continue
                self._seen_missing[k] = self._seen_missing.get(k, 0) + 1
                if self._seen_missing[k] >= MISSING_SYNCS_BEFORE_UNLINK:
                    seen.pop(k)
                    self._seen_missing.pop(k)
        spools = {s["id"]: {**spool_display(s), "weight_known": s.get("remaining_weight") is not None} for s in raw}
        registered = {s["id"]: s.get("registered") for s in raw}
        fm_by_id = {int(h["id"]): h for h in fm}
        stats = {"linked": 0, "weights": 0, "bindings": 0, "exports": 0, "created": 0}
        no_auto = set(self.db.get("fm_no_autocreate", []) or [])

        # 0. prune links whose FM row vanished (twice in a row), whose Spoolman spool was deleted, or whose
        #    Spoolman id now belongs to a different (re-created) spool.
        for r in link_rows:
            sid, fid = r["spool_id"], r["fm_id"]
            gone_fm = fid not in fm_by_id
            self._missing[fid] = self._missing.get(fid, 0) + 1 if gone_fm else 0
            # (The same moment in another text format, e.g. after a Spoolman upgrade, is the same spool.)
            reused = (bool(r.get("spool_registered")) and bool(registered.get(sid))
                      and not _same_time(registered[sid], r["spool_registered"]))
            if gone_fm and self._missing[fid] >= MISSING_SYNCS_BEFORE_UNLINK:
                self.db.execute("DELETE FROM fm_links WHERE fm_id=?", (fid,))
                links.pop(sid, None)
                no_auto.add(sid)   # the user deleted it in Bambu: do not silently create it again
                self.db.execute("DELETE FROM fm_created WHERE fm_id=?", (fid,))
            elif sid not in spools or reused:
                # Spoolman spool deleted (its id may be reused by an unrelated spool): never re-link this row.
                self.db.execute("DELETE FROM fm_links WHERE fm_id=?", (fid,))
                self.db.execute("INSERT OR IGNORE INTO fm_ignored(fm_id) VALUES(?)", (fid,))
                links.pop(sid, None)
        self.db.set("fm_no_autocreate", sorted(no_auto))
        by_fm = {v: k for k, v in links.items()}

        # 1. adopt unconfirmed creates first, then automatic links: marker note (same base material),
        #    RFID (preferred over a manual row), confident unique matches (exact material)
        stats["linked"] += self._resolve_pending_creates(fm, spools, by_fm)
        links = {v: k for k, v in by_fm.items()}
        ignored = self.ignored()
        for h in fm:
            fid = int(h["id"])
            if fid in by_fm or fid in ignored:
                continue
            note = h.get("note") or ""
            sid = None
            if note.startswith(MARKER) and note[len(MARKER):].isdigit() and int(note[len(MARKER):]) in spools:
                cand = spools[int(note[len(MARKER):])]
                if _base(cand["material"]) == _base(h.get("filamentType")):
                    sid = cand["id"]
            elif _rfid(h.get("RFID")):
                sid = next((s["id"] for s in spools.values() if _rfid(s["tag"]) == _rfid(h.get("RFID"))), None)
                if sid in links and not _rfid((fm_by_id.get(links[sid]) or {}).get("RFID")):
                    # The spool was linked to a manual row; the RFID row describes the same physical spool better.
                    old = links.pop(sid)
                    by_fm.pop(old, None)
                    self._retire_manual_row(old, sid)
                    ignored.add(old)   # never handed to another spool, not even later in this sync
                    self.db.event("info", f"Spole #{sid} koblet om til Bambu-spolen med RFID")
            if sid and sid not in links:
                self._link(sid, fid, "auto", registered=registered.get(sid))
                links[sid], by_fm[fid] = fid, sid
                stats["linked"] += 1
        stats["linked"] += self._auto_link_unique(fm, spools, links, by_fm, ignored, registered)
        links = {v: k for k, v in by_fm.items()}

        # 1b. our own duplicate rows (retired for an RFID row) that Bambu kept while they were bound to a slot
        retire = self.db.get("fm_retire", []) or []
        if retire and fm:
            unbound = [i for i in retire if i in fm_by_id and not fm_by_id[i].get("inPrinter")]
            vanished = [i for i in retire if i not in fm_by_id]
            for i in vanished:
                self.db.execute("DELETE FROM fm_created WHERE fm_id=?", (i,))
            if vanished:
                self.db.set("fm_retire", [i for i in retire if i not in vanished])
            if unbound:
                self._delete_retired(unbound)

        # 2. finish creates whose marker note could not be written (only while the note is still empty)
        for r in self.db.query("SELECT fm_id, spool_id FROM fm_created"):
            h = fm_by_id.get(r["fm_id"])
            if h and not (h.get("note") or "").strip() and r["spool_id"] in spools:
                self._finish_created(r["fm_id"], r["spool_id"], self._fm_body(spools[r["spool_id"]]))

        # 3. bindings made in Studio/Handy -> Spool Sync (before our own writes change updatedAt)
        stats["bindings"] = self._import_bindings(fm, spools, by_fm, seen)

        # 4. our assignments -> FM bindings
        stats["exports"] = self._reconcile_exports(fm, links, seen)
        self.db.set("fm_binding_seen", seen)

        # 5. weights: Spoolman -> FM for linked manual spools (archived => 0; unknown weight => leave alone).
        #    Rows whose binding has not been evaluated yet (e.g. the printer was offline) are left alone: a weight PUT
        #    bumps updatedAt, which is the time estimate for that binding. A pending binding keeps its own time.
        for sid, fid in links.items():
            sp, h = spools.get(sid), fm_by_id.get(fid)
            if not sp or not h or _rfid(h.get("RFID")):
                continue
            rec = seen.get(str(fid))
            if (rec is None and h.get("inPrinter")) or (rec is not None and rec.get("mount") != _mount(h)):
                continue
            if sp["archived"]:
                sp = {**sp, "remaining": 0, "weight_known": True}
            if not sp["weight_known"]:
                continue
            try:
                if self._push_weight(h, sp):
                    stats["weights"] += 1
            except CloudError as e:
                log.warning("weight push failed for FM %s: %s", fid, e)

        # 6. optionally create FM spools for spools that are really missing there
        if self.auto_create and self.last_sync:
            for sid in self._missing_in_fm(fm, spools, links, ignored):
                if sid in no_auto:
                    continue
                try:
                    self.create_for(sid)
                    stats["created"] += 1
                except (CloudError, SpoolmanError, ValueError) as e:
                    log.warning("create in FM failed for spool %s: %s", sid, e)
                    break
        self.last_sync = time.time()
        self.last_error = ""
        return stats

    def _retire_manual_row(self, fm_id: int, spool_id: int) -> None:
        """A spool moved from its manual FM row to its RFID row: the manual row is a duplicate now. Remove it when
        Spool Sync created it; otherwise (or if Bambu keeps it) never link it to another spool."""
        self.db.execute("DELETE FROM fm_links WHERE fm_id=?", (fm_id,))
        self.db.execute("INSERT OR IGNORE INTO fm_ignored(fm_id) VALUES(?)", (fm_id,))
        if self.db.query_one("SELECT 1 FROM fm_created WHERE fm_id=?", (fm_id,)):
            retire = set(self.db.get("fm_retire", []) or [])
            retire.add(fm_id)
            self.db.set("fm_retire", sorted(retire))
            self._delete_retired([fm_id], spool_id)

    def _delete_retired(self, fm_ids: list[int], spool_id: int | None = None) -> None:
        """Delete our own duplicate rows. Bambu keeps a row bound to a slot, so this is retried on later syncs."""
        if config.SHADOW:
            self._shadow_skip("DELETE /my/filament/v2/batch")
            return
        try:
            self._call("DELETE", "/my/filament/v2/batch", {"ids": fm_ids})
            left = {int(h["id"]) for h in self.fetch()}
        except CloudError as e:
            log.warning("could not remove duplicate FM rows %s: %s", fm_ids, e)
            return
        if not left:
            return   # an empty answer proves nothing (Bambu's glitch): check again next time
        gone = [i for i in fm_ids if i not in left]
        for i in gone:
            self.db.execute("DELETE FROM fm_created WHERE fm_id=?", (i,))
            self.db.execute("DELETE FROM fm_ignored WHERE fm_id=?", (i,))
        retire = [i for i in (self.db.get("fm_retire", []) or []) if i not in gone]
        self.db.set("fm_retire", retire)
        if gone:
            who = f" for spole #{spool_id}" if spool_id else ""
            self.db.event("info", f"Den manuelle Bambu-spole{who} er fjernet (RFID-spolen bruges)")

    def _push_weight(self, fm: dict, sp: dict) -> bool:
        if config.SHADOW:
            self._shadow_skip("PUT /my/filament/v2")
            return False
        target = int(round(max(sp["remaining"], 0)))
        total = int(round(sp["initial"] or fm.get("totalNetWeight") or 1000))
        if int(fm.get("netWeight") or 0) == target and int(fm.get("totalNetWeight") or 0) == total:
            return False
        self._call("PUT", "/my/filament/v2", {"id": int(fm["id"]), "filamentName": fm.get("filamentName") or sp["name"],
                                              "netWeight": target, "totalNetWeight": total})
        return True

    def _auto_link_unique(self, fm: list[dict], spools: dict, links: dict, by_fm: dict, ignored: set[int],
                          registered: dict) -> int:
        """Link pairs that are each other's only confident candidate (exact material, score >= 0.75)."""
        # Rows marked for a spool (spoolman:<id>) are never handed to another spool.
        free_fm = [h for h in fm if int(h["id"]) not in by_fm and int(h["id"]) not in ignored and not _rfid(h.get("RFID"))
                   and not (h.get("note") or "").startswith(MARKER)]
        free_sp = [s for s in spools.values() if s["id"] not in links and not s["archived"]]
        n = 0
        for h in free_fm:
            cands = [s for s in free_sp if match_score(h, s, exact_material=True) >= 0.75]
            if len(cands) != 1:
                continue
            s = cands[0]
            back = [x for x in free_fm if int(x["id"]) not in by_fm and match_score(x, s, exact_material=True) >= 0.75]
            if len(back) != 1:
                continue
            self._link(s["id"], int(h["id"]), "entydigt match", registered=registered.get(s["id"]))
            links[s["id"]], by_fm[int(h["id"])] = int(h["id"]), s["id"]
            free_sp.remove(s)
            n += 1
        return n

    def _missing_in_fm(self, fm: list[dict], spools: dict, links: dict, ignored: set[int]) -> list[int]:
        """Spoolman spools that are neither linked nor plausibly one of the unlinked/ignored FM rows."""
        by_fm = {v: k for k, v in links.items()}
        free_fm = [h for h in fm if int(h["id"]) not in by_fm]
        pending = self._pending_creates()
        out = []
        for sid, s in spools.items():
            if sid in links or s["archived"] or s["remaining"] <= 0 or not s.get("weight_known", True):
                continue
            if s["tag"]:
                continue  # a Bambu RFID spool: Bambu adds its own row when the spool is loaded
            if str(sid) in pending:
                continue
            if any(_plausible(h, s) for h in free_fm):
                continue
            out.append(sid)
        return out

    # ---------------------------------------------------------------- hooks from the engine
    def on_assignment(self, slot: str, spool_id: int | None) -> None:
        """Spool Sync changed a slot: let the (serialised) sync reconcile FM's bindings right away."""
        if self.enabled and slot.startswith("ams:"):
            self.trigger()

    # ---------------------------------------------------------------- background loop
    def start(self, interval: int = 300) -> None:
        def loop():
            self._stop.wait(20)  # let MQTT deliver the AMS state first
            while not self._stop.is_set():
                try:
                    if self.enabled and self.engine.cloud:
                        self.sync()
                except Exception:
                    log.exception("FM sync loop error")
                self._wake.wait(interval)
                self._wake.clear()
        threading.Thread(target=loop, name="fm-sync", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def trigger(self) -> None:
        """Run a sync soon (after a deduction, a weight change or an assignment)."""
        self._wake.set()

    # ---------------------------------------------------------------- UI data
    def overview(self) -> dict:
        spools: dict = {}
        try:
            spools = {s["id"]: {**spool_display(s), "weight_known": s.get("remaining_weight") is not None}
                      for s in self.spoolman.spools(allow_archived=False)}
        except SpoolmanError:
            pass
        links = self.links()
        by_fm = {v: k for k, v in links.items()}
        ignored = self.ignored()
        unlinked, ignored_rows = [], []
        for h in self.fm_spools:
            fid = int(h["id"])
            if fid in by_fm:
                continue
            ranked = sorted(((match_score(h, s), s["id"]) for s in spools.values() if s["id"] not in links), reverse=True)
            row = {
                "fm_id": fid, "vendor": h.get("filamentVendor") or "", "material": h.get("filamentType") or "",
                "name": h.get("displayName") or h.get("filamentName") or "", "color": _hex(h.get("color")),
                "net": h.get("netWeight"), "total": h.get("totalNetWeight"), "rfid": bool(_rfid(h.get("RFID"))),
                "slot": slot_label(fm_slot_key(h)) if fm_slot_key(h) else "",
                "suggestions": [sid for score, sid in ranked if score >= 0.5][:5],
            }
            (ignored_rows if fid in ignored else unlinked).append(row)
        # Nothing is offered for creation from an empty list we cannot trust (Bambu's empty-list glitch).
        trusted = bool(self.fm_spools) or not self._expect_rows()
        missing = self._missing_in_fm(self.fm_spools, spools, links, ignored) if self.last_sync and trusted else []
        return {
            "enabled": self.enabled, "auto_create": self.auto_create, "empty_long": self.empty_for_long(), "fm_total": len(self.fm_spools),
            "linked": len(links), "linked_spools": sorted(links), "unlinked_fm": unlinked, "ignored_fm": ignored_rows,
            "missing_in_fm": missing, "created_by_us": self.db.query_one("SELECT COUNT(*) c FROM fm_created")["c"],
            "last_sync": self.last_sync, "error": self.last_error,
        }
