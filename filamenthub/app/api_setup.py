"""First-run wizard state: what is already set up and whether the wizard should be shown."""
from __future__ import annotations

import sys
import time

import requests
from fastapi import APIRouter

from . import config, power

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
