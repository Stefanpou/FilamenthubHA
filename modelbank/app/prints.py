"""Keeps each model's prints and their finish photos from FilamentHub's print library.

A print belongs to a model when its name fits the model (see filamenthub.auto_matches) or when it was linked by hand.
Once found, the link is stored on the model, and the print (facts + photo) is kept in Modelbank even if FilamentHub
later deletes it. A file added after it was printed is linked to those earlier prints by the same matching."""
from __future__ import annotations

import logging
import re
import threading

from . import config, filamenthub as fhm
from .db import Database

log = logging.getLogger("modelbank.prints")
_lock = threading.Lock()
SYNC_SECONDS = 120


def photo_path(mid: int, task_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,100}", task_id):
        raise ValueError("ugyldigt print-id")
    d = config.DATA_DIR / "photos" / str(mid)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{task_id}.jpg"


def drop_photos(mid: int) -> None:
    d = config.DATA_DIR / "photos" / str(mid)
    if d.is_dir():
        for p in d.iterdir():
            p.unlink(missing_ok=True)
        d.rmdir()


def _fetch_photo(client: fhm.Client, mid: int, tid: str) -> bool:
    try:
        r = client.raw(f"/api/library/{tid}/photo.jpg")
    except fhm.FHError as e:
        log.info("photo %s: %s", tid, e)
        return False
    if not r.content[:3] == b"\xff\xd8\xff" and not r.content[:8] == b"\x89PNG\r\n\x1a\n":
        return False
    photo_path(mid, tid).write_bytes(r.content)
    return True


def sync(db: Database, client: fhm.Client, only: int | None = None, entries: list[dict] | None = None) -> dict:
    """Brings stored prints + photos up to date. Returns counts."""
    if not client.configured():
        return {"skipped": "not configured"}
    with _lock:
        if entries is None:
            entries = client.library()
        by_id = {str(e.get("task_id")): e for e in entries}
        models = db.models()
        auto = fhm.auto_matches(models, entries)
        linked = photos = 0
        for m in models:
            if only is not None and m["id"] != only:
                continue
            links, unlinks = set(m["links"]), set(m["unlinks"])
            new = auto.get(m["id"], set()) - links - unlinks
            if new:
                links |= new
                db.set_fields(m["id"], {"links": sorted(links)})
                linked += len(new)
            have = {p["task_id"]: p for p in db.prints(m["id"])}
            for tid in unlinks & set(have):
                db.delete_print(m["id"], tid)
                photo_path(m["id"], tid).unlink(missing_ok=True)
            for tid in links - unlinks:
                e = by_id.get(tid)
                if not e:
                    continue          # gone from FilamentHub: keep what we stored
                got = have.get(tid, {}).get("photo", False)
                if not got and e.get("has_photo"):
                    got = _fetch_photo(client, m["id"], tid)
                    photos += got
                db.upsert_print(m["id"], fhm.print_row(e), photo=got)
        return {"linked": linked, "photos": photos}


class PrintSync:
    def __init__(self, db: Database, client: fhm.Client):
        self.db, self.client = db, client
        self._stop = threading.Event()
        self._kick = threading.Event()
        self.error: str | None = None

    def start(self) -> None:
        threading.Thread(target=self._loop, name="printsync", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()

    def kick(self) -> None:
        self._kick.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.client.clear()
                r = sync(self.db, self.client)
                self.error = None
                if r.get("linked") or r.get("photos"):
                    log.info("prints: %s", r)
            except fhm.FHError as e:
                self.error = str(e)
                log.info("prints: %s", e)
            except Exception:
                log.exception("print sync failed")
            self._kick.wait(SYNC_SECONDS)
            self._kick.clear()
