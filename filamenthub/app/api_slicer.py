"""Slicer settings per filament: suggestions, the saved choice, a Bambu Studio file and upload to Bambu Cloud."""
from __future__ import annotations

import json
import re
import sys
import time

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


def _process_state(saved: dict | None, model: str) -> dict | None:
    """The filament's process values over Bambu's process preset: {base, settings, defaults, choices}."""
    names = [p["n"] for p in slicer.processes_for(model)]
    if not names:
        return None
    proc = (saved or {}).get("process") or {}
    base = slicer.process_by_name(proc.get("base") or "")
    base = base if base and base["n"] in names else slicer.process_by_name(slicer.default_process(model))
    defaults = slicer.process_defaults(base)
    try:
        own = slicer.clean_process(proc.get("settings"))
    except ValueError:
        own = {}
    return {"base": base["n"], "settings": {**defaults, **own}, "defaults": defaults, "choices": names}


def _state(spool_id: int, fil: dict | None = None) -> dict:
    fil = fil or _filament(spool_id)
    model = _model()
    vendor = (fil.get("vendor") or {}).get("name") or ""
    cands = slicer.match(vendor, fil.get("name"), fil.get("material"), model)
    saved = slicer.stored(extra_value(fil, slicer.SLICER_FIELD))
    if saved and slicer.by_name(saved["profile"]):
        prof = slicer.by_name(saved["profile"])
        current = {**slicer.summary(prof), "settings": {**prof["s"], **saved.get("settings", {})}, "saved": True,
                   "defaults": dict(prof["s"])}
    elif cands:
        current = {**slicer.summary(cands[0]), "saved": False, "defaults": dict(cands[0]["s"])}
    else:
        current = None
    process = _process_state(saved, model) if current else None
    manual = slicer.manual_keys({**(saved or {}), "process": (saved or {}).get("process")}, model) if saved else []
    name = slicer.preset_name(vendor, _line(fil), model) if model else ""
    return {"model": model, "machine": slicer.machine_name(model) if model else "", "filament_id": fil.get("id"),
            "current": current, "candidates": [slicer.summary(p) for p in cands], "process": process,
            "manual": manual, "edited_at": (saved or {}).get("edited_at"), "preset_name": name,
            "process_name": slicer.process_name(process["base"], name) if process and name else ""}


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


def _process_preset(spool_id: int, name: str | None) -> dict | None:
    """The spool's user process preset, or None when its values are Bambu's own (nothing to send)."""
    st = _state(spool_id)
    proc = st["process"]
    if not proc:
        return None
    pr = slicer.build_process(proc["base"], proc["settings"], (name or st["process_name"]).strip())
    return pr if pr["values"] else None


@router.get("/api/slicer/match")
def match(vendor: str = "", name: str = "", material: str = ""):
    model = _model()
    return {"model": model, "candidates": [slicer.summary(p) for p in slicer.match(vendor, name, material, model)]}


@router.get("/api/spools/{spool_id}/slicer")
def spool_slicer(spool_id: int):
    return _state(spool_id)


class ProcessChoice(BaseModel):
    base: str = Field(default="", max_length=200)
    settings: dict[str, str] = Field(default_factory=dict)


class SlicerChoice(BaseModel):
    profile: str = Field(max_length=200)
    settings: dict[str, str] = Field(default_factory=dict)
    process: ProcessChoice | None = None


def _norm(v) -> str:
    return " ".join(str(v or "").lower().split())


def _siblings(fil: dict) -> list[dict]:
    """The filament and every other one of the same type and colour (vendor, material, name and colour alike)."""
    key = lambda f: (_norm((f.get("vendor") or {}).get("name")), _norm(f.get("material")), _norm(f.get("name")),
                     _norm(f.get("color_hex")), _norm(f.get("multi_color_hexes")))
    try:
        others = [f for f in _main().spoolman.filaments() if f.get("id") != fil.get("id") and key(f) == key(fil)]
    except (SpoolmanError, AttributeError):
        others = []
    return [fil, *others]


@router.put("/api/spools/{spool_id}/slicer")
def save_choice(spool_id: int, body: SlicerChoice):
    """Save the profile, its edits and the process values on the spool's filament - every spool of it, and every
    other filament of the same type and colour, gets the same settings."""
    prof = slicer.by_name(body.profile)
    if not prof:
        raise HTTPException(400, "Ukendt slicer-profil")
    edits = {k: str(v).replace(",", ".") for k, v in body.settings.items() if k in slicer.OVERRIDE and str(v).strip()}
    choice = {"profile": prof["n"], "settings": {**prof["s"], **edits}}
    model = _model()
    if body.process is not None and model:
        base = slicer.process_by_name(body.process.base) or slicer.process_by_name(slicer.default_process(model))
        if base:
            try:
                changed = slicer.changed_process(base, body.process.settings)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            choice["process"] = {"base": base["n"], "settings": changed}
    fil = _filament(spool_id)
    old = slicer.stored(extra_value(fil, slicer.SLICER_FIELD)) or {}
    if old.get("process") and "process" not in choice:
        choice["process"] = old["process"]
    manual = slicer.manual_keys(choice, model)
    same = {k: old.get(k) for k in ("profile", "settings", "process")} == {k: choice.get(k) for k in ("profile", "settings", "process")}
    choice["edited_at"] = old.get("edited_at") if same else (time.time() if manual else None)
    if not choice["edited_at"]:
        choice.pop("edited_at")
    value = json.dumps(json.dumps(choice))
    sibs = _siblings(fil)
    try:
        for f in sibs:
            _main().spoolman.update_filament(f["id"], extra={slicer.SLICER_FIELD: value})
    except SpoolmanError as e:
        raise HTTPException(502, str(e)) from e
    if len(sibs) > 1:
        _main().db.event("info", f"Printerindstillinger gemt på {len(sibs)} filamenter af samme type og farve")
    return {**_state(spool_id), "updated": len(sibs)}


def _filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._@-]+", "", name).strip() or "filament"


def _file(data: dict, name: str) -> Response:
    return Response(json.dumps(data, ensure_ascii=False, indent=4), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(name)}.json"'})


@router.get("/api/spools/{spool_id}/slicer/preset.json")
def preset_file(spool_id: int, name: str | None = Query(default=None, max_length=200)):
    pr = _preset(spool_id, name, None, None)
    return _file(slicer.studio_file(pr), pr["name"])


@router.get("/api/spools/{spool_id}/slicer/process.json")
def process_file(spool_id: int, name: str | None = Query(default=None, max_length=200)):
    pr = _process_preset(spool_id, name)
    if not pr:
        raise HTTPException(404, "Procesværdierne er Bambus egne – der er ingen procesprofil at hente")
    return _file(slicer.process_file(pr), pr["name"])


class CloudUpload(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    process_name: str | None = Field(default=None, max_length=200)
    replace: bool = False


@router.post("/api/spools/{spool_id}/slicer/cloud")
def upload(spool_id: int, body: CloudUpload):
    """Create the presets in the Bambu Cloud account (Bambu Studio fetches them at its next sync): the filament
    preset, and the process preset when the filament has its own process values."""
    cloud = _cloud()
    return _send_all(cloud, _preset(spool_id, body.name, None, None), _process_preset(spool_id, body.process_name),
                     body.replace)


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
    process: ProcessChoice | None = None
    process_name: str | None = Field(default=None, max_length=200)
    replace: bool = False


def _db_preset(body: ProfilePreset) -> dict:
    model = _model()
    if not model:
        raise HTTPException(400, "Ingen printer valgt – vælg den under Indstillinger")
    edits = {k: str(v).replace(",", ".").strip() for k, v in body.settings.items() if k in slicer.OVERRIDE and str(v).strip()}
    try:
        return slicer.build_preset(body.profile, edits, model, body.name.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _db_process(body: ProfilePreset) -> dict | None:
    model = _model()
    if body.process is None or not model:
        return None
    base = body.process.base or slicer.default_process(model)
    if base not in {p["n"] for p in slicer.processes_for(model)}:
        raise HTTPException(400, "Ukendt procesprofil til printeren")
    try:
        pr = slicer.build_process(base, body.process.settings,
                                  (body.process_name or slicer.process_name(base, body.name)).strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return pr if pr["values"] else None


@router.get("/api/slicer/processes")
def processes():
    """Bambu's process presets for the printer with their values of the settings a filament may change."""
    model = _model()
    return {"model": model, "default": slicer.default_process(model) if model else "",
            "items": [{"name": p["n"], "defaults": slicer.process_defaults(p)} for p in slicer.processes_for(model)]}


@router.post("/api/slicer/profiles/preset.json")
def profile_file(body: ProfilePreset):
    pr = _db_preset(body)
    return _file(slicer.studio_file(pr), pr["name"])


@router.post("/api/slicer/profiles/process.json")
def profile_process_file(body: ProfilePreset):
    pr = _db_process(body)
    if not pr:
        raise HTTPException(404, "Procesværdierne er Bambus egne – der er ingen procesprofil at hente")
    return _file(slicer.process_file(pr), pr["name"])


def _version(v: str) -> list[int]:
    return [int(x) for x in (v or "").split(".") if x.isdigit()]


def _send_all(cloud, filament: dict, process: dict | None, replace: bool = False) -> dict:
    """Create the filament preset (and the process preset) in Bambu Cloud. A name that is already there is refused
    (409) - or, with replace, the old preset is deleted first. Both names are checked before anything is sent."""
    jobs = [("filament", filament, slicer.cloud_body)] + ([("print", process, slicer.process_cloud_body)] if process else [])
    try:
        lists = {kind: cloud.slicer_presets(kind) for kind, _, _ in jobs}
        olds = []
        for kind, pr, _ in jobs:
            hit = [p for p in lists[kind] if (p.get("name") or "").lower() == pr["name"].lower()]
            if hit and not replace:
                raise HTTPException(409, f"Der findes allerede en profil med navnet »{pr['name']}« i Bambu Cloud")
            olds += [p["setting_id"] for p in hit if p.get("setting_id")]
        versions = sorted((p.get("version") or "" for ps in lists.values() for p in ps), key=_version)
        version = versions[-1] if versions and _version(versions[-1]) >= _version(slicer.PRESET_VERSION) else slicer.PRESET_VERSION
        for sid in olds:
            cloud.delete_slicer_preset(sid)
        out = {}
        for kind, pr, body in jobs:
            res = cloud.create_slicer_preset(body(pr, version))
            if not res.get("setting_id"):
                raise HTTPException(502, f"Bambu svarede uden profil-id: {res.get('message') or res}")
            out[kind] = {"setting_id": res["setting_id"], "name": pr["name"], "inherits": pr["inherits"], "values": pr["values"]}
    except CloudError as e:
        raise HTTPException(502, str(e)) from e
    return {**out["filament"], "process": out.get("print"), "replaced": len(olds)}


@router.post("/api/slicer/profiles/cloud")
def profile_to_cloud(body: ProfilePreset):
    cloud = _cloud()
    return _send_all(cloud, _db_preset(body), _db_process(body), body.replace)


@router.get("/api/slicer/cloud")
def cloud_presets():
    """The filament and process presets in the Bambu Cloud account (Bambu Studio's "User presets")."""
    cloud = _cloud()
    keep = ("setting_id", "name", "base_id", "version", "update_time", "updated_time", "filament_id")
    items = []
    try:
        for kind in ("filament", "print"):
            items += [{**{k: p.get(k) for k in keep if p.get(k) is not None}, "kind": kind} for p in cloud.slicer_presets(kind)]
    except CloudError as e:
        raise HTTPException(502, str(e)) from e
    return {"items": sorted(items, key=lambda p: (p["kind"] != "filament", (p.get("name") or "").lower()))}


@router.delete("/api/slicer/cloud/{setting_id}")
def cloud_delete(setting_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", setting_id):
        raise HTTPException(400, "Ugyldigt profil-id")
    try:
        _cloud().delete_slicer_preset(setting_id)
    except CloudError as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True}
