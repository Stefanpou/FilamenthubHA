"""Electricity from Home Assistant: the printer's smart plug (power W, energy kWh) and the electricity price.

A meter thread reads Home Assistant every 30 s. While a print is in progress its kWh and cost are added up (the price
of each moment, not an average), so every print gets what it used and cost. The price calculator gets the average
price over a coming print window from the price sensor's hourly / quarter-hourly prices (Energi Data Service, Nord
Pool and the like keep them as attributes).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import config
from .printer_state import ACTIVE_STATES, print_summary

log = logging.getLogger("spoolsync.power")
router = APIRouter()

KEY = "power"
DEFAULTS = {"url": "http://homeassistant.local:8123", "token": "", "power_entity": "", "energy_entity": "",
            "price_entity": "", "fixed_price": None, "watts": 147.0}
SAMPLE = 30.0
# Attribute lists with known prices. Forecasts are left out: the calculator shows how much of the window is known.
PRICE_LISTS = ("raw_today", "raw_tomorrow", "prices_today", "prices_tomorrow", "today_prices", "tomorrow_prices",
               "prices", "data")


def _main():
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "engine"):
        m = sys.modules.get("__main__")
    if m is None or not hasattr(m, "engine"):
        from . import main as m
    return m


SUPERVISOR_URL = "http://supervisor/core"


def stored_settings(db) -> dict:
    """What the user saved (never contains the add-on's supervisor fallback, so saving cannot persist it)."""
    return {**DEFAULTS, **(db.get(KEY) or {})}


def settings(db) -> dict:
    cfg = stored_settings(db)
    if config.ADDON and not cfg["token"]:
        # Home Assistant add-on: reach Home Assistant through the Supervisor proxy with the add-on's own token.
        tok = os.environ.get("SUPERVISOR_TOKEN", "").strip()
        if tok:
            cfg = {**cfg, "url": SUPERVISOR_URL, "token": tok}
    return cfg


def configured(cfg: dict) -> bool:
    return bool(cfg["url"] and cfg["token"] and (cfg["power_entity"] or cfg["energy_entity"]))


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _ts(v) -> float | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class HomeAssistant:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str):
        try:
            r = self.s.get(f"{self.url}{path}", timeout=8)
        except requests.RequestException as e:
            raise HTTPException(502, f"Home Assistant svarer ikke ({type(e).__name__})") from e
        if r.status_code == 401:
            raise HTTPException(502, "Home Assistant afviste tokenet")
        if r.status_code == 404:
            raise HTTPException(404, "Findes ikke i Home Assistant")
        if r.status_code >= 400:
            raise HTTPException(502, f"Home Assistant-fejl {r.status_code}")
        return r.json()

    def state(self, entity: str) -> dict:
        return self._get(f"/api/states/{entity}")

    def call(self, domain: str, service: str, entity: str) -> None:
        """Call a Home Assistant service on one entity (e.g. switch.turn_off)."""
        try:
            r = self.s.post(f"{self.url}/api/services/{domain}/{service}", json={"entity_id": entity}, timeout=10)
        except requests.RequestException as e:
            raise HTTPException(502, f"Home Assistant svarer ikke ({type(e).__name__})") from e
        if r.status_code == 401:
            raise HTTPException(502, "Home Assistant afviste tokenet")
        if r.status_code >= 400:
            raise HTTPException(502, f"Home Assistant-fejl {r.status_code}")

    def camera_image(self, entity: str, max_bytes: int = 15 * 1024 * 1024) -> tuple[bytes, str]:
        """A still image from a camera entity: (bytes, content type)."""
        try:
            r = self.s.get(f"{self.url}/api/camera_proxy/{entity}", timeout=20)
        except requests.RequestException as e:
            raise HTTPException(502, f"Home Assistant svarer ikke ({type(e).__name__})") from e
        if r.status_code >= 400:
            raise HTTPException(502, f"Kameraet svarede ikke (Home Assistant-fejl {r.status_code})")
        if len(r.content) > max_bytes or not r.content:
            raise HTTPException(502, "Kameraet sendte et tomt eller for stort billede")
        return r.content, r.headers.get("content-type", "image/jpeg").split(";")[0]

    def states(self) -> list[dict]:
        return self._get("/api/states")


def power_w(st: dict) -> float | None:
    v = _num(st.get("state"))
    unit = str((st.get("attributes") or {}).get("unit_of_measurement") or "")
    return v * 1000 if v is not None and unit == "kW" else v


def energy_kwh(st: dict) -> float | None:
    v = _num(st.get("state"))
    unit = str((st.get("attributes") or {}).get("unit_of_measurement") or "")
    return v / 1000 if v is not None and unit == "Wh" else v


def price_scale(st: dict) -> float:
    """Prices in øre are turned into kroner."""
    unit = str((st.get("attributes") or {}).get("unit_of_measurement") or "").lower()
    return 0.01 if unit.startswith("øre") or unit.startswith("ore") else 1.0


def price_now(st: dict) -> float | None:
    v = _num(st.get("state"))
    return v * price_scale(st) if v is not None else None


def price_intervals(st: dict) -> list[tuple[float, float, float]]:
    """(start, end, kr/kWh) from the sensor's attribute lists, sorted, without overlaps."""
    attrs = st.get("attributes") or {}
    scale = price_scale(st)
    raw: list[tuple[float, float | None, float]] = []
    for key in PRICE_LISTS:
        for item in attrs.get(key) or []:
            if not isinstance(item, dict):
                continue
            start = next((_ts(item.get(k)) for k in ("start", "hour", "time", "datetime", "from") if item.get(k)), None)
            end = next((_ts(item.get(k)) for k in ("end", "to") if item.get(k)), None)
            val = next((_num(item.get(k)) for k in ("value", "price", "total", "total_price") if item.get(k) is not None), None)
            if start is not None and val is not None:
                raw.append((start, end, val * scale))
    raw.sort(key=lambda x: x[0])
    out: list[tuple[float, float, float]] = []
    for i, (s, e, v) in enumerate(raw):
        if e is None:   # no end: up to the next item, at most an hour; the last one as long as the one before
            nxt = next((r[0] for r in raw[i + 1:] if r[0] > s), None)
            step = s - out[-1][0] if out else 3600
            e = min(nxt, s + 3600) if nxt else s + min(step, 3600)
        if out and s < out[-1][1]:
            continue   # the same time twice (two lists overlap)
        if e > s:
            out.append((s, e, v))
    return out


def window_price(intervals: list[tuple[float, float, float]], start: float, seconds: float) -> tuple[float | None, float]:
    """Average kr/kWh over the known part of [start, start+seconds] and the share of the window that is known."""
    end = start + seconds
    known = total = 0.0
    for s, e, v in intervals:
        o = min(e, end) - max(s, start)
        if o > 0:
            known += o
            total += o * v
    if seconds <= 0 or known <= 0:
        return None, 0.0
    return total / known, min(1.0, known / seconds)


class Meter:
    def __init__(self, db):
        self.db = db
        self.now: dict = {"ok": False, "error": "", "ts": 0, "power_w": None, "price": None}
        self._last: dict | None = None   # previous sample of the print in progress
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._price_state: dict | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="power-meter", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(SAMPLE):
            try:
                self.sample()
            except Exception:
                log.exception("power sample failed")

    def client(self, cfg: dict | None = None) -> HomeAssistant:
        cfg = cfg or settings(self.db)
        return HomeAssistant(cfg["url"], cfg["token"])

    def sample(self, engine=None, now: float | None = None) -> None:
        cfg = settings(self.db)
        if not configured(cfg):
            self.now = {"ok": False, "error": "", "ts": 0, "power_w": None, "price": None}
            self._last = None
            return
        now = now or time.time()
        ha = self.client(cfg)
        try:
            w = power_w(ha.state(cfg["power_entity"])) if cfg["power_entity"] else None
            kwh = energy_kwh(ha.state(cfg["energy_entity"])) if cfg["energy_entity"] else None
            price = None
            if cfg["price_entity"]:
                self._price_state = ha.state(cfg["price_entity"])
                price = price_now(self._price_state)
        except HTTPException as e:
            self.now = {**self.now, "ok": False, "error": e.detail}
            self._last = None
            return
        if price is None:
            price = _num(cfg["fixed_price"])
        self.now = {"ok": True, "error": "", "ts": now, "power_w": w, "price": price}

        engine = engine or _main().engine
        with engine.lock:
            s = print_summary(engine.state) if engine.state else None
        if not s or not s["task_id"] or s["state"] not in ACTIVE_STATES:
            self._last = None
            return
        tid = s["task_id"]
        last, self._last = self._last, {"tid": tid, "ts": now, "kwh": kwh, "w": w}
        if not last or last["tid"] != tid:
            self.db.execute("INSERT OR IGNORE INTO print_energy(task_id, name, first_ts, last_ts) VALUES(?,?,?,?)",
                            (tid, s["name"], now, now))
            return
        dt = now - last["ts"]
        used = None
        if kwh is not None and last["kwh"] is not None and 0 <= kwh - last["kwh"] < 5:
            used = kwh - last["kwh"]
        elif w is not None and 0 < dt <= 300:
            # No energy counter (or it was reset): power x time, the average of both samples.
            used = ((w + (last["w"] if last["w"] is not None else w)) / 2) * dt / 3_600_000
        if used is None:
            return
        cost = used * price if price is not None else 0.0
        self.db.execute(
            "UPDATE print_energy SET kwh=kwh+?, cost=cost+?, priced_kwh=priced_kwh+?, last_ts=? WHERE task_id=?",
            (used, cost, used if price is not None else 0.0, now, tid))

    def avg_watts(self) -> float | None:
        """Average power of recorded prints (at least an hour of data), for the calculator's default."""
        r = self.db.query_one("SELECT SUM(kwh) k, SUM(last_ts-first_ts) t FROM print_energy WHERE last_ts-first_ts > 3600")
        return round(r["k"] * 3_600_000 / r["t"]) if r and r["k"] and r["t"] else None


def _energy_rows(db) -> list[dict]:
    """Prints of 10 min or more with energy: kwh, secs, nozzles and the material it used most of."""
    return db.query(
        "SELECT p.task_id, p.kwh, p.last_ts - p.first_ts AS secs, p.nozzles, "
        "(SELECT UPPER(TRIM(d.material)) FROM deductions d WHERE d.task_id = p.task_id AND d.state = 'applied' "
        " AND COALESCE(d.material, '') != '' GROUP BY UPPER(TRIM(d.material)) ORDER BY SUM(d.grams) DESC LIMIT 1) AS mat "
        "FROM print_energy p WHERE p.last_ts - p.first_ts >= 600 AND p.kwh > 0")


def _avg(rows) -> dict | None:
    k, t = sum(r["kwh"] for r in rows), sum(r["secs"] for r in rows)
    return {"watts": round(k * 3_600_000 / t), "prints": len(rows), "hours": round(t / 3600, 1)} if t > 0 else None


def watts_by_material(db, dual: bool = False) -> dict[str, dict]:
    """Average power while printing, per material (the one a print used most of), from prints of 10 min or more:
    ASA and ABS heat bed and chamber far more than PLA. `dual`: only prints that used both nozzles (they keep two
    hotends hot); otherwise only the others (one nozzle, or not known)."""
    agg: dict[str, list[dict]] = {}
    for r in _energy_rows(db):
        if r["mat"] and ((r["nozzles"] or 1) >= 2) == dual:
            agg.setdefault(r["mat"], []).append(r)
    return {m: a for m, rows in sorted(agg.items()) if (a := _avg(rows))}


def watts_dual(db) -> dict | None:
    """Average power of all prints that used both nozzles (any material)."""
    return _avg([r for r in _energy_rows(db) if (r["nozzles"] or 1) >= 2])


def note_nozzles(db, tasks: list[dict]) -> int:
    """Stores how many nozzles each print used (Bambu Cloud task: amsDetailMapping[].nozzleId) on its energy row.
    Matched by task id, else by title + start time (prints started on the printer have a local task id)."""
    from .sync import parse_time
    rows = db.query("SELECT task_id, name, first_ts FROM print_energy WHERE nozzles IS NULL")
    if not rows or not tasks:
        return 0
    by_id = {str(t.get("id")): t for t in tasks if t.get("id")}
    done = 0
    for r in rows:
        t = by_id.get(r["task_id"]) or next(
            (t for t in tasks if (t.get("title") or "") == (r["name"] or "")
             and abs((parse_time(t.get("startTime")) or 0) - r["first_ts"]) < 1800), None)
        ids = {m.get("nozzleId") for m in (t or {}).get("amsDetailMapping") or []
               if m.get("nozzleId") is not None and (m.get("weight") or 0) > 0}
        if ids:
            db.execute("UPDATE print_energy SET nozzles = ? WHERE task_id = ?", (len(ids), r["task_id"]))
            done += 1
    return done


meter: Meter | None = None


def get_meter() -> Meter:
    global meter
    if meter is None:
        meter = Meter(_main().db)
    return meter


def energy_for(db, task_ids: list[str]) -> dict[str, dict]:
    if not task_ids:
        return {}
    rows = db.query(f"SELECT * FROM print_energy WHERE task_id IN ({','.join('?' * len(task_ids))})", tuple(task_ids))
    return {r["task_id"]: {"kwh": round(r["kwh"], 3), "cost": round(r["cost"], 2) if r["priced_kwh"] else None,
                           "hours": round((r["last_ts"] - r["first_ts"]) / 3600, 2), "nozzles": r.get("nozzles")} for r in rows}


# ============================================================================ routes
def _public(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if k != "token"} | {"token_set": bool(cfg["token"])}


class PowerSettingsIn(BaseModel):
    url: str | None = Field(default=None, max_length=300)
    token: str | None = Field(default=None, max_length=1000)   # None = keep, "" = clear
    power_entity: str | None = Field(default=None, max_length=200)
    energy_entity: str | None = Field(default=None, max_length=200)
    price_entity: str | None = Field(default=None, max_length=200)
    fixed_price: float | None = Field(default=None, ge=0, le=100)
    clear_fixed_price: bool | None = None
    watts: float | None = Field(default=None, gt=0, le=5000)


@router.get("/api/power/settings")
def get_settings():
    return _public(settings(_main().db))


@router.post("/api/power/settings")
def save_settings(body: PowerSettingsIn):
    m = _main()
    cfg = stored_settings(m.db)
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "clear_fixed_price":
            if v:
                cfg["fixed_price"] = None
            continue
        if k == "url":
            v = v.strip().rstrip("/")
            if v and not v.startswith(("http://", "https://")):
                raise HTTPException(400, "Adressen skal starte med http:// eller https://")
        cfg[k] = v.strip() if isinstance(v, str) else v
    m.db.set(KEY, {k: cfg[k] for k in DEFAULTS})
    try:
        get_meter().sample()
    except Exception:
        log.exception("power sample after save failed")
    return _public(cfg)


@router.get("/api/power/entities")
def entities():
    cfg = settings(_main().db)
    if not (cfg["url"] and cfg["token"]):
        raise HTTPException(400, "Indtast Home Assistant-adresse og token først")
    out = {"power": [], "energy": [], "price": []}
    for st in get_meter().client(cfg).states():
        eid = str(st.get("entity_id") or "")
        if not eid.startswith("sensor."):
            continue
        a = st.get("attributes") or {}
        unit = str(a.get("unit_of_measurement") or "")
        row = {"entity_id": eid, "name": a.get("friendly_name") or eid, "unit": unit, "state": st.get("state")}
        if unit in ("W", "kW"):
            out["power"].append(row)
        elif unit in ("Wh", "kWh"):
            out["energy"].append(row)
        elif "/kwh" in unit.lower().replace(" ", "") or a.get("device_class") == "monetary" and "pris" in eid + str(row["name"]).lower():
            out["price"].append(row)
    for v in out.values():
        v.sort(key=lambda r: (not any(x in (r["entity_id"] + str(r["name"])).lower() for x in ("shelly", "x2d", "print", "plug", "stik")), r["name"]))
    return out


@router.get("/api/power")
def power_now():
    m = _main()
    met = get_meter()
    cfg = settings(m.db)
    with m.engine.lock:
        s = print_summary(m.engine.state) if m.engine.state else None
    current = None
    if s and s["task_id"] and s["state"] in ACTIVE_STATES:
        current = energy_for(m.db, [s["task_id"]]).get(s["task_id"])
    return {"configured": configured(cfg), **met.now, "current": current,
            "avg_watts": met.avg_watts(), "watts": cfg["watts"], "by_material": watts_by_material(m.db),
            "by_material_dual": watts_by_material(m.db, dual=True), "dual": watts_dual(m.db)}


@router.get("/api/power/window")
def price_window(hours: float = 1.0):
    """Average electricity price over the next `hours` (for the price calculator)."""
    hours = max(0.0, min(hours, 96.0))
    m = _main()
    cfg = settings(m.db)
    met = get_meter()
    st = None
    if cfg["url"] and cfg["token"] and cfg["price_entity"]:
        try:
            st = met.client(cfg).state(cfg["price_entity"])
        except HTTPException:
            st = met._price_state
    if st:
        avg, coverage = window_price(price_intervals(st), time.time(), hours * 3600)
        if avg is not None:
            return {"price": round(avg, 4), "coverage": round(coverage, 3), "source": "elpris i printvinduet"}
        now = price_now(st)
        if now is not None:
            return {"price": round(now, 4), "coverage": 0.0, "source": "elpris lige nu"}
    fixed = _num(cfg["fixed_price"])
    return {"price": fixed, "coverage": 0.0, "source": "fast pris" if fixed is not None else "ingen elpris"}


def attach(app) -> None:
    """Start the meter with the app."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a):
            get_meter().start()
            try:
                yield
            finally:
                get_meter().stop()

    app.router.lifespan_context = lifespan
