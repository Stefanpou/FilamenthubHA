"""The model files on disk (MODELS_DIR, normally /share/modelbank) and their rows + cached thumbnail/mesh."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import config, parse, prints
from .db import Database

log = logging.getLogger("modelbank.library")
_scan_lock = threading.Lock()


class LibraryError(ValueError):
    pass


def root() -> Path:
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return config.MODELS_DIR


def cache(kind: str) -> Path:
    p = config.DATA_DIR / "cache" / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


def thumb_path(mid: int) -> Path:
    return cache("thumb") / f"{mid}.png"


def mesh_path(mid: int) -> Path:
    return cache("mesh") / f"{mid}.fhm"


def clean_folder(folder: str) -> str:
    """'a/b' style folder below the root; no '..', no hidden parts, no absolute paths."""
    parts = [p.strip() for p in re.split(r"[\\/]+", folder or "") if p.strip()]
    for p in parts:
        if p in (".", "..") or p.startswith(".") or len(p) > 120 or re.search(r'[\x00-\x1f<>:"|?*]', p):
            raise LibraryError(f"Ugyldigt mappenavn: {p}")
    return "/".join(parts)


def clean_name(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", base)
    if not base or base.startswith(".") or not base.lower().endswith(config.EXTS):
        raise LibraryError("Kun .3mf- og .stl-filer")
    return base[:200]


def full(relpath: str) -> Path:
    p = (root() / relpath).resolve()
    if root().resolve() not in p.parents:
        raise LibraryError("Ugyldig sti")
    return p


def _rel(p: Path) -> str:
    return p.relative_to(root()).as_posix()


def _visible(rel: str) -> bool:
    return not any(part.startswith(".") for part in rel.split("/"))


def folders() -> list[str]:
    out = []
    for dirpath, dirnames, _ in os.walk(root()):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel = Path(dirpath).relative_to(root()).as_posix()
        if rel != ".":
            out.append(rel)
    return out


def create_folder(folder: str) -> str:
    f = clean_folder(folder)
    if not f:
        raise LibraryError("Skriv et mappenavn")
    full(f).mkdir(parents=True, exist_ok=True)
    return f


def _store_parsed(db: Database, mid: int, parsed: parse.Parsed, keep_client_thumb: bool) -> str:
    """Writes thumbnail + mesh to the cache, returns the thumb kind ('file', 'client' or '')."""
    tp, mp = thumb_path(mid), mesh_path(mid)
    if parsed.mesh:
        mp.write_bytes(parsed.mesh)
    else:
        mp.unlink(missing_ok=True)
    if parsed.thumb:
        tp.write_bytes(parsed.thumb)
        return "file"
    if keep_client_thumb and tp.exists():
        return "client"
    tp.unlink(missing_ok=True)
    return ""


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest(db: Database, path: Path) -> int:
    rel = _rel(path)
    st = path.stat()
    parsed = parse.parse_file(path)
    row = db.by_path(rel)
    if row:
        thumb = _store_parsed(db, row["id"], parsed, keep_client_thumb=False)
        db.update_file(row["id"], st.st_size, st.st_mtime, parsed.meta, thumb)
        db.set_fields(row["id"], {"sha": file_sha(path)})
        return row["id"]
    name = parsed.meta.get("title") or path.stem
    mid = db.insert(rel, name, st.st_size, st.st_mtime, parsed.meta, "")
    thumb = _store_parsed(db, mid, parsed, keep_client_thumb=False)
    db.set_fields(mid, {"thumb": thumb, "sha": file_sha(path)})
    return mid


def scan(db: Database) -> dict:
    """Brings the rows in line with the files: new files in, changed ones re-read, missing ones out."""
    if not _scan_lock.acquire(blocking=False):
        return {"busy": True}
    try:
        seen, added, updated = set(), 0, 0
        for dirpath, dirnames, filenames in os.walk(root()):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.startswith(".") or not fn.lower().endswith(config.EXTS):
                    continue
                p = Path(dirpath) / fn
                rel = _rel(p)
                seen.add(rel)
                try:
                    st = p.stat()
                    row = db.by_path(rel)
                    if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1e-3 \
                            and (row["meta"] or {}).get("v") == parse.PARSE_VERSION:
                        if not row.get("sha"):
                            db.set_fields(row["id"], {"sha": file_sha(p)})
                        continue
                    ingest(db, p)
                    added += row is None
                    updated += row is not None
                except OSError as e:
                    log.warning("scan %s: %s", rel, e)
        removed = 0
        for m in db.models():
            if m["relpath"] not in seen:
                _drop_cache(m["id"])
                db.delete(m["id"])
                removed += 1
        db.set("last_scan", time.time())
        return {"added": added, "updated": updated, "removed": removed, "total": len(seen)}
    finally:
        _scan_lock.release()


def _drop_cache(mid: int) -> None:
    thumb_path(mid).unlink(missing_ok=True)
    mesh_path(mid).unlink(missing_ok=True)
    prints.drop_photos(mid)


def unique_target(folder: str, name: str) -> Path:
    d = full(folder) if folder else root()
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    stem, ext = os.path.splitext(name)
    i = 2
    while p.exists():
        p = d / f"{stem} ({i}){ext}"
        i += 1
    return p


def begin_upload(folder: str, filename: str) -> tuple[Path, Path]:
    """(hidden temp file, final path). The caller writes the temp file, then calls finish_upload."""
    folder = clean_folder(folder)
    target = unique_target(folder, clean_name(filename))
    return target.with_name(f".upload-{os.getpid()}-{time.time_ns()}"), target


def duplicate_of(db: Database, sha: str) -> dict | None:
    """The model that already has exactly this content (and whose file is still there)."""
    m = db.by_sha(sha)
    if m and full(m["relpath"]).is_file():
        return m
    return None


def finish_upload(db: Database, tmp: Path, target: Path) -> int:
    try:
        if tmp.stat().st_size == 0:
            raise LibraryError("Filen er tom")
        if target.exists():                      # someone else took the name meanwhile
            target = unique_target(_rel(target.parent) if target.parent != root() else "", target.name)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return ingest(db, target)


def move(db: Database, mid: int, folder: str) -> dict:
    m = db.model(mid)
    if not m:
        raise LibraryError("Findes ikke")
    folder = clean_folder(folder)
    src = full(m["relpath"])
    if (src.parent.relative_to(root()).as_posix() if src.parent != root() else "") == folder:
        return m
    dst = unique_target(folder, src.name)
    shutil.move(str(src), str(dst))
    st = dst.stat()
    db.set_fields(mid, {"relpath": _rel(dst)})
    db.update_file(mid, st.st_size, st.st_mtime, m["meta"], m["thumb"])
    return db.model(mid)


def trash(db: Database, mid: int) -> None:
    """Moves the file to MODELS_DIR/.slettet/<same path> (empty it from Samba when you are sure)."""
    m = db.model(mid)
    if not m:
        raise LibraryError("Findes ikke")
    src = full(m["relpath"])
    if src.exists():
        dst = root() / config.TRASH / m["relpath"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst = dst.with_name(f"{dst.stem}-{int(time.time())}{dst.suffix}")
        shutil.move(str(src), str(dst))
    _drop_cache(mid)
    db.delete(mid)


def set_client_thumb(db: Database, mid: int, png: bytes) -> None:
    m = db.model(mid)
    if not m:
        raise LibraryError("Findes ikke")
    if m["thumb"] == "file":
        return                       # the file's own picture wins
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > parse.MAX_THUMB:
        raise LibraryError("Ugyldigt billede")
    thumb_path(mid).write_bytes(png)
    db.set_fields(mid, {"thumb": "client"})


class Scanner:
    """Re-scans every SCAN_MINUTES so files copied in over Samba show up by themselves."""

    def __init__(self, db: Database):
        self.db = db
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="scanner", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                r = scan(self.db)
                if r.get("added") or r.get("removed") or r.get("updated"):
                    log.info("scan: %s", r)
            except Exception:
                log.exception("scan failed")
            self._stop.wait(max(1.0, config.SCAN_MINUTES * 60))
