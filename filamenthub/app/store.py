"""FilamentHub's own filament inventory (replaces Spoolman).

`LocalStore` has exactly the public interface of `app.spoolman.Spoolman` and returns the same dict shapes as
Spoolman 0.26, so the engine (sync.py), Filament Manager sync (fm.py) and the web API work unchanged. It is backed by
SQLite (`inventory.db` in the data directory; WAL, one connection guarded by a lock - same approach as db.py).

SpoolmanDB (the public filament database) is downloaded directly, cached in the data directory and refreshed in the
background at most every 6 hours; offline, the cache is served and no request ever waits for the network.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .spoolman import TAG_FIELD, SpoolmanError

log = logging.getLogger("spoolsync.store")

SPOOLMANDB_URL = "https://donkie.github.io/SpoolmanDB/filaments.json"
SPOOLMANDB_MATERIALS_URL = "https://donkie.github.io/SpoolmanDB/materials.json"
EXTERNAL_MAX_AGE = 6 * 3600
EXTERNAL_RETRY = 15 * 60   # no new SpoolmanDB download within 15 min of the last attempt

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registered TEXT NOT NULL,
    name TEXT NOT NULL,
    comment TEXT,
    empty_spool_weight REAL,
    external_id TEXT,
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS filaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registered TEXT NOT NULL,
    name TEXT,
    vendor_id INTEGER REFERENCES vendors(id) ON DELETE SET NULL,
    material TEXT,
    price REAL,
    density REAL NOT NULL DEFAULT 1.24,
    diameter REAL NOT NULL DEFAULT 1.75,
    weight REAL,
    spool_weight REAL,
    article_number TEXT,
    comment TEXT,
    settings_extruder_temp INTEGER,
    settings_bed_temp INTEGER,
    color_hex TEXT,
    multi_color_hexes TEXT,
    multi_color_direction TEXT,
    external_id TEXT,
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS spools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registered TEXT NOT NULL,
    first_used TEXT,
    last_used TEXT,
    filament_id INTEGER NOT NULL REFERENCES filaments(id),
    price REAL,
    initial_weight REAL,
    spool_weight REAL,
    used_weight REAL NOT NULL DEFAULT 0,
    location TEXT,
    lot_nr TEXT,
    comment TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_spools_filament ON spools(filament_id);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spool_id INTEGER NOT NULL,
    grams REAL NOT NULL,
    ts REAL NOT NULL,
    source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_usage_spool ON usage(spool_id, ts);
CREATE TABLE IF NOT EXISTS fields (
    entity_type TEXT NOT NULL,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    field_type TEXT NOT NULL DEFAULT 'text',
    "order" INTEGER NOT NULL DEFAULT 0,
    unit TEXT,
    default_value TEXT,
    choices TEXT,
    multi_choice INTEGER,
    PRIMARY KEY (entity_type, key)
);
"""

VENDOR_COLS = ("name", "comment", "empty_spool_weight", "external_id")
FILAMENT_COLS = ("name", "vendor_id", "material", "price", "density", "diameter", "weight", "spool_weight",
                 "article_number", "comment", "settings_extruder_temp", "settings_bed_temp", "color_hex",
                 "multi_color_hexes", "multi_color_direction", "external_id")
SPOOL_COLS = ("filament_id", "first_used", "last_used", "price", "initial_weight", "spool_weight", "location",
              "lot_nr", "comment", "archived")
TEXT_COLS = ("name", "comment", "material", "article_number", "multi_color_direction", "external_id", "location",
             "lot_nr", "first_used", "last_used")
ENTITIES = ("vendor", "filament", "spool")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _hex(v):
    return None if v is None else str(v).lstrip("#").upper()


def _num(v, name: str):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SpoolmanError(f"{name} skal være et tal", status=422) from None
    if not math.isfinite(f):
        raise SpoolmanError(f"{name} skal være et tal", status=422)
    return f


def length_from_weight(weight: float | None, density: float | None, diameter: float | None) -> float | None:
    """mm of filament for a weight in grams (density g/cm3, diameter mm)."""
    if weight is None or not density or not diameter:
        return None
    return round(weight * 1000.0 / (density * math.pi * (diameter / 2.0) ** 2), 2)


def weight_from_length(length_mm: float, density: float, diameter: float) -> float:
    return length_mm * density * math.pi * (diameter / 2.0) ** 2 / 1000.0


class LocalStore:
    def __init__(self, data_dir: Path | str, path: Path | str | None = None, timeout: float = 15,
                 download: bool = True):
        self.data_dir = Path(data_dir) if str(data_dir) != ":memory:" else None
        db_path = str(path) if path is not None else (str(self.data_dir / "inventory.db") if self.data_dir else ":memory:")
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.download = download and self.data_dir is not None
        self._conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            if "nominal_weight" not in {r[1] for r in self._conn.execute("PRAGMA table_info(spools)")}:
                # the weight the price was paid for, when a weighing found more on the spool (NULL = initial_weight)
                self._conn.execute("ALTER TABLE spools ADD COLUMN nominal_weight REAL")
            if self._conn.execute("PRAGMA user_version").fetchone()[0] < 1:
                # 0.2.2 raised the start weight of overfilled spools without keeping the weight they were sold as:
                # those are priced per the filament's weight again.
                self._conn.execute(
                    "UPDATE spools SET nominal_weight=(SELECT weight FROM filaments f WHERE f.id=spools.filament_id) "
                    "WHERE (nominal_weight IS NULL OR nominal_weight>=initial_weight) "
                    "AND initial_weight>(SELECT weight FROM filaments f WHERE f.id=spools.filament_id)")
                self._conn.execute("PRAGMA user_version=1")
        # SpoolmanDB cache
        self._ext_lock = threading.Lock()
        self._ext_filaments: list[dict] = []
        self._ext_materials: list[dict] = []
        self._ext_loaded_ts = 0.0     # when the in-memory copy was fetched (file mtime / download time)
        self._ext_disk_checked = False
        self._ext_refreshing = False
        self._ext_last_attempt = 0.0   # start of the last download attempt (backoff while offline)
        self._ext_error = ""
        self._stop = threading.Event()

    # ---- low level -------------------------------------------------------
    def _q(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _q1(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._q(sql, params)
        return rows[0] if rows else None

    def _x(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            return self._conn.execute(sql, params)

    # ---- shape builders --------------------------------------------------
    @staticmethod
    def _vendor_out(r: dict) -> dict:
        return {"id": r["id"], "registered": r["registered"], "name": r["name"], "comment": r["comment"],
                "empty_spool_weight": r["empty_spool_weight"], "external_id": r["external_id"],
                "extra": json.loads(r["extra"] or "{}")}

    @staticmethod
    def _filament_out(r: dict, vendor: dict | None) -> dict:
        out = {"id": r["id"], "registered": r["registered"], "name": r["name"]}
        if vendor:
            out["vendor"] = vendor
        for k in ("material", "price", "density", "diameter", "weight", "spool_weight", "article_number", "comment",
                  "settings_extruder_temp", "settings_bed_temp", "color_hex", "multi_color_hexes",
                  "multi_color_direction", "external_id"):
            out[k] = r[k]
        out["extra"] = json.loads(r["extra"] or "{}")
        return out

    @staticmethod
    def _spool_out(r: dict, fil: dict) -> dict:
        initial = r["initial_weight"] if r["initial_weight"] is not None else fil.get("weight")
        spool_weight = r["spool_weight"] if r["spool_weight"] is not None else fil.get("spool_weight")
        if spool_weight is None:
            spool_weight = (fil.get("vendor") or {}).get("empty_spool_weight")
        used = float(r["used_weight"] or 0)
        remaining = max(initial - used, 0.0) if initial is not None else None
        density, diameter = fil.get("density"), fil.get("diameter")
        return {
            "id": r["id"], "registered": r["registered"], "first_used": r["first_used"], "last_used": r["last_used"],
            "filament": fil, "price": r["price"] if r["price"] is not None else fil.get("price"),
            "remaining_weight": remaining, "initial_weight": initial, "spool_weight": spool_weight,
            "nominal_weight": r.get("nominal_weight") or initial,
            "used_weight": used,
            "remaining_length": length_from_weight(remaining, density, diameter),
            "used_length": length_from_weight(used, density, diameter),
            "location": r["location"], "lot_nr": r["lot_nr"], "comment": r["comment"],
            "archived": bool(r["archived"]), "extra": json.loads(r["extra"] or "{}"),
        }

    def _vendor_map(self) -> dict[int, dict]:
        return {r["id"]: self._vendor_out(r) for r in self._q("SELECT * FROM vendors")}

    def _filament_rows(self, where: str = "", params: tuple = ()) -> list[dict]:
        vendors = self._vendor_map()
        return [self._filament_out(r, vendors.get(r["vendor_id"]))
                for r in self._q(f"SELECT * FROM filaments {where} ORDER BY id", params)]

    # ---- health / setup --------------------------------------------------
    def health(self) -> bool:
        try:
            self._q1("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    def ensure_extra_field(self, key: str = TAG_FIELD, name: str = "RFID tag", field_type: str = "text",
                           entity: str = "spool") -> None:
        if not any(f["key"] == key for f in self.fields(entity)):
            self.add_field(entity, key, name=name, field_type=field_type)

    # ---- extra fields (Spoolman "field" API) -----------------------------
    def fields(self, entity: str) -> list[dict]:
        if entity not in ENTITIES:
            raise SpoolmanError("Ukendt type", status=404)
        out = []
        for r in self._q('SELECT * FROM fields WHERE entity_type=? ORDER BY "order", key', (entity,)):
            out.append({"key": r["key"], "name": r["name"], "order": r["order"], "unit": r["unit"],
                        "field_type": r["field_type"], "default_value": r["default_value"],
                        "choices": json.loads(r["choices"]) if r["choices"] else None,
                        "multi_choice": None if r["multi_choice"] is None else bool(r["multi_choice"]),
                        "entity_type": entity})
        return out

    def add_field(self, entity: str, key: str, *, name: str, field_type: str = "text", order: int = 0,
                  unit: str | None = None, default_value: str | None = None, choices: list | None = None,
                  multi_choice: bool | None = None) -> list[dict]:
        if entity not in ENTITIES:
            raise SpoolmanError("Ukendt type", status=404)
        if not key or not all(c.isalnum() or c == "_" for c in key):
            raise SpoolmanError("Ugyldig nøgle", status=422)
        if field_type not in ("text", "integer", "integer_range", "float", "float_range", "datetime", "boolean", "choice"):
            raise SpoolmanError("Ugyldig felttype", status=422)
        if self._q1("SELECT 1 FROM fields WHERE entity_type=? AND key=?", (entity, key)):
            raise SpoolmanError("Feltet findes allerede", status=409)
        self._x('INSERT INTO fields(entity_type, key, name, field_type, "order", unit, default_value, choices, '
                'multi_choice) VALUES(?,?,?,?,?,?,?,?,?)',
                (entity, key, name, field_type, order, unit, default_value,
                 json.dumps(choices) if choices is not None else None,
                 None if multi_choice is None else int(bool(multi_choice))))
        return self.fields(entity)

    def delete_field(self, entity: str, key: str) -> list[dict]:
        if entity not in ENTITIES:
            raise SpoolmanError("Ukendt type", status=404)
        self._x("DELETE FROM fields WHERE entity_type=? AND key=?", (entity, key))
        table = {"vendor": "vendors", "filament": "filaments", "spool": "spools"}[entity]
        with self.lock:
            for r in self._conn.execute(f"SELECT id, extra FROM {table}").fetchall():
                ex = json.loads(r["extra"] or "{}")
                if key in ex:
                    ex.pop(key)
                    self._conn.execute(f"UPDATE {table} SET extra=? WHERE id=?", (json.dumps(ex), r["id"]))
        return self.fields(entity)

    # ---- generic row writing ---------------------------------------------
    def _clean(self, cols: tuple, fields: dict, *, creating: bool) -> dict:
        out = {}
        for k, v in fields.items():
            if k not in cols:
                continue
            if k in ("price", "density", "diameter", "weight", "spool_weight", "initial_weight", "empty_spool_weight"):
                v = _num(v, k)
                if v is not None and v < 0:
                    raise SpoolmanError(f"{k} må ikke være negativ", status=422)
            elif k in ("color_hex",):
                v = _hex(v) or None
            elif k == "multi_color_hexes":
                v = ",".join(_hex(c) for c in str(v).split(",") if c.strip()) if v else None
            elif k == "archived":
                v = int(bool(v))
            elif k in ("settings_extruder_temp", "settings_bed_temp"):
                v = None if v is None else int(v)
            elif k in TEXT_COLS and v is not None:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    v = str(v)
                elif not isinstance(v, str):
                    raise SpoolmanError(f"{k} skal være tekst", status=422)
            out[k] = v
        return out

    @staticmethod
    def _merge_extra(old_json: str, new: dict | None) -> str:
        ex = json.loads(old_json or "{}")
        for k, v in (new or {}).items():
            if v is None:
                ex.pop(k, None)
            else:
                ex[k] = v if isinstance(v, str) else json.dumps(v)
        return json.dumps(ex)

    # ---- vendors -----------------------------------------------------------
    def vendors(self) -> list[dict]:
        return list(self._vendor_map().values())

    def vendor(self, vendor_id: int) -> dict:
        r = self._q1("SELECT * FROM vendors WHERE id=?", (int(vendor_id),))
        if not r:
            raise SpoolmanError(f"Producent {vendor_id} findes ikke", status=404)
        return self._vendor_out(r)

    def create_vendor(self, **fields) -> dict:
        d = self._clean(VENDOR_COLS, fields, creating=True)
        if not (d.get("name") or "").strip():
            raise SpoolmanError("Producenten skal have et navn", status=422)
        d["name"] = d["name"].strip()
        cur = self._x("INSERT INTO vendors(registered, name, comment, empty_spool_weight, external_id, extra) "
                      "VALUES(?,?,?,?,?,?)",
                      (_now(), d["name"], d.get("comment"), d.get("empty_spool_weight"), d.get("external_id"),
                       self._merge_extra("{}", fields.get("extra"))))
        return self.vendor(cur.lastrowid)

    def update_vendor(self, vendor_id: int, **fields) -> dict:
        cur = self._q1("SELECT * FROM vendors WHERE id=?", (int(vendor_id),))
        if not cur:
            raise SpoolmanError(f"Producent {vendor_id} findes ikke", status=404)
        d = self._clean(VENDOR_COLS, fields, creating=False)
        if "name" in d and not (d["name"] or "").strip():
            raise SpoolmanError("Producenten skal have et navn", status=422)
        if "extra" in fields:
            d["extra"] = self._merge_extra(cur["extra"], fields["extra"])
        self._update("vendors", int(vendor_id), d)
        return self.vendor(vendor_id)

    def delete_vendor(self, vendor_id: int) -> None:
        if not self._q1("SELECT 1 FROM vendors WHERE id=?", (int(vendor_id),)):
            raise SpoolmanError(f"Producent {vendor_id} findes ikke", status=404)
        self._x("DELETE FROM vendors WHERE id=?", (int(vendor_id),))   # filaments keep existing, without a vendor

    def get_or_create_vendor(self, name: str) -> dict:
        name = name.strip()
        with self.lock:
            for v in self.vendors():
                if v.get("name", "").strip().lower() == name.lower():
                    return v
            return self.create_vendor(name=name)

    def _update(self, table: str, row_id: int, d: dict) -> None:
        if not d:
            return
        cols = ", ".join(f'"{k}"=?' for k in d)
        self._x(f"UPDATE {table} SET {cols} WHERE id=?", (*d.values(), row_id))

    # ---- filaments -----------------------------------------------------------
    def filaments(self) -> list[dict]:
        return self._filament_rows()

    def filament(self, filament_id: int) -> dict:
        rows = self._filament_rows("WHERE id=?", (int(filament_id),))
        if not rows:
            raise SpoolmanError(f"Filament {filament_id} findes ikke", status=404)
        return rows[0]

    def create_filament(self, **fields) -> dict:
        d = self._clean(FILAMENT_COLS, fields, creating=True)
        if d.get("vendor_id") is not None:
            self.vendor(d["vendor_id"])   # 404 if missing
        d.setdefault("density", 1.24)
        d.setdefault("diameter", 1.75)
        if d.get("density") is None or d.get("diameter") is None:
            raise SpoolmanError("Densitet og diameter er påkrævet", status=422)
        d["registered"] = _now()
        d["extra"] = self._merge_extra("{}", fields.get("extra"))
        cols = ", ".join(f'"{k}"' for k in d)
        cur = self._x(f"INSERT INTO filaments({cols}) VALUES({','.join('?' * len(d))})", tuple(d.values()))
        return self.filament(cur.lastrowid)

    def update_filament(self, filament_id: int, **fields) -> dict:
        cur = self._q1("SELECT * FROM filaments WHERE id=?", (int(filament_id),))
        if not cur:
            raise SpoolmanError(f"Filament {filament_id} findes ikke", status=404)
        d = self._clean(FILAMENT_COLS, fields, creating=False)
        if d.get("vendor_id") is not None:
            self.vendor(d["vendor_id"])
        for k in ("density", "diameter"):
            if k in d and d[k] is None:
                raise SpoolmanError(f"{k} kan ikke fjernes", status=422)
        if "extra" in fields:
            d["extra"] = self._merge_extra(cur["extra"], fields["extra"])
        self._update("filaments", int(filament_id), d)
        return self.filament(filament_id)

    def delete_filament(self, filament_id: int) -> None:
        if not self._q1("SELECT 1 FROM filaments WHERE id=?", (int(filament_id),)):
            raise SpoolmanError(f"Filament {filament_id} findes ikke", status=404)
        if self._q1("SELECT 1 FROM spools WHERE filament_id=?", (int(filament_id),)):
            raise SpoolmanError("Filamentet har spoler og kan ikke slettes", status=409)
        self._x("DELETE FROM filaments WHERE id=?", (int(filament_id),))

    def get_or_create_filament(self, *, vendor_id: int, name: str, material: str, color_hex: str,
                               weight: float, spool_weight: float | None, density: float,
                               diameter: float = 1.75) -> dict:
        color_hex = color_hex.lstrip("#").upper()
        with self.lock:
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
            return self.create_filament(**body)

    # ---- spools --------------------------------------------------------------
    def _spools(self, where: str = "", params: tuple = ()) -> list[dict]:
        with self.lock:
            fils = {f["id"]: f for f in self._filament_rows()}
            rows = self._q(f"SELECT * FROM spools {where} ORDER BY id", params)
        return [self._spool_out(r, fils[r["filament_id"]]) for r in rows if r["filament_id"] in fils]

    def spools(self, allow_archived: bool = False, max_age: float = 5.0) -> list[dict]:
        return self._spools() if allow_archived else self._spools("WHERE archived=0")

    def invalidate(self) -> None:
        """(Spoolman cached its list; local reads are cheap, so there is nothing to drop.)"""

    def spool(self, spool_id: int) -> dict:
        rows = self._spools("WHERE id=?", (int(spool_id),))
        if not rows:
            raise SpoolmanError(f"Spole {spool_id} findes ikke", status=404)
        return rows[0]

    def _raw_spool(self, spool_id: int) -> dict:
        r = self._q1("SELECT * FROM spools WHERE id=?", (int(spool_id),))
        if not r:
            raise SpoolmanError(f"Spole {spool_id} findes ikke", status=404)
        return r

    def _apply_weights(self, spool_id: int, fields: dict, d: dict) -> None:
        """remaining_weight / used_weight in a create/update body -> the stored used_weight."""
        if "used_weight" in fields and fields["used_weight"] is not None:
            d["used_weight"] = max(_num(fields["used_weight"], "used_weight"), 0.0)
        elif "remaining_weight" in fields and fields["remaining_weight"] is not None:
            rem = _num(fields["remaining_weight"], "remaining_weight")
            cur = self._raw_spool(spool_id) if spool_id else None
            fil = self.filament((d.get("filament_id") or (cur or {}).get("filament_id")))
            initial = d["initial_weight"] if d.get("initial_weight") is not None else (
                cur["initial_weight"] if cur and "initial_weight" not in d and cur["initial_weight"] is not None
                else fil.get("weight"))
            if initial is None:
                raise SpoolmanError("Startvægten er ukendt – kan ikke beregne forbrug", status=400)
            if rem > initial:   # more on the spool than its nominal weight (overfilled): it started with that much
                d["nominal_weight"] = (cur or {}).get("nominal_weight") or initial   # the price stays for this weight
                d["initial_weight"], initial = rem, rem
            d["used_weight"] = max(initial - rem, 0.0)

    def create_spool(self, **fields) -> dict:
        d = self._clean(SPOOL_COLS, fields, creating=True)
        if d.get("filament_id") is None:
            raise SpoolmanError("filament_id er påkrævet", status=422)
        self.filament(d["filament_id"])   # 404 if missing
        d["archived"] = int(bool(d.get("archived", 0)))
        self._apply_weights(0, fields, d)
        d.setdefault("used_weight", 0.0)
        d["registered"] = _now()
        d["extra"] = self._merge_extra("{}", fields.get("extra"))
        cols = ", ".join(f'"{k}"' for k in d)
        cur = self._x(f"INSERT INTO spools({cols}) VALUES({','.join('?' * len(d))})", tuple(d.values()))
        return self.spool(cur.lastrowid)

    def update_spool(self, spool_id: int, **fields) -> dict:
        with self.lock:   # (read-merge-write of `extra` must not interleave with another update)
            cur = self._raw_spool(spool_id)
            d = self._clean(SPOOL_COLS, fields, creating=False)
            if d.get("filament_id") is not None:
                self.filament(d["filament_id"])
            elif "filament_id" in d:
                d.pop("filament_id")
            if "initial_weight" in d:   # a start weight typed by the user is also what the price is for
                d["nominal_weight"] = None
            self._apply_weights(int(spool_id), fields, d)
            if "extra" in fields:
                d["extra"] = self._merge_extra(cur["extra"], fields["extra"])
            self._update("spools", int(spool_id), d)
            return self.spool(spool_id)

    def delete_spool(self, spool_id: int) -> None:
        self._raw_spool(spool_id)
        with self.lock:
            self._conn.execute("DELETE FROM usage WHERE spool_id=?", (int(spool_id),))
            self._conn.execute("DELETE FROM spools WHERE id=?", (int(spool_id),))

    def archive_spool(self, spool_id: int, archived: bool = True) -> dict:
        return self.update_spool(spool_id, archived=archived)

    def set_extra(self, spool_id: int, key: str, value: str | None) -> dict:
        return self.update_spool(spool_id, extra={key: None if value is None else json.dumps(value)})

    def use_weight(self, spool_id: int, grams: float, source: str = "engine") -> dict:
        grams = round(_num(grams, "use_weight") or 0.0, 2)
        with self.lock:
            cur = self._raw_spool(spool_id)
            now = _now()
            used = max(float(cur["used_weight"] or 0) + grams, 0.0)
            self._conn.execute("UPDATE spools SET used_weight=?, last_used=?, first_used=COALESCE(first_used, ?) WHERE id=?",
                               (used, now, now, int(spool_id)))
            self._conn.execute("INSERT INTO usage(spool_id, grams, ts, source) VALUES(?,?,?,?)",
                               (int(spool_id), grams, time.time(), source))
        return self.spool(spool_id)

    def use_length(self, spool_id: int, length_mm: float, source: str = "api") -> dict:
        fil = self.spool(spool_id)["filament"]
        return self.use_weight(spool_id, weight_from_length(float(length_mm), fil["density"], fil["diameter"]), source)

    def measure(self, spool_id: int, gross_weight: float) -> dict:
        gross = _num(gross_weight, "weight")
        sp = self.spool(spool_id)
        if sp["initial_weight"] is None:
            raise SpoolmanError("Startvægten er ukendt – kan ikke beregne forbrug", status=400)
        remaining = max(gross - (sp["spool_weight"] or 0), 0.0)
        nominal = self._raw_spool(spool_id)["nominal_weight"]
        if remaining > sp["initial_weight"]:   # heavier than nominal (overfilled): it started with that much
            nominal = nominal or sp["initial_weight"]   # the price stays for the weight it was sold as
        initial = max(sp["initial_weight"], remaining)
        self._x("UPDATE spools SET used_weight=?, initial_weight=?, nominal_weight=? WHERE id=?",
                (initial - remaining, initial, nominal, int(spool_id)))
        return self.spool(spool_id)

    def usage_log(self, spool_id: int | None = None, limit: int = 200) -> list[dict]:
        if spool_id is None:
            return self._q("SELECT * FROM usage ORDER BY id DESC LIMIT ?", (limit,))
        return self._q("SELECT * FROM usage WHERE spool_id=? ORDER BY id DESC LIMIT ?", (int(spool_id), limit))

    # ---- locations ----------------------------------------------------------
    def locations(self) -> list[str]:
        return [r["location"] for r in self._q(
            "SELECT DISTINCT location FROM spools WHERE location IS NOT NULL AND TRIM(location)<>'' "
            "ORDER BY location COLLATE NOCASE")]

    def rename_location(self, old: str, new: str) -> str:
        self._x("UPDATE spools SET location=? WHERE location=?", (new, old))
        return new

    # ---- SpoolmanDB (external filaments), downloaded directly -----------------
    def _ext_path(self, name: str) -> Path | None:
        return self.data_dir / f"spoolmandb_{name}.json" if self.data_dir else None

    def _load_ext_from_disk(self) -> None:
        self._ext_disk_checked = True
        pf, pm = self._ext_path("filaments"), self._ext_path("materials")
        try:
            if pf and pf.exists():
                self._ext_filaments = json.loads(pf.read_text("utf-8"))
                self._ext_loaded_ts = pf.stat().st_mtime
            if pm and pm.exists():
                self._ext_materials = json.loads(pm.read_text("utf-8"))
        except (OSError, ValueError) as e:
            log.warning("could not read SpoolmanDB cache: %s", e)

    def refresh_external(self, force: bool = False) -> bool:
        """Download SpoolmanDB now (blocking; runs in the background thread). Keeps the old cache when offline."""
        if not self.download:
            return False
        if not force and self._ext_filaments and time.time() - self._ext_loaded_ts < EXTERNAL_MAX_AGE:
            return True
        try:
            hdr = {"User-Agent": "FilamentHub/1.0"}
            fr = requests.get(SPOOLMANDB_URL, timeout=60, headers=hdr)
            fr.raise_for_status()
            filaments = fr.json()
            mr = requests.get(SPOOLMANDB_MATERIALS_URL, timeout=30, headers=hdr)
            mr.raise_for_status()
            materials = mr.json()
            if not isinstance(filaments, list) or not filaments:
                raise ValueError("tomt svar")
            for name, data in (("filaments", filaments), ("materials", materials)):
                p = self._ext_path(name)
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), "utf-8")
                os.replace(tmp, p)
            with self._ext_lock:
                self._ext_filaments, self._ext_materials = filaments, materials if isinstance(materials, list) else []
                self._ext_loaded_ts = time.time()
                self._ext_error = ""
            log.info("SpoolmanDB: %d filaments", len(filaments))
            return True
        except (requests.RequestException, ValueError, OSError) as e:
            self._ext_error = str(e)
            log.warning("SpoolmanDB refresh failed (serving cache): %s", e)
            return False
        finally:
            self._ext_refreshing = False

    def _kick_refresh(self) -> None:
        if not self.download or self._ext_refreshing or time.time() - self._ext_last_attempt < EXTERNAL_RETRY:
            return
        self._ext_refreshing = True
        self._ext_last_attempt = time.time()
        threading.Thread(target=self.refresh_external, name="spoolmandb", daemon=True).start()

    def _ext_ready(self) -> None:
        with self._ext_lock:
            if not self._ext_disk_checked:
                self._load_ext_from_disk()
        if not self._ext_filaments or time.time() - self._ext_loaded_ts > EXTERNAL_MAX_AGE:
            self._kick_refresh()   # never blocks: the answer below is whatever the cache holds right now

    def external_filaments(self) -> list[dict]:
        self._ext_ready()
        return self._ext_filaments

    def external_materials(self) -> list[dict]:
        self._ext_ready()
        return self._ext_materials

    def external_status(self) -> dict:
        return {"filaments": len(self._ext_filaments), "updated": self._ext_loaded_ts or None, "error": self._ext_error}

    def start(self) -> None:
        """Background refresher (every 6 h; the first run only if the cache is missing or stale)."""
        def loop():
            while not self._stop.is_set():
                self._ext_ready()
                self._stop.wait(EXTERNAL_MAX_AGE / 4)
        threading.Thread(target=loop, name="spoolmandb-loop", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
