"""Small Spoolman REST client (https://github.com/Donkie/Spoolman)."""
from __future__ import annotations

import json
import logging
import time

import requests

log = logging.getLogger("spoolsync.spoolman")

TAG_FIELD = "tag"  # spool extra field holding the Bambu RFID tray_uuid (shared with other Bambu tools)
# Filament extra fields with what the filament databases know beyond Spoolman's own columns (key -> name).
FILAMENT_FIELDS = {"ean": "EAN-stregkode", "color_name": "Farvenavn", "finish": "Finish", "spool_type": "Rulletype",
                   "properties": "Egenskaber", "nozzle_range": "Dysetemp. anbefalet", "bed_range": "Pladetemp. anbefalet",
                   "td": "TD-værdi (HueForge)", "category": "Kategori"}   # category: e.g. "Silk", "Basic" (else from name)
DRIED_FIELD = "dried"  # spool extra field (Spoolman datetime): when the spool was last dried


class SpoolmanError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        # retryable: the request never reached Spoolman, so it is safe to send it again.
        self.retryable = retryable
        self.status = status


class Spoolman:
    def __init__(self, base_url: str, timeout: float = 15):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.timeout = timeout
        self._cache: tuple[float, list[dict]] | None = None

    def _req(self, method: str, path: str, **kw):
        try:
            resp = requests.request(method, self.base + path, timeout=self.timeout, **kw)
        except requests.RequestException as e:
            # ConnectionError (incl. ConnectTimeout) means nothing was sent; a ReadTimeout might have been applied.
            raise SpoolmanError(f"Spoolman svarer ikke: {e}",
                                retryable=isinstance(e, requests.ConnectionError)) from e
        if resp.status_code >= 400:
            raise SpoolmanError(f"Spoolman fejl {resp.status_code} på {path}: {resp.text[:300]}",
                                status=resp.status_code)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ---- health / setup --------------------------------------------------
    def health(self) -> bool:
        try:
            return (self._req("GET", "/health") or {}).get("status") == "healthy"
        except SpoolmanError:
            return False

    def ensure_extra_field(self, key: str = TAG_FIELD, name: str = "RFID tag") -> None:
        fields = self._req("GET", "/field/spool") or []
        if any(f.get("key") == key for f in fields):
            return
        self._req("POST", f"/field/spool/{key}", json={"name": name, "field_type": "text"})

    # ---- spools ----------------------------------------------------------
    def spools(self, allow_archived: bool = False, max_age: float = 5.0) -> list[dict]:
        if not allow_archived and self._cache and time.time() - self._cache[0] < max_age:
            return self._cache[1]
        data = self._req("GET", "/spool", params={"allow_archived": str(allow_archived).lower()}) or []
        if not allow_archived:
            self._cache = (time.time(), data)
        return data

    def invalidate(self) -> None:
        self._cache = None

    def spool(self, spool_id: int) -> dict:
        return self._req("GET", f"/spool/{int(spool_id)}")

    def use_weight(self, spool_id: int, grams: float) -> dict:
        self.invalidate()
        return self._req("PUT", f"/spool/{int(spool_id)}/use", json={"use_weight": round(float(grams), 2)})

    def measure(self, spool_id: int, gross_weight: float) -> dict:
        self.invalidate()
        return self._req("PUT", f"/spool/{int(spool_id)}/measure", json={"weight": float(gross_weight)})

    def update_spool(self, spool_id: int, **fields) -> dict:
        self.invalidate()
        return self._req("PATCH", f"/spool/{int(spool_id)}", json=fields)

    def set_extra(self, spool_id: int, key: str, value: str | None) -> dict:
        return self.update_spool(spool_id, extra={key: None if value is None else json.dumps(value)})

    def create_spool(self, **fields) -> dict:
        self.invalidate()
        return self._req("POST", "/spool", json=fields)

    # ---- external filament database (SpoolmanDB, synced hourly by Spoolman) ---------
    def external_filaments(self) -> list[dict]:
        return self._req("GET", "/external/filament") or []

    def external_materials(self) -> list[dict]:
        return self._req("GET", "/external/material") or []

    def create_filament(self, **fields) -> dict:
        return self._req("POST", "/filament", json=fields)

    def update_filament(self, filament_id: int, **fields) -> dict:
        return self._req("PATCH", f"/filament/{int(filament_id)}", json=fields)

    def filament(self, filament_id: int) -> dict:
        return self._req("GET", f"/filament/{int(filament_id)}")

    # ---- filaments / vendors ---------------------------------------------
    def vendors(self) -> list[dict]:
        return self._req("GET", "/vendor") or []

    def get_or_create_vendor(self, name: str) -> dict:
        name = name.strip()
        for v in self.vendors():
            if v.get("name", "").strip().lower() == name.lower():
                return v
        return self._req("POST", "/vendor", json={"name": name})

    def filaments(self) -> list[dict]:
        return self._req("GET", "/filament") or []

    def get_or_create_filament(self, *, vendor_id: int, name: str, material: str, color_hex: str,
                               weight: float, spool_weight: float | None, density: float,
                               diameter: float = 1.75) -> dict:
        color_hex = color_hex.lstrip("#").upper()
        for f in self.filaments():
            if ((f.get("vendor") or {}).get("id") == vendor_id
                    and (f.get("name") or "").strip().lower() == name.strip().lower()
                    and (f.get("material") or "").strip().lower() == material.strip().lower()
                    and (f.get("color_hex") or "").upper() == color_hex
                    and abs(float(f.get("weight") or 0) - weight) < 0.5):
                return f
        body = {"vendor_id": vendor_id, "name": name.strip(), "material": material.strip(), "color_hex": color_hex,
                "weight": weight, "density": density, "diameter": diameter}
        if spool_weight is not None:
            body["spool_weight"] = spool_weight
        return self._req("POST", "/filament", json=body)


def extra_value(spool: dict, key: str) -> str:
    """Decode a JSON-encoded extra field value ('"abc"' -> 'abc')."""
    raw = (spool.get("extra") or {}).get(key)
    if raw is None:
        return ""
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        val = raw
    return "" if val is None else str(val)


def spool_display(spool: dict) -> dict:
    """Compact representation used by the web UI."""
    f = spool.get("filament") or {}
    vendor = (f.get("vendor") or {}).get("name") or ""
    color = (f.get("color_hex") or "").upper()
    multi = [c.upper() for c in (f.get("multi_color_hexes") or "").split(",") if c]
    return {
        "id": spool["id"],
        "vendor": vendor,
        "name": f.get("name") or "",
        "material": f.get("material") or "",
        "color": color[:6] if color else (multi[0][:6] if multi else ""),
        "colors": [c[:6] for c in multi],
        "remaining": round(float(spool.get("remaining_weight") or 0), 1),
        "initial": float(spool.get("initial_weight") or f.get("weight") or 0),
        "used": round(float(spool.get("used_weight") or 0), 1),
        "spool_weight": spool.get("spool_weight") if spool.get("spool_weight") is not None else f.get("spool_weight"),
        "location": spool.get("location") or "",
        "comment": spool.get("comment") or "",
        "archived": bool(spool.get("archived")),
        "last_used": spool.get("last_used"),
        "tag": extra_value(spool, TAG_FIELD),
        "dried": extra_value(spool, DRIED_FIELD)[:10] or None,   # YYYY-MM-DD
        "density": f.get("density"),
        "registered": spool.get("registered"),
        "kg_price": _kg_price(spool),
        "price": spool.get("price"),
        "article_number": f.get("article_number") or "",
        "temps": [f.get("settings_extruder_temp"), f.get("settings_bed_temp")],
        "details": {k: v for k in FILAMENT_FIELDS if (v := extra_value(f, k))},
    }


def _kg_price(spool: dict) -> float | None:
    """Price per kg: the spool's price (else the filament's) over its start weight."""
    f = spool.get("filament") or {}
    price = spool.get("price") if spool.get("price") is not None else f.get("price")
    weight = spool.get("initial_weight") or f.get("weight")
    try:
        return round(float(price) / float(weight) * 1000, 2) if price is not None and weight else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
