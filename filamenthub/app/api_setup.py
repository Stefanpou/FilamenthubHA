"""First-run wizard state: what is already set up and whether the wizard should be shown."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

import requests
from fastapi import APIRouter, HTTPException, Request

from . import config, power, restore

log = logging.getLogger("spoolsync.setup")

router = APIRouter()


def _main():
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "engine"):
        from . import main as m
    return m


def _ha_ok(db) -> bool:
    cfg = power.settings(db)
    if not (cfg["url"] and cfg["token"]):
        return False
    try:
        r = requests.get(cfg["url"].rstrip("/") + "/api/", headers={"Authorization": f"Bearer {cfg['token']}"}, timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


@router.get("/api/setup/state")
def setup_state():
    m = _main()
    db, eng = m.db, m.engine
    bambu, device = bool(eng.auth), bool(eng.device)
    if not db.get("setup_done") and bambu and device:
        db.set("setup_done", time.time())   # an install that already works never sees the wizard
    needed = not db.get("setup_done") and not (bambu and device)
    return {"needed": needed, "addon": config.ADDON,
            "steps": {"bambu": bambu, "device": device, "ha": _ha_ok(db)}}


@router.post("/api/setup/done")
def setup_done():
    _main().db.set("setup_done", time.time())
    return {"ok": True}


# ------------------------------------------------------------------------------------------- move an install
# Add-on only, and only on a fresh install (no Bambu login yet): the body is a .tar.gz of another install's data
# directory. It is stored as /data/import.tar.gz and the process exits; the add-on's run script then calls
# app.restore.apply() (which swaps the data in) before the app opens its database again.
IMPORT_MAX = 500 * 1024 * 1024


@router.post("/api/setup/import")
async def setup_import(request: Request):
    m = _main()
    if not config.ADDON:
        raise HTTPException(404, "Kun i Home Assistant-tilføjelsen")
    if m.engine.auth or m.db.get("setup_done"):
        raise HTTPException(409, "Kan kun bruges på en ny installation (før opsætningen)")
    part = config.DATA_DIR / (restore.IMPORT_NAME + ".part")
    size = 0
    with open(part, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > IMPORT_MAX:
                f.close(); part.unlink(missing_ok=True)
                raise HTTPException(413, "Filen er for stor")
            f.write(chunk)
    try:
        restore.check(part)
    except ValueError as e:
        part.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    part.rename(config.DATA_DIR / restore.IMPORT_NAME)
    log.warning("Data import received (%d bytes); restarting to apply it", size)
    threading.Timer(1.0, lambda: os._exit(0)).start()   # the add-on's s6 service starts us again
    return {"ok": True, "bytes": size, "restarting": True}
