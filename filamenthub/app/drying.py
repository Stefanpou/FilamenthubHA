"""AMS drying: sessions seen in the printer's reports (an AMS unit with `dry_time` > 0, the minutes left) and the
electricity used meanwhile, measured with the print meter in app/power.py. Earlier sessions can be read from Home
Assistant's history (the Bambu Lab integration's "drying" binary sensor) with the power and price history."""
from __future__ import annotations

import logging
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from . import power

log = logging.getLogger("filamenthub.drying")
router = APIRouter()

STALE = 600          # an open session not seen for this long is closed (printer off, AMS unplugged)
MIN_SESSION = 120    # shorter on/off blips are not sessions


def _main():
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "db"):
        from . import main as m
    return m


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def units_drying(state: dict | None) -> dict[int, dict]:
    """{unit id: {left_min, temp, hours, filament}} for the AMS units drying right now."""
    out = {}
    for u in ((state or {}).get("ams") or {}).get("ams") or []:
        left = _num(u.get("dry_time"))
        try:
            uid = int(u.get("id"))
        except (TypeError, ValueError):
            continue
        if left and left > 0:
            ds = u.get("dry_setting") if isinstance(u.get("dry_setting"), dict) else {}
            out[uid] = {"left_min": int(left), "temp": _num(ds.get("dry_temperature")),
                        "hours": _num(ds.get("dry_duration")), "filament": str(ds.get("dry_filament") or "") or None}
    return out


class Tracker:
    """Fed by the power meter every sample: opens, meters and closes drying sessions."""

    def __init__(self, db):
        self.db = db
        self._last: dict | None = None   # previous sample {ts, kwh, w}

    def sample(self, state: dict | None, now: float, w: float | None, kwh: float | None, price: float | None,
               printing: bool, models: dict[int, str] | None = None) -> None:
        drying = units_drying(state)
        open_rows = {r["unit"]: r for r in self.db.query("SELECT * FROM drying WHERE end_ts IS NULL")}
        for uid, r in open_rows.items():
            if uid not in drying and (state is not None or now - r["last_ts"] > STALE):
                self._close(r)
        if not drying:
            self._last = None
            return
        last, self._last = self._last, {"ts": now, "kwh": kwh, "w": w}
        used = None
        if last:
            dt = now - last["ts"]
            if kwh is not None and last["kwh"] is not None and 0 <= kwh - last["kwh"] < 5:
                used = kwh - last["kwh"]
            elif w is not None and 0 < dt <= 300:
                used = ((w + (last["w"] if last["w"] is not None else w)) / 2) * dt / 3_600_000
        share = (used or 0.0) / len(drying)   # two units drying at once share the printer's meter
        for uid, d in drying.items():
            r = open_rows.get(uid)
            if r is None:
                self.db.execute("INSERT INTO drying(unit, model, start_ts, last_ts, temp, hours, filament, with_print) "
                                "VALUES(?,?,?,?,?,?,?,?)", (uid, (models or {}).get(uid), now, now, d["temp"], d["hours"],
                                                            d["filament"], int(printing)))
                continue
            self.db.execute(
                "UPDATE drying SET last_ts=?, kwh=kwh+?, cost=cost+?, priced_kwh=priced_kwh+?, temp=COALESCE(?, temp), "
                "hours=COALESCE(?, hours), filament=COALESCE(?, filament), with_print=MAX(with_print, ?), "
                "model=COALESCE(model, ?) WHERE id=?",
                (now, share, share * price if price is not None else 0.0, share if price is not None else 0.0,
                 d["temp"], d["hours"], d["filament"], int(printing), (models or {}).get(uid), r["id"]))

    def _close(self, r: dict) -> None:
        if r["last_ts"] - r["start_ts"] < MIN_SESSION:
            self.db.execute("DELETE FROM drying WHERE id=?", (r["id"],))
        else:
            self.db.execute("UPDATE drying SET end_ts=last_ts WHERE id=?", (r["id"],))


_tracker: Tracker | None = None


def get_tracker() -> Tracker:
    global _tracker
    if _tracker is None:
        _tracker = Tracker(_main().db)
    return _tracker


# ============================================================================ from Home Assistant's history
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _history(ha: power.HomeAssistant, entity: str, start: float, end: float) -> list[dict]:
    r = ha._get(f"/api/history/period/{quote(_iso(start))}?filter_entity_id={quote(entity)}"
                f"&end_time={quote(_iso(end))}&minimal_response&no_attributes")
    return r[0] if r else []


def _periods(rows: list[dict], end: float) -> list[tuple[float, float]]:
    """The 'on' periods of a binary sensor's history; blips under MIN_SESSION and gaps under 2 min are merged away."""
    out: list[list[float]] = []
    for i, x in enumerate(rows):
        if x.get("state") != "on":
            continue
        s = power._ts(x.get("last_changed"))
        e = power._ts(rows[i + 1].get("last_changed")) if i + 1 < len(rows) else end
        if s is None or e is None:
            continue
        if out and s - out[-1][1] < 120:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [(s, e) for s, e in out if e - s >= MIN_SESSION]


def _steps(rows: list[dict]) -> list[tuple[float, float]]:
    return [(t, v) for x in rows if (t := power._ts(x.get("last_changed"))) is not None and (v := _num(x.get("state"))) is not None]


def _value_at(steps: list[tuple[float, float]], t: float) -> float | None:
    v = None
    for ts, val in steps:
        if ts > t:
            break
        v = val
    return v


def energy_between(ha: power.HomeAssistant, cfg: dict, start: float, end: float) -> tuple[float, float | None]:
    """(kWh, kr) used by the printer's plug between two times, from the power sensor's history (power x time) and
    the price sensor's history (or the fixed price)."""
    pw = _history(ha, cfg["power_entity"], start - 3600, end)
    unit_kw = str((ha.state(cfg["power_entity"]).get("attributes") or {}).get("unit_of_measurement") or "") == "kW"
    steps = [(t, v * 1000 if unit_kw else v) for t, v in _steps(pw)]
    prices, scale = [], 1.0
    if cfg["price_entity"]:
        prices = _steps(_history(ha, cfg["price_entity"], start - 3 * 3600, end))
        scale = power.price_scale(ha.state(cfg["price_entity"]))
    fixed = _num(cfg.get("fixed_price"))
    kwh = cost = 0.0
    priced = False
    edges = sorted({start, end, *(t for t, _ in steps if start < t < end), *(t for t, _ in prices if start < t < end)})
    for a, b in zip(edges, edges[1:]):
        w = _value_at(steps, a)
        if w is None or w < 0:
            continue
        k = w * (b - a) / 3_600_000
        p = _value_at(prices, a)
        p = p * scale if p is not None else fixed
        kwh += k
        if p is not None:
            cost += k * p
            priced = True
    return kwh, (cost if priced else None)


DRY_SENSOR = re.compile(r"^binary_sensor\..*_ams_(\d+)_(torring|tørring|drying)$")


def import_from_ha(db, days: int = 14) -> int:
    """Add the drying sessions Home Assistant's Bambu Lab integration saw in the last `days` that are not here."""
    cfg = power.settings(db)
    if not power.configured(cfg) or not cfg["power_entity"]:
        raise HTTPException(400, "Vælg printerens strømmåler under Indstillinger → Elforbrug først")
    ha = power.HomeAssistant(cfg["url"], cfg["token"])
    sensors = [(s["entity_id"], int(m.group(1)) - 1) for s in ha.states() if (m := DRY_SENSOR.match(s["entity_id"]))]
    if not sensors:
        raise HTTPException(404, "Fandt ingen tørre-sensor fra Bambu Lab-integrationen i Home Assistant")
    now = time.time()
    have = db.query("SELECT unit, start_ts, COALESCE(end_ts, last_ts) e FROM drying")
    n = 0
    for entity, unit in sensors:
        for s, e in _periods(_history(ha, entity, now - days * 86400, now), now):
            if any(h["unit"] == unit and h["start_ts"] < e and h["e"] > s for h in have):
                continue
            if e >= now - 60:
                continue   # still drying: the live tracker has it
            kwh, cost = energy_between(ha, cfg, s, e)
            db.execute("INSERT INTO drying(unit, start_ts, last_ts, end_ts, kwh, cost, priced_kwh, source) "
                       "VALUES(?,?,?,?,?,?,?, 'ha')", (unit, s, e, e, kwh, cost or 0.0, kwh if cost is not None else 0.0))
            n += 1
    return n


# ============================================================================ routes
def _row(r: dict, prints: list[tuple[float, float]]) -> dict:
    end = r["end_ts"] or r["last_ts"]
    with_print = bool(r["with_print"]) or any(s < end and e > r["start_ts"] for s, e in prints)
    return {"id": r["id"], "unit": r["unit"], "model": r["model"], "start": r["start_ts"], "end": r["end_ts"],
            "hours": round((end - r["start_ts"]) / 3600, 2), "set_hours": r["hours"], "temp": r["temp"],
            "filament": r["filament"], "kwh": round(r["kwh"], 3), "cost": round(r["cost"], 2) if r["priced_kwh"] else None,
            "with_print": with_print, "source": r["source"]}


@router.get("/api/drying")
def get_drying(limit: int = 20):
    m = _main()
    rows = m.db.query("SELECT * FROM drying ORDER BY start_ts DESC LIMIT ?", (max(1, min(limit, 200)),))
    prints = [(p["first_ts"], p["last_ts"]) for p in m.db.query("SELECT first_ts, last_ts FROM print_energy WHERE last_ts > ?",
                                                                 (time.time() - 90 * 86400,))]
    with m.engine.lock:
        now = units_drying(m.engine.state)
    month = m.db.query_one("SELECT SUM(kwh) k, SUM(cost) c, SUM(priced_kwh) p, COUNT(*) n FROM drying WHERE start_ts > ?",
                           (time.time() - 30 * 86400,)) or {}
    return {"now": [{"unit": u, **d} for u, d in now.items()],
            "sessions": [_row(r, prints) for r in rows],
            "month": {"kwh": round(month.get("k") or 0, 2), "cost": round(month["c"], 2) if month.get("p") else None,
                      "count": month.get("n") or 0}}


@router.post("/api/drying/import")
def post_import():
    return {"imported": import_from_ha(_main().db)}


@router.delete("/api/drying/{sid}")
def delete_session(sid: int):
    _main().db.execute("DELETE FROM drying WHERE id=? AND end_ts IS NOT NULL", (sid,))
    return {"ok": True}
