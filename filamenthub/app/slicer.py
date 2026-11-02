"""Slicer settings for a filament (plate temperatures, flow ratio, max volumetric speed, ...).

Source: OrcaSlicer's filament profiles (Bambu Lab's own printer profiles plus every vendor's generic
profile), compacted by tools/build_slicer_profiles.py into data/slicer_profiles.json.gz.

A filament's settings become a Bambu Studio user preset that inherits from Bambu's system preset for the
printer (as Bambu Studio itself stores them) and holds only what differs: a file to import, or straight
into the Bambu Cloud account, where Bambu Studio and Bambu Handy pick it up.
"""
from __future__ import annotations

import gzip
import json
import re
import time
from functools import lru_cache
from pathlib import Path

INDEX = Path(__file__).parent / "data" / "slicer_profiles.json.gz"
SLICER_FIELD = "slicer"   # filament extra field: {"profile": name, "settings": {key: value}} as JSON text

# Bambu product name (as the cloud reports it) -> the model code in profile names ("@BBL X1C").
MODEL_CODES = {"x1carbon": "X1C", "x1c": "X1C", "x1": "X1", "x1e": "X1E", "p1p": "P1P", "p1s": "X1C",
               "a1": "A1", "a1mini": "A1M", "h2d": "H2D", "h2dpro": "H2DP", "h2s": "H2S", "h2c": "H2C",
               "p2s": "P2S", "x2d": "X2D", "a2l": "A2L"}
MACHINE_NAMES = {"X1C": "Bambu Lab X1 Carbon", "A1M": "Bambu Lab A1 mini", "H2DP": "Bambu Lab H2D Pro"}
# Bambu Studio's extruder variants on printers with several (their per-extruder lists have one value each).
VARIANTS = '"Direct Drive Standard";"Direct Drive High Flow";"Bowden Standard";"Bowden High Flow"'
MULTI_VARIANT = {"X2D", "H2D", "H2DP", "H2S", "H2C", "P2S", "A2L"}
PER_EXTRUDER = {"nozzle_temperature", "nozzle_temperature_initial_layer", "filament_flow_ratio",
                "filament_max_volumetric_speed"}
# What a preset may change relative to its Bambu base (the rest - retraction, cooling logic, g-code - stays Bambu's).
OVERRIDE = ("nozzle_temperature", "nozzle_temperature_initial_layer", "nozzle_temperature_range_low",
            "nozzle_temperature_range_high", "supertack_plate_temp", "supertack_plate_temp_initial_layer",
            "textured_plate_temp", "textured_plate_temp_initial_layer", "cool_plate_temp",
            "cool_plate_temp_initial_layer", "hot_plate_temp", "hot_plate_temp_initial_layer", "eng_plate_temp",
            "eng_plate_temp_initial_layer", "filament_flow_ratio", "filament_max_volumetric_speed",
            "fan_min_speed", "fan_max_speed", "overhang_fan_speed", "filament_density", "filament_cost")
# Settings the app lets you change (and shows first).
EDITABLE = ("nozzle_temperature", "supertack_plate_temp", "textured_plate_temp", "filament_flow_ratio",
            "filament_max_volumetric_speed")
PRESET_VERSION = "2.6.0.3"
_STOP = {"system", "bbl", "nozzle", "filament", "the", "and", "with", "for"}


def model_code(product: str | None) -> str:
    key = re.sub(r"[^a-z0-9]", "", (product or "").lower())
    return MODEL_CODES.get(key, (product or "").upper().replace(" ", ""))


def machine_name(model: str, nozzle: str = "0.4") -> str:
    return f"{MACHINE_NAMES.get(model, f'Bambu Lab {model}')} {nozzle} nozzle"


@lru_cache(maxsize=1)
def profiles() -> tuple[dict, ...]:
    try:
        with gzip.open(INDEX, "rt", encoding="utf-8") as fh:
            return tuple(json.load(fh)["profiles"])
    except (OSError, ValueError, KeyError):
        return ()


def by_name(name: str) -> dict | None:
    return next((p for p in profiles() if p["n"] == name), None)


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _words(s: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", (s or "").lower()) if w not in _STOP}


def _same_vendor(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a.startswith(b) or b.startswith(a))


def match(vendor: str | None, name: str | None, material: str | None, model: str, limit: int = 8) -> list[dict]:
    """Profiles for this filament on this printer model, best first (the vendor's own, else Generic)."""
    mat = (material or "").strip().upper()
    if not mat:
        return []
    mat_words = _words(mat)
    name_words = _words(name) - mat_words - _words(vendor)
    scored = []
    for p in profiles():
        if p["m"] not in (model, ""):
            continue
        base_words = _words(p["b"])
        if (p["t"] or "").upper() != mat and not mat_words <= base_words:
            continue
        generic = p["v"].lower() == "generic"
        if not generic and not _same_vendor(p["v"], vendor or ""):
            continue
        rest = base_words - mat_words - _words(p["v"])
        score = (0 if generic else 10) + 3 * len(rest & name_words) - len(rest - name_words)
        score += 2 if p["m"] == model else 0
        score += 1 if (p["t"] or "").upper() == mat else 0
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], x[1]["n"]))
    return [p for _, p in scored[:limit]]


def summary(p: dict) -> dict:
    return {"profile": p["n"], "vendor": p["v"], "type": p["t"], "model": p["m"], "settings": dict(p["s"])}


def base_for(p: dict, model: str) -> dict | None:
    """Bambu's system preset a user preset for `p` inherits from on this printer."""
    if p["m"] == model and p["id"].startswith("GF"):
        return p
    generics = [g for g in profiles() if g["m"] == model and g["v"].lower() == "generic" and g["id"].startswith("GF")]
    t = (p["t"] or "").upper()
    for want in (f"generic {p['b'].split(' ', 1)[-1]}".lower(), f"generic {t}".lower()):
        hit = next((g for g in generics if g["b"].lower() == want), None)
        if hit:
            return hit
    return next((g for g in generics if (g["t"] or "").upper() == t), None)


def preset_name(vendor: str, line: str, model: str, nozzle: str = "0.4") -> str:
    return f"{' '.join((vendor or '').split())} {' '.join((line or '').split())} @{machine_name(model, nozzle)}".strip()


def build_preset(profile: str, settings: dict | None, model: str, name: str, variants: str | None = None) -> dict:
    """{name, inherits, base_id, values} - values are the settings that differ from the Bambu base."""
    p = by_name(profile)
    if not p:
        raise ValueError(f"Ukendt slicer-profil: {profile}")
    base = base_for(p, model)
    if not base:
        raise ValueError(f"Bambu har ingen grundprofil til {p['t']} på {model}")
    merged = {**p["s"], **{k: str(v) for k, v in (settings or {}).items() if v not in (None, "")}}
    values = {k: merged[k] for k in OVERRIDE if k in merged and merged[k] != base["s"].get(k)}
    if base["v"].lower() != p["v"].lower():
        values["filament_vendor"] = p["v"]
    return {"name": name, "inherits": base["n"], "base_id": base["id"], "values": values,
            "variants": variants if variants is not None else (VARIANTS if model in MULTI_VARIANT else "")}


def _count(variants: str) -> int:
    return variants.count(";") + 1 if variants else 1


def studio_file(pr: dict) -> dict:
    """A Bambu Studio user preset file (File > Import > Import Configs)."""
    n = _count(pr["variants"])
    out = {"type": "filament", "name": pr["name"], "inherits": pr["inherits"], "from": "User",
           "is_custom_defined": "0", "version": PRESET_VERSION, "filament_settings_id": [pr["name"]]}
    if pr["variants"]:
        out["filament_extruder_variant"] = [v.strip('"') for v in pr["variants"].split(";")]
    for k, v in pr["values"].items():
        out[k] = [v] * (n if k in PER_EXTRUDER else 1)
    return out


def cloud_body(pr: dict, version: str = PRESET_VERSION) -> dict:
    """Body for Bambu Cloud's slicer settings API, laid out like the presets Bambu Studio uploads."""
    n = _count(pr["variants"])
    setting = {"inherits": pr["inherits"], "filament_settings_id": f'"{pr["name"]}"',
               "updated_time": str(int(time.time()))}
    if pr["variants"]:
        setting["filament_extruder_variant"] = pr["variants"]
    for k, v in pr["values"].items():
        setting[k] = ",".join([v] * n) if k in PER_EXTRUDER else v
    return {"type": "filament", "name": pr["name"], "version": version, "base_id": pr["base_id"], "setting": setting}


def stored(extra_text: str) -> dict | None:
    """The filament's saved choice ({"profile", "settings"}) from its extra field, if any."""
    try:
        val = json.loads(extra_text) if extra_text else None
    except ValueError:
        return None
    return val if isinstance(val, dict) and val.get("profile") else None
