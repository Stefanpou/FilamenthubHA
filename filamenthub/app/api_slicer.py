"""Slicer settings per filament: suggestions, the saved choice, a Bambu Studio file and upload to Bambu Cloud."""
from __future__ import annotations

import json
import re
import sys

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from . import slicer
from .bambu_cloud import CloudError
from .spoolman import SpoolmanError, extra_value

router = APIRouter()
_COLOURS = {"white", "black", "grey", "gray", "red", "blue", "green", "yellow", "orange", "purple", "pink", "silver",
            "gold", "brown", "beige", "clear", "transparent", "natural", "cyan", "magenta", "violet", "ivory"}


def _main():
    """The running app module (`python -m app.main` makes it __main__; tests import it as app.main)."""
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "engine"):
        m = sys.modules.get("__main__")
    if m is None or not hasattr(m, "engine"):
        from . import main as m
    return m


def _model() -> str:
    return slicer.model_code((_main().engine.device or {}).get("model"))


def _filament(spool_id: int) -> dict:
    try:
        return (_main().spoolman.spool(spool_id) or {}).get("filament") or {}
    except SpoolmanError as e:
        raise HTTPException(404, str(e)) from e


def _line(fil: dict) -> str:
    """The product line for a preset name: the filament name without its colour ("RAPID White" -> "RAPID")."""
    name = fil.get("name") or ""
    colour = extra_value(fil, "color_name")
    if colour and name.lower().endswith(colour.lower()):
        name = name[: -len(colour)]
    words = [w for w in name.split() if w.lower() not in _COLOURS]
    material = fil.get("material") or ""
    if material.lower() not in (w.lower() for w in words):
        words.insert(0, material)
    return " ".join(words)


def _state(spool_id: int) -> dict:
    fil = _filament(spool_id)
    model = _model()
    vendor = (fil.get("vendor") or {}).get("name") or ""
    cands = slicer.match(vendor, fil.get("name"), fil.get("material"), model)
    saved = slicer.stored(extra_value(fil, slicer.SLICER_FIELD))
    if saved and slicer.by_name(saved["profile"]):
        prof = slicer.by_name(saved["profile"])
        current = {**slicer.summary(prof), "settings": {**prof["s"], **saved.get("settings", {})}, "saved": True}
    elif cands:
        current = {**slicer.summary(cands[0]), "saved": False}
    else:
        current = None
    return {"model": model, "machine": slicer.machine_name(model) if model else "", "filament_id": fil.get("id"),
            "current": current, "candidates": [slicer.summary(p) for p in cands],
            "preset_name": slicer.preset_name(vendor, _line(fil), model) if model else ""}


def _preset(spool_id: int, name: str | None, profile: str | None, settings: dict | None) -> dict:
    st = _state(spool_id)
    if not st["model"]:
        raise HTTPException(400, "Ingen printer valgt – vælg den under Indstillinger")
    cur = st["current"]
    profile = profile or (cur or {}).get("profile")
    if not profile:
        raise HTTPException(404, "Ingen slicer-profil til denne filament")
    if settings is None and cur and cur["profile"] == profile:
        settings = cur["settings"]
    try:
        return slicer.build_preset(profile, settings, st["model"], (name or st["preset_name"]).strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/slicer/match")
def match(vendor: str = "", name: str = "", material: str = ""):
    model = _model()
    return {"model": model, "candidates": [slicer.summary(p) for p in slicer.match(vendor, name, material, model)]}


@router.get("/api/spools/{spool_id}/slicer")
def spool_slicer(spool_id: int):
    return _state(spool_id)


class SlicerChoice(BaseModel):
    profile: str = Field(max_length=200)
    settings: dict[str, str] = Field(default_factory=dict)


@router.put("/api/spools/{spool_id}/slicer")
def save_choice(spool_id: int, body: SlicerChoice):
    """Save the profile (and edits) on the spool's filament - every spool of it shares the settings."""
    prof = slicer.by_name(body.profile)
    if not prof:
        raise HTTPException(400, "Ukendt slicer-profil")
    edits = {k: str(v).replace(",", ".") for k, v in body.settings.items() if k in slicer.OVERRIDE and str(v).strip()}
    fil = _filament(spool_id)
    value = json.dumps({"profile": prof["n"], "settings": {**prof["s"], **edits}})
    try:
        _main().spoolman.update_filament(fil["id"], extra={slicer.SLICER_FIELD: json.dumps(value)})
    except SpoolmanError as e:
        raise HTTPException(502, str(e)) from e
    return _state(spool_id)


def _filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._@-]+", "", name).strip() or "filament"


@router.get("/api/spools/{spool_id}/slicer/preset.json")
def preset_file(spool_id: int, name: str | None = Query(default=None, max_length=200)):
    pr = _preset(spool_id, name, None, None)
    data = json.dumps(slicer.studio_file(pr), ensure_ascii=False, indent=4)
    return Response(data, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(pr["name"])}.json"'})


class CloudUpload(BaseModel):
    name: str | None = Field(default=None, max_length=200)


@router.post("/api/spools/{spool_id}/slicer/cloud")
def upload(spool_id: int, body: CloudUpload):
    """Create the preset in the Bambu Cloud account (Bambu Studio fetches it at its next sync)."""
    cloud = _cloud()
    return _send(cloud, _preset(spool_id, body.name, None, None))


# ---- the profile database: search every profile, then a file or Bambu Cloud without a spool ---------------------
def _cloud():
    cloud = _main().engine.cloud
    if not cloud:
        raise HTTPException(400, "Log ind på Bambu Cloud under Indstillinger først")
    return cloud


def _facets(pool: list[dict], key: str) -> list[dict]:
    counts: dict[str, int] = {}
    for p in pool:
        if p[key]:
            counts[p[key]] = counts.get(p[key], 0) + 1
    return [{"name": k, "count": n} for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


@router.get("/api/slicer/profiles")
def browse(q: str = Query(default="", max_length=120), vendor: str = Query(default="", max_length=80),
           material: str = Query(default="", max_length=40), all_printers: bool = False,
           limit: int = Query(default=60, ge=1, le=300), offset: int = Query(default=0, ge=0)):
    model = _model()
    hits, pool = slicer.search(model, q, vendor, material, all_printers)
    items = [{**slicer.summary(p), "base": p["b"], "builtin": slicer.builtin(p, model),
              "preset_name": slicer.default_name(p, model) if model else ""} for p in hits[offset:offset + limit]]
    return {"model": model, "machine": slicer.machine_name(model) if model else "", "total": len(hits),
            "items": items, "vendors": _facets(pool, "v"), "materials": _facets(pool, "t")}


class ProfilePreset(BaseModel):
    profile: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=200)
    settings: dict[str, str] = Field(default_factory=dict)


def _db_preset(body: ProfilePreset) -> dict:
    model = _model()
    if not model:
        raise HTTPException(400, "Ingen printer valgt – vælg den under Indstillinger")
    edits = {k: str(v).replace(",", ".").strip() for k, v in body.settings.items() if k in slicer.OVERRIDE and str(v).strip()}
    try:
        return slicer.build_preset(body.profile, edits, model, body.name.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/slicer/profiles/preset.json")
def profile_file(body: ProfilePreset):
    pr = _db_preset(body)
    data = json.dumps(slicer.studio_file(pr), ensure_ascii=False, indent=4)
    return Response(data, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(pr["name"])}.json"'})


def _send(cloud, pr: dict) -> dict:
    try:
        existing = cloud.slicer_presets("filament")
        if any((p.get("name") or "").lower() == pr["name"].lower() for p in existing):
            raise HTTPException(409, f"Der findes allerede en profil med navnet »{pr['name']}« i Bambu Cloud")
        versions = sorted((p.get("version") or "" for p in existing), key=lambda v: [int(x) for x in v.split(".") if x.isdigit()])
        res = cloud.create_slicer_preset(slicer.cloud_body(pr, versions[-1] if versions else slicer.PRESET_VERSION))
    except CloudError as e:
        raise HTTPException(502, str(e)) from e
    if not res.get("setting_id"):
        raise HTTPException(502, f"Bambu svarede uden profil-id: {res.get('message') or res}")
    return {"setting_id": res["setting_id"], "name": pr["name"], "inherits": pr["inherits"], "values": pr["values"]}


@router.post("/api/slicer/profiles/cloud")
def profile_to_cloud(body: ProfilePreset):
    cloud = _cloud()
    return _send(cloud, _db_preset(body))


@router.get("/api/slicer/cloud")
def cloud_presets():
    """The filament presets in the Bambu Cloud account (what Bambu Studio shows under "User presets")."""
    try:
        items = _cloud().slicer_presets("filament")
    except CloudError as e:
        raise HTTPException(502, str(e)) from e
    keep = ("setting_id", "name", "base_id", "version", "update_time", "updated_time", "filament_id")
    return {"items": sorted(({k: p.get(k) for k in keep if p.get(k) is not None} for p in items),
                            key=lambda p: (p.get("name") or "").lower())}


@router.delete("/api/slicer/cloud/{setting_id}")
def cloud_delete(setting_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", setting_id):
        raise HTTPException(400, "Ugyldigt profil-id")
    try:
        _cloud().delete_slicer_preset(setting_id)
    except CloudError as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True}
