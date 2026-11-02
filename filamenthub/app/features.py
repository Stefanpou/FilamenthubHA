"""Optional functions, each off until switched on under Indstillinger > Funktioner.

  plug      a button for the printer's smart plug (a Home Assistant switch)
  auto_off  turn the plug off after a finished print, once the nozzle has cooled down
  photo     a picture of the build plate from a Home Assistant camera when a print finishes
  library   a print library: every finished print with its photo, cover, project file (3MF from Bambu Cloud),
            filaments and cost - searchable, and the file can be downloaded to print it again

Uses the Home Assistant address and token from Elforbrug (power.py). A watcher thread follows the printer state;
files live in DATA_DIR/library/<task id>/ (meta.json, photo.jpg, cover.png, model.3mf).
"""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
import sys
import threading
import time
import zipfile
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, power
from .printer_state import ACTIVE_STATES, print_summary

log = logging.getLogger("spoolsync.features")
router = APIRouter()

KEY = "features"
DEFAULTS = {"plug": False, "plug_entity": "", "auto_off": False, "off_delay_min": 10, "off_max_temp": 50,
            "photo": False, "camera_entity": "", "library": False}
WATCH = 10.0                 # seconds between looks at the printer state
ARCHIVE_TRIES = (60, 120, 300, 600, 1200, 1800)   # Bambu Cloud lists a task a little after it ends
MAX_FILE = 150 * 1024 * 1024
FILES = {"photo.jpg": "image/jpeg", "cover.png": "image/png", "model.3mf": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"}


def _main():
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "engine"):
        m = sys.modules.get("__main__")
    if m is None or not hasattr(m, "engine"):
        from . import main as m
    return m


def settings(db) -> dict:
    return {**DEFAULTS, **(db.get(KEY) or {})}


def _ha(db) -> power.HomeAssistant:
    cfg = power.settings(db)
    if not (cfg["url"] and cfg["token"]):
        raise HTTPException(400, "Indtast Home Assistant-adresse og token under Elforbrug først")
    return power.HomeAssistant(cfg["url"], cfg["token"])


def root():
    return config.DATA_DIR / "library"


def _dir(task_id: str):
    safe = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "-_")[:40]
    if not safe:
        raise HTTPException(400, "Ugyldigt print-id")
    return root() / safe


def _read_meta(d) -> dict:
    try:
        return json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        return {}


def _write_meta(d, meta: dict) -> None:
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False))
    tmp.replace(d / "meta.json")


def _iso_ts(v) -> float | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ============================================================================ the work
def take_photo(db, task_id: str, name: str = "") -> dict:
    """Store a still from the camera as the print's photo."""
    cfg = settings(db)
    if not cfg["camera_entity"]:
        raise HTTPException(400, "Vælg et kamera under Indstillinger > Funktioner")
    img, _ = _ha(db).camera_image(cfg["camera_entity"])
    d = _dir(task_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "photo.jpg").write_bytes(img)
    meta = _read_meta(d)
    meta.setdefault("task_id", str(task_id))
    if name and not meta.get("title"):
        meta["title"] = name
    meta.setdefault("end", time.time())
    meta["photo_ts"] = time.time()
    _write_meta(d, meta)
    return meta


def _find_task(tasks: list[dict], task_id: str, name: str, end: float | None) -> dict | None:
    hit = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
    if hit or not end:
        return hit
    # A local print can carry another id than its cloud task: same title, ended within 30 minutes.
    near = [t for t in tasks if (t.get("title") or "") == name and _iso_ts(t.get("endTime"))
            and abs(_iso_ts(t.get("endTime")) - end) < 1800]
    return near[0] if near else None


def archive(cloud, task: dict, task_id: str | None = None) -> dict:
    """Put a Bambu Cloud task in the library: facts, cover and the project file (3MF)."""
    tid = str(task_id or task.get("id"))
    d = _dir(tid)
    meta = _read_meta(d)
    meta.update({
        "task_id": tid, "cloud_id": str(task.get("id") or ""), "title": task.get("title") or meta.get("title") or "",
        "start": _iso_ts(task.get("startTime")), "end": _iso_ts(task.get("endTime")) or meta.get("end"),
        "status": {2: "finish", 3: "failed"}.get(task.get("status"), str(task.get("status") or "")),
        "weight": task.get("weight"), "seconds": task.get("costTime"), "plate": task.get("plateIndex") or 1,
        "plate_name": task.get("plateName") or "", "design_id": task.get("designId") or None,
        "design_title": task.get("designTitle") or "", "bed": task.get("bedType") or "",
        "printer": task.get("deviceModel") or "", "materials": [
            {"type": m.get("filamentType") or m.get("targetFilamentType") or "", "color": str(m.get("targetColor") or m.get("sourceColor") or "")[:6]}
            for m in task.get("amsDetailMapping") or [] if isinstance(m, dict)],
    })
    d.mkdir(parents=True, exist_ok=True)
    if task.get("cover") and not (d / "cover.png").exists():
        try:
            (d / "cover.png").write_bytes(cloud.download(task["cover"], max_bytes=8 * 1024 * 1024))
        except Exception as e:
            log.info("library %s: no cover (%s)", tid, type(e).__name__)
    if not (d / "model.3mf").exists() and task.get("profileId") and str(task["profileId"]) != "0":
        try:
            prof = cloud.get_profile(task["profileId"], task.get("modelId") or "")
            blob = cloud.download(prof["url"], max_bytes=MAX_FILE) if prof.get("url") else b""
            if blob and not _has_mesh(blob) and task.get("instanceId"):
                blob = cloud.download(cloud.get_instance_3mf(task["instanceId"])["url"], max_bytes=MAX_FILE) or blob
            if blob:
                (d / "model.3mf").write_bytes(blob)
                meta["file_name"] = _file_name(meta["title"])
        except Exception as e:   # (messages may hold signed links: only the type is logged)
            log.info("library %s: no project file (%s)", tid, type(e).__name__)
    meta["archived"] = True
    _write_meta(d, meta)
    return meta


def _has_mesh(blob: bytes) -> bool:
    try:
        return any(n.startswith("3D/Objects/") or n == "3D/3dmodel.model" and len(blob) > 50_000
                   for n in zipfile.ZipFile(io.BytesIO(blob)).namelist())
    except (zipfile.BadZipFile, ValueError):
        return False


def _file_name(title: str) -> str:
    return (re.sub(r"[^\w .()-]+", "", title or "", flags=re.UNICODE).strip()[:80] or "print") + ".3mf"


class Watcher:
    """Follows the printer: photo + library entry when a print finishes, then the plug off once it has cooled."""

    def __init__(self, db):
        self.db = db
        self.prev: tuple[str, str] | None = None
        self.pending: dict | None = db.get("features_pending") or None   # the finished print still being handled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="features", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(WATCH):
            try:
                self.tick()
            except Exception:
                log.exception("features tick failed")

    def _save(self) -> None:
        self.db.set("features_pending", self.pending or {})

    def tick(self, engine=None, now: float | None = None) -> None:
        engine = engine or _main().engine
        now = now or time.time()
        with engine.lock:
            s = print_summary(engine.state) if engine.state else None
            temps = _nozzle_temps(engine.state) if engine.state else []
        cfg = settings(self.db)
        cur = (s["task_id"], s["state"]) if s else None
        if cur and self.prev and cur != self.prev and s["state"] == "FINISH" and self.prev[1] in ACTIVE_STATES and s["task_id"]:
            self.pending = {"task_id": s["task_id"], "name": s["name"], "end": now, "photo": False,
                            "archive_try": 0, "next_archive": now + ARCHIVE_TRIES[0], "archived": False, "off": False}
            self.db.event("info", f"Print færdigt: {s['name'] or s['task_id']}")
            self._save()
        if cur:
            self.prev = cur
        p = self.pending
        if not p:
            return
        if s and s["state"] in ACTIVE_STATES and s["task_id"] != p["task_id"]:
            self.pending = None   # a new print started: the old one is done with (and the plug stays on)
            self._save()
            return
        if cfg["photo"] and not p["photo"]:
            p["photo"] = True   # one attempt: a failed photo must not keep the plug on
            try:
                take_photo(self.db, p["task_id"], p["name"])
            except Exception as e:
                self.db.event("warn", f"Kunne ikke tage billede af pladen: {getattr(e, 'detail', type(e).__name__)}")
            self._save()
        if cfg["library"] and not p["archived"] and now >= p["next_archive"]:
            self._try_archive(engine, p, now)
        if cfg["plug"] and cfg["auto_off"] and cfg["plug_entity"] and not p["off"]:
            waited = now - p["end"]
            cool = bool(temps) and max(temps) <= float(cfg["off_max_temp"])
            if waited >= float(cfg["off_delay_min"]) * 60 and (cool or waited >= (float(cfg["off_delay_min"]) + 20) * 60):
                p["off"] = True
                try:
                    _ha(self.db).call("switch", "turn_off", cfg["plug_entity"])
                    self.db.event("info", f"Printerens stik slukket {round(waited / 60)} min efter printet"
                                          + (f" (dysen {max(temps):.0f} °C)" if temps else ""))
                except Exception as e:
                    self.db.event("warn", f"Kunne ikke slukke printerens stik: {getattr(e, 'detail', type(e).__name__)}")
                self._save()
        done = ((p["photo"] or not cfg["photo"]) and (p["archived"] or not cfg["library"] or p["archive_try"] >= len(ARCHIVE_TRIES))
                and (p["off"] or not (cfg["plug"] and cfg["auto_off"] and cfg["plug_entity"])))
        if done:
            self.pending = None
            self._save()

    def _try_archive(self, engine, p: dict, now: float) -> None:
        cloud = getattr(engine, "cloud", None)
        p["archive_try"] += 1
        if p["archive_try"] < len(ARCHIVE_TRIES):
            p["next_archive"] = now + ARCHIVE_TRIES[p["archive_try"]]
        else:
            p["next_archive"] = float("inf")
        try:
            tasks = cloud.get_tasks((engine.device or {}).get("serial"), limit=20) if cloud else []
            task = _find_task(tasks, p["task_id"], p["name"], p["end"])
            if task:
                archive(cloud, task, p["task_id"])
                p["archived"] = True
            elif p["archive_try"] >= len(ARCHIVE_TRIES):
                meta = _read_meta(_dir(p["task_id"]))   # never listed in the cloud: keep what is known
                _write_meta(_dir(p["task_id"]), {**meta, "task_id": p["task_id"], "title": meta.get("title") or p["name"],
                                                 "end": meta.get("end") or p["end"], "archived": True})
        except Exception as e:
            log.info("library: archiving %s failed (%s)", p["task_id"], type(e).__name__)
        self._save()


def _nozzle_temps(state: dict) -> list[float]:
    from .live import parse_nozzles
    return [n["temp"] for n in parse_nozzles(state) if n.get("temp") is not None]


watcher: Watcher | None = None


def get_watcher() -> Watcher:
    global watcher
    if watcher is None:
        watcher = Watcher(_main().db)
    return watcher


def attach(app) -> None:
    from contextlib import asynccontextmanager
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a):
            get_watcher().start()
            try:
                yield
            finally:
                get_watcher().stop()

    app.router.lifespan_context = lifespan


# ============================================================================ API
class FeaturesIn(BaseModel):
    plug: bool | None = None
    plug_entity: str | None = Field(default=None, max_length=200)
    auto_off: bool | None = None
    off_delay_min: int | None = Field(default=None, ge=0, le=240)
    off_max_temp: int | None = Field(default=None, ge=30, le=120)
    photo: bool | None = None
    camera_entity: str | None = Field(default=None, max_length=200)
    library: bool | None = None


@router.get("/api/features")
def get_features():
    return settings(_main().db)


@router.post("/api/features")
def save_features(body: FeaturesIn):
    db = _main().db
    cfg = settings(db)
    for k, v in body.model_dump(exclude_none=True).items():
        if k.endswith("_entity"):
            v = v.strip()
            if v and not re.fullmatch(r"(switch|light|camera)\.[a-z0-9_]+", v):
                raise HTTPException(400, f"Ugyldig enhed: {v}")
        cfg[k] = v
    db.set(KEY, {k: cfg[k] for k in DEFAULTS})
    return cfg


@router.get("/api/features/entities")
def entities():
    """Switches (the plug) and cameras in Home Assistant, the likely ones first."""
    out = {"switches": [], "cameras": []}
    for st in _ha(_main().db).states():
        eid = str(st.get("entity_id") or "")
        a = st.get("attributes") or {}
        row = {"entity_id": eid, "name": a.get("friendly_name") or eid, "state": st.get("state")}
        if eid.startswith("switch."):
            out["switches"].append(row)
        elif eid.startswith("camera."):
            out["cameras"].append(row)
    likely = ("print", "x1", "p1", "a1", "h2", "x2", "bambu", "3d", "plug", "stik", "shelly")
    for v in out.values():
        v.sort(key=lambda r: (r["state"] == "unavailable",
                              not any(x in (r["entity_id"] + str(r["name"])).lower() for x in likely), str(r["name"]).lower()))
    return out


@router.get("/api/plug")
def plug_state():
    db = _main().db
    cfg = settings(db)
    if not (cfg["plug"] and cfg["plug_entity"]):
        return {"enabled": False}
    st = _ha(db).state(cfg["plug_entity"])
    return {"enabled": True, "entity": cfg["plug_entity"], "on": st.get("state") == "on", "state": st.get("state"),
            "name": (st.get("attributes") or {}).get("friendly_name") or cfg["plug_entity"]}


class PlugIn(BaseModel):
    on: bool
    force: bool = False


@router.post("/api/plug")
def plug_set(body: PlugIn):
    m = _main()
    cfg = settings(m.db)
    if not (cfg["plug"] and cfg["plug_entity"]):
        raise HTTPException(400, "Stikkontakten er ikke slået til under Indstillinger > Funktioner")
    if not body.on and not body.force:
        with m.engine.lock:
            s = print_summary(m.engine.state) if m.engine.state else None
        if s and s["state"] in ACTIVE_STATES:
            raise HTTPException(409, "Printeren er i gang med et print – sluk alligevel?")
    _ha(m.db).call("switch", "turn_on" if body.on else "turn_off", cfg["plug_entity"])
    m.db.event("info", f"Printerens stik {'tændt' if body.on else 'slukket'} fra appen")
    return plug_state() | {"on": body.on}


def _deductions(db, task_ids: list[str]) -> dict[str, list[dict]]:
    if not task_ids:
        return {}
    rows = db.query(f"SELECT task_id, spool_id, SUM(grams) g, material, color FROM deductions WHERE state='applied' "
                    f"AND task_id IN ({','.join('?' * len(task_ids))}) GROUP BY task_id, spool_id", tuple(task_ids))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["task_id"], []).append({"spool_id": r["spool_id"], "grams": round(r["g"], 1),
                                                 "material": r["material"], "color": r["color"]})
    return out


def _entry(d, meta: dict) -> dict:
    files = {f: (d / f).is_file() for f in FILES}
    return {**meta, "has_photo": files["photo.jpg"], "has_cover": files["cover.png"], "has_file": files["model.3mf"],
            "file_size": (d / "model.3mf").stat().st_size if files["model.3mf"] else 0}


def entries(db) -> list[dict]:
    try:
        dirs = [p for p in root().iterdir() if p.is_dir()]
    except OSError:
        return []
    items = [_entry(d, m) for d in dirs if (m := _read_meta(d))]
    items.sort(key=lambda e: e.get("end") or 0, reverse=True)
    return items


@router.get("/api/library")
def library(q: str = Query(default="", max_length=200), limit: int = Query(default=200, ge=1, le=2000)):
    m = _main()
    items = entries(m.db)
    ded = _deductions(m.db, [e["task_id"] for e in items])
    try:
        spools = m._spools_by_id(allow_archived=True)
    except Exception:
        spools = {}
    for e in items:
        e["spools"] = [{**x, "name": " ".join(filter(None, [(spools.get(x["spool_id"]) or {}).get("vendor"),
                                                         (spools.get(x["spool_id"]) or {}).get("name")]))}
                       for x in ded.get(e["task_id"], [])]
        energy = power.energy_for(m.db, [e["task_id"]]).get(e["task_id"])
        e["energy"] = energy
    words = [w for w in q.lower().split() if w]
    if words:
        def text(e):
            return " ".join([e.get("title") or "", e.get("design_title") or "", e.get("plate_name") or "", e.get("printer") or "",
                             " ".join(f"{m_['type']}" for m_ in e.get("materials") or []),
                             " ".join(f"{s['name']} {s.get('material') or ''}" for s in e["spools"])]).lower()
        items = [e for e in items if all(w in text(e) for w in words)]
    return {"items": items[:limit], "total": len(items)}


@router.get("/api/library/latest")
def library_latest():
    items = entries(_main().db)
    return next((e for e in items if e["has_photo"] or e["has_cover"]), None)


@router.get("/api/library/{task_id}/{name}")
def library_file(task_id: str, name: str):
    if name not in FILES:
        raise HTTPException(404, "Findes ikke")
    d = _dir(task_id)
    p = d / name
    if not p.is_file():
        raise HTTPException(404, "Findes ikke")
    if name == "model.3mf":
        return FileResponse(p, media_type=FILES[name], filename=_read_meta(d).get("file_name") or _file_name(""))
    return FileResponse(p, media_type=FILES[name], headers={"Cache-Control": "private, max-age=86400"})


@router.post("/api/library/{task_id}/photo")
def library_photo(task_id: str):
    """Take a new photo now (replaces the old one)."""
    d = _dir(task_id)
    if not (d / "meta.json").exists():
        raise HTTPException(404, "Printet findes ikke i biblioteket")
    take_photo(_main().db, task_id)
    return _entry(d, _read_meta(d))


@router.delete("/api/library/{task_id}")
def library_delete(task_id: str):
    d = _dir(task_id)
    if not d.is_dir():
        raise HTTPException(404, "Findes ikke")
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


_import_lock = threading.Lock()
_import_state: dict = {"running": False, "done": 0, "total": 0, "error": ""}


@router.post("/api/library/import")
def library_import(limit: int = Query(default=50, ge=1, le=200)):
    """Fetch earlier prints from Bambu Cloud into the library (no photos - those only exist from now on)."""
    m = _main()
    cloud = getattr(m.engine, "cloud", None)
    if not cloud:
        raise HTTPException(400, "Log ind på Bambu Cloud under Indstillinger først")
    if not _import_lock.acquire(blocking=False):
        return _import_state
    try:
        tasks = cloud.get_tasks((m.engine.device or {}).get("serial"), limit=limit)
    except Exception as e:
        _import_lock.release()
        raise HTTPException(502, f"Kunne ikke hente printhistorik: {type(e).__name__}") from e
    todo = [t for t in tasks if t.get("id") and t.get("status") in (2, 3) and not _read_meta(_dir(str(t["id"]))).get("archived")]
    _import_state.update(running=True, done=0, total=len(todo), error="")

    def run():
        try:
            for t in todo:
                try:
                    archive(cloud, t)
                except Exception as e:
                    _import_state["error"] = type(e).__name__
                _import_state["done"] += 1
        finally:
            _import_state["running"] = False
            _import_lock.release()

    threading.Thread(target=run, name="library-import", daemon=True).start()
    return _import_state


@router.get("/api/library/import")
def library_import_state():
    return _import_state
