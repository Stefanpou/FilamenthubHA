"""Live printer dashboard backend: Server-Sent Events + snapshot + cover/model of the current job.

  GET /api/live            text/event-stream, `event: state` with the snapshot JSON (see AGENTS.md), <= 1/s, keep-alive
                           comment every 15 s
  GET /api/live/snapshot   the same JSON once
  GET /api/printer/cover   cover image of the current (or, when idle, the latest) job, proxied from Bambu Cloud
  GET /api/printer/model.3mf  compact binary mesh of the job's plate (format: app/model3d.py, "FHM1")

All Bambu Cloud access goes through BambuCloud (cloud only, no LAN). The model is fetched in a background thread,
cached in DATA_DIR/live/<task id>/ and pruned.
"""
from __future__ import annotations

import asyncio
import itertools
import io
import json
import logging
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import config, model3d
from .bambu_cloud import BambuCloud, CloudError, ams_model_hint, model_family, model_name
from .printer_state import ACTIVE_STATES, END_STATES, parse_slots, print_summary

log = logging.getLogger("spoolsync.live")
router = APIRouter()

MIN_INTERVAL = 1.0       # seconds between pushes to one client
KEEPALIVE = 15.0
POLL = 0.5
MAX_CLIENTS = 64
CACHE_KEEP = 12          # task directories kept in DATA_DIR/live
CACHE_MAX_BYTES = 120 * 1024 * 1024
GCODE_STATES_DA = {"IDLE": "Klar", "RUNNING": "Printer", "PAUSE": "Pause", "FINISH": "Færdig", "FAILED": "Fejl",
                   "PREPARE": "Forbereder", "SLICING": "Forbereder"}
# Bambu `stg_cur` / `stg` codes (the steps before and between printing), as in ha-bambulab's CURRENT_STAGE_IDS.
STAGES_DA = {
    1: "Automatisk nivellering af pladen", 2: "Opvarmer printpladen", 3: "Kalibrerer XY-mekanikken",
    4: "Skifter filament", 5: "Pause (M400)", 6: "Pause: filamentet er sluppet op", 7: "Opvarmer dysen",
    8: "Kalibrerer ekstrudering", 9: "Scanner printpladen", 10: "Tjekker første lag",
    11: "Genkender pladetypen", 12: "Kalibrerer Micro Lidar", 13: "Hjemkører printhovedet",
    14: "Rengør dysespidsen", 15: "Tjekker dysetemperaturen", 16: "Sat på pause", 17: "Pause: frontlågen faldt af",
    18: "Kalibrerer Micro Lidar", 19: "Kalibrerer flow", 20: "Pause: fejl i dysetemperaturen",
    21: "Pause: fejl i pladetemperaturen", 22: "Aflæsser filament", 23: "Pause: motoren sprang trin over",
    24: "Indlæser filament", 25: "Kalibrerer motorstøj", 26: "Pause: AMS mistet", 27: "Pause: lav blæserhastighed",
    28: "Pause: fejl i kammertemperaturen", 29: "Køler kammeret", 30: "Pause fra G-code",
    31: "Demonstrerer motorstøj", 32: "Pause: filament på dysen", 33: "Pause: fejl i skæreren",
    34: "Pause: fejl i første lag", 35: "Pause: dysen er tilstoppet", 36: "Tjekker præcision før kalibrering",
    37: "Kalibrerer præcision", 38: "Tjekker præcision efter kalibrering", 39: "Kalibrerer dyseforskydning",
    40: "Nivellerer pladen ved høj temperatur", 41: "Tjekker hurtigudløseren", 42: "Tjekker låge og låg",
    43: "Kalibrerer laser", 44: "Tjekker laserpladen", 45: "Tjekker kameraets position", 46: "Kalibrerer kameraet",
    47: "Nivellerer pladen (fase 1)", 48: "Nivellerer pladen (fase 2)", 49: "Opvarmer kammeret",
    50: "Køler printpladen", 51: "Printer kalibreringslinjer", 52: "Tjekker materialet",
    53: "Kalibrerer livekameraet", 54: "Venter på pladetemperaturen", 55: "Tjekker materialets position",
    56: "Kalibrerer skæremodulet", 57: "Måler overfladen", 58: "Termisk forkonditionering",
    59: "Hjemkører knivholderen", 60: "Kalibrerer kameraforskydning", 61: "Kalibrerer knivholderens position",
    62: "Tester skift af hotend", 63: "Venter på jævn kammertemperatur", 64: "Klargør hotend",
    65: "Kalibrerer klumpdetektion på dysen", 66: "Renser kammerluften",
}
PAUSE_STAGES = {5, 6, 16, 17, 20, 21, 23, 26, 27, 28, 30, 32, 33, 34, 35}
NO_STAGE = {0, -1, 255}         # printing / idle


# ============================================================================ state -> JSON
def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _split_temp(raw):
    """Bambu packs temperatures as one integer: low 16 bits = current, high 16 bits = target (new X1/H2/X2 firmware).
    Small values (< 1000) are a plain current temperature. Returns (current, target|None)."""
    n = _num(raw)
    if n is None:
        return None, None
    n = int(n)
    if n < 0:
        return None, None
    if n <= 0xFFFF:
        return float(n), None
    cur, tar = n & 0xFFFF, (n >> 16) & 0xFFFF
    return float(cur), float(tar)


def _round(v):
    return None if v is None else round(v, 1)


def parse_nozzles(state: dict) -> list[dict]:
    """[{id, temp, target}] per nozzle. Newer firmware: device.extruder.info[].temp (packed); older single-nozzle
    printers: nozzle_temper / nozzle_target_temper."""
    result: dict[int, dict] = {}
    info = ((state.get("device") or {}).get("extruder") or {}).get("info")
    if isinstance(info, list):
        for e in info:
            if not isinstance(e, dict):
                continue
            try:
                nid = int(e.get("id"))
            except (TypeError, ValueError):
                continue
            cur, tar = _split_temp(e.get("temp"))
            result[nid] = {"id": nid, "temp": _round(cur), "target": _round(tar) if tar is not None else 0}
    flat_t = _num(state.get("nozzle_temper"))
    flat_g = _num(state.get("nozzle_target_temper"))
    if not result and flat_t is not None:
        result[0] = {"id": 0, "temp": _round(flat_t), "target": _round(flat_g) if flat_g is not None else 0}
    elif result and flat_t is not None:
        # The flat keys follow the active nozzle: fill in what the packed value lacked (e.g. a plain 49 = no target).
        act = active_nozzle(state)
        n = result.get(act if act is not None else min(result))
        if n is not None and (n["temp"] is None or (n["target"] in (0, None) and flat_g)):
            n["temp"] = _round(flat_t) if n["temp"] is None else n["temp"]
            n["target"] = _round(flat_g) if flat_g is not None else n["target"]
    return [result[k] for k in sorted(result)]


def active_nozzle(state: dict) -> int | None:
    """Index of the nozzle in use. device.extruder.state: bits 4-7 = active extruder (X2D/H2D)."""
    ext = (state.get("device") or {}).get("extruder") or {}
    st = _num(ext.get("state"))
    info = ext.get("info")
    if st is not None and isinstance(info, list) and len(info) > 1:
        n = (int(st) >> 4) & 0xF
        ids = {int(e["id"]) for e in info if isinstance(e, dict) and str(e.get("id", "")).isdigit()}
        if n in ids:
            return n
    if isinstance(info, list):
        # the nozzle whose heater has a target
        for e in info:
            if isinstance(e, dict):
                _, tar = _split_temp(e.get("temp"))
                if tar and str(e.get("id", "")).isdigit():
                    return int(e["id"])
    return 0 if isinstance(info, list) and info else (0 if state.get("nozzle_temper") is not None else None)


def parse_bed(state: dict) -> dict:
    cur = tar = None
    bed = (state.get("device") or {}).get("bed") or {}
    c, t = _split_temp((bed.get("info") or {}).get("temp") if isinstance(bed, dict) else None)
    if c is None:
        c, t = _split_temp((state.get("device") or {}).get("bed_temp"))
    cur, tar = c, t
    if cur is None:
        cur = _num(state.get("bed_temper"))
    flat_t = _num(state.get("bed_target_temper"))
    if flat_t is not None:
        tar = flat_t
    return {"temp": _round(cur), "target": _round(tar) if tar is not None else 0}


def parse_chamber(state: dict) -> dict:
    ctc = (state.get("device") or {}).get("ctc") or {}
    c, t = _split_temp((ctc.get("info") or {}).get("temp") if isinstance(ctc, dict) else None)
    if c is None:
        c = _num(state.get("chamber_temper"))
    if c is None:
        c = _num((state.get("info") or {}).get("temp")) if isinstance(state.get("info"), dict) else None
    out = {"temp": _round(c)}
    if t:
        out["target"] = _round(t)
    return out


def parse_fan_pct(state: dict):
    """Part cooling fan: Bambu reports a 0-15 gear as text."""
    g = _num(state.get("cooling_fan_speed"))
    if g is None:
        return None
    return max(0, min(100, int(round(g / 15 * 100))))


def parse_light(state: dict):
    for r in state.get("lights_report") or []:
        if isinstance(r, dict) and r.get("node") == "chamber_light":
            return str(r.get("mode")) in ("on", "flashing")
    return None


def parse_extras(state: dict) -> dict:
    """Extra, optional fields the UI can use (aux/chamber fans, work light, error)."""
    out: dict = {}
    for key, name in (("big_fan1_speed", "aux_fan_pct"), ("big_fan2_speed", "chamber_fan_pct")):
        g = _num(state.get(key))
        if g is not None:
            out[name] = max(0, min(100, int(round(g / 15 * 100))))
    for r in state.get("lights_report") or []:
        if isinstance(r, dict) and r.get("node") == "work_light":
            out["work_light"] = str(r.get("mode")) in ("on", "flashing")
    err = _num(state.get("print_error")) or _num(state.get("mc_print_error_code"))
    out["error"] = int(err) if err else 0
    return out


def parse_stage(state: dict, gcode_state: str) -> dict | None:
    """The step the printer is doing outside of printing (heating, levelling, calibrating, ...) and the steps it has
    planned for this job (`stg`): {code, label, pause, steps: [{code, label, status: done|current|todo}]}."""
    if gcode_state not in ("RUNNING", "PREPARE", "PAUSE", "SLICING"):
        return None
    code = _num(state.get("stg_cur"))
    if code is None or int(code) in NO_STAGE:
        return None
    code = int(code)
    planned = [int(c) for c in (state.get("stg") or []) if isinstance(c, (int, float)) and int(c) not in NO_STAGE]
    steps = []
    if code in planned:
        at = planned.index(code)
        for i, c in enumerate(planned):
            steps.append({"code": c, "label": STAGES_DA.get(c, f"Trin {c}"),
                          "status": "done" if i < at else "current" if i == at else "todo"})
    return {"code": code, "label": STAGES_DA.get(code, f"Trin {code}"), "pause": code in PAUSE_STAGES, "steps": steps}


def printer_block(state: dict, device: dict | None, online: bool) -> dict:
    s = print_summary(state) if state else {"state": "UNKNOWN", "percent": 0, "remaining_min": 0, "name": "",
                                            "layer": 0, "total_layers": 0}
    gs = s["state"] if s["state"] != "UNKNOWN" else ("IDLE" if state else "UNKNOWN")
    layer = s["layer"]
    # Some firmwares (X2D) report the layer in `3D` too; prefer the top-level value, fall back to it.
    tl = s["total_layers"] or int(_num((state.get("3D") or {}).get("total_layer_num"), 0) or 0)
    if not layer:
        layer = int(_num((state.get("3D") or {}).get("layer_num"), 0) or 0)
    spd = _num(state.get("spd_lvl"))
    dev = device or {}
    model = model_name(dev.get("model") or dev.get("model_code"), dev.get("serial")) or "Bambu Lab printer"
    nozzles = parse_nozzles(state)
    stage = parse_stage(state, gs)
    block = {
        "name": dev.get("name") or model,
        "model": model,
        "family": model_family(model, len(nozzles)),
        "online": bool(online),
        "gcode_state": gs,
        "state_da": "Forbereder" if stage and not stage["pause"] and gs == "RUNNING" and not layer else GCODE_STATES_DA.get(gs, "Ukendt"),
        "stage": stage,
        "percent": s["percent"],
        "remaining_min": s["remaining_min"],
        "layer": layer,
        "total_layers": tl,
        "job_name": s["name"],
        "nozzles": nozzles,
        "active_nozzle": active_nozzle(state),
        "bed": parse_bed(state),
        "chamber": parse_chamber(state),
        "fan_pct": parse_fan_pct(state),
        "speed_level": int(spd) if spd is not None else None,
        "light": parse_light(state),
        "wifi": state.get("wifi_signal") or None,
    }
    block.update(parse_extras(state))
    return block


def feed_map(state: dict) -> dict[str, int]:
    """slot key -> nozzle id it is loaded to (device.extruder.info[].snow = unit << 8 | tray)."""
    out: dict[str, int] = {}
    info = ((state.get("device") or {}).get("extruder") or {}).get("info")
    for e in info if isinstance(info, list) else []:
        snow = e.get("snow") if isinstance(e, dict) else None
        if not isinstance(snow, int) or snow == 0xFFFF:
            continue
        unit, tray = snow >> 8, snow & 0xFF
        try:
            nid = int(e.get("id"))
        except (TypeError, ValueError):
            continue
        if unit in (254, 255):
            out[f"ext:{unit}"] = nid
        elif unit >= 128:
            out[f"ams:{unit}:0"] = nid
        else:
            out[f"ams:{unit}:{tray & 3}"] = nid
    return out


def slots_block(state: dict, modules: dict, assignments: dict, spools: dict) -> list[dict]:
    parsed = parse_slots(state, modules) if state else []
    feeds = feed_map(state)
    act = active_nozzle(state)
    ext = [s for s in parsed if s.kind == "ext"]
    keep_ext = None
    if ext:  # ONE physical holder: the one with a filament or in use, else the first
        keep_ext = next((s for s in ext if s.present), None) or next((s for s in ext if s.active), None) or ext[0]
    out = []
    for s in parsed:
        if s.kind == "ext" and s is not keep_ext:
            continue
        a = assignments.get(s.key)
        sp = spools.get(a["spool_id"]) if a else None
        rem_g = rem_pct = None
        if sp:
            rem_g = round(sp.get("remaining") or 0)
            init = sp.get("initial") or 0
            if init > 0:
                rem_pct = max(0, min(100, round(100 * (sp.get("remaining") or 0) / init)))
        if rem_pct is None and s.remain is not None and s.remain >= 0:
            rem_pct = int(s.remain)
        out.append({
            "key": s.key, "label": s.label, "kind": s.kind, "unit": s.unit, "tray": s.tray, "present": s.present,
            "material": s.material, "color": s.color, "colors": s.colors, "sub_brand": s.sub_brand,
            "remaining_g": rem_g, "remaining_pct": rem_pct,
            # feeding the nozzle that is in use right now (the other nozzle may hold a standby spool)
            "active": bool(s.active and (act is None or feeds.get(s.key, act) == act)),
            "nozzle": feeds.get(s.key),
            "spool": ({"id": sp["id"], "vendor": sp.get("vendor", ""), "name": sp.get("name", "")} if sp else None),
        })
    return out


AMS_INFO_TYPES = {1: "AMS", 2: "AMS lite", 3: "AMS 2 Pro", 4: "AMS HT"}   # low nibble of the unit's `info` (hex string)


def ams_unit_model(unit: dict, uid: int, modules: dict, hint: str = "AMS") -> str:
    """AMS flavour of one unit: get_version module name > the unit's own `info` type nibble > id range > hint."""
    mod = (modules.get(str(uid)) or {}).get("model")
    if mod:
        return "AMS lite" if mod.lower() == "ams lite" else mod
    try:
        t = int(str(unit.get("info")), 16) & 0xF
    except (TypeError, ValueError):
        t = 0
    if t in AMS_INFO_TYPES:
        return AMS_INFO_TYPES[t]
    return "AMS HT" if uid >= 128 else hint


def ams_block(state: dict, modules: dict, hint: str = "AMS") -> list[dict]:
    """Per AMS unit. `humidity` is a real percentage (AMS 2 Pro / HT report `humidity_raw`), `humidity_level` the 1-5
    index every AMS reports (AMS 1 / lite have nothing else; 5 = driest)."""
    out = []
    for u in sorted((state.get("ams") or {}).get("ams") or [], key=lambda x: int(x.get("id", 0) or 0)):
        try:
            uid = int(u.get("id"))
        except (TypeError, ValueError):
            continue
        hum_raw = _num(u.get("humidity_raw"))
        level = int(_num(u.get("humidity"), 0) or 0)
        out.append({
            "unit": uid, "model": ams_unit_model(u, uid, modules, hint),
            "humidity": int(hum_raw) if hum_raw is not None and hum_raw > 0 else None,
            "humidity_level": level if 1 <= level <= 5 else None,
            "temp": _num(u.get("temp")),
        })
    return out


def build_snapshot(state: dict, modules: dict | None, device: dict | None, assignments: dict | None,
                   spools: dict | None, online: bool, model: dict | None = None) -> dict:
    """Pure function: everything the dashboard shows, from the accumulated printer state."""
    modules = modules or {}
    state = state or {}
    return {
        "ts": round(time.time(), 1),
        "printer": printer_block(state, device, online),
        "slots": slots_block(state, modules, assignments or {}, spools or {}),
        "ams": ams_block(state, modules, ams_model_hint(model_name((device or {}).get("model"), (device or {}).get("serial")))),
        "model": model or empty_model(),
    }


def empty_model() -> dict:
    return {"task_id": "", "cover_url": None, "model_url": None, "available": False, "plate": 1, "status": "none",
            "kind": None, "job": "none"}


# ============================================================================ cloud model / cover cache
class ModelCache:
    """Fetches cover + mesh of one job in a background thread and keeps them in DATA_DIR/live/<task id>/."""

    BACKOFF = (20, 90, 300, 900, 1800, 3600)   # a cloud task can be listed late

    def __init__(self, root=None):
        self._root = root
        self._lock = threading.Lock()
        self._exec: ThreadPoolExecutor | None = None
        self._jobs: dict[str, dict] = {}      # task_id -> {status, attempts, next_try, error}
        self._latest: dict = {"ts": 0.0, "job": None}

    @property
    def root(self):
        return self._root or (config.DATA_DIR / "live")

    def _dir(self, task_id: str):
        safe = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "-_")[:40]
        if not safe:
            raise ValueError("ugyldigt task-id")
        return self.root / safe

    # ---- public (non-blocking) -------------------------------------------
    def info(self, job: dict | None, engine, kind_of_job: str) -> dict:
        if not job or not job.get("task_id"):
            return empty_model()
        tid = str(job["task_id"])
        try:
            d = self._dir(tid)
        except ValueError:
            return empty_model()
        meta = self._read_meta(d)
        status = "none"
        if meta and meta.get("ok"):
            status = "ready"
        else:
            status = self._kick(job, engine)
        out = {
            "task_id": tid, "plate": int(job.get("plate") or 1), "job": kind_of_job, "status": status,
            "cover_url": "/api/printer/cover" if (d / "cover.png").exists() else None,
            "model_url": "/api/printer/model.3mf" if (d / "model.bin").exists() else None,
            "available": (d / "model.bin").exists(),
            "kind": (meta or {}).get("kind"),
        }
        return out

    def file(self, task_id: str, name: str):
        try:
            p = self._dir(task_id) / name
        except ValueError:
            return None
        return p if p.is_file() else None

    def latest_job(self, engine) -> dict | None:
        """Most recent cloud task of the printer (cached; refreshed at most every 5 minutes, in the background)."""
        now = time.time()
        with self._lock:
            due = now - self._latest["ts"] > 300
            if due:
                self._latest["ts"] = now
            job = self._latest["job"]
        if due and getattr(engine, "cloud", None):
            self._executor().submit(self._refresh_latest, engine)
        return job

    # ---- internals ---------------------------------------------------------
    def _executor(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._exec is None:
                self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-model")
            return self._exec

    def shutdown(self) -> None:
        with self._lock:
            ex, self._exec = self._exec, None
        if ex:
            ex.shutdown(wait=False, cancel_futures=True)

    def _read_meta(self, d):
        try:
            return json.loads((d / "meta.json").read_text())
        except (OSError, ValueError):
            return None

    def _kick(self, job: dict, engine) -> str:
        tid = str(job["task_id"])
        now = time.time()
        with self._lock:
            st = self._jobs.get(tid)
            if st and st["status"] == "loading":
                return "loading"
            if st and (st["status"] == "failed" and (st["attempts"] >= len(self.BACKOFF) or now < st["next_try"])):
                return "unavailable"
            attempts = st["attempts"] if st else 0
            self._jobs[tid] = {"status": "loading", "attempts": attempts, "next_try": 0, "error": ""}
        if not getattr(engine, "cloud", None):
            with self._lock:
                self._jobs[tid] = {"status": "failed", "attempts": 99, "next_try": now + 60, "error": "ikke logget ind"}
            return "unavailable"
        self._executor().submit(self._run, job, engine)
        return "loading"

    def _run(self, job: dict, engine) -> None:
        tid = str(job["task_id"])
        try:
            meta = self.fetch(job, engine.cloud, (engine.device or {}).get("serial"))
            if not meta.get("ok"):   # neither picture nor model: retry with backoff instead of on every snapshot
                raise model3d.ModelError(meta.get("error") or "intet billede eller model")
            with self._lock:
                self._jobs[tid] = {"status": "ready", "attempts": 0, "next_try": 0, "error": ""}
            self.prune(keep=tid)
        except Exception as e:  # a failed fetch must never break the dashboard
            log.warning("model fetch for task %s failed: %s", tid, type(e).__name__)   # (messages may hold signed URLs)
            with self._lock:
                att = (self._jobs.get(tid) or {}).get("attempts", 0) + 1
                delay = self.BACKOFF[min(att - 1, len(self.BACKOFF) - 1)]
                self._jobs[tid] = {"status": "failed", "attempts": att, "next_try": time.time() + delay,
                                   "error": (str(e) if isinstance(e, model3d.ModelError) else type(e).__name__)[:200]}

    def _refresh_latest(self, engine) -> None:
        try:
            tasks = engine.cloud.get_tasks((engine.device or {}).get("serial"), limit=10)
        except Exception as e:
            log.info("latest task lookup failed: %s", e)
            return
        for t in tasks:
            if t.get("profileId") and t.get("modelId"):
                with self._lock:
                    self._latest["job"] = job_from_task(t)
                return

    # ---- the actual cloud work (also called directly by tests/scripts) -------------------
    def fetch(self, job: dict, cloud: BambuCloud, serial: str | None) -> dict:
        tid = str(job["task_id"])
        plate = int(job.get("plate") or 1)
        task = job.get("task")
        if task is None or not task.get("profileId"):
            for t in cloud.get_tasks(serial, limit=50):
                if str(t.get("id")) == tid:
                    task = t
                    break
        profile_id = job.get("profile_id") or (task or {}).get("profileId")
        model_id = job.get("model_id") or (task or {}).get("modelId")
        if task and task.get("plateIndex"):
            plate = int(task["plateIndex"]) if not job.get("plate") else plate
        if not profile_id or not model_id or str(profile_id) == "0":
            return self._cover_only(job, task, cloud, plate, "Jobbet har ingen skyprofil")
        prof = cloud.get_profile(profile_id, model_id)
        url = prof.get("url")
        if not url:
            return self._cover_only(job, task, cloud, plate, "Profilen har ingen fil")
        cfg = cloud.download(url, max_bytes=30 * 1024 * 1024)
        try:
            zf = zipfile.ZipFile(io.BytesIO(cfg))
        except zipfile.BadZipFile as e:
            raise model3d.ModelError("ikke en 3MF") from e
        model3d.check_zip(zf)
        names = set(zf.namelist())
        d = self._dir(tid)
        d.mkdir(parents=True, exist_ok=True)
        cover = None
        err_cover = ""
        for nm in (f"Metadata/plate_{plate}.png", f"Metadata/plate_no_light_{plate}.png", "Metadata/plate_1.png"):
            if nm in names:
                try:
                    cover = model3d.read_limited(zf, nm, model3d.MAX_COVER)
                except model3d.ModelError as e:
                    err_cover = str(e)
                break
        if cover is None and (task or {}).get("cover"):
            cover = cloud.download(task["cover"], max_bytes=5 * 1024 * 1024)
        if cover:
            (d / "cover.png").write_bytes(cover)
        colors = model3d.slice_colors(zf)
        for i, m in enumerate((task or {}).get("amsDetailMapping") or []):   # real spool colours beat sliced ones
            tc = str(m.get("targetColor") or "")[:6].upper()
            keys = sorted(colors, key=int)
            if len(tc) == 6 and i < len(keys) and set(tc) <= set("0123456789ABCDEF"):
                colors[keys[i]] = tc
        pinfo = model3d.plate_info(zf, plate)
        name = (task or {}).get("title") or job.get("name") or ""
        blob = None
        kind = None
        err = err_cover
        inst = (task or {}).get("instanceId")
        if inst:
            try:
                f3 = cloud.get_instance_3mf(inst)
                full = cloud.download(f3["url"], max_bytes=80 * 1024 * 1024)
                cand = model3d.mesh_from_3mf(full, plate=plate if self._has_plate(full, plate) else 1,
                                             colors=colors, name=name)
                if self._plausible(cand, pinfo):
                    blob, kind = cand, "mesh"
                else:
                    err = "modelfilen passer ikke til pladen"
            except Exception as e:
                err = f"mesh: {e if isinstance(e, model3d.ModelError) else type(e).__name__}"
                log.info("task %s: no mesh from MakerWorld (%s)", tid, type(e).__name__)
        if blob is None:
            h = self._height(job, pinfo)
            try:
                blob = model3d.silhouette_from_config(cfg, plate, h, colors=colors, name=name)
                kind = "silhouette"
            except (model3d.ModelError, zipfile.BadZipFile) as e:
                err = f"{err}; silhuet: {e}".strip("; ")
        if blob is not None:
            (d / "model.bin").write_bytes(blob)
        meta = {"ok": bool(cover or blob), "kind": kind, "plate": plate, "task_id": tid, "name": name,
                "error": err, "ts": time.time()}
        (d / "meta.json").write_text(json.dumps(meta))
        return meta

    def _cover_only(self, job: dict, task: dict | None, cloud: BambuCloud, plate: int, why: str) -> dict:
        """Jobs without a project file (e.g. "cloud_file" sent from Bambu Studio, local files): show at least the
        task's cover picture."""
        if not (task or {}).get("cover"):
            raise CloudError(why)
        tid = str(job["task_id"])
        cover = cloud.download(task["cover"], max_bytes=5 * 1024 * 1024)
        d = self._dir(tid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "cover.png").write_bytes(cover)
        meta = {"ok": True, "kind": "cover", "plate": plate, "task_id": tid,
                "name": task.get("title") or job.get("name") or "", "error": why, "ts": time.time()}
        (d / "meta.json").write_text(json.dumps(meta))
        return meta

    @staticmethod
    def _has_plate(full: bytes, plate: int) -> bool:
        try:
            return f"Metadata/plate_{plate}.json" in zipfile.ZipFile(io.BytesIO(full)).namelist()
        except (zipfile.BadZipFile, ValueError):
            return False

    @staticmethod
    def _plausible(blob: bytes, pinfo: dict) -> bool:
        bb = pinfo.get("bbox_all")
        if not bb:
            return True
        meta, _ = model3d.unpack_model(blob)
        mb = meta["bbox"]
        dw, dh = bb[2] - bb[0], bb[3] - bb[1]
        mw, mh = mb[3] - mb[0], mb[4] - mb[1]
        return abs(dw - mw) <= max(3.0, 0.15 * dw) and abs(dh - mh) <= max(3.0, 0.15 * dh)

    @staticmethod
    def _height(job: dict, pinfo: dict) -> float:
        lh = 0.2
        objs = pinfo.get("bbox_objects") or []
        if objs and objs[0].get("layer_height"):
            lh = float(objs[0]["layer_height"])
        layers = int(job.get("total_layers") or 0)
        if layers > 0:
            return min(250.0, layers * lh)
        return 30.0

    def prune(self, keep: str = "") -> None:
        try:
            root = self.root
            dirs = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        kept_bytes = 0
        for i, p in enumerate(dirs):
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            kept_bytes += size
            if p.name != keep and (i >= CACHE_KEEP or kept_bytes > CACHE_MAX_BYTES):
                shutil.rmtree(p, ignore_errors=True)


def job_from_task(t: dict) -> dict:
    return {"task_id": str(t.get("id")), "profile_id": str(t.get("profileId") or ""), "model_id": t.get("modelId"),
            "plate": int(t.get("plateIndex") or 1), "name": t.get("title") or "", "task": t, "total_layers": 0}


def job_from_state(state: dict) -> dict | None:
    s = print_summary(state) if state else None
    if not s or not s["task_id"]:
        return None
    try:
        plate = int(state.get("plate_idx") or state.get("plate_id") or 0) or None
    except (TypeError, ValueError):
        plate = None
    if plate is None:
        import re
        m = re.search(r"plate_(\d+)", str(state.get("gcode_file") or state.get("file") or ""))
        plate = int(m.group(1)) if m else 1
    pid = str(state.get("profile_id") or "")
    return {"task_id": s["task_id"], "profile_id": "" if pid == "0" else pid,
            "model_id": state.get("model_id") or "", "plate": plate, "name": s["name"],
            "total_layers": s["total_layers"] or int(_num((state.get("3D") or {}).get("total_layer_num"), 0) or 0)}


models = ModelCache()


def current_job(engine) -> tuple[dict | None, str]:
    """(job, "current" | "last" | "none"): what the printer is printing now, else its most recent cloud task."""
    with engine.lock:
        state = engine.state
        s = print_summary(state) if state else None
        job = job_from_state(state)
    if job and s and (s["state"] in ACTIVE_STATES or s["state"] in END_STATES):
        return job, "current"
    last = models.latest_job(engine)
    if last:
        if job and s and s["total_layers"]:
            last = dict(last, total_layers=s["total_layers"])
        return last, "last"
    return (job, "last") if job else (None, "none")


# ============================================================================ snapshot cache shared by all clients
_snap_lock = threading.Lock()
_snap = {"ts": 0.0, "data": None, "text": ""}


def _main():
    """The running app module (`python -m app.main` makes it __main__, tests import it as app.main). Never import it a
    second time: that would create a second Engine."""
    import sys
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "engine"):
        m = sys.modules.get("__main__")
    if m is None or not hasattr(m, "engine"):
        from . import main as m
    return m


def _spools_by_id() -> dict:
    try:
        return _main()._spools_by_id()
    except Exception:  # Spoolman down: the dashboard still works without spool names
        return {}


def make_snapshot(engine=None) -> dict:
    if engine is None:
        engine = _main().engine
    with engine.lock:
        state = json.loads(json.dumps(engine.state)) if engine.state else {}
        modules = dict(engine.modules or {})
    online = engine.mqtt_status == "connected" and bool(state)
    try:
        assignments = engine.db.current_assignments()
    except Exception:
        assignments = {}
    job, kind = current_job(engine)
    model = models.info(job, engine, kind)
    return build_snapshot(state, modules, engine.device, assignments, _spools_by_id(), online, model)


def shared_snapshot(max_age: float = 0.7) -> tuple[dict, str]:
    """One snapshot computation for any number of clients."""
    now = time.time()
    with _snap_lock:
        if _snap["data"] is not None and now - _snap["ts"] < max_age:
            return _snap["data"], _snap["text"]
        data = make_snapshot()
        # `ts` changes every time; the comparison text leaves it out so unchanged data is not re-sent.
        cmp = json.dumps({k: v for k, v in data.items() if k != "ts"}, separators=(",", ":"), sort_keys=True)
        _snap.update(ts=now, data=data, text=cmp)
        return data, cmp


# ============================================================================ routes
_active: dict[int, float] = {}   # open streams -> last time their loop ran
_ids = itertools.count()
STALE = 5.0                      # a stream whose loop has not run for this long is gone (its socket was dropped)


def client_count() -> int:
    """Open /api/live streams. A stream removes itself when it ends; one whose generator was abandoned without being
    closed (client gone before/while starting) stops ticking and is dropped after STALE seconds, so no slot leaks."""
    now = time.monotonic()
    for k, ts in list(_active.items()):
        if now - ts > STALE:
            _active.pop(k, None)
    return len(_active)


@router.get("/api/live/snapshot")
async def live_snapshot():
    data, _ = await run_in_threadpool(shared_snapshot, 0.3)
    return JSONResponse(data)


@router.get("/api/live")
async def live(request: Request):
    if client_count() >= MAX_CLIENTS:
        return JSONResponse({"detail": "For mange forbindelser"}, status_code=429)
    key = next(_ids)
    _active[key] = time.monotonic()

    async def gen():
        try:
            last_text = None
            last_sent = 0.0
            last_any = time.monotonic()
            yield "retry: 3000\n\n"
            while True:
                _active[key] = time.monotonic()
                if await request.is_disconnected():
                    break
                now = time.monotonic()
                if now - last_sent >= MIN_INTERVAL or last_text is None:
                    data, text = await run_in_threadpool(shared_snapshot)
                    if text != last_text:
                        last_text = text
                        last_sent = last_any = now
                        yield f"event: state\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
                if time.monotonic() - last_any >= KEEPALIVE:
                    last_any = time.monotonic()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(POLL)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        finally:
            _active.pop(key, None)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


def _job_file(name: str, media: str):
    engine = _main().engine
    job, _ = current_job(engine)
    if not job:
        return JSONResponse({"detail": "Intet job"}, status_code=404)
    p = models.file(job["task_id"], name)
    if p is None:
        return JSONResponse({"detail": "Ikke tilgængelig (endnu)"}, status_code=404)
    return FileResponse(p, media_type=media, headers={"Cache-Control": "private, max-age=60"})


@router.get("/api/printer/cover")
async def printer_cover():
    return await run_in_threadpool(_job_file, "cover.png", "image/png")


@router.get("/api/printer/model.3mf")
async def printer_model():
    return await run_in_threadpool(_job_file, "model.bin", "application/octet-stream")
