"""SQLite persistence. All access goes through one connection guarded by a lock."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

# Only explicit decisions can outrank a binding made in Bambu Studio/Handy: the user assigning/removing a spool in
# Spool Sync, or an RFID read. Everything automatic (re-insert guesses, empty-slot removals, printer-setting
# choices, earlier Bambu imports) is weak.
STRONG_SOURCES = {"manuel", "rfid"}
STRONG_END_REASONS = {"manuelt"}

SCHEMA = """
-- Electricity per print, from Home Assistant (app/power.py).
CREATE TABLE IF NOT EXISTS print_energy (
    task_id    TEXT PRIMARY KEY,
    name       TEXT,
    kwh        REAL NOT NULL DEFAULT 0,
    cost       REAL NOT NULL DEFAULT 0,
    priced_kwh REAL NOT NULL DEFAULT 0,   -- the part of kwh that had a price (cost covers only that)
    first_ts   REAL NOT NULL,
    last_ts    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- History of which spool sat in which slot. end_ts NULL = current.
CREATE TABLE IF NOT EXISTS assignments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    slot     TEXT NOT NULL,
    spool_id INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    end_ts   REAL,
    source   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assign_slot ON assignments(slot, start_ts);
-- Live progress seen over MQTT per cloud task id.
CREATE TABLE IF NOT EXISTS task_progress (
    task_id     TEXT PRIMARY KEY,
    max_percent INTEGER NOT NULL DEFAULT 0,
    last_state  TEXT,
    name        TEXT,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_tasks (
    task_id       TEXT PRIMARY KEY,
    processed_ts  REAL NOT NULL,
    status        INTEGER,
    title         TEXT,
    start_time    TEXT,
    end_time      TEXT,
    factor        REAL NOT NULL,
    factor_source TEXT NOT NULL,
    total_weight  REAL
);
-- One row per (task, slot). state: applying | applied | pending | skipped | uncertain
CREATE TABLE IF NOT EXISTS deductions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    slot       TEXT NOT NULL,
    spool_id   INTEGER,
    grams      REAL NOT NULL,
    full_grams REAL NOT NULL,
    material   TEXT,
    color      TEXT,
    ts         REAL NOT NULL,
    state      TEXT NOT NULL,
    note       TEXT,
    UNIQUE(task_id, slot)
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);
-- Product barcodes (EAN/UPC) the user has scanned before -> Spoolman filament.
CREATE TABLE IF NOT EXISTS barcodes (
    code        TEXT PRIMARY KEY,
    filament_id INTEGER NOT NULL,
    created_ts  REAL NOT NULL
);
-- Cached results of online barcode lookups (so a code is only looked up once).
CREATE TABLE IF NOT EXISTS barcode_cache (
    code       TEXT PRIMARY KEY,
    result     TEXT NOT NULL,
    fetched_ts REAL NOT NULL
);
-- Bambu Filament Manager <-> Spoolman links.
CREATE TABLE IF NOT EXISTS fm_links (
    spool_id  INTEGER PRIMARY KEY,
    fm_id     INTEGER NOT NULL UNIQUE,
    linked_ts REAL NOT NULL,
    source    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fm_ignored (fm_id INTEGER PRIMARY KEY);
-- FM spools Spool Sync created (so they can be removed again).
CREATE TABLE IF NOT EXISTS fm_created (fm_id INTEGER PRIMARY KEY, spool_id INTEGER, ts REAL);
-- Last spool that left a slot (for automatic re-assignment on re-insert).
CREATE TABLE IF NOT EXISTS slot_last (
    slot       TEXT PRIMARY KEY,
    spool_id   INTEGER NOT NULL,
    emptied_ts REAL NOT NULL,
    material   TEXT,
    color      TEXT
);
"""


class Database:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the first release (idempotent)."""
        wanted = {"task_progress": {"print_type": "TEXT", "slots": "TEXT", "slot_history": "TEXT", "running_since": "REAL",
                                    "state_log": "TEXT", "feed_log": "TEXT", "runout_events": "TEXT"},
                  "assignments": {"end_reason": "TEXT", "decided_ts": "REAL", "spool_registered": "TEXT"},
                  "deductions": {"spool_registered": "TEXT"}, "barcodes": {"filament_registered": "TEXT"},
                  "fm_links": {"spool_registered": "TEXT"}}
        for table, cols in wanted.items():
            have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for col, typ in cols.items():
                if col not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    # ---- low level -------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- key/value -------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def delete(self, key: str) -> None:
        self.execute("DELETE FROM kv WHERE key=?", (key,))

    # ---- events ----------------------------------------------------------
    def event(self, level: str, message: str) -> None:
        self.execute("INSERT INTO events(ts, level, message) VALUES(?, ?, ?)", (time.time(), level, message))
        # Keep the log bounded.
        self.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - 500")

    # ---- assignments -----------------------------------------------------
    def current_assignment(self, slot: str) -> dict | None:
        return self.query_one(
            "SELECT * FROM assignments WHERE slot=? AND end_ts IS NULL ORDER BY start_ts DESC LIMIT 1", (slot,)
        )

    def current_assignments(self) -> dict[str, dict]:
        rows = self.query("SELECT * FROM assignments WHERE end_ts IS NULL ORDER BY start_ts")
        return {r["slot"]: r for r in rows}

    def assign(self, slot: str, spool_id: int, source: str, ts: float | None = None, end_reason: str = "erstattet",
               decided_ts: float | None = None, moved_reason: str | None = None) -> None:
        """Put spool in slot. Ends whatever was in the slot (end_reason) and removes the spool from any other slot
        (moved_reason, default the same).

        ts is when the spool went in (it may lie in the past for a correction); decided_ts is when that was decided,
        if later (how the decision ranks against bindings made in Bambu Studio/Handy)."""
        ts = ts or time.time()
        with self.lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE assignments SET end_ts=?, end_reason=? WHERE end_ts IS NULL AND slot=?", (ts, end_reason, slot))
                self._conn.execute(
                    "UPDATE assignments SET end_ts=?, end_reason=? WHERE end_ts IS NULL AND spool_id=?",
                    (ts, moved_reason or end_reason, spool_id),
                )
                self._conn.execute(
                    "INSERT INTO assignments(slot, spool_id, start_ts, end_ts, source, decided_ts) VALUES(?, ?, ?, NULL, ?, ?)",
                    (slot, spool_id, ts, source, decided_ts),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def unassign(self, slot: str, ts: float | None = None, reason: str | None = None) -> dict | None:
        ts = ts or time.time()
        with self.lock:
            cur = self.current_assignment(slot)
            if cur:
                self._conn.execute("UPDATE assignments SET end_ts=?, end_reason=? WHERE id=?", (ts, reason, cur["id"]))
            return cur

    def latest_strong_decision(self, slot: str, spool_id: int | None = None) -> float:
        """Newest explicit decision (the user, or an RFID read) about this slot, or putting this spool elsewhere.

        Automatic reactions (re-insert guesses, empty-slot removals, printer-setting choices, Bambu imports) are
        weak and never outrank a binding the user made in Bambu Studio/Handy."""
        latest = 0.0
        for r in self.query("SELECT * FROM assignments WHERE slot=?", (slot,)):
            if r["source"] in STRONG_SOURCES:
                latest = max(latest, r["start_ts"], r.get("decided_ts") or 0)
            if r["end_ts"] and (r.get("end_reason") or "") in STRONG_END_REASONS:
                latest = max(latest, r["end_ts"])
        if spool_id is not None:
            for r in self.query("SELECT * FROM assignments WHERE spool_id=? AND slot<>?", (spool_id, slot)):
                if r["source"] in STRONG_SOURCES:
                    # the user put this spool somewhere else later
                    latest = max(latest, r["start_ts"], r.get("decided_ts") or 0)
        return latest

    def assignment_for_window(self, slot: str, start: float, end: float) -> dict | None:
        """The assignment that covered the largest part of [start, end] for the slot."""
        rows = self.query(
            "SELECT * FROM assignments WHERE slot=? AND start_ts <= ? AND (end_ts IS NULL OR end_ts >= ?)",
            (slot, end, start),
        )
        if end < start:
            start, end = end, start
        best, best_overlap = None, float("-inf")
        for r in rows:
            r_end = r["end_ts"] if r["end_ts"] is not None else float("inf")
            overlap = min(end, r_end) - max(start, r["start_ts"])
            # Prefer the later assignment on ties (e.g. zero-length windows).
            if overlap > best_overlap or (overlap == best_overlap and best and r["start_ts"] > best["start_ts"]):
                best, best_overlap = r, overlap
        return best
