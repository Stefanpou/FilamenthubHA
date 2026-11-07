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


def builtin(p: dict, model: str) -> bool:
    """Bambu's own system preset for this printer: Bambu Studio already has it."""
    return p["m"] == model and p["id"].startswith("GF") and p["v"].lower() in ("bambu lab", "generic")


def for_printer(model: str, all_printers: bool = False) -> list[dict]:
    """The profiles that make sense on this printer: its own (`@BBL <model>`) and the vendors' generic ones
    (`@System`), minus a generic one when the vendor has the same line for this printer."""
    ps = profiles()
    if all_printers or not model:
        return list(ps)
    own = {(p["v"].lower(), p["b"].lower()) for p in ps if p["m"] == model}
    return [p for p in ps if p["m"] == model or (p["m"] == "" and (p["v"].lower(), p["b"].lower()) not in own)]


def search(model: str, q: str = "", vendor: str = "", material: str = "", all_printers: bool = False) -> tuple[list[dict], list[dict]]:
    """(hits, pool): the profiles matching every word of `q` (in vendor, name, material and printer) and the
    vendor/material filters, sorted by vendor and name; pool is the printer's set the facets count over."""
    pool = for_printer(model, all_printers)
    words = [w for w in re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", q.lower())]
    hits = []
    for p in pool:
        if vendor and p["v"].lower() != vendor.lower():
            continue
        if material and (p["t"] or "").upper() != material.upper():
            continue
        hay = f"{p['v']} {p['n']} {p['t']}".lower()
        if all(w in hay or w in _norm(hay) for w in words):
            hits.append(p)
    hits.sort(key=lambda p: (p["v"].lower() != "bambu lab", p["v"].lower(), p["b"].lower(), p["m"], p["n"]))
    return hits, pool


def default_name(p: dict, model: str, nozzle: str = "0.4") -> str:
    """A name for the user preset: "<vendor> <line> @<printer>" (the vendor once, also when the line has it)."""
    line = p["b"]
    if not _same_vendor(line.split(" ", 1)[0], p["v"]) and p["v"].lower() != "generic":
        line = f"{p['v']} {line}"
    return f"{' '.join(line.split())} @{machine_name(model, nozzle)}"


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


# ---- process (print) settings a filament may change: a user process preset per filament ---------------------------
# Bambu Studio keeps these in the process preset, not the filament one, so they travel as a second user preset that
# inherits from one of Bambu's process presets for the printer (data/process_profiles.json.gz, Bambu Studio's own).
PROCESS_INDEX = Path(__file__).parent / "data" / "process_profiles.json.gz"
PROCESS_KEYS = ("ironing_speed", "ironing_flow", "initial_layer_flow_ratio", "top_solid_infill_flow_ratio")
PROCESS_DEFAULTS = {"ironing_speed": "20", "ironing_flow": "10", "initial_layer_flow_ratio": "1",
                    "top_solid_infill_flow_ratio": "1"}   # Bambu Studio's defaults when a preset leaves them out
PROCESS_LIMITS = {"ironing_speed": (1, 500), "ironing_flow": (0, 100), "initial_layer_flow_ratio": (0, 2),
                  "top_solid_infill_flow_ratio": (0, 2)}
PER_VARIANT_PROCESS = {"top_solid_infill_flow_ratio"}


@lru_cache(maxsize=1)
def process_profiles() -> tuple[dict, ...]:
    try:
        with gzip.open(PROCESS_INDEX, "rt", encoding="utf-8") as fh:
            return tuple(json.load(fh)["profiles"])
    except (OSError, ValueError, KeyError):
        return ()


def processes_for(model: str) -> list[dict]:
    return [p for p in process_profiles() if p["m"] == model]


def process_by_name(name: str) -> dict | None:
    return next((p for p in process_profiles() if p["n"] == name), None)


def default_process(model: str) -> str:
    ps = processes_for(model)
    return next((p["n"] for p in ps if p["n"].startswith("0.20mm Standard")), ps[0]["n"] if ps else "")


def _scalar(v) -> str:
    v = v[0] if isinstance(v, list) and v else v
    return str(v).rstrip("%") if v not in (None, "") else ""


def process_defaults(base: dict) -> dict:
    """The four values as Bambu's process preset has them ("10%" -> "10", per-variant lists -> the first)."""
    return {k: _scalar(base["s"].get(k)) or PROCESS_DEFAULTS[k] for k in PROCESS_KEYS}


def clean_process(values: dict | None) -> dict:
    """Only the known keys, as plain numbers within Bambu Studio's limits (ValueError otherwise)."""
    out = {}
    for k, v in (values or {}).items():
        if k not in PROCESS_KEYS or str(v).strip() in ("", "None"):
            continue
        try:
            x = float(str(v).replace(",", ".").rstrip("%").strip())
        except ValueError:
            raise ValueError(f"Ugyldig værdi for {k}: {v}") from None
        lo, hi = PROCESS_LIMITS[k]
        if not lo <= x <= hi:
            raise ValueError(f"{k} skal være mellem {lo:g} og {hi:g}")
        out[k] = f"{x:g}"
    return out


def changed_process(base: dict, values: dict | None) -> dict:
    d = process_defaults(base)
    return {k: v for k, v in clean_process(values).items() if float(v) != float(d[k])}


def process_name(base_name: str, filament_name: str) -> str:
    """"0.20mm Standard @BBL X2D – Elegoo Rapid PETG" from the filament preset's "Elegoo Rapid PETG @Bambu Lab ..."."""
    line = filament_name.split(" @", 1)[0].strip()
    return f"{base_name} - {line}" if line else base_name


def build_process(base_name: str, values: dict | None, name: str) -> dict:
    """{name, inherits, base_id, values, variants} - values are the four settings that differ from Bambu's preset."""
    base = process_by_name(base_name)
    if not base:
        raise ValueError(f"Ukendt procesprofil: {base_name}")
    return {"name": name, "inherits": base["n"], "base_id": base["id"], "values": changed_process(base, values),
            "variant": base["s"].get("print_extruder_variant") or [], "extruder": base["s"].get("print_extruder_id") or []}


def _process_value(k: str, v: str) -> str:
    return f"{v}%" if k == "ironing_flow" else v


def process_file(pr: dict) -> dict:
    """A Bambu Studio user process preset file (File > Import > Import Configs)."""
    n = max(len(pr["variant"]), 1)
    out = {"type": "process", "name": pr["name"], "inherits": pr["inherits"], "from": "User",
           "is_custom_defined": "0", "version": PRESET_VERSION, "print_settings_id": pr["name"]}
    if pr["variant"] and any(k in PER_VARIANT_PROCESS for k in pr["values"]):
        out["print_extruder_variant"], out["print_extruder_id"] = list(pr["variant"]), list(pr["extruder"])
    for k, v in pr["values"].items():
        out[k] = [v] * n if k in PER_VARIANT_PROCESS else _process_value(k, v)
    return out


def process_cloud_body(pr: dict, version: str = PRESET_VERSION) -> dict:
    """Body for Bambu Cloud's slicer settings API, laid out like the process presets Bambu Studio uploads."""
    n = max(len(pr["variant"]), 1)
    setting = {"inherits": pr["inherits"], "print_settings_id": pr["name"], "updated_time": str(int(time.time()))}
    if pr["variant"] and any(k in PER_VARIANT_PROCESS for k in pr["values"]):
        setting["print_extruder_variant"] = ";".join(f'"{v}"' for v in pr["variant"])
        setting["print_extruder_id"] = ",".join(pr["extruder"])
    for k, v in pr["values"].items():
        setting[k] = ",".join([v] * n) if k in PER_VARIANT_PROCESS else _process_value(k, v)
    return {"type": "print", "name": pr["name"], "version": version, "base_id": pr["base_id"], "setting": setting}


def manual_keys(choice: dict, model: str) -> list[str]:
    """The settings that differ from the profile's own (filament) or Bambu's process preset (process): what was
    changed by hand."""
    out = []
    p = by_name(choice.get("profile") or "")
    if p:
        for k, v in (choice.get("settings") or {}).items():
            d = p["s"].get(k)
            try:
                same = d is not None and float(str(v)) == float(str(d))
            except ValueError:
                same = str(v) == str(d)
            if not same and k in OVERRIDE:
                out.append(k)
    proc = choice.get("process") or {}
    base = process_by_name(proc.get("base") or "") or process_by_name(default_process(model))
    if base:
        try:
            out += list(changed_process(base, proc.get("settings")))
        except ValueError:
            pass
    return out
