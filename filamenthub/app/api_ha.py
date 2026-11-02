"""Home Assistant endpoints: kiosk token + settings (session only) and a compact summary (token or session)."""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import homeassistant as ha

router = APIRouter()
publisher: ha.Publisher | None = None


def _main():
    """The running app module (`python -m app.main` makes it __main__; tests import it as app.main)."""
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "engine"):
        m = sys.modules.get("__main__")
    if m is None or not hasattr(m, "engine"):
        from . import main as m
    return m


def snapshot() -> dict:
    m = _main()
    return ha.build_snapshot(m.engine, m.spoolman, m.db)


class MqttIn(BaseModel):
    enabled: bool | None = None
    host: str | None = Field(default=None, max_length=200)
    port: int | None = Field(default=None, ge=1, le=65535)
    user: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=500)   # None = keep, "" = clear
    prefix: str | None = None


class SettingsIn(BaseModel):
    ha_url: str | None = Field(default=None, max_length=300)
    base_url: str | None = Field(default=None, max_length=300)   # how HA reaches FilamentHub (cover picture link)
    low_stock_g: int | None = Field(default=None, ge=0, le=5000)
    mqtt: MqttIn | None = None
    rotate_token: bool = False


def _public(db) -> dict:
    s = ha.load_settings(db)
    m = dict(s["mqtt"])
    m["password_set"] = bool(m.pop("password", ""))
    m["status"] = publisher.status if publisher else "disabled"
    m["detail"] = publisher.detail if publisher else ""
    tok = ha.kiosk_token(db)
    return {"ha_url": s["ha_url"], "base_url": s["base_url"], "low_stock_g": s["low_stock_g"], "mqtt": m,
            "origins": ha.allowed_origins(s), "kiosk_token": tok, "kiosk_path": f"/ha?token={tok}"}


@router.get("/api/ha/settings")
def get_settings():
    return _public(_main().db)


@router.post("/api/ha/settings")
def post_settings(body: SettingsIn):
    db = _main().db
    s = ha.load_settings(db)
    if body.ha_url is not None:
        if body.ha_url.strip() and not ha.norm_origin(body.ha_url):
            raise HTTPException(400, "HA-adressen skal være som http://homeassistant.local:8123")
        s["ha_url"] = ha.norm_origin(body.ha_url) or ""
    if body.base_url is not None:
        if body.base_url.strip() and not ha.norm_origin(body.base_url):
            raise HTTPException(400, "Adressen skal være som http://192.168.1.50:8080")
        s["base_url"] = ha.norm_origin(body.base_url) or ""
    if body.low_stock_g is not None:
        s["low_stock_g"] = body.low_stock_g
    if body.mqtt is not None:
        m = s["mqtt"]
        for k in ("enabled", "host", "port", "user"):
            v = getattr(body.mqtt, k)
            if v is not None:
                m[k] = v.strip() if isinstance(v, str) else v
        if body.mqtt.password is not None:
            m["password"] = body.mqtt.password
        if body.mqtt.prefix is not None:
            if not ha.PREFIX_RE.match(body.mqtt.prefix):
                raise HTTPException(400, "Ugyldigt discovery-præfiks")
            m["prefix"] = body.mqtt.prefix
    ha.save_settings(db, s)
    if body.rotate_token:
        ha.rotate_token(db)
    return _public(db)


@router.get("/api/ha/summary")
def summary():
    """Compact, flat JSON for Home Assistant `rest` sensors (no MQTT needed)."""
    snap = snapshot()
    p, inv = snap["printer"], snap["inventory"]
    return {
        **p,
        "slots": {s["id"]: {"label": s["label"], "material": s["material"], "color": s["color"],
                            "remaining_g": s["remaining_g"], "active": s["active"]} for s in snap["slots"]},
        "spool_count": inv["spool_count"], "total_g": inv["total_g"], "by_material": inv["by_material"],
        "low_stock": inv["low_stock_count"] > 0, "low_stock_count": inv["low_stock_count"],
        "low_stock_spools": inv["low_stock"],
    }


def attach(app) -> None:
    """Start the MQTT publisher with the app (wraps the lifespan; it idles unless enabled in the settings)."""
    global publisher
    m = _main()
    publisher = ha.Publisher(m.db, snapshot, base_url=lambda: ha.load_settings(m.db)["base_url"],
                             cover_token=lambda: ha.cover_token(m.db))
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with inner(a) as state:
            publisher.start()
            try:
                yield state
            finally:
                publisher.stop()

    app.router.lifespan_context = lifespan
