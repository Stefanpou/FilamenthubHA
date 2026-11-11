"""The sync engine: keeps slot -> spool assignments and deducts filament after each print.

Data flow
---------
* Bambu Cloud MQTT (read-only) -> live AMS contents, RFID uuids, print state/progress.
* Bambu Cloud print history (/my/tasks) -> exact grams per AMS slot for every print (slicer data).
* When a print ends (or on the periodic poll) every unprocessed task is turned into one
  deduction per slot and sent to Spoolman (PUT /spool/<id>/use).

Safety rules (no double or silent deductions)
---------------------------------------------
* A (task, slot) pair is deducted at most once (UNIQUE constraint + state machine).
* A row is written as "applying" *before* Spoolman is called and "applied" after. A crash in
  between leaves "uncertain" rows that the user checks manually instead of deducting twice.
* If a slot had no spool assigned, or the print result is unknown, the deduction is parked as
  "pending" and shown in the app so it can be assigned with one tap.
* Only prints started after the app was set up are processed (plus a print that was running
  at setup time), so old history is never deducted again.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import requests

from . import config
from .bambu_cloud import AuthExpired, BambuCloud, CloudError, device_model
from .db import Database
from .mqtt_client import CloudMqtt
from .printer_state import (ACTIVE_STATES, END_STATES, SlotInfo, active_slots, color_distance, merge_print_report,
                            parse_modules, parse_slots, print_summary, slot_from_task_entry, slot_label,
                            slots_from_mapping)
from .spoolman import TAG_FIELD, Spoolman, SpoolmanError, extra_value, spool_display

log = logging.getLogger("spoolsync.engine")

COLOR_MATCH = 70.0   # RGB distance counted as "same colour"
RFID_READ_GRACE = 60  # seconds a tray may read no RFID before a tagged spool in it is considered replaced
FM_ASK_WAIT = 90      # extra wait before asking "which spool?" while a Bambu Filament Manager binding may answer it
QUICK_SWAP_MIN = 15   # an assigned slot empty at least this long (but shorter than EMPTY_DEBOUNCE) may hold another spool
ACTIVE_RECENT = 90    # a slot that fed the nozzle this recently still counts as feeding (multi-filament jobs)
# Assignments Spool Sync made on its own (not by the user or RFID); they may be undone automatically.
AUTOMATIC_SOURCES = {"bambu", "printer", "genindsat", "import"}
# Ends of an assignment that mean the spool physically left the slot (another spool may have been put in).
PHYSICAL_ENDS = {"tom", "løb tør", "filament ændret på printeren", "anden RFID-spole", "flyttet til andet slot",
                 "byttet i slottet", "ukendt spole i slottet", "ukendt RFID-spole i slottet"}


def parse_time(value) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _same_time(a: str, b: str) -> bool:
    """Two Spoolman timestamps for the same moment (a format change after an upgrade is not a new spool)."""
    ta, tb = parse_time(a), parse_time(b)
    return abs(ta - tb) < 1 if ta is not None and tb is not None else str(a) == str(b)


def material_match(a: str, b: str) -> bool:
    a, b = (a or "").strip().upper(), (b or "").strip().upper()
    if not a or not b:
        return True  # unknown on one side: do not flag
    return a == b or a.startswith(b) or b.startswith(a) or a.split("-")[0] == b.split("-")[0]


class Engine:
    def __init__(self, db: Database, spoolman: Spoolman):
        self.db = db
        self.spoolman = spoolman
        self.lock = threading.RLock()
        self._task_lock = threading.Lock()
        self.state: dict = {}
        self.modules: dict = db.get("modules", {}) or {}
        self.cloud: BambuCloud | None = None
        self.mqtt: CloudMqtt | None = None
        self.mqtt_status = "stopped"
        self.mqtt_detail = ""
        self.cloud_error = ""
        self.last_task_poll = 0.0
        self.last_task_poll_ok = 0.0
        self._scheduled_checks: list[float] = []
        self._prev_print: tuple[str, str] | None = None   # (task_id, gcode_state)
        self._last_end_ts = 0.0
        self._running_task: dict | None = None   # cloud task of the print in progress (planned grams per slot)
        self._empty_since: dict[str, float] = {}
        # Material/colour set for each slot and whether it held a spool, as last evaluated. Kept across restarts,
        # so a spool swapped while the service was down is still noticed.
        memory = db.get("slot_memory", {}) or {}
        self._slot_settings: dict[str, tuple] = {k: tuple(v) for k, v in (memory.get("settings") or {}).items()}
        self._slot_present: dict[str, bool] = dict(memory.get("present") or {})
        self._slot_memory_saved = memory
        # The AMS/holder part of the last full report, kept across restarts: after a restart the printer (off or
        # asleep) may not answer for hours, and the slots should still show what is loaded meanwhile.
        self._last_trays: dict = db.get("last_trays", {}) or trays_from_memory(memory)
        # Slots we already sent a "which spool?" notification for, and when to ask for others (after the RFID
        # read grace). Kept across restarts too, so a question due during a restart is not lost.
        self._asked: set[str] = set(memory.get("asked") or [])
        self._tray_seen: dict[str, tuple] = {}   # last material/colour each slot reported while occupied
        self._outbox: list[tuple] = []           # notifications waiting for a successful slot evaluation
        self._ask_due: dict[str, float] = dict(memory.get("ask_due") or {})
        self._empty_runout: dict[str, bool] = {}  # slot went empty while the running job used it
        self._no_rfid_since: dict[str, float] = {}  # present tray without RFID since
        self._no_rfid_checked: set[str] = set()     # ... and evaluated after the read grace ran out
        self._returned: dict[str, tuple] = {}       # assigned slot back after a short empty spell: (empty since, runout)
        self._phys: dict[str, tuple] = {}           # last physical tray state (present, material, colour) per slot
        self._phys_ts: dict[str, float] = {}        # ... and when it last changed (unknown before the service started)
        self._phys_changed: dict[str, float] = {}   # when a slot last changed physically while the service watched
        self._active_ts: dict[str, float] = {}      # when each slot was last seen feeding a nozzle
        self._live_switch: dict = {}                 # live estimate: task entry -> (backup slot, progress at the switch)
        self._feed_seen: tuple | None = None        # (task id, feeding slots) last written to task_progress.feed_log
        self._unstampable: set[int] = set()         # spools whose Spoolman stamp cannot be read (deleted)
        self._free_empty: dict[str, tuple] = {}     # unassigned job slot went empty: (since, while feeding)
        self._slot_signature = None
        self._last_slot_eval = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_expiry_warning = 0.0
        # Hooks set by the web app (Filament Manager sync). Called in a background thread.
        self.assignment_hook = None   # fn(slot, spool_id | None)
        self.deduction_hook = None    # fn()
        self.fm_enabled = lambda: False   # set by the web app: is the Filament Manager sync on?
        self.fm_poke = None               # fn(): run a Filament Manager sync soon
        self._recover_interrupted()

    # ================================================================ lifecycle
    def start(self) -> None:
        self._start_cloud()
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.mqtt:
            self.mqtt.stop()

    def _recover_interrupted(self) -> None:
        rows = self.db.query("SELECT * FROM deductions WHERE state='applying'")
        for r in rows:
            self.db.execute("UPDATE deductions SET state='uncertain', note=? WHERE id=?",
                            ("Programmet stoppede midt i fratrækningen – tjek spolens vægt", r["id"]))
            self.db.event("warning", f"Usikker fratrækning for print {r['task_id']} ({slot_label(r['slot'])}) – tjek vægten")

    @property
    def auth(self) -> dict | None:
        return self.db.get("bambu_auth")

    @property
    def device(self) -> dict | None:
        return self.db.get("device")

    def _start_cloud(self) -> None:
        with self.lock:
            if self.mqtt:
                self.mqtt.stop()
                self.mqtt = None
            auth, device = self.auth, self.device
            if not auth:
                self.cloud = None
                self.mqtt_status = "not_logged_in"
                return
            self.cloud = BambuCloud(config.BAMBU_REGION, auth["token"])
            if not device:
                self.mqtt_status = "no_device"
                return
            self.state = {}
            self._slot_signature = None
            self.mqtt = CloudMqtt(
                host=self.cloud.mqtt_host, uid=auth["uid"], token=auth["token"], serial=device["serial"],
                on_report=self.on_report, on_status=self._on_mqtt_status,
                pushall_interval=config.PUSHALL_INTERVAL,
            )
            self.mqtt.start()

    def _on_mqtt_status(self, status: str, detail: str) -> None:
        prev = self.mqtt_status
        self.mqtt_status, self.mqtt_detail = status, detail
        if status == "auth_failed" and prev != "auth_failed":
            self.db.event("error", "Bambu Cloud afviste forbindelsen – log ind igen under Indstillinger")
            notify("Spool Sync: log ind igen", "Bambu Cloud-login er udløbet. Forbrug registreres, når du logger ind igen.")
        elif status == "connected" and prev != "connected":
            self.db.event("info", "Forbundet til printeren via Bambu Cloud")

    # ================================================================ auth / setup
    def save_login(self, cloud: BambuCloud, login, email: str, method: str) -> None:
        uid = cloud.get_uid()
        self.db.set("bambu_auth", {
            "token": login.token, "refresh_token": login.refresh_token, "uid": uid, "email": email,
            "expires_at": login.expires_at, "obtained_at": time.time(), "method": method,
        })
        self.db.event("info", f"Logget ind på Bambu Cloud ({email or 'token'})")
        devices = cloud.get_devices()
        if not self.device and len(devices) == 1:
            self.select_device(devices[0])
        self.cloud_error = ""
        self._start_cloud()

    def logout(self) -> None:
        self.db.delete("bambu_auth")
        self.db.event("info", "Logget ud af Bambu Cloud")
        self._start_cloud()

    def select_device(self, dev: dict) -> None:
        serial = str(dev.get("dev_id") or dev.get("serial"))
        self.db.set("device", {
            "serial": serial, "name": dev.get("name") or dev.get("dev_product_name") or serial,
            "model": device_model(dev),
            "model_code": dev.get("dev_model_name") or "",
        })
        if self.db.get("baseline_ts") is None:
            self.db.set("baseline_ts", time.time())
        self.db.event("info", f"Printer valgt: {dev.get('name') or serial}")
        self._start_cloud()

    # ================================================================ MQTT input
    def on_report(self, payload: dict) -> None:
        with self.lock:
            raw = payload.get("print") if isinstance(payload.get("print"), dict) else {}
            if raw:
                self.state = merge_print_report(self.state, raw)
            info = payload.get("info")
            if isinstance(info, dict) and info.get("command") == "get_version":
                mods = parse_modules(info)
                if mods:
                    self.modules = mods
                    self.db.set("modules", mods)
            if not self.state:
                return
            now = time.time()
            self._remember_trays(now)
            for key in active_slots(self.state):
                self._active_ts[key] = now
            self._track_print(raw)
            self._evaluate_slots()

    # ================================================================ print tracking
    def _track_print(self, raw: dict) -> None:
        summary = print_summary(self.state)
        tid, gs = summary["task_id"], summary["state"]
        # Only trust a progress value that arrived in this very message: the merged state can still
        # hold the previous print's 100 % until the printer sends a fresh value.
        pct = None
        if "mc_percent" in raw:
            try:
                pct = int(raw["mc_percent"])
            except (TypeError, ValueError):
                pct = None
        if gs == "PREPARE":
            pct = 0
        layer = None
        if "layer_num" in raw:
            try:
                layer = int(raw["layer_num"])
            except (TypeError, ValueError):
                layer = None
        now = time.time()
        prev = self._prev_print
        if tid and gs in ACTIVE_STATES:
            # Which slots the job uses (needed for local prints that never reach Bambu Cloud).
            slots = json.dumps(slots_from_mapping(self.state.get("mapping"))) if "mapping" in self.state else None
            print_type = str(self.state.get("print_type") or "")
            row = self.db.query_one("SELECT * FROM task_progress WHERE task_id=?", (tid,))
            # running_since: when the first layer started. The X2D already says RUNNING while it heats and calibrates
            # (layer 0), and a spool swapped before the first layer was never printed from. Only known when the job
            # was seen before its first layer: 0 = watching for it, NULL = unknown (e.g. first seen mid-print).
            if row is None:
                watching = gs in ("PREPARE", "SLICING") or (gs == "RUNNING" and layer == 0)
                self.db.execute(
                    "INSERT INTO task_progress(task_id, max_percent, last_state, name, first_seen, last_seen, print_type, slots, running_since) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (tid, pct or 0, gs, summary["name"], now, now, print_type, slots, 0.0 if watching else None))
                self.db.event("info", f"Print startet: {summary['name'] or tid}" + (" (lokalt print)" if print_type == "local" else ""))
                self._log_state(tid, now, gs)
            else:
                # Progress above 0 % also means it prints; a printer that reports no layer numbers at all: the
                # switch to RUNNING is the best estimate.
                started = (row.get("running_since") == 0 and gs == "RUNNING"
                           and ((layer is not None and layer >= 1) or (pct is not None and pct >= 1)
                                or "layer_num" not in self.state))
                if (started or (pct is not None and pct > row["max_percent"]) or row["last_state"] != gs
                        or now - row["last_seen"] > 60 or (slots and slots != row["slots"])
                        or (print_type and print_type != row["print_type"])):
                    self.db.execute(
                        "UPDATE task_progress SET max_percent=?, last_state=?, last_seen=?, print_type=COALESCE(NULLIF(?, ''), print_type), "
                        "slots=COALESCE(?, slots), running_since=COALESCE(?, running_since) WHERE task_id=?",
                        (max(row["max_percent"], pct or 0), gs, now, print_type, slots, now if started else None, tid))
                if row["last_state"] != gs:
                    self._log_state(tid, now, gs)
            if slots:
                # Keep every change of the job's slot mapping (AMS auto-refill switches slots mid-print).
                row = self.db.query_one("SELECT slot_history FROM task_progress WHERE task_id=?", (tid,))
                try:
                    hist = json.loads(row["slot_history"]) if row and row.get("slot_history") else []
                except ValueError:
                    hist = []
                if not hist or hist[-1][1] != json.loads(slots):
                    hist.append([now, json.loads(slots)])
                    self.db.execute("UPDATE task_progress SET slot_history=? WHERE task_id=?", (json.dumps(hist[-50:]), tid))
            has_feed = (isinstance((self.state.get("device") or {}).get("extruder", {}).get("info"), list)
                        or "tray_now" in (self.state.get("ams") or {}))
            feed = sorted(active_slots(self.state))
            if has_feed and self._feed_seen != (tid, feed):
                # Which slots fed a nozzle when (multi-filament jobs: a swapped idle colour is not charged).
                row = self.db.query_one("SELECT feed_log FROM task_progress WHERE task_id=?", (tid,))
                try:
                    log_ = json.loads(row["feed_log"]) if row and row.get("feed_log") else []
                except ValueError:
                    log_ = []
                if not log_ or log_[-1][1] != feed:
                    log_.append([now, feed])
                    self.db.execute("UPDATE task_progress SET feed_log=? WHERE task_id=?",
                                    (json.dumps([[round(t), s] for t, s in log_[-4000:]], separators=(",", ":")), tid))
                self._feed_seen = (tid, feed)
        elif tid and gs in END_STATES:
            row = self.db.query_one("SELECT * FROM task_progress WHERE task_id=?", (tid,))
            if row and row["last_state"] != gs:
                self.db.execute("UPDATE task_progress SET last_state=?, last_seen=?, max_percent=? WHERE task_id=?",
                                (gs, now, 100 if gs == "FINISH" else row["max_percent"], tid))
                self._log_state(tid, now, gs)
        was_active = prev is not None and prev[1] in ACTIVE_STATES
        if (gs in END_STATES or gs == "IDLE") and (was_active or prev is None):
            if prev is None or prev != (tid, gs):
                self._last_end_ts = now
                self._schedule_task_checks()
        self._prev_print = (tid, gs)

    def _log_state(self, tid: str, ts: float, gs: str) -> None:
        """Keep the job's state changes (RUNNING/PAUSE/...): a runout is split by real printing time."""
        row = self.db.query_one("SELECT state_log FROM task_progress WHERE task_id=?", (tid,))
        try:
            entries = json.loads(row["state_log"]) if row and row.get("state_log") else []
        except ValueError:
            entries = []
        if not entries or entries[-1][1] != gs:
            entries.append([ts, gs])
            self.db.execute("UPDATE task_progress SET state_log=? WHERE task_id=?", (json.dumps(entries[-200:]), tid))

    def _schedule_task_checks(self) -> None:
        now = time.time()
        self._scheduled_checks = sorted(set(self._scheduled_checks + [now + d for d in (20, 90, 300, 900, 1800)]))

    # ================================================================ slots
    def current_slots(self) -> list[SlotInfo]:
        with self.lock:
            return parse_slots(self.state, self.modules) if self.state else []

    def _remember_trays(self, now: float) -> None:
        units = (self.state.get("ams") or {}).get("ams")
        if not units:
            return
        trays = {"ams": {"ams": units}, "vir_slot": self.state.get("vir_slot"), "vt_tray": self.state.get("vt_tray")}
        if trays != {k: v for k, v in self._last_trays.items() if k != "ts"} or now - self._last_trays.get("ts", 0) > 3600:
            self._last_trays = {**trays, "ts": now}
            self.db.set("last_trays", self._last_trays)

    def display_slots(self) -> tuple[list[SlotInfo], dict | None]:
        """The slots to show: live ones, else the last known ones while the printer has not sent its AMS since the
        service started ({"ts": when they were reported, if known}). Never used for decisions."""
        with self.lock:
            live = self.current_slots()
            if live or not self._last_trays.get("ams"):
                return live, None
            known = parse_slots({k: v for k, v in self._last_trays.items() if k != "ts"}, self.modules)
            return known, {"ts": self._last_trays.get("ts")}

    def _evaluate_slots(self, force: bool = False) -> None:
        slots = parse_slots(self.state, self.modules)
        if not slots:
            self._forget_vanished(set())
            return
        sig = tuple((s.key, s.present, s.material, s.color, s.tray_uuid, s.tag_uid) for s in slots)
        now = time.time()
        if not force and sig == self._slot_signature and not self._empty_since and now - self._last_slot_eval < 300:
            return
        self._slot_signature = sig
        self._last_slot_eval = now
        # Change detection state is only committed when the whole evaluation succeeds; otherwise a
        # settings change seen during a Spoolman outage would be forgotten.
        snapshot = (dict(self._slot_settings), dict(self._slot_present), set(self._asked), dict(self._ask_due))
        self._outbox: list[tuple] = []
        try:
            self._apply_slot_rules(slots, now)
        except SpoolmanError as e:
            log.warning("slot evaluation postponed: %s", e)
            self._slot_settings, self._slot_present, self._asked, self._ask_due = snapshot
            self._slot_signature = None  # retry on next report
            self._outbox = []            # notifications are only sent once the evaluation went through
            return
        memory = {"settings": {k: list(v) for k, v in self._slot_settings.items()}, "present": dict(self._slot_present),
                  "asked": sorted(self._asked), "ask_due": dict(self._ask_due)}
        if memory != self._slot_memory_saved:
            self.db.set("slot_memory", memory)
            self._slot_memory_saved = memory
        for args in self._outbox:
            notify(*args)
        self._outbox = []

    def _apply_slot_rules(self, slots: list[SlotInfo], now: float) -> None:
        assignments = self.db.current_assignments()
        spools_cache: list[dict] | None = None

        def spools() -> list[dict]:
            nonlocal spools_cache
            if spools_cache is None:
                spools_cache = self.spoolman.spools()
            return spools_cache

        # ---- phase 1: presence / empty slots. Needs no Spoolman, so a Spoolman outage never hides a runout.
        self._forget_vanished({s.key for s in slots})
        inserted: dict[str, bool] = {}
        for s in slots:
            cur = assignments.get(s.key)
            inserted[s.key] = self._slot_present.get(s.key) is False and s.present
            self._slot_present[s.key] = s.present
            if not s.present:
                self._asked.discard(s.key)
                self._ask_due.pop(s.key, None)
            phys = (s.present, (s.material or "").upper(), s.color or "")
            prev_phys = self._phys.get(s.key)
            if prev_phys != phys:
                self._phys[s.key] = phys
                self._phys_ts[s.key] = now   # something physical happened in the slot (or it is first seen)
                if prev_phys is not None:
                    self._phys_changed[s.key] = now   # ... a real change, not the first sight after a start
            if s.present:
                self._free_empty.pop(s.key, None)
            if s.kind != "ext" and not s.present:
                self._no_rfid_since.pop(s.key, None)
                self._no_rfid_checked.discard(s.key)
                self._returned.pop(s.key, None)
                if not cur:
                    # A job slot without an assigned spool can run out too: remember it, so the AMS backup slot
                    # that took over is still charged for its part.
                    if prev_phys is not None and prev_phys[0] and s.key not in self._free_empty:
                        self._free_empty[s.key] = (now, self._slot_feeding(s.key))
                    fe = self._free_empty.get(s.key)
                    if fe and now - fe[0] >= config.EMPTY_DEBOUNCE:
                        self._free_empty.pop(s.key)
                        if fe[1]:
                            self._note_runout_event(s.key, fe[0])
                if cur:
                    if s.key not in self._empty_since:
                        self._empty_since[s.key] = now
                        # A runout is decided at the moment the slot goes empty: only while the print is running
                        # and the job uses this slot (not while preparing, paused or idle).
                        self._empty_runout[s.key] = self._slot_feeding(s.key)
                    if now - self._empty_since[s.key] >= config.EMPTY_DEBOUNCE:
                        mat, col = self._tray_seen.get(s.key, (None, None))  # Spoolman may be unreachable now
                        ran_out = self._empty_runout.pop(s.key, False)
                        # The spool left when the slot went empty (not when the debounce ran out).
                        self._unassign(s.key, reason="løb tør" if ran_out else "tom", material=mat, color=col,
                                       ts=self._empty_since[s.key], ran_out=ran_out)
                        self._empty_since.pop(s.key, None)
                        assignments = self.db.current_assignments()
                else:
                    self._empty_since.pop(s.key, None)
                    self._empty_runout.pop(s.key, None)
                continue
            if s.key in self._empty_since and cur and now - self._empty_since[s.key] >= QUICK_SWAP_MIN:
                # Back after a real empty spell that was shorter than the debounce: maybe another spool (phase 2).
                self._returned[s.key] = (self._empty_since[s.key], self._empty_runout.get(s.key, False))
            self._empty_since.pop(s.key, None)
            self._empty_runout.pop(s.key, None)
            if not s.present or s.kind == "ext":
                # (The external spool holder has no RFID reader: no read grace to track.)
                self._no_rfid_since.pop(s.key, None)
                self._no_rfid_checked.discard(s.key)
            if s.present:
                if s.material or s.color:
                    self._tray_seen[s.key] = (s.material or None, s.color or None)
                if s.tray_uuid or s.tag_uid:
                    self._no_rfid_since.pop(s.key, None)
                    self._no_rfid_checked.discard(s.key)
                elif s.kind != "ext" and s.key not in self._no_rfid_since:
                    self._no_rfid_since[s.key] = now
                    self._no_rfid_checked.discard(s.key)

        # ---- phase 2: rules that need Spoolman. A failure only postpones that slot.
        present = {x.key: x.present for x in slots}
        failed = None
        for s in slots:
            if not s.present:
                continue
            try:
                assignments = self._slot_rules(s, assignments, spools, now, inserted.get(s.key, False), present)
                if s.key in self._no_rfid_since and now - self._no_rfid_since[s.key] >= RFID_READ_GRACE:
                    self._no_rfid_checked.add(s.key)
            except SpoolmanError as e:
                failed = e
        if failed:
            raise failed
        self._legacy_import(slots, spools)

    def _note_runout_event(self, slot: str, ts: float) -> None:
        """A slot without an assigned spool ran out during the running print."""
        tid = print_summary(self.state)["task_id"]
        row = self.db.query_one("SELECT runout_events FROM task_progress WHERE task_id=?", (tid,)) if tid else None
        if not row:
            return
        try:
            events = json.loads(row["runout_events"]) if row.get("runout_events") else []
        except ValueError:
            events = []
        events.append([slot, ts])
        self.db.execute("UPDATE task_progress SET runout_events=? WHERE task_id=?", (json.dumps(events[-20:]), tid))
        self.db.event("info", f"{slot_label(slot)} løb tør under printet (ingen spole var tildelt)")

    def _forget_vanished(self, keys: set[str]) -> None:
        """Drop per-slot timers of slots the printer no longer reports (AMS unplugged or re-numbered)."""
        for d in (self._empty_since, self._empty_runout, self._no_rfid_since, self._returned, self._free_empty,
                  self._ask_due):
            for k in [k for k in d if k not in keys]:
                d.pop(k, None)
        self._no_rfid_checked &= keys

    def _eval_due(self, now: float) -> bool:
        """Something time-based is waiting: a tray's RFID read grace ran out and the slot was not evaluated since, an
        empty job slot without an assigned spool is in its debounce, or a "which spool?" question is due."""
        return (any(now - t >= RFID_READ_GRACE and k not in self._no_rfid_checked for k, t in self._no_rfid_since.items())
                or any(now - t >= config.EMPTY_DEBOUNCE for t, _ in self._free_empty.values())
                or any(now >= t for t in self._ask_due.values()))

    def _slot_feeding(self, slot: str) -> bool:
        """Is the running print using this slot right now (or just before the printer switched away from it)?"""
        if (self.state.get("gcode_state") or "") != "RUNNING":
            return False
        tid = print_summary(self.state)["task_id"]
        row = self.db.query_one("SELECT running_since, slot_history FROM task_progress WHERE task_id=?", (tid,)) if tid else None
        if row and row.get("running_since") == 0:
            return False  # still heating/calibrating: nothing has been printed from the slot yet
        mapping = slots_from_mapping(self.state.get("mapping"))
        try:
            hist = json.loads(row["slot_history"]) if row and row.get("slot_history") else []
        except ValueError:
            hist = []
        # AMS auto-refill may already have switched the job to the backup slot when the empty tray is reported.
        if slot not in mapping and not any(slot in entry[1] for entry in hist[-2:]):
            return False
        if len(set(mapping)) > 1 and self._active_ts:
            # A multi-filament job feeds one filament at a time: only the slot loaded in an extruder now can run out,
            # or one that fed just before the job moved on to another slot (AMS auto-refill). Another colour's spool
            # taken out meanwhile (retracted, still in the mapping) is not empty. (Without nozzle data: any job slot.)
            if slot in active_slots(self.state):
                return True
            return slot not in mapping and time.time() - self._active_ts.get(slot, 0) <= ACTIVE_RECENT
        return True

    def _slot_rules(self, s: SlotInfo, assignments: dict, spools, now: float, was_inserted: bool = False,
                    present: dict[str, bool] | None = None) -> dict:
        """Rules for one present slot. Returns the (possibly updated) current assignments."""
        cur = assignments.get(s.key)
        if cur:
            self._asked.discard(s.key)
            self._ask_due.pop(s.key, None)
        # Did the user change material/colour for this slot (on the printer, in Handy or Studio)?
        settings = ((s.material or "").upper(), s.color or "")
        prev_settings = self._slot_settings.get(s.key)
        self._slot_settings[s.key] = settings
        settings_changed = prev_settings is not None and prev_settings != settings

        returned = self._returned.get(s.key)
        if returned and cur and cur["start_ts"] < returned[0]:
            # The slot was empty for a while (shorter than the debounce) and holds a spool again. A spool that ran
            # out cannot come back; a tray that now reads another (or a first) RFID tag holds another spool; and when
            # an identical spool is in stock, it may just as well be that one.
            empty_ts, runout = returned
            rfids = {x.upper() for x in (s.tray_uuid, s.tag_uid) if x}
            if runout:
                gone = True
            elif rfids and s.kind != "ext":
                cur_sp = next((x for x in spools() if x["id"] == cur["spool_id"]), None)
                gone = not cur_sp or extra_value(cur_sp, TAG_FIELD).upper() not in rfids
            else:
                gone = self._twin_in_stock(s, cur["spool_id"], assignments, spools)
            if gone:
                mat, col = self._tray_seen.get(s.key, (None, None))
                self._unassign(s.key, reason="løb tør" if runout else "tom", material=mat, color=col, ts=empty_ts,
                               ran_out=runout)
                if runout:
                    self.db.execute("DELETE FROM slot_last WHERE slot=?", (s.key,))
                cur = None
                assignments = self.db.current_assignments()
        self._returned.pop(s.key, None)

        # ---- RFID (Bambu spools): identify the exact spool
        rfids = {x for x in (s.tray_uuid, s.tag_uid) if x}
        if rfids and s.kind != "ext":
            self._ask_due.pop(s.key, None)  # a Bambu spool: never ask "which spool?"
            matches = [sp for sp in spools() if extra_value(sp, TAG_FIELD).upper() in rfids]
            if matches:
                # If several spools carry the tag, the one already in the slot wins (user corrections stick).
                match = next((sp for sp in matches if cur and sp["id"] == cur["spool_id"]), matches[0])
                if not cur or cur["spool_id"] != match["id"]:
                    # Another tag than the assigned spool's is physical proof of a swap (the print is split there),
                    # and the tag read here proves the spool physically left any other slot.
                    self._assign(s.key, match["id"], "rfid", end_reason="anden RFID-spole" if cur else "erstattet",
                                 moved_reason="flyttet til andet slot")
                    assignments = self.db.current_assignments()
                return assignments
            if cur:
                cur_sp = next((x for x in spools() if x["id"] == cur["spool_id"]), None)
                cur_tag = extra_value(cur_sp, TAG_FIELD).upper() if cur_sp else ""
                d = spool_display(cur_sp) if cur_sp else None
                if cur_tag and cur_tag not in rfids:
                    # A different Bambu spool is now in the slot.
                    self._unassign(s.key, reason="anden RFID-spole", material=None, color=None)
                    assignments = self.db.current_assignments()
                elif not cur_tag and (cur["source"] in AUTOMATIC_SOURCES or (d and (
                        not material_match(d["material"], s.material)
                        or (d["color"] and s.color and color_distance(d["color"], s.color) > 60)))):
                    # An automatically chosen manual spool cannot be the RFID spool now in the tray, and a
                    # Bambu RFID spool that replaced a manual spool faster than the empty-slot debounce is not it.
                    self._unassign(s.key, reason="anden RFID-spole", material=None, color=None)
                    self.db.execute("DELETE FROM slot_last WHERE slot=?", (s.key,))
                    assignments = self.db.current_assignments()
            return assignments  # unknown RFID spool: the app offers to link/create it

        replaced = False
        if (cur and s.kind != "ext" and now - self._no_rfid_since.get(s.key, now) >= RFID_READ_GRACE
                and not (cur["source"] == "manuel" and cur["start_ts"] >= self._no_rfid_since[s.key])):
            # A tagged (Bambu) spool is assigned, but the tray has read no RFID for a while: the spool was replaced
            # by a spool without RFID faster than the empty-slot debounce. (Not when the user picked it after the
            # tray stopped reading RFID: e.g. a damaged tag, or RFID reading switched off.)
            cur_sp = next((x for x in spools() if x["id"] == cur["spool_id"]), None)
            if cur_sp and extra_value(cur_sp, TAG_FIELD):
                self._unassign(s.key, reason="ukendt spole i slottet", material=None, color=None)
                self.db.execute("DELETE FROM slot_last WHERE slot=?", (s.key,))
                assignments = self.db.current_assignments()
                cur, replaced = None, True

        # ---- non-RFID spool re-inserted into the same slot shortly after
        if not cur and not replaced:
            last = self.db.query_one("SELECT * FROM slot_last WHERE slot=?", (s.key,))
            if (last and now - last["emptied_ts"] <= config.REINSERT_WINDOW
                    and (last["material"] or last["color"])       # never match on missing data
                    and material_match(last["material"] or "", s.material)
                    and (not last["color"] or not s.color or color_distance(last["color"], s.color) <= COLOR_MATCH)
                    and not any(a["spool_id"] == last["spool_id"] for a in assignments.values())):
                sp = next((x for x in spools() if x["id"] == last["spool_id"]), None)
                d = spool_display(sp) if sp else None
                # Not a spool that is empty (e.g. ran out) and not an RFID spool in a tray that reads no RFID.
                # And only when no identical twin is in stock: a fresh twin swapped in would otherwise be charged
                # to the old spool - then the user is asked instead.
                twin = self._twin_in_stock(s, last["spool_id"], assignments, spools)
                if sp and not sp.get("archived") and d["remaining"] > 0 and not d["tag"] and not twin:
                    self._assign(s.key, sp["id"], "genindsat")
                    self._ask_due.pop(s.key, None)
                    return self.db.current_assignments()
        if settings_changed and cur:
            # Quick swap: the slot now holds another filament than the assigned spool.
            cur_sp = next((x for x in spools() if x["id"] == cur["spool_id"]), None)
            d = spool_display(cur_sp) if cur_sp else None
            if d and (not material_match(d["material"], s.material)
                      or (d["color"] and s.color and color_distance(d["color"], s.color) > 60)):
                self._unassign(s.key, reason="filament ændret på printeren", material=None, color=None)
                # The swapped-out spool must not come back through the re-insert rule.
                self.db.execute("DELETE FROM slot_last WHERE slot=?", (s.key,))
                assignments = self.db.current_assignments()
                cur = None
        if not cur and (settings_changed or replaced):
            # ---- chosen on the printer screen: material + exact colour match exactly one spool
            sp = self._unique_printer_match(s, spools(), assignments, present)
            if sp:
                self._assign(s.key, sp["id"], "printer")
                self._ask_due.pop(s.key, None)
                return self.db.current_assignments()
        if cur or s.kind == "ext" or s.key in self._asked:
            return assignments
        if (was_inserted or settings_changed or replaced) and s.key not in self._ask_due:
            # New unknown spool: ask on the phone which one it is, once per insertion. Not before the RFID had
            # time to be read (a Bambu spool needs no question), and while the Filament Manager sync is on, not
            # before a binding made in Bambu Studio / Handy had the chance to answer it.
            wait = 0 if replaced else RFID_READ_GRACE
            if self.fm_enabled():
                wait += FM_ASK_WAIT
                if self.fm_poke:
                    self.fm_poke()
            self._ask_due[s.key] = now + wait
        if s.key in self._ask_due and now >= self._ask_due[s.key]:
            self._ask_due.pop(s.key)
            self._asked.add(s.key)
            what = " ".join(x for x in (s.material, s.sub_brand) if x)
            self._outbox.append((f"Ny spole i {s.label}",
                                 f"Hvilken spole har du sat i {s.label}{f' ({what})' if what else ''}? Tryk for at vælge.",
                                 f"/#pick={s.key}"))
        return assignments

    def _unique_printer_match(self, s: SlotInfo, spools: list[dict], assignments: dict,
                              present: dict[str, bool] | None = None) -> dict | None:
        """The only in-stock spool matching the material and exact colour set for the slot on the printer.

        Spools assigned to other slots count as candidates too (a spool being moved is still assigned to its old
        slot for a moment), so an identical twin on the shelf is never picked by mistake. The single match may be
        moved here only if its old slot is empty now.
        """
        if s.kind == "ext" or not s.material or not s.color:
            return None
        where = {a["spool_id"]: slot for slot, a in assignments.items()}
        cands = []
        for sp in spools:
            d = spool_display(sp)
            if (d["archived"] or d["remaining"] <= 0 or d["tag"]
                    or (d["material"] or "").upper() != s.material.upper()
                    or not d["color"] or color_distance(d["color"], s.color) > 12):
                continue
            cands.append(sp)
        if len(cands) != 1:
            return None
        old_slot = where.get(cands[0]["id"])
        if old_slot and old_slot != s.key and (present or {}).get(old_slot, True):
            return None  # it is still sitting in another slot
        return cands[0]

    def _twin_in_stock(self, s: SlotInfo, spool_id: int, assignments: dict, spools) -> bool:
        """Is another untagged spool in stock that looks like what the tray holds and is not in another slot? (One
        still assigned to a slot that is physically empty now is on its way somewhere - perhaps into this slot.)"""
        assigned = {a["spool_id"] for k, a in assignments.items() if (self._phys.get(k) or (True,))[0]}
        return any(
            x["id"] != spool_id and x["id"] not in assigned and not x.get("archived")
            and (xd := spool_display(x))["remaining"] > 0 and not xd["tag"]
            and material_match(xd["material"], s.material)
            and (not xd["color"] or not s.color or color_distance(xd["color"], s.color) <= COLOR_MATCH)
            for x in spools())

    def _spool_tag(self, spool_id: int, spools: list[dict]) -> str:
        sp = next((x for x in spools if x["id"] == spool_id), None)
        return extra_value(sp, TAG_FIELD).upper() if sp else ""

    def _legacy_import(self, slots: list[SlotInfo], spools) -> None:
        """One-time import of 'active_tray' assignments written by SpoolmanSync/ha-bambulab setups."""
        if self.db.get("legacy_import_done"):
            return
        units = sorted({s.unit for s in slots if s.kind == "ams"})
        serial_to_unit = {m.get("serial"): int(u) for u, m in self.modules.items() if m.get("serial")}
        device = self.device or {}
        candidates = []
        for sp in spools():
            val = extra_value(sp, "active_tray")
            if not val or "_tray_" not in val or "_AMS_" not in val:
                continue
            if device.get("serial") and device["serial"] not in val:
                continue
            ams_serial = val.split("_AMS_", 1)[1].rsplit("_tray_", 1)[0]
            try:
                tray_no = int(val.rsplit("_tray_", 1)[1])
            except ValueError:
                continue
            candidates.append((sp, ams_serial, tray_no))
        if not candidates:
            self.db.set("legacy_import_done", True)
            return
        serials = {c[1] for c in candidates}
        if not serial_to_unit:
            if units == [0] and len(serials) == 1:
                serial_to_unit = {next(iter(serials)): 0}
            else:
                return  # wait for module info (get_version)
        assignments = self.db.current_assignments()
        by_key = {s.key: s for s in slots}
        imported = 0
        for sp, ams_serial, tray_no in candidates:
            unit = serial_to_unit.get(ams_serial)
            if unit is None or not 1 <= tray_no <= 4:
                continue
            key = f"ams:{unit}:{tray_no - 1}"
            slot = by_key.get(key)
            if not slot or not slot.present or key in assignments or sp.get("archived"):
                continue
            if any(a["spool_id"] == sp["id"] for a in assignments.values()):
                continue
            disp = spool_display(sp)
            rfids = {x for x in (slot.tray_uuid, slot.tag_uid) if x}
            same_rfid = bool(rfids) and disp["tag"].upper() in rfids
            looks_same = (not rfids and material_match(disp["material"], slot.material)
                          and (not slot.color or not disp["color"] or color_distance(disp["color"], slot.color) <= COLOR_MATCH))
            if same_rfid or looks_same:
                self._assign(key, sp["id"], "import")
                assignments = self.db.current_assignments()
                imported += 1
        self.db.set("legacy_import_done", True)
        if imported:
            self.db.event("info", f"Importerede {imported} eksisterende slot-tildelinger fra Spoolman")

    # ---- hooks ---------------------------------------------------------------
    def _notify_assignment(self, slot: str, spool_id: int | None) -> None:
        hook = self.assignment_hook
        if hook:
            threading.Thread(target=_safe_call, args=(hook, slot, spool_id), daemon=True).start()

    def _notify_deduction(self) -> None:
        hook = self.deduction_hook
        if hook:
            threading.Thread(target=_safe_call, args=(hook,), daemon=True).start()

    # ---- assignment helpers ------------------------------------------------
    def _location_text(self, slot: str) -> str:
        name = (self.device or {}).get("name") or "Printer"
        return f"{name} - {slot_label(slot)}"

    def _assign(self, slot: str, spool_id: int, source: str, export: bool = True, ts: float | None = None,
                end_reason: str | None = None, moved_reason: str | None = None) -> None:
        prev_of_spool = [a for a in self.db.current_assignments().values() if a["spool_id"] == spool_id and a["slot"] != slot]
        end_reason = end_reason or {"bambu": "erstattet af Bambu Studio/Handy",
                                    "printer": "filament ændret på printeren"}.get(source, "erstattet")
        if prev_of_spool and moved_reason is None:
            old = prev_of_spool[0]
            phys = self._phys.get(old["slot"])
            if (phys is not None and not phys[0]) or self._phys_changed.get(old["slot"], 0) > old["start_ts"]:
                # Its old slot is empty (or changed) now: the spool was moved, not a wrong choice corrected.
                moved_reason = "flyttet til andet slot"
        cur = self.db.current_assignment(slot)
        if (end_reason in ("erstattet", "erstattet af Bambu Studio/Handy") and ts is None and cur
                and cur["spool_id"] != spool_id and self._phys_changed.get(slot, 0) > cur["start_ts"]):
            # Something physical happened in the slot since its spool was chosen: a swap, not a correction.
            end_reason = "byttet i slottet"
        # A start in the past (a correction) still ranks as decided now against Bambu Studio/Handy bindings.
        decided = time.time() if ts is not None and ts < time.time() else None
        self.db.assign(slot, spool_id, source, ts=ts, end_reason=end_reason, decided_ts=decided, moved_reason=moved_reason)
        if export:
            for a in prev_of_spool:
                self._notify_assignment(a["slot"], None)
            self._notify_assignment(slot, spool_id)
        try:
            # Spoolman reuses the number of a deleted spool: remember which spool this number was.
            sp = self.spoolman.spool(spool_id)
            self.db.execute("UPDATE assignments SET spool_registered=? WHERE slot=? AND spool_id=? AND end_ts IS NULL",
                            (sp.get("registered"), slot, spool_id))
            tag = extra_value(sp, TAG_FIELD).upper()
            sent = (self.db.get("tray_creating") or {}).get(tag) if tag else None
            reg = parse_time(sp.get("registered"))
            if isinstance(sent, (int, float)) and time.time() - sent < 3600 and (reg is None or reg >= sent - 60):
                # 'Opret i lager' created this spool, but Spoolman's answer was lost: the AMS weight it was created
                # with is still a weighing. (Not a spool that existed before and only got the tag now.)
                self.note_weighed(spool_id, ts=sent)
                self.note_tray_creating(tag, None)
        except SpoolmanError as e:
            log.warning("could not read spool %s: %s", spool_id, e)
        try:
            self.spoolman.update_spool(spool_id, location=self._location_text(slot))
        except SpoolmanError as e:
            log.warning("could not update location: %s", e)
        src = {"rfid": "RFID", "manuel": "manuelt", "genindsat": "genindsat", "import": "import", "bambu": "Bambu Studio/Handy",
               "printer": "valgt på printeren"}.get(source, source)
        moved = f" (flyttet fra {slot_label(prev_of_spool[0]['slot'])})" if prev_of_spool else ""
        self.db.event("info", f"Spole #{spool_id} sat i {slot_label(slot)} ({src}){moved}")

    def _unassign(self, slot: str, reason: str, material: str | None, color: str | None, export: bool = True,
                  ts: float | None = None, ran_out: bool = False) -> None:
        # One timestamp for the assignment end and slot_last (runout detection relies on it).
        ts = ts or time.time()
        existing = self.db.current_assignment(slot)
        if existing and ts < existing["start_ts"]:
            ts = existing["start_ts"]  # never end an assignment before it began (e.g. assigned during the empty spell)
        cur = self.db.unassign(slot, ts=ts, reason=reason)
        if not cur:
            return
        if export:
            self._notify_assignment(slot, None)
        spool_id = cur["spool_id"]
        self.db.execute(
            "INSERT INTO slot_last(slot, spool_id, emptied_ts, material, color) VALUES(?,?,?,?,?) "
            "ON CONFLICT(slot) DO UPDATE SET spool_id=excluded.spool_id, emptied_ts=excluded.emptied_ts, "
            "material=excluded.material, color=excluded.color",
            (slot, spool_id, ts, material, color))
        try:
            sp = self.spoolman.spool(spool_id)
            disp = spool_display(sp)
            if not material and not color:
                self.db.execute("UPDATE slot_last SET material=?, color=? WHERE slot=? AND spool_id=?",
                                (disp.get("material"), disp.get("color"), slot, spool_id))
            if (sp.get("location") or "") == self._location_text(slot):
                self.spoolman.update_spool(spool_id, location="")
        except SpoolmanError as e:
            log.warning("could not clear location: %s", e)
        self.db.event("info", f"Spole #{spool_id} fjernet fra {slot_label(slot)} ({reason})")
        if ran_out:
            self._zero_ran_out(slot, spool_id, cur.get("spool_registered"))

    def _zero_ran_out(self, slot: str, spool_id: int, registered: str | None) -> None:
        """A spool that ran out during the print is empty: set it to 0 g now instead of when the print is processed.
        The row ("<slot>#runout") belongs to the print; its own processing then finds the spool at 0 g."""
        with self.lock:
            tid = str((print_summary(self.state) if self.state else {}).get("task_id") or "")
        try:
            left = max(0.0, float(self.spoolman.spool(spool_id).get("remaining_weight") or 0))
        except SpoolmanError as e:
            log.warning("could not zero spool %s after runout: %s", spool_id, e)
            return   # the print's processing sets it to 0 later
        if left < 0.05:
            return
        note = f"{slot_label(slot)} løb tør under printet – spolen sat til 0 g"
        if tid:
            if self.db.query_one("SELECT 1 FROM deductions WHERE task_id=? AND slot=?", (tid, f"{slot}#runout")):
                return
            if not self._apply_now(tid, f"{slot}#runout", spool_id, left, note, registered=registered):
                return
        else:
            try:
                self.spoolman.use_weight(spool_id, left)
            except SpoolmanError as e:
                log.warning("could not zero spool %s after runout: %s", spool_id, e)
                return
        self.db.event("info", f"Spole #{spool_id} løb tør i {slot_label(slot)} – sat til 0 g (stod til {left:.0f} g)")

    # ---- public (UI) actions -------------------------------------------------
    def manual_assign(self, slot: str, spool_id: int, link_rfid: bool = True, force: bool = False,
                      source: str = "manuel", export: bool = True, not_if_strong_after: float | None = None) -> bool:
        """Put a spool in a slot. With not_if_strong_after, skip (return False) when an explicit decision about the
        slot or spool is newer than that time - checked under the engine lock, after the Spoolman lookup."""
        sp = self.spoolman.spool(spool_id)
        if sp.get("archived"):
            raise ValueError("Spolen er arkiveret")
        with self.lock:
            if not_if_strong_after is not None and self.db.latest_strong_decision(slot, spool_id) > not_if_strong_after:
                return False
            s = next((x for x in self.current_slots() if x.key == slot), None)
            if link_rfid and s and s.kind != "ext":
                rfid = s.tray_uuid or s.tag_uid
                slot_rfids = {x for x in (s.tray_uuid, s.tag_uid) if x}
                existing = extra_value(sp, TAG_FIELD).upper()
                if rfid and existing and existing not in slot_rfids and not force:
                    raise ValueError("Spolen er allerede knyttet til en anden RFID-spole")
                if rfid and existing not in slot_rfids:
                    # The tag belongs to exactly one spool: move it away from any other spool carrying it.
                    for other in self.spoolman.spools(allow_archived=True):
                        if other["id"] != spool_id and extra_value(other, TAG_FIELD).upper() in slot_rfids:
                            self.spoolman.set_extra(other["id"], TAG_FIELD, None)
                            self.db.event("info", f"RFID flyttet fra spole #{other['id']} til #{spool_id}")
                    self.spoolman.set_extra(spool_id, TAG_FIELD, rfid)
                    self.db.event("info", f"RFID fra {slot_label(slot)} knyttet til spole #{spool_id}")
            since = self._correction_start(slot, spool_id) if source == "manuel" else None
            if since is not None:
                # The user corrects an automatic guess and nothing physical happened in the slot since: the chosen
                # spool was there all along (for prints not deducted yet).
                self._assign(slot, spool_id, source, export=export, ts=since, end_reason="rettet")
                self.db.event("info", f"Rettelse: spole #{spool_id} har siddet i {slot_label(slot)} siden "
                                      f"{datetime.fromtimestamp(since).strftime('%d.%m %H:%M')}")
            else:
                self._assign(slot, spool_id, source, export=export)
            self._slot_signature = None
            return True

    def _correction_start(self, slot: str, spool_id: int) -> float | None:
        """When a manual choice replaces an automatic guess with nothing physical happening in the slot since the
        guess, the start of the guess (but after the last print already deducted from the guessed spool)."""
        cur = self.db.current_assignment(slot)
        if not cur or cur["spool_id"] == spool_id or cur["source"] not in AUTOMATIC_SOURCES:
            return None
        if self._phys_ts.get(slot, float("inf")) > cur["start_ts"]:
            return None  # the tray changed after the guess (or the service restarted since): no backdating
        if any(a["spool_id"] == spool_id for a in self.db.current_assignments().values()):
            return None  # the chosen spool sits in another slot right now
        since = cur["start_ts"]
        row = self.db.query_one(
            "SELECT MAX(p.end_time) e FROM deductions d JOIN processed_tasks p ON p.task_id=d.task_id "
            "WHERE d.slot=? AND d.spool_id=? AND d.state='applied'", (slot, cur["spool_id"]))
        done = parse_time(row["e"]) if row and row.get("e") else None
        if done:
            since = max(since, done + 1)
        return since if since < time.time() else None

    def manual_unassign(self, slot: str) -> None:
        with self.lock:
            self._unassign(slot, reason="manuelt", material=None, color=None)
            # Manual removal should not trigger automatic re-insert matching.
            self.db.execute("DELETE FROM slot_last WHERE slot=?", (slot,))
            self._slot_signature = None

    # ================================================================ tasks -> deductions
    def process_tasks(self) -> dict:
        """Fetch cloud print history and deduct every finished, unprocessed print. Returns a summary."""
        if not self._task_lock.acquire(blocking=False):
            return {"skipped": "busy"}
        try:
            return self._process_tasks()
        finally:
            self._task_lock.release()

    def _process_tasks(self) -> dict:
        self.last_task_poll = time.time()
        if not self.cloud or not self.device:
            return {"skipped": "not configured"}
        serial = self.device["serial"]
        try:
            tasks = self.cloud.get_tasks(serial, limit=50)
            self.cloud_error = ""
        except AuthExpired as e:
            self.cloud_error = str(e)
            return {"error": str(e)}
        except (CloudError, requests.RequestException) as e:
            self.cloud_error = str(e)
            log.warning("task poll failed: %s", e)
            return {"error": str(e)}
        self.last_task_poll_ok = time.time()
        try:
            from .power import note_nozzles
            note_nozzles(self.db, tasks)        # 1 or 2 nozzles per print: dual-nozzle prints draw more power
        except Exception:  # noqa: BLE001 - never let this stop filament accounting
            log.exception("noting nozzles failed")
        baseline = float(self.db.get("baseline_ts") or time.time())
        live = print_summary(self.state) if self.state else {"task_id": "", "state": ""}
        done = 0
        # Oldest first so spool history is applied in order.
        for task in sorted(tasks, key=lambda t: parse_time(t.get("startTime")) or 0):
            tid = str(task.get("id") or "")
            if not tid:
                continue
            if self.db.query_one("SELECT 1 FROM processed_tasks WHERE task_id=?", (tid,)):
                continue
            start = parse_time(task.get("startTime"))
            seen_live = self.db.query_one("SELECT * FROM task_progress WHERE task_id=?", (tid,))
            if not seen_live and (start is None or start < baseline - 60):
                continue
            if live.get("task_id") == tid and live.get("state") in ACTIVE_STATES:
                self._running_task = task   # for the live estimate while it prints
                continue  # still printing
            try:
                if self._process_task(task, seen_live):
                    done += 1
            except SpoolmanError as e:
                self.db.event("warning", f"Spoolman utilgængelig – prøver print {tid} igen senere ({e})")
                break
        done += self._process_prints_missing_in_cloud({str(t.get("id")) for t in tasks}, tasks)
        return {"processed": done}

    def _process_prints_missing_in_cloud(self, cloud_ids: set[str], tasks: list[dict]) -> int:
        """Prints seen live that Bambu Cloud has no record of (started from the printer screen / LAN).

        Without developer mode the printer's files cannot be read, so the grams are unknown. They are
        never dropped silently: each used slot becomes a pending item where the user enters the grams.
        """
        if not tasks:
            return 0  # an empty history is more likely an API hiccup than "nothing printed"
        now = time.time()
        oldest_cloud = min((parse_time(t.get("startTime")) or now) for t in tasks)
        rows = self.db.query(
            "SELECT * FROM task_progress WHERE last_state IN ('FINISH','FAILED') "
            "AND task_id NOT IN (SELECT task_id FROM processed_tasks)")
        done = 0
        for row in rows:
            tid = row["task_id"]
            if tid in cloud_ids or row["first_seen"] < oldest_cloud:
                continue  # in the cloud list (handled above) or too old to judge from this page of history
            age = now - row["last_seen"]
            is_local = (row.get("print_type") or "") in ("local", "lan", "sdcard", "usb")
            if age < (600 if is_local else 6 * 3600):
                continue
            slots: list[str] = []
            try:
                # Every slot the job used, also ones it left (AMS auto-refill after a runout).
                for entry in json.loads(row["slot_history"]) if row.get("slot_history") else []:
                    slots += [s for s in entry[1] if s not in slots]
                slots += [s for s in (json.loads(row["slots"]) if row.get("slots") else []) if s not in slots]
            except ValueError:
                pass
            # A spool that ran out is empty: set it to 0 g (its usage up to the runout is then accounted for).
            zero: dict[str, tuple] = {}
            try:
                for slot in slots:
                    r = self._runout(slot, row["first_seen"], row["last_seen"] + 1, row)
                    if r and r["spool_id"]:
                        if self._spool_number_reused(r["spool_id"], r.get("registered")):
                            left = None   # the empty spool was deleted; its number belongs to another spool now
                        else:
                            try:
                                left = max(0.0, float(self.spoolman.spool(r["spool_id"]).get("remaining_weight") or 0))
                            except SpoolmanError as e:
                                if e.status != 404:
                                    raise
                                left = 0.0
                        zero[slot] = (r["spool_id"], left, r)
            except SpoolmanError as e:
                log.warning("local print %s postponed: %s", tid, e)
                continue
            state_txt = "fuldført" if row["last_state"] == "FINISH" else f"afbrudt ved {row['max_percent']} %"
            note = ("Lokalt print – Bambu Cloud har ingen forbrugsdata. Indtast gram (se Bambu Studio), eller vej spolen "
                    "under Spoler og spring over" if is_local else
                    "Printet findes ikke i Bambu Cloud – indtast gram (se Bambu Studio), eller vej spolen under Spoler "
                    "og spring over")
            for slot in slots or ["unknown"]:
                if self.db.query_one("SELECT 1 FROM deductions WHERE task_id=? AND slot=?", (tid, slot)):
                    continue
                if slot in zero:
                    spool_id, left, r = zero[slot]
                    if left is None:
                        self.db.execute(
                            "INSERT INTO deductions(task_id, slot, spool_id, grams, full_grams, material, color, ts, state, note) "
                            "VALUES(?,?,NULL,0,0,NULL,NULL,?,'skipped',?)",
                            (tid, slot, now, f"{slot_label(slot)} løb tør under printet (spole #{spool_id} er slettet i Spoolman)"))
                    elif not self._apply_now(tid, slot, spool_id, left,
                                             f"{slot_label(slot)} løb tør under printet – spolen sat til 0 g" if left >= 0.05
                                             else f"{slot_label(slot)} løb tør under printet (spolen stod allerede til 0 g)",
                                             registered=r.get("registered")):
                        return done   # Spoolman unreachable: try again on the next poll
                    self._undo_put_back(spool_id, r["t"], r.get("registered"))
                    if not r["next"] and not self.db.query_one("SELECT 1 FROM deductions WHERE task_id=? AND slot=?",
                                                               (tid, f"{slot}#2")):
                        # No backup slot took over: the print went on in this slot with a fresh spool.
                        after = self.db.assignment_for_window(slot, r["t"], row["last_seen"])
                        sid, reg, gone = self._named_spool(after if after and after["spool_id"] != spool_id else None)
                        self.db.execute(
                            "INSERT INTO deductions(task_id, slot, spool_id, grams, full_grams, material, color, ts, state, note, "
                            "spool_registered) VALUES(?,?,?,0,0,NULL,NULL,?,'pending',?,?)",
                            (tid, f"{slot}#2", sid, now,
                             f"{note} – forbruget efter at {slot_label(slot)} løb tør ({state_txt}){gone}", reg))
                    continue
                w_start = row["running_since"] if row.get("running_since") and row["running_since"] < row["last_seen"] else row["first_seen"]
                a = self.db.assignment_for_window(slot, w_start, row["last_seen"]) if slot != "unknown" else None
                sid, reg, gone = self._named_spool(a)
                self.db.execute(
                    "INSERT INTO deductions(task_id, slot, spool_id, grams, full_grams, material, color, ts, state, note, "
                    "spool_registered) VALUES(?,?,?,0,0,NULL,NULL,?,'pending',?,?)",
                    (tid, slot, sid, now, f"{note} ({state_txt}){gone}", reg))
            self.db.execute(
                "INSERT OR REPLACE INTO processed_tasks(task_id, processed_ts, status, title, start_time, end_time, factor, factor_source, total_weight) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (tid, now, None, row["name"] or tid,
                 datetime.fromtimestamp(row["first_seen"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 datetime.fromtimestamp(row["last_seen"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 -1, f"{'lokalt print' if is_local else 'ikke i Bambu Cloud'} – {state_txt}, forbrug ukendt", 0))
            self.db.event("warning", f"Print \"{row['name'] or tid}\" har ingen forbrugsdata i Bambu Cloud – indtast gram under Historik")
            notify("Spool Sync: indtast forbrug", f"Printet \"{row['name'] or tid}\" blev ikke sendt via Bambu Cloud. Indtast forbruget i appen.")
            done += 1
        return done

    def _spool_number_reused(self, spool_id: int, registered: str | None) -> bool:
        """Was the spool an assignment row refers to (`registered` = Spoolman's stamp stored on that row) deleted in
        Spoolman, and its number given to a new spool since? Then nothing from that row is charged to the unrelated
        new spool, and slot assignments still pointing at the deleted spool end (not ones of the new spool)."""
        if not registered:
            return False   # a row from before the stamp was stored: cannot tell
        try:
            now_reg = self.spoolman.spool(spool_id).get("registered")
        except SpoolmanError:
            return False   # cannot tell now (use_weight will tell whether Spoolman is reachable)
        if not now_reg or _same_time(now_reg, registered):
            return False
        for slot, a in self.db.current_assignments().items():
            if a["spool_id"] == spool_id and a.get("spool_registered") and not _same_time(a["spool_registered"], now_reg):
                with self.lock:
                    self._unassign(slot, reason="slettet", material=None, color=None)
                    self.db.execute("DELETE FROM slot_last WHERE slot=?", (slot,))
        return True

    def _named_spool(self, a: dict | None) -> tuple[int | None, str | None, str]:
        """The spool (and its stamp) a row for the user names from assignment `a`; none, with a note, when that spool
        was deleted and its number given to another spool since."""
        if not a or not a["spool_id"]:
            return None, None, ""
        if self._spool_number_reused(a["spool_id"], a.get("spool_registered")):
            return None, None, f" – spole #{a['spool_id']} er slettet i Spoolman, og nummeret bruges nu af en anden spole"
        return a["spool_id"], a.get("spool_registered"), ""

    def _undo_put_back(self, spool_id: int, runout_ts: float, registered: str | None = None) -> None:
        """A spool that ran out but was automatically put back into a slot afterwards (a guess) is empty: take it out
        - as of when it was put back, so prints in between that are not processed yet are not charged to it. (Not a
        new spool that got the number after the empty one was deleted: `registered` is the empty spool's stamp.)"""
        for a_slot, a in self.db.current_assignments().items():
            if registered and a.get("spool_registered") and not _same_time(registered, a["spool_registered"]):
                continue
            if a["spool_id"] == spool_id and a["source"] in AUTOMATIC_SOURCES and a["start_ts"] > runout_ts:
                with self.lock:
                    self._unassign(a_slot, reason="løb tør", material=None, color=None, ts=a["start_ts"])
                    self.db.execute("DELETE FROM slot_last WHERE slot=?", (a_slot,))

    def _apply_now(self, tid: str, slot: str, spool_id: int, grams: float, note: str, registered: str | None = None) -> bool:
        """Deduct one row right away (same safety as print deductions). False when Spoolman was not reached."""
        if grams < 0.05:
            self.db.execute(
                "INSERT INTO deductions(task_id, slot, spool_id, grams, full_grams, material, color, ts, state, note) "
                "VALUES(?,?,?,0,0,NULL,NULL,?,'skipped',?)", (tid, slot, spool_id, time.time(), note))
            return True
        cur = self.db.execute(
            "INSERT INTO deductions(task_id, slot, spool_id, grams, full_grams, material, color, ts, state, note, "
            "spool_registered) VALUES(?,?,?,?,0,NULL,NULL,?,'applying',?,?)",
            (tid, slot, spool_id, grams, time.time(), note, registered))
        try:
            self.spoolman.use_weight(spool_id, grams)
        except SpoolmanError as e:
            if e.retryable:
                self.db.execute("DELETE FROM deductions WHERE id=?", (cur.lastrowid,))
                return False
            state = "pending" if e.status and e.status < 500 else "uncertain"
            self.db.execute("UPDATE deductions SET state=?, note=? WHERE id=?", (state, f"Spoolman-fejl: {e}"[:300], cur.lastrowid))
            return True
        self.db.execute("UPDATE deductions SET state='applied' WHERE id=?", (cur.lastrowid,))
        return True

    def _factor_for(self, task: dict, progress: dict | None) -> tuple[float | None, str]:
        status = task.get("status")
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = None
        live_end = progress["last_state"] if progress else None
        if progress and live_end == "FAILED":
            return max(0.0, min(1.0, progress["max_percent"] / 100.0)), f"afbrudt ved {progress['max_percent']} %"
        if status == 2 or live_end == "FINISH":
            return 1.0, "fuldført"
        if status == 3:
            if progress:
                return max(0.0, min(1.0, progress["max_percent"] / 100.0)), f"afbrudt ved {progress['max_percent']} %"
            start, end = parse_time(task.get("startTime")), parse_time(task.get("endTime"))
            cost = float(task.get("costTime") or 0)
            if start and end and cost > 0 and end > start:
                frac = max(0.0, min(1.0, (end - start) / cost))
                return frac, f"afbrudt – anslået {round(frac * 100)} % ud fra printtid"
            return None, "afbrudt – ukendt fremskridt"
        return None, f"ukendt status ({task.get('status')})"

    def _process_task(self, task: dict, progress: dict | None) -> bool:
        tid = str(task["id"])
        if any(self._empty_runout.get(k) for k in list(self._empty_since)) or any(f for _, f in list(self._free_empty.values())):
            return False  # a slot went empty while printing and is still in its debounce: decide the runout first
        status = task.get("status")
        end = parse_time(task.get("endTime"))
        factor, source = self._factor_for(task, progress)
        try:
            status_i = int(status)
        except (TypeError, ValueError):
            status_i = None
        if status_i not in (2, 3):
            if progress and progress["last_state"] in END_STATES:
                # Printer reported the end live; the cloud status just lags. Its endTime is a placeholder.
                end = progress["last_seen"]
            else:
                # Status 4 = running. While running, Bambu's endTime is a placeholder (a few seconds after
                # start), so it must never be used to decide that a print is over. Only give up on a task that
                # stays non-final far beyond its estimated duration, and then let the user decide.
                start_ts = parse_time(task.get("startTime")) or 0
                cost = float(task.get("costTime") or 0)
                if not start_ts or time.time() - start_ts < max(48 * 3600, cost * 3):
                    return False
                factor, source = None, f"ukendt printstatus ({status}) – vælg selv"
        start = parse_time(task.get("startTime")) or (progress["first_seen"] if progress else time.time())
        end = end or (progress["last_seen"] if progress else time.time())
        # Which spool sat in a slot is judged from when the printer really started printing: a spool swapped out
        # during heating/calibration (PREPARE) was never printed from.
        print_start = start
        if progress and progress.get("running_since") and start <= progress["running_since"] < end:
            print_start = progress["running_since"]
        title = task.get("title") or task.get("designTitle") or (progress or {}).get("name") or tid

        per_slot: dict[str, dict] = {}
        total = float(task.get("weight") or 0)
        entries = task.get("amsDetailMapping") or []
        for e in entries:
            slot = slot_from_task_entry(e)
            grams = float(e.get("weight") or 0)
            if grams <= 0:
                continue
            key = slot or "unknown"
            d = per_slot.setdefault(key, {"grams": 0.0, "material": e.get("filamentType") or "",
                                          "color": (e.get("sourceColor") or e.get("targetColor") or "")[:6]})
            d["grams"] += grams
        if not per_slot and total > 0:
            per_slot["unknown"] = {"grams": total, "material": "", "color": ""}
        # A finished print without any usage in Bambu Cloud: ask the user instead of silently booking 0 g.
        no_data = not per_slot and factor is not None and factor > 0

        # Turn every slot into one or more portions (slot row, spool, grams, full grams, note).
        # Pass 1: runouts (any number of filaments). A spool that ran out is physically empty: it is set to 0 g, and
        # the rest of that filament's usage (by real printing time) moves to the slot the job continued from - and
        # on along the chain when that spool ran out as well (AMS backup spools used one after the other).
        portions: list[tuple] = []
        stamps: dict[str, str | None] = {}   # portion key -> Spoolman stamp of the assignment row it is based on
        # Several filaments: a swapped spool is charged by the time its own slot fed the nozzle, not the whole job.
        multi = len([s for s in per_slot if not s.startswith("unknown")]) > 1
        moved: dict[str, dict] = {}
        zeroed: dict[str, dict] = {}
        left_after = self._printing_time(progress, print_start, end)
        if factor is not None:
            for slot in per_slot:
                if slot.startswith("unknown"):
                    continue
                r = self._runout(slot, start, end, progress)
                if not r:
                    continue
                d = per_slot[slot]
                own = d["grams"] * factor
                fed = left_after
                if multi and progress and progress.get("running_since"):
                    # Several filaments: the time this filament itself fed a nozzle, from this slot and the slots
                    # the job continued from after it ran out (not the whole job's printing time). Only for a job
                    # watched from its start: a feed log that starts mid-print would put the unseen time on one slot.
                    chain, t_c, nxt_c = {slot}, r["t"], r["next"]
                    while nxt_c and nxt_c not in chain:
                        chain.add(nxt_c)
                        rn = self._runout(nxt_c, t_c, end, progress)
                        if not (rn and rn["t"] > t_c):
                            break
                        t_c, nxt_c = rn["t"], rn["next"]
                    fed = self._printing_time(progress, print_start, end, feeding=chain) or left_after
                    if r["next"] is None and fed(r["t"]) < 0.02 * fed(print_start):
                        # It went on from a slot we cannot tell (the mapping did not change): keep the whole-job
                        # estimate, so the rest becomes a row for the user instead of vanishing.
                        fed = left_after
                fed_total = fed(print_start)
                share = own * (fed(r["t"]) / fed_total if fed_total > 0 else 0.0)
                zeroed[slot] = dict(r, d=d, used=own - share)
                labels, t, nxt = [slot_label(slot)], r["t"], r["next"]
                while share >= 0.05:
                    if nxt is None or nxt == slot or nxt in zeroed and zeroed[nxt]["t"] <= t:
                        target = f"unknown#{slot}"   # one pending row per filament that ran out
                    else:
                        rn = self._runout(nxt, t, end, progress)
                        if rn and rn["t"] > t:
                            # The next spool ran out too: it is empty now; only the time after goes on.
                            after = share * (fed(rn["t"]) / fed(t) if fed(t) > 0 else 0.0)
                            z = zeroed.setdefault(nxt, dict(rn, d=d, used=0.0))
                            z["used"] += share - after
                            share = after
                            labels.append(slot_label(nxt))
                            t, nxt = rn["t"], rn["next"]
                            continue
                        target = nxt
                    pct = round(100 * fed(t) / fed_total) if fed_total > 0 else 0
                    m = moved.setdefault(target, {"grams": 0.0, "notes": [], "t": t, "d": d})
                    m["grams"] += share
                    m["t"] = min(m["t"], t)
                    m["notes"].append(f"{' og derefter '.join(labels)} løb tør ({pct} % af printet fortsatte herfra)")
                    break
        for slot in [k for k in moved if k in zeroed]:
            # A slot that ran out and later received usage again (refilled): let the user decide.
            m = moved.pop(slot)
            u = moved.setdefault(f"unknown#{slot}", {"grams": 0.0, "notes": [], "t": m["t"], "d": m["d"]})
            u["grams"] += m["grams"]
            u["notes"] += m["notes"]
        for slot, r in zeroed.items():
            d = per_slot.get(slot) or r["d"]
            spool_id = r["spool_id"]
            label = slot_label(slot)
            full = round(per_slot[slot]["grams"], 2) if slot in per_slot else 0.0
            if spool_id is None:
                # No spool was assigned to the slot that ran out: its part up to the runout is the user's to place.
                portions.append((slot, None, round(r["used"], 2), full, d,
                                 f"Ingen spole var tildelt {label} (den løb tør under printet) – vælg spolen", "pending"))
                continue
            if self._spool_number_reused(spool_id, r.get("registered")):
                # The empty spool was deleted and its number now belongs to another (e.g. the refill) spool.
                self._undo_put_back(spool_id, r["t"], r.get("registered"))
                portions.append((slot, None, 0.0, full, d, f"{label} løb tør under printet (spole #{spool_id} er slettet "
                                                           f"i Spoolman)", "skipped"))
                continue
            try:
                left = max(0.0, float(self.spoolman.spool(spool_id).get("remaining_weight") or 0))
            except SpoolmanError as e:
                if e.status != 404:
                    raise   # a read: always safe to try again on the next poll
                left = 0.0  # the spool was deleted in Spoolman
            self._undo_put_back(spool_id, r["t"], r.get("registered"))
            stamps[slot] = r.get("registered")
            if left >= 0.05:
                portions.append((slot, spool_id, round(left, 2), full, d, f"{label} løb tør under printet – spolen sat til 0 g", "apply"))
            else:
                portions.append((slot, spool_id, 0.0, full, d, f"{label} løb tør under printet (spolen stod allerede til 0 g)", "skipped"))
        for m in moved.values():
            m["grams"] = round(m["grams"], 2)

        # Pass 2: every other slot, plus usage moved to it by a runout.
        for slot in list(per_slot) + [k for k in moved if k not in per_slot]:
            if slot in zeroed:
                continue
            own = per_slot.get(slot)
            extra = moved.get(slot)
            d = own or extra["d"]
            full = round(own["grams"], 2) if own else 0.0
            grams = (round(full * factor, 2) if factor is not None else full) if own else 0.0
            if extra:
                grams = round(grams + extra["grams"], 2)
                full = round(full + extra["grams"], 2)
            if factor is not None and grams < 0.05:
                portions.append((slot, None, 0.0, full, d, f"0 g ({source})", "skipped"))
                continue
            note = source if not extra else "; ".join(extra["notes"])
            if slot.startswith("unknown"):
                why = ("Printet fortsatte fra et andet slot efter en spole løb tør – vælg spolen" if extra
                       else "Ukendt slot")
                portions.append((slot, None, grams, full, d, why if factor is not None else source, "pending"))
                continue
            window_start = print_start if own else extra["t"]
            fed_after = (self._printing_time(progress, print_start, end, feeding=slot) if multi else None) or left_after
            segments = self._slot_segments(slot, window_start, end, fed_after) if factor is not None else []
            if len(segments) > 1:
                # The spool in the slot was swapped during the print (no runout, e.g. a low spool replaced during a
                # pause): each spool is charged for the printing time it was in the slot.
                for i, (spool_id, frac, reg) in enumerate(segments):
                    key = slot if i == 0 else f"{slot}#{i + 1}"
                    part = round(grams * frac, 2)
                    if spool_id is None:
                        portions.append((key, None, part, round(full * frac, 2), d,
                                         f"Spolen i {slot_label(slot)} blev skiftet under printet ({round(frac * 100)} % "
                                         f"af tiden) – vælg den nye spole", "pending" if part >= 0.05 else "skipped"))
                        continue
                    stamps[key] = reg
                    portions.append((key, spool_id, part, round(full * frac, 2), d,
                                     f"{note}; spolen blev skiftet under printet ({round(frac * 100)} % af tiden)",
                                     "apply" if part >= 0.05 else "skipped"))
                continue
            if segments:
                # Several spools sat in the slot, but only one of them fed the nozzle (the others while another
                # colour was printing).
                spool_id, stamps[slot] = segments[0][0], segments[0][2]
            else:
                assignment = self.db.assignment_for_window(slot, window_start, end)
                spool_id = assignment["spool_id"] if assignment else None
                stamps[slot] = assignment.get("spool_registered") if assignment else None
            if factor is None or spool_id is None:
                why = source if factor is None else f"Ingen spole var tildelt {slot_label(slot)}"
                portions.append((slot, spool_id, grams, full, d, why, "pending"))
                continue
            portions.append((slot, spool_id, grams, full, d, note, "apply"))

        if no_data:
            portions.append(("unknown", None, 0.0, 0.0, {"material": "", "color": ""},
                             "Bambu Cloud har ingen forbrugsdata – indtast gram", "pending"))

        weighed = self.db.get("weighed", {}) or {}
        pending = 0
        for slot, spool_id, grams, full, d, note, kind in portions:
            if self.db.query_one("SELECT 1 FROM deductions WHERE task_id=? AND slot=?", (tid, slot)):
                continue  # already handled in an earlier (interrupted) run
            if spool_id and kind in ("apply", "pending") and self._spool_number_reused(spool_id, stamps.get(slot)):
                gone = f"spole #{spool_id} er slettet i Spoolman, og nummeret bruges nu af en anden spole – vælg spolen"
                note = gone[0].upper() + gone[1:] if kind == "apply" else f"{note} – {gone}"
                kind, spool_id = "pending", None
            w = float(weighed.get(str(spool_id)) or 0) if spool_id else 0.0
            if kind == "apply" and w > end:
                # The spool was weighed after the print: the weighing already contains this usage.
                kind, note = "skipped", f"{note} – spolen blev vejet efter printet, så forbruget er allerede med"
            elif kind == "apply" and w > print_start:
                kind, note = "pending", f"{note} – spolen blev vejet under printet: tjek forbruget"
            if kind == "skipped":
                self.db.execute(
                    "INSERT INTO deductions(task_id, slot, spool_id, grams, full_grams, material, color, ts, state, note) "
                    "VALUES(?,?,?,0,?,?,?,?,'skipped',?)", (tid, slot, spool_id, full, d["material"], d["color"], time.time(), note))
                continue
            if kind == "pending":
                self.db.execute(
                    "INSERT INTO deductions(task_id, slot, spool_id, grams, full_grams, material, color, ts, state, note, "
                    "spool_registered) VALUES(?,?,?,?,?,?,?,?,'pending',?,?)",
                    (tid, slot, spool_id, grams, full, d["material"], d["color"], time.time(), note,
                     stamps.get(slot) if spool_id else None))
                pending += 1
                continue
            cur = self.db.execute(
                "INSERT INTO deductions(task_id, slot, spool_id, grams, full_grams, material, color, ts, state, note, "
                "spool_registered) VALUES(?,?,?,?,?,?,?,?,'applying',?,?)",
                (tid, slot, spool_id, grams, full, d["material"], d["color"], time.time(), note, stamps.get(slot)))
            row_id = cur.lastrowid
            try:
                self.spoolman.use_weight(spool_id, grams)
            except SpoolmanError as e:
                if e.retryable:
                    self.db.execute("DELETE FROM deductions WHERE id=?", (row_id,))
                    raise
                state = "pending" if e.status and e.status < 500 else "uncertain"
                self.db.execute("UPDATE deductions SET state=?, note=? WHERE id=?", (state, f"Spoolman-fejl: {e}"[:300], row_id))
                pending += 1
                continue
            self.db.execute("UPDATE deductions SET state='applied' WHERE id=?", (row_id,))

        self.db.execute(
            "INSERT OR REPLACE INTO processed_tasks(task_id, processed_ts, status, title, start_time, end_time, factor, factor_source, total_weight) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (tid, time.time(), status_i, title, task.get("startTime"), task.get("endTime"),
             factor if factor is not None else -1, source, total))
        applied = self.db.query_one("SELECT COALESCE(SUM(grams),0) g FROM deductions WHERE task_id=? AND state='applied'", (tid,))["g"]
        msg = f"Print \"{title}\" ({source}): {applied:.1f} g trukket fra"
        if pending:
            msg += f", {pending} afventer din handling"
            notify("Spool Sync: handling kræves", f"Print \"{title}\": forbrug kunne ikke placeres automatisk. Åbn appen.")
        self.db.event("warning" if pending else "info", msg)
        if applied:
            self._notify_deduction()
        return True

    @staticmethod
    def _printing_time(progress: dict | None, first: float, end: float, feeding: str | set[str] | None = None):
        """Function t -> seconds the printer really printed between t and the end (RUNNING, from the first layer;
        pauses and heating/calibration do not count).

        With `feeding` (a slot or a set of slots): only the printing time while one of them fed a nozzle
        (task_progress.feed_log); None when the job has no feed data for them."""
        try:
            entries = json.loads(progress["state_log"]) if progress and progress.get("state_log") else []
        except ValueError:
            entries = []
        spans: list[tuple[float, float]] = []
        run_from = None
        if entries and entries[0][1] in ("RUNNING", "PAUSE"):
            run_from = first   # first seen mid-print: assume it printed until then
            if entries[0][1] == "PAUSE":
                spans.append((first, entries[0][0]))
                run_from = None
        for ts, state in entries:
            if state == "RUNNING" and run_from is None:
                run_from = ts
            elif state != "RUNNING" and run_from is not None:
                spans.append((run_from, ts))
                run_from = None
        if run_from is not None:
            spans.append((run_from, end))
        spans = [(max(a, first), min(b, end)) for a, b in spans if min(b, end) > max(a, first)]
        if not spans:
            spans = [(first, end)]
        if feeding is not None:
            try:
                feed = json.loads(progress["feed_log"]) if progress and progress.get("feed_log") else []
            except ValueError:
                feed = []
            fed: list[tuple[float, float]] = []
            for i, (ts, slots) in enumerate(feed):
                if ({feeding} if isinstance(feeding, str) else set(feeding)) & set(slots):
                    # The first entry also covers the time before it (seen mid-print).
                    fed.append((first if i == 0 else ts, feed[i + 1][0] if i + 1 < len(feed) else end))
            spans = [(max(a, c), min(b, d)) for a, b in spans for c, d in fed if min(b, d) > max(a, c)]
            if not spans:
                return None

        def left_after(t: float) -> float:
            return float(sum(max(0.0, b - max(a, t)) for a, b in spans))
        return left_after

    def _slot_segments(self, slot: str, start: float, end: float, left_after) -> list[tuple[int | None, float]]:
        """The spools that were physically in the slot during [start, end], with their share of the printing time.

        Only a physical swap (the slot went empty or the printer reported another filament / RFID spool) starts a new
        spool's share; a later choice made without one (a re-pick, a correction) covers the time since the last swap.
        A spool's share ends when it physically left; the time until the next spool was chosen belongs to that next
        spool, and when none was chosen before the end, to a spool the user still has to pick (spool None).
        `left_after` is the slot's own feeding time in multi-filament jobs: a spool swapped while its colour was not
        being printed gets no share. Returns (spool_id, share, Spoolman stamp of the row) per spool (a single one when a
        correction replaced the choice, or only one of several spools fed); [] when one row covers the whole print."""
        rows = self.db.query("SELECT * FROM assignments WHERE slot=? AND start_ts < ? AND (end_ts IS NULL OR end_ts > ?) "
                             "ORDER BY start_ts", (slot, end, start))
        segs: list[dict] = []
        used_rows = 0
        for r in rows:
            a, b = max(r["start_ts"], start), min(r["end_ts"] or end, end)
            if b <= a:
                continue
            used_rows += 1
            same = bool(segs) and segs[-1]["spool_id"] == r["spool_id"]
            # The same number with another Spoolman stamp is another spool (the old one deleted, its number reused).
            other_spool = (same and segs[-1]["registered"] and r.get("spool_registered")
                           and not _same_time(segs[-1]["registered"], r["spool_registered"]))
            if segs and not other_spool and (segs[-1]["reason"] not in PHYSICAL_ENDS or same):
                segs[-1].update(spool_id=r["spool_id"], reason=r.get("end_reason"), left=r["end_ts"],
                                registered=r.get("spool_registered") or (segs[-1]["registered"] if same else None))
                continue
            segs.append({"spool_id": r["spool_id"], "reason": r.get("end_reason"), "left": r["end_ts"],
                         "registered": r.get("spool_registered")})
        total = left_after(start)
        if not segs or total <= 0:
            return []
        last = segs[-1]
        if last["reason"] in PHYSICAL_ENDS and last["left"] and last["left"] < end:
            segs.append({"spool_id": None, "reason": None, "left": None, "registered": None})   # nothing chosen since
        bounds = [start] + [s["left"] for s in segs[:-1]] + [end]
        parts = [(s["spool_id"], (left_after(bounds[i]) - left_after(bounds[i + 1])) / total, s["registered"])
                 for i, s in enumerate(segs)]
        if len(parts) > 1 and parts[-1][0] is None and parts[-1][1] < 0.02:
            parts = parts[:-2] + [(parts[-2][0], parts[-2][1] + parts[-1][1], parts[-2][2])]   # taken out right at the end
        if len(parts) < 2:
            # Several choices with no physical swap between them (corrections): the last one covers the whole print.
            return parts if used_rows > 1 and parts and parts[0][0] is not None else []
        fed = [p for p in parts if p[1] >= 0.005]   # spools in the slot while it was not fed (idle colour) get nothing
        if 0 < len(fed) < len(parts):
            s = sum(p[1] for p in fed)
            return [(sid, frac / s, reg) for sid, frac, reg in fed]
        return parts

    def _runout(self, slot: str, start: float, end: float, progress: dict | None) -> dict | None:
        """Did the slot's spool run out during the print?

        Returns {t, spool_id, next}: when it went empty, which spool it was, and the slot the job continued from
        (None if unknown). Evidence: an assignment of the slot ended with end_reason 'løb tør', which is only set
        when the slot went empty while the print was running and the job used the slot. Rows from before that
        reason existed (NULL) count when slot_last was written at the same moment. A slot without an assigned spool
        that ran out is recorded in task_progress.runout_events (spool_id None).
        """
        rows = self.db.query("SELECT * FROM assignments WHERE slot=? AND end_ts > ? AND end_ts < ? ORDER BY end_ts",
                             (slot, start, end))
        last = self.db.query_one("SELECT * FROM slot_last WHERE slot=?", (slot,))
        found = None
        for r in rows:
            reason = r.get("end_reason")
            legacy = (reason is None and last and last["spool_id"] == r["spool_id"]
                      and abs(last["emptied_ts"] - r["end_ts"]) <= 10)
            if reason == "løb tør" or legacy:
                found = (r["end_ts"], r["spool_id"], r.get("spool_registered"))
                break
        try:
            events = json.loads(progress["runout_events"]) if progress and progress.get("runout_events") else []
        except ValueError:
            events = []
        for ev_slot, ts in events:
            if ev_slot == slot and start < ts < end and (found is None or ts < found[0]):
                found = (ts, None, None)
        if not found:
            return None
        return {"t": found[0], "spool_id": found[1], "registered": found[2],
                "next": self._continued_from(slot, found[0], progress)}

    @staticmethod
    def _continued_from(slot: str, t: float, progress: dict | None) -> str | None:
        """The slot that replaced `slot` in the job's mapping around time t (AMS auto-refill), if unambiguous."""
        try:
            hist = json.loads(progress["slot_history"]) if progress and progress.get("slot_history") else []
        except ValueError:
            hist = []
        # The mapping in force when the slot ran out (a later return to the refilled slot does not count).
        before = None
        for i, (ts, slots) in enumerate(hist):
            if ts > t + 10:
                break
            if slot in slots:
                before = i
        if before is not None:
            for ts, slots in hist[before + 1:]:
                new = [s for s in slots if s not in hist[before][1]]
                if slot not in slots:
                    return new[0] if len(new) == 1 else None
        # Older prints without history: a single-filament job whose live mapping now points elsewhere.
        try:
            live = json.loads(progress["slots"]) if progress and progress.get("slots") else []
        except ValueError:
            live = []
        others = [s for s in live if s != slot]
        return others[0] if len(others) == 1 and len(live) == 1 else None

    def resolve_deduction(self, deduction_id: int, spool_id: int | None, grams: float | None = None,
                          gross_weight: float | None = None, force: bool = False) -> None:
        row = self.db.query_one("SELECT * FROM deductions WHERE id=?", (deduction_id,))
        if not row:
            raise ValueError("Ukendt post")
        if row["state"] not in ("pending", "uncertain"):
            raise ValueError("Posten er allerede håndteret")
        if spool_id is None:
            self.db.execute("UPDATE deductions SET state='skipped', note=COALESCE(note,'') || ' – sprunget over' "
                            "WHERE id=? AND state IN ('pending','uncertain')", (deduction_id,))
            self.db.event("info", f"Fratrækning for {slot_label(row['slot'])} sprunget over")
            return
        if row["state"] == "uncertain":
            # May already have been deducted before a crash – never deduct it a second time automatically.
            raise ValueError("Denne post kan allerede være trukket fra. Tjek spolens vægt og tryk 'Vægten er tjekket'")
        raw_sp = self.spoolman.spool(spool_id)
        if (spool_id == row["spool_id"] and row.get("spool_registered") and raw_sp.get("registered")
                and not _same_time(raw_sp["registered"], row["spool_registered"])):
            # e.g. a page opened before the reuse: the row's spool was deleted and this is another spool.
            raise ValueError(f"Spole #{spool_id} er slettet i Spoolman, og nummeret bruges nu af en anden spole – "
                             f"vælg den spole, printet kom fra")
        sp = spool_display(raw_sp)
        if gross_weight is not None:
            # The user weighed the spool instead: the spool gets the measured weight (up or down, like weighing under
            # Spoler), and the row records what was missing from its old remaining weight.
            if sp["spool_weight"] is None:
                raise ValueError("Spolens tomvægt kendes ikke – indtast forbruget i gram, eller vej spolen under Spoler")
            net = max(0.0, gross_weight - sp["spool_weight"])
            g = round(max(0.0, sp["remaining"] - net), 2)
            cur = self.db.execute("UPDATE deductions SET state='applying', spool_id=?, grams=?, spool_registered=? "
                                  "WHERE id=? AND state='pending'",
                                  (spool_id, g, raw_sp.get("registered"), deduction_id))
            if cur.rowcount == 0:
                raise ValueError("Posten er allerede håndteret")
            try:
                self.spoolman.measure(spool_id, gross_weight)
            except SpoolmanError as e:
                # Setting a weight is safe to repeat: the row goes back as it was. When the answer was lost, the
                # weight may be set already - record the weighing, so typing grams afterwards is not charged on top.
                if not (e.retryable or e.status):
                    self.note_weighed(spool_id)
                self.db.execute("UPDATE deductions SET state='pending', spool_id=?, grams=?, spool_registered=? WHERE id=?",
                                (row["spool_id"], row["grams"], row.get("spool_registered"), deduction_id))
                raise
            self.db.execute("UPDATE deductions SET state='applied', note=COALESCE(note,'') || ? WHERE id=?",
                            (f" – vejet: {net:.0f} g tilbage", deduction_id))
            self.note_weighed(spool_id)
            self.db.event("info", f"Spole #{spool_id} vejet ved tildeling: {net:.0f} g tilbage")
            return
        g = float(grams) if grams is not None else float(row["grams"])
        task = self.db.query_one("SELECT end_time FROM processed_tasks WHERE task_id=?", (row["task_id"],))
        printed = parse_time(task["end_time"]) if task and task.get("end_time") else None
        weighed = float((self.db.get("weighed", {}) or {}).get(str(spool_id)) or 0)
        if not force and printed and weighed > printed:
            raise ValueError(f"Spole #{spool_id} er vejet efter printet, så forbruget er allerede med i vægten. "
                             f"Spring posten over – eller træk fra alligevel.")
        if g < 0:
            raise ValueError("Negativ vægt")
        if g <= 0 and float(row["full_grams"] or 0) <= 0:
            raise ValueError("Indtast forbruget i gram")
        if not force and g > max(sp["remaining"] * 1.1, sp["remaining"] + 50):
            raise ValueError(f"Spole #{spool_id} har kun {sp['remaining']:.0f} g tilbage, men der skal trækkes {g:.0f} g fra. "
                             f"Er det vægten på vægten? Så vej spolen i stedet.")
        # Claim the row atomically: a double tap or a second device cannot deduct it twice.
        cur = self.db.execute("UPDATE deductions SET state='applying', spool_id=?, grams=?, spool_registered=? "
                              "WHERE id=? AND state='pending'",
                              (spool_id, g, raw_sp.get("registered"), deduction_id))
        if cur.rowcount == 0:
            raise ValueError("Posten er allerede håndteret")
        if g < 0.05:
            self.db.execute("UPDATE deductions SET state='applied', note=COALESCE(note,'') || ' – intet forbrug' "
                            "WHERE id=?", (deduction_id,))
            return
        try:
            self.spoolman.use_weight(spool_id, g)
        except SpoolmanError as e:
            if e.retryable or (e.status and e.status < 500):
                # Never reached Spoolman, or definitively refused: nothing was deducted; the row is as it was.
                self.db.execute("UPDATE deductions SET state='pending', spool_id=?, grams=?, spool_registered=? WHERE id=?",
                                (row["spool_id"], row["grams"], row.get("spool_registered"), deduction_id))
                raise
            # No answer: Spoolman may have deducted it. Never deduct it again automatically.
            self.db.execute("UPDATE deductions SET state='uncertain', note=COALESCE(note,'') || "
                            "' – Spoolman svarede ikke: tjek spolens vægt' WHERE id=?", (deduction_id,))
            raise ValueError("Spoolman svarede ikke, så fratrækningen kan være gennemført. Tjek spolens vægt, og tryk "
                             "'Vægten er tjekket'") from e
        self.db.execute("UPDATE deductions SET state='applied', note=COALESCE(note,'') || ' – tildelt manuelt' WHERE id=?", (deduction_id,))
        self.db.event("info", f"{g:.1f} g trukket fra spole #{spool_id} (manuelt)")
        self._notify_deduction()

    def note_tray_creating(self, tag: str, sent: float | None) -> None:
        """'Opret i lager' sends a spool with this RFID tag to Spoolman (`sent`), or has its answer (None). A spool with
        the tag that turns up meanwhile (the answer was lost) is assigned with its AMS weight as a weighing."""
        now = time.time()
        m = {k: v for k, v in (self.db.get("tray_creating") or {}).items()
             if isinstance(v, (int, float)) and now - v < 3600 and k != tag}
        if sent:
            m[tag] = sent
        self.db.set("tray_creating", m)

    def note_weighed(self, spool_id: int, ts: float | None = None) -> None:
        """The user set the spool's weight (weighed it): prints that ended before this (or `ts`, when the weight was
        taken earlier) are already in that weight. A later weighing is never moved back."""
        w = self.db.get("weighed", {}) or {}
        w[str(spool_id)] = max(ts or time.time(), float(w.get(str(spool_id)) or 0))
        if len(w) > 500:
            w = dict(sorted(w.items(), key=lambda kv: kv[1])[-500:])
        self.db.set("weighed", w)

    def mark_checked(self, deduction_id: int) -> None:
        """User confirmed an 'uncertain' row (weight checked)."""
        self.db.execute("UPDATE deductions SET state='applied', note=COALESCE(note,'') || ' – kontrolleret' WHERE id=? AND state='uncertain'",
                        (deduction_id,))

    # ================================================================ background loop
    def _loop(self) -> None:
        while not self._stop.wait(5):
            now = time.time()
            try:
                due = [t for t in self._scheduled_checks if t <= now]
                active = bool(self.state) and print_summary(self.state)["state"] in ACTIVE_STATES
                recent = now - self._last_end_ts < 3600
                interval = config.TASK_POLL_INTERVAL if (active or recent or self.mqtt_status != "connected") else 900
                if due or now - self.last_task_poll >= interval:
                    self._scheduled_checks = [t for t in self._scheduled_checks if t > now]
                    self.process_tasks()
                with self.lock:
                    if self.state and (self._empty_since or self._eval_due(now) or now - self._last_slot_eval > 300):
                        self._evaluate_slots(force=True)
                self._check_token_expiry(now)
                self._watchdog(now)
                self._stamp_assignments()
            except Exception:
                log.exception("engine loop error")

    def _check_token_expiry(self, now: float) -> None:
        auth = self.auth
        if not auth or not auth.get("expires_at"):
            return
        left = auth["expires_at"] - now
        if left < config.TOKEN_WARN_DAYS * 86400 and now - self._last_expiry_warning > 86400:
            self._last_expiry_warning = now
            days = max(0, int(left // 86400))
            self.db.event("warning", f"Bambu-login udløber om {days} dage – log ind igen under Indstillinger")
            notify("Spool Sync: log ind igen snart", f"Bambu-login udløber om {days} dage.")

    def _stamp_assignments(self) -> None:
        """Assignments made before Spoolman's stamp was stored get the spool's current one (the best truth there is),
        so that a later reuse of the number is noticed."""
        for a in self.db.query("SELECT id, spool_id FROM assignments WHERE end_ts IS NULL AND spool_registered IS NULL"):
            if a["spool_id"] in self._unstampable:
                continue
            try:
                reg = self.spoolman.spool(a["spool_id"]).get("registered")
            except SpoolmanError as e:
                if e.status != 404:
                    return   # Spoolman not reachable: next round
                reg = None
            if not reg:
                self._unstampable.add(a["spool_id"])
                continue
            self.db.execute("UPDATE assignments SET spool_registered=? WHERE id=? AND spool_registered IS NULL", (reg, a["id"]))

    def _watchdog(self, now: float) -> None:
        m = self.mqtt
        if m and m.status == "connected" and m.last_message and now - m.last_message > 1200:
            m.last_message = now
            m.request_pushall()

    # ================================================================ status for UI
    def live_usage(self) -> dict[int, float]:
        """Spool id -> grams the print in progress has used so far (estimate: the slicer's grams per slot x the
        printer's progress). Display only - nothing is written; the real deduction replaces it when the print is
        processed. Kept after the print ends until then, so the spool does not jump back up in between. Empty for a
        print Bambu Cloud has no record of (no planned grams)."""
        task = self._running_task
        if not task:
            return {}
        tid = str(task.get("id") or "")
        with self.lock:
            live = print_summary(self.state) if self.state else {}
        if live.get("task_id") == tid and live.get("state") in ACTIVE_STATES:
            frac = live["percent"] / 100.0
        else:
            prog = self.db.query_one("SELECT * FROM task_progress WHERE task_id=?", (tid,))
            if not prog or prog["last_state"] not in END_STATES or time.time() - prog["last_seen"] > 24 * 3600:
                return {}
            frac = 1.0 if prog["last_state"] == "FINISH" else prog["max_percent"] / 100.0
        if (self.db.query_one("SELECT 1 FROM processed_tasks WHERE task_id=?", (tid,))
                or self.db.query_one("SELECT 1 FROM deductions WHERE task_id=? AND slot NOT LIKE '%#runout'", (tid,))):
            self._running_task = None
            return {}
        frac = max(0.0, min(1.0, frac))
        assignments = self.db.current_assignments()
        entries = [e for e in task.get("amsDetailMapping") or [] if slot_from_task_entry(e)]
        # The AMS switches to a backup spool when one runs out ("AMS filament backup"); the printer's own mapping
        # then names the new slot while the cloud task keeps the old one. Count the new spool from the switch on.
        with self.lock:
            now_slots = slots_from_mapping(self.state.get("mapping")) if self.state else []
        sw = self._live_switch if self._live_switch.get("tid") == tid else {"tid": tid}
        self._live_switch = sw
        live_ok = live.get("task_id") == tid
        out: dict[int, float] = {}
        for i, e in enumerate(entries):
            slot, start = slot_from_task_entry(e), 0.0
            if live_ok and len(now_slots) == len(entries) and now_slots[i] != slot:
                prev = sw.get(i)
                if not prev or prev[0] != now_slots[i]:
                    sw[i] = prev = (now_slots[i], frac)
                slot, start = prev
            elif not live_ok and i in sw:
                slot, start = sw[i]
            w = float(e.get("weight") or 0)
            # (the spool that ran out keeps what it gave before the switch, while it is still assigned)
            for sl, grams in ((slot, w * max(0.0, frac - start)), (slot_from_task_entry(e), w * start if start else 0.0)):
                a = assignments.get(sl) if sl else None
                if a and a["spool_id"] and grams > 0:
                    out[a["spool_id"]] = out.get(a["spool_id"], 0.0) + grams
        return {k: round(v, 2) for k, v in out.items()}

    def status(self) -> dict:
        auth = self.auth
        with self.lock:
            summary = print_summary(self.state) if self.state else None
            slots, slots_from = self.display_slots()
        return {
            "bambu": {
                "logged_in": bool(auth),
                "email": (auth or {}).get("email"),
                "expires_at": (auth or {}).get("expires_at"),
                "method": (auth or {}).get("method"),
                "mqtt": self.mqtt_status,
                "mqtt_detail": self.mqtt_detail,
                "last_message": self.mqtt.last_message if self.mqtt else 0,
                "cloud_error": self.cloud_error,
                "last_task_poll_ok": self.last_task_poll_ok,
            },
            "device": self.device,
            "print": summary,
            "slots": [s.to_dict() for s in slots],
            "slots_stale": slots_from,   # set: the printer has not reported its AMS yet, these are the last known
            "assignments": self.db.current_assignments(),
            "pending": self.db.query_one(
                "SELECT COUNT(*) c FROM deductions WHERE state IN ('pending','uncertain')")["c"],
        }


def trays_from_memory(memory: dict) -> dict:
    """A minimal AMS/holder report rebuilt from the stored slot memory (material, colour, present per slot), for
    installs that have not stored a full one yet."""
    settings, present = memory.get("settings") or {}, memory.get("present") or {}
    units: dict[int, list] = {}
    ext = []
    for key in sorted(set(settings) | set(present)):
        mat, col = (list(settings.get(key) or []) + ["", ""])[:2]
        tray = {"tray_type": mat if present.get(key) else "", "tray_color": (col + "FF") if col and present.get(key) else ""}
        parts = key.split(":")
        try:
            if parts[0] == "ams" and len(parts) == 3:
                units.setdefault(int(parts[1]), []).append({"id": parts[2], "state": 1 if present.get(key) else 0, **tray})
            elif parts[0] == "ext" and len(parts) == 2:
                ext.append({"id": parts[1], **tray})
        except ValueError:
            continue
    if not units:
        return {}
    return {"ams": {"ams": [{"id": str(u), "tray": t} for u, t in sorted(units.items())]}, "vir_slot": ext, "ts": None}


# ---------------------------------------------------------------------------- notify
def notify(title: str, message: str, path: str = "/") -> None:
    """POST {"title", "message", "url"} to NOTIFY_WEBHOOK (e.g. a Home Assistant webhook -> phone notification)."""
    if not config.NOTIFY_WEBHOOK:
        return
    url = (config.PUBLIC_URL + path) if config.PUBLIC_URL else path

    def _send():
        try:
            requests.post(config.NOTIFY_WEBHOOK, json={"title": title, "message": message, "url": url}, timeout=10)
        except requests.RequestException as e:
            log.warning("notify failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()


def _safe_call(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # hooks must never break the engine
        log.exception("hook failed")
