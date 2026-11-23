"""SQLite: one row per model file + a small key/value table for settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  relpath TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  mtime REAL NOT NULL DEFAULT 0,
  added REAL NOT NULL,
  parsed INTEGER NOT NULL DEFAULT 0,
  meta TEXT NOT NULL DEFAULT '{}',
  tags TEXT NOT NULL DEFAULT '[]',
  notes TEXT NOT NULL DEFAULT '',
  designer TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  favorite INTEGER NOT NULL DEFAULT 0,
  links TEXT NOT NULL DEFAULT '[]',
  unlinks TEXT NOT NULL DEFAULT '[]',
  thumb TEXT NOT NULL DEFAULT '',
  manual TEXT NOT NULL DEFAULT '{}',
  grp TEXT NOT NULL DEFAULT '',
  sha TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prints (
  model_id INTEGER NOT NULL,
  task_id TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  end_ts REAL,
  photo INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (model_id, task_id)
);
"""
JSON_COLS = ("meta", "tags", "links", "unlinks", "manual")
EDITABLE = ("name", "tags", "notes", "designer", "source_url", "favorite", "links", "unlinks", "manual", "grp")


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(models)")}
        if "grp" not in cols:            # 0.1/0.2 databases
            self.conn.execute("ALTER TABLE models ADD COLUMN grp TEXT NOT NULL DEFAULT ''")
        if "sha" not in cols:            # before 0.5
            self.conn.execute("ALTER TABLE models ADD COLUMN sha TEXT NOT NULL DEFAULT ''")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.commit()

    # ---- key/value
    def get(self, k: str, default=None):
        with self.lock:
            r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, k: str, v) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                              (k, json.dumps(v)))
            self.conn.commit()

    # ---- models
    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        for c in JSON_COLS:
            d[c] = json.loads(d[c] or ("{}" if c in ("meta", "manual") else "[]"))
        d["favorite"] = bool(d["favorite"])
        return d

    def models(self) -> list[dict]:
        with self.lock:
            return [self._row(r) for r in self.conn.execute("SELECT * FROM models ORDER BY added DESC, id DESC")]

    def model(self, mid: int) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
        return self._row(r) if r else None

    def by_path(self, relpath: str) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT * FROM models WHERE relpath=?", (relpath,)).fetchone()
        return self._row(r) if r else None

    def by_sha(self, sha: str) -> dict | None:
        if not sha:
            return None
        with self.lock:
            r = self.conn.execute("SELECT * FROM models WHERE sha=? ORDER BY id LIMIT 1", (sha,)).fetchone()
        return self._row(r) if r else None

    def insert(self, relpath: str, name: str, size: int, mtime: float, meta: dict, thumb: str) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO models(relpath, name, size, mtime, added, parsed, meta, thumb, designer) "
                "VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (relpath, name, size, mtime, time.time(), json.dumps(meta), thumb, meta.get("designer") or ""))
            self.conn.commit()
            return int(cur.lastrowid)

    def update_file(self, mid: int, size: int, mtime: float, meta: dict, thumb: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE models SET size=?, mtime=?, meta=?, thumb=?, parsed=1 WHERE id=?",
                              (size, mtime, json.dumps(meta), thumb, mid))
            self.conn.commit()

    def set_fields(self, mid: int, fields: dict) -> None:
        cols, vals = [], []
        for k, v in fields.items():
            if k not in EDITABLE and k not in ("relpath", "thumb", "sha"):
                raise KeyError(k)
            cols.append(f"{k}=?")
            vals.append(json.dumps(v) if k in JSON_COLS else int(v) if k == "favorite" else v)
        if not cols:
            return
        with self.lock:
            self.conn.execute(f"UPDATE models SET {', '.join(cols)} WHERE id=?", (*vals, mid))
            self.conn.commit()

    def delete(self, mid: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM models WHERE id=?", (mid,))
            self.conn.execute("DELETE FROM prints WHERE model_id=?", (mid,))
            self.conn.commit()

    # ---- prints of a model (kept even when FilamentHub forgets them)
    def prints(self, mid: int) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT task_id, data, end_ts, photo FROM prints WHERE model_id=? "
                                     "ORDER BY COALESCE(end_ts, 0) DESC, task_id DESC", (mid,)).fetchall()
        return [{**json.loads(r["data"]), "task_id": r["task_id"], "end": r["end_ts"], "photo": bool(r["photo"])} for r in rows]

    def print_counts(self) -> dict[int, dict]:
        """{model id: {"n": prints, "photo": newest task id with a photo}}"""
        out: dict[int, dict] = {}
        with self.lock:
            for r in self.conn.execute("SELECT model_id, task_id, photo FROM prints ORDER BY COALESCE(end_ts, 0) DESC"):
                d = out.setdefault(r["model_id"], {"n": 0, "photo": None})
                d["n"] += 1
                if r["photo"] and not d["photo"]:
                    d["photo"] = r["task_id"]
        return out

    def upsert_print(self, mid: int, row: dict, photo: bool | None = None) -> None:
        data = json.dumps({k: v for k, v in row.items() if k not in ("task_id", "end", "photo")})
        with self.lock:
            self.conn.execute(
                "INSERT INTO prints(model_id, task_id, data, end_ts, photo) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(model_id, task_id) DO UPDATE SET data=excluded.data, end_ts=excluded.end_ts"
                + (", photo=excluded.photo" if photo is not None else ""),
                (mid, row["task_id"], data, row.get("end"), int(bool(photo))))
            self.conn.commit()

    def delete_print(self, mid: int, task_id: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM prints WHERE model_id=? AND task_id=?", (mid, task_id))
            self.conn.commit()
