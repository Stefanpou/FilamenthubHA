"""Talks to FilamentHub (same add-on repository) with its API token: spools (/api/v1/spool), the print library
(/api/library) and the measured power + electricity price (/api/power, /api/power/window).

Pure helpers below the client (stock, matching, price) take plain dicts so they are easy to test."""
from __future__ import annotations

import logging
import re
import threading
import time

import requests

from . import config
from .db import Database

log = logging.getLogger("modelbank.fh")
TTL = 60.0
TIMEOUT = 6.0


class FHError(Exception):
    pass


class Client:
    def __init__(self, db: Database):
        self.db = db
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def settings(self) -> dict:
        s = self.db.get("fh", {}) or {}
        return {"url": (s.get("url") or config.default_filamenthub_url()).rstrip("/"), "token": s.get("token") or ""}

    def configured(self) -> bool:
        return bool(self.settings()["token"])

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def get(self, path: str, ttl: float = TTL):
        with self._lock:
            hit = self._cache.get(path)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        s = self.settings()
        if not s["token"]:
            raise FHError("Indsæt FilamentHubs API-token under Indstillinger")
        try:
            r = requests.get(s["url"] + path, headers={"Authorization": f"Bearer {s['token']}"}, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise FHError(f"FilamentHub svarer ikke ({type(e).__name__})") from e
        if r.status_code == 401:
            raise FHError("FilamentHub afviste API-tokenet – tjek tokenet, og at FilamentHub er 0.3.27 eller nyere")
        if not r.ok:
            raise FHError(f"FilamentHub svarede {r.status_code}")
        data = r.json()
        with self._lock:
            self._cache[path] = (time.time(), data)
        return data

    def raw(self, path: str) -> requests.Response:
        s = self.settings()
        if not s["token"]:
            raise FHError("FilamentHub er ikke sat op")
        try:
            r = requests.get(s["url"] + path, headers={"Authorization": f"Bearer {s['token']}"}, timeout=TIMEOUT * 2)
        except requests.RequestException as e:
            raise FHError(f"FilamentHub svarer ikke ({type(e).__name__})") from e
        if not r.ok:
            raise FHError(f"FilamentHub svarede {r.status_code}")
        return r

    def spools(self) -> list[dict]:
        return [spool_view(s) for s in self.get("/api/v1/spool") if not s.get("archived")]

    def library(self) -> list[dict]:
        return self.get("/api/library?limit=2000").get("items") or []

    def power(self) -> dict:
        return self.get("/api/power", ttl=300)

    def price_window(self, hours: float) -> dict:
        return self.get(f"/api/power/window?hours={max(0.0, min(hours, 96.0)):.2f}", ttl=300)

    def status(self) -> dict:
        s = self.settings()
        out = {"url": s["url"], "configured": bool(s["token"]), "ok": False, "error": None}
        if not s["token"]:
            out["error"] = "Intet API-token"
            return out
        try:
            info = self.get("/api/v1/info", ttl=5)
            self.get("/api/library?limit=1", ttl=5)
            out.update(ok=True, version=info.get("version"))
        except FHError as e:
            out["error"] = str(e)
        return out


# ----------------------------------------------------------------------------- spools / stock
def spool_view(s: dict) -> dict:
    f = s.get("filament") or {}
    v = f.get("vendor") or {}
    nominal = s.get("nominal_weight") or s.get("initial_weight") or f.get("weight")
    kg = None
    if s.get("price") and float(nominal or 0) > 0:
        kg = float(s["price"]) / float(nominal) * 1000
    elif f.get("price") and float(f.get("weight") or 0) > 0:
        kg = float(f["price"]) / float(f["weight"]) * 1000
    color = (f.get("color_hex") or "").lstrip("#")[:6].upper()
    multi = [c.lstrip("#")[:6].upper() for c in (f.get("multi_color_hexes") or "").split(",") if c.strip()]
    return {"id": s.get("id"), "name": " ".join(filter(None, [v.get("name"), f.get("name")])),
            "material": (f.get("material") or "").upper(), "color": color or (multi[0] if multi else ""),
            "remaining": float(s.get("remaining_weight") or 0), "kg_price": round(kg, 2) if kg else None,
            "location": s.get("location") or ""}


def _rgb(h: str):
    h = (h or "").lstrip("#")
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return None


def color_distance(a: str, b: str) -> float:
    """Perceptual-ish RGB distance (redmean), 0 .. ~765."""
    x, y = _rgb(a), _rgb(b)
    if not x or not y:
        return 999.0
    rm = (x[0] + y[0]) / 2
    dr, dg, db_ = x[0] - y[0], x[1] - y[1], x[2] - y[2]
    return ((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db_ * db_) ** 0.5


SAME_COLOR = 90.0


def material_key(m: str) -> str:
    """'PETG HF' / 'PETG-HF' / 'petg' -> 'PETG'; 'PLA Matte' -> 'PLA'; 'PETG-CF' stays (a different filament)."""
    m = (m or "").upper().strip()
    m = re.sub(r"[\s_]+", " ", m)
    for suffix in (" HF", "-HF", " BASIC", " MATTE", " SILK", " PLUS", "+", " TOUGH", " TRANSLUCENT"):
        if m.endswith(suffix):
            m = m[: -len(suffix)]
    return m.strip()


def stock_for(filaments: list[dict], spools: list[dict]) -> list[dict]:
    """For each filament the model needs: the best spool (same material, enough left, closest colour) and a status:
    ok (same colour, enough), color (enough of the material, other colour), low (some, not enough on one spool),
    missing (none of that material), unknown (the file says nothing about grams)."""
    out = []
    for f in filaments:
        key = material_key(f.get("type") or "")
        need = f.get("grams")
        cands = [s for s in spools if key and material_key(s["material"]) == key and s["remaining"] > 0]
        cands.sort(key=lambda s: (color_distance(f.get("color") or "", s["color"]), -s["remaining"]))
        enough = [s for s in cands if need is None or s["remaining"] >= need]
        best = enough[0] if enough else (cands[0] if cands else None)
        dist = color_distance(f.get("color") or "", best["color"]) if best else None
        if not cands:
            status = "missing"
        elif need is None:
            status = "unknown" if not f.get("color") or dist > SAME_COLOR else "ok"
        elif not enough:
            status = "low"
        elif f.get("color") and dist > SAME_COLOR:
            status = "color"
        else:
            status = "ok"
        out.append({**f, "material_key": key, "status": status, "spool": best,
                    "total_material": round(sum(s["remaining"] for s in cands), 1),
                    "alternatives": [s for s in enough[:4] if not best or s["id"] != best["id"]][:3]})
    return out


# ----------------------------------------------------------------------------- print history
def norm(s: str) -> str:
    """Compact comparable name: lower case letters and digits only, without .3mf/.stl/.gcode and 'plate N'.
    'PowK+_holder_31jul22.3mf' and 'PowK_holder_31jul22' -> 'powkholder31jul22'."""
    s = (s or "").lower().strip()
    s = re.sub(r"\.(gcode\.)?(3mf|stl|gcode)$", "", s)
    s = re.sub(r"\bplate\s*\d+\b", " ", s)
    return re.sub(r"[^0-9a-zæøå]+", "", s)


MIN_PREFIX = 6


def model_keys(model: dict) -> set[str]:
    return {norm(model.get("name") or ""), norm((model.get("relpath") or "").rsplit("/", 1)[-1]),
            norm((model.get("meta") or {}).get("title") or "")} - {""}


def match_score(keys: set[str], entry: dict) -> int:
    """0 = not this model. Same name as the print's title, MakerWorld design or file: 10000 + length. Bambu names a
    print '<project>_<plate name>', so a title that starts with the model's name also counts: its length."""
    names = {norm(entry.get("title") or ""), norm(entry.get("design_title") or ""), norm(entry.get("file_name") or "")} - {""}
    best = 0
    for k in keys:
        if k in names:
            best = max(best, 10000 + len(k))
        elif len(k) >= MIN_PREFIX and any(n.startswith(k) for n in names):
            best = max(best, len(k))
    return best


def auto_matches(models: list[dict], entries: list[dict]) -> dict[int, set[str]]:
    """{model id: task ids} - each print goes to the model(s) whose name fits it best (exact beats prefix, longer
    beats shorter), so 'Benchy' and 'Benchy v2' do not both get every Benchy print."""
    keys = {m["id"]: model_keys(m) for m in models}
    out: dict[int, set[str]] = {}
    for e in entries:
        tid = str(e.get("task_id") or "")
        if not tid:
            continue
        scores = {mid: match_score(k, e) for mid, k in keys.items()}
        top = max(scores.values(), default=0)
        if top <= 0:
            continue
        for mid, sc in scores.items():
            if sc == top:
                out.setdefault(mid, set()).add(tid)
    return out


def print_row(e: dict) -> dict:
    return {"task_id": str(e.get("task_id")), "title": e.get("title") or "", "end": e.get("end"), "status": e.get("status"),
            "weight": e.get("weight"), "seconds": e.get("seconds"), "plate": e.get("plate"),
            "materials": e.get("materials") or []}


# ----------------------------------------------------------------------------- price
def price(lines: list[dict], hours: float, power: dict | None, window: dict | None, margin: dict) -> dict:
    """Same model as FilamentHub's price calculator: filament (grams x kr/kg) + electricity (watts x hours x the
    price in the coming print window) + margin (percent on top, or a fixed sale price).
    lines: [{"grams", "kg_price", "material"}]"""
    filament = sum((l.get("grams") or 0) / 1000 * (l.get("kg_price") or 0) for l in lines)
    main = max(lines, key=lambda l: l.get("grams") or 0, default={}).get("material") or ""
    p = power or {}
    by = (p.get("by_material") or {}).get(material_key(main)) or (p.get("by_material") or {}).get(main.upper())
    watts = (by or {}).get("watts") or p.get("avg_watts") or p.get("watts") or margin.get("watts") or 147
    kwh = watts * hours / 1000
    el_price = (window or {}).get("price")
    el = kwh * el_price if el_price is not None else None
    cost = filament + (el or 0)
    if margin.get("mode") == "fixed" and margin.get("fixed") is not None:
        sale = float(margin["fixed"])
    else:
        sale = cost * (1 + float(margin.get("pct") or 0) / 100)
    return {"filament": round(filament, 2), "kwh": round(kwh, 3), "watts": round(watts), "watts_measured": bool(by),
            "el_price": el_price, "el_source": (window or {}).get("source"), "electricity": None if el is None else round(el, 2),
            "cost": round(cost, 2), "sale": round(sale, 2),
            "margin_pct": round((sale / cost - 1) * 100, 1) if cost > 0 else None}
