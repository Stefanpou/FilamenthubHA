"""Printer state: merging MQTT delta reports and turning them into AMS slot information.

Slot keys used everywhere in the app:
    "ams:<unit>:<tray>"  regular AMS / AMS 2 Pro (unit 0-3, tray 0-3), AMS HT (unit 128-135, tray 0)
    "ext:<id>"           external spool holder (vir_slot / vt_tray id 254 or 255)
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, asdict

ZERO_UUID = "00000000000000000000000000000000"
ZERO_TAG = "0000000000000000"
ACTIVE_STATES = {"PREPARE", "SLICING", "RUNNING", "PAUSE"}
END_STATES = {"FINISH", "FAILED"}


# --------------------------------------------------------------------------- merge
def _merge_list_by_id(old: list, new: list, tray_mode: bool = False) -> list:
    if not all(isinstance(x, dict) and "id" in x for x in new):
        return copy.deepcopy(new)
    index = {str(x.get("id")): i for i, x in enumerate(old) if isinstance(x, dict)}
    result = [copy.deepcopy(x) for x in old]
    for item in new:
        key = str(item.get("id"))
        if key in index:
            cur = result[index[key]]
            if tray_mode and set(item.keys()) <= {"id", "state"}:
                # A tray reported with only id/state means "no spool metadata": drop stale fields.
                result[index[key]] = copy.deepcopy(item)
            else:
                result[index[key]] = deep_merge(cur, item)
        else:
            result.append(copy.deepcopy(item))
            index[key] = len(result) - 1
    return result


def deep_merge(old: dict, new: dict, path: tuple = ()) -> dict:
    out = dict(old)
    for k, v in new.items():
        p = path + (k,)
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v, p)
        elif isinstance(v, list) and isinstance(out.get(k), list) and p in _ID_LISTS:
            out[k] = _merge_list_by_id(out[k], v, tray_mode=(p == ("ams", "ams", "tray")))
        elif isinstance(v, list) and p == ("ams", "ams") and isinstance(out.get(k), list):
            out[k] = _merge_ams_units(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _merge_ams_units(old: list, new: list) -> list:
    index = {str(x.get("id")): i for i, x in enumerate(old) if isinstance(x, dict)}
    result = [copy.deepcopy(x) for x in old]
    for unit in new:
        key = str(unit.get("id"))
        if key in index:
            cur = result[index[key]]
            merged = {k: v for k, v in cur.items()}
            for k, v in unit.items():
                if k == "tray" and isinstance(v, list) and isinstance(cur.get("tray"), list):
                    merged["tray"] = _merge_list_by_id(cur["tray"], v, tray_mode=True)
                elif isinstance(v, dict) and isinstance(cur.get(k), dict):
                    merged[k] = deep_merge(cur[k], v)
                else:
                    merged[k] = copy.deepcopy(v)
            result[index[key]] = merged
        else:
            result.append(copy.deepcopy(unit))
            index[key] = len(result) - 1
    return result


_ID_LISTS = {("vir_slot",), ("device", "extruder", "info"), ("device", "nozzle", "info")}


def merge_print_report(state: dict, report: dict) -> dict:
    """Merge a `print` object from an MQTT report into the accumulated state."""
    ams_units_present = isinstance(report.get("ams"), dict) and isinstance(report["ams"].get("ams"), list)
    merged = deep_merge(state, report)
    if ams_units_present and "ams_exist_bits" in report.get("ams", {}):
        # Full AMS report: drop units that are no longer connected.
        try:
            bits = int(str(report["ams"]["ams_exist_bits"]), 16)
            units = merged.get("ams", {}).get("ams", [])
            merged["ams"]["ams"] = [u for u in units if _unit_exists(u, bits)]
        except (ValueError, TypeError):
            pass
    return merged


def _unit_exists(unit: dict, bits: int) -> bool:
    try:
        uid = int(unit.get("id"))
    except (TypeError, ValueError):
        return True
    if uid >= 128:  # AMS HT units are not in ams_exist_bits on all firmwares; keep them.
        return True
    return bool(bits & (1 << uid))


# --------------------------------------------------------------------------- slots
@dataclass
class SlotInfo:
    key: str
    label: str
    kind: str            # "ams" | "ht" | "ext"
    unit: int
    tray: int
    present: bool        # a spool is physically there (or, for external, a filament is configured)
    material: str
    color: str           # RRGGBB (upper-case) or ""
    colors: list
    sub_brand: str
    filament_id: str     # Bambu tray_info_idx, e.g. GFA01
    tray_uuid: str       # Bambu RFID spool uuid ("" if none)
    tag_uid: str         # RFID tag uid ("" if none)
    remain: int          # percent from RFID / AMS estimate, -1 unknown
    active: bool         # currently feeding the nozzle
    unit_model: str
    weight: int = 0      # net weight of a full spool from the AMS (tray_weight, e.g. 500 for PVA), 0 unknown

    def to_dict(self) -> dict:
        return asdict(self)


def slot_label(key: str) -> str:
    # Deduction rows may carry a suffix ("ams:0:2#2": a second spool in the same slot during one print).
    kind, *rest = key.split("#")[0].split(":")
    if kind == "unknown":
        return "Ukendt slot"
    if kind == "ext":
        return "Ekstern 1" if rest[0] == "255" else "Ekstern 2"
    unit, tray = int(rest[0]), int(rest[1])
    if unit >= 128:
        return f"AMS HT {unit - 127}"
    return f"{chr(ord('A') + unit)}{tray + 1}"


def slot_from_flat(flat: int) -> str | None:
    """Flat slot index used by cloud amsDetailMapping 'ams' and print.ams_mapping."""
    if 0 <= flat < 16:
        return f"ams:{flat // 4}:{flat % 4}"
    if 16 <= flat < 24:
        return f"ams:{128 + flat - 16}:0"
    if 128 <= flat < 136:
        return f"ams:{flat}:0"
    if flat in (254, 255):
        return f"ext:{flat}"
    return None


def slot_from_task_entry(entry: dict) -> str | None:
    """Slot key for one entry of a cloud task's amsDetailMapping.

    The flat 'ams' index is authoritative. Real X2D data shows that 'amsId'/'slotId' are only filled
    for cloud-sliced prints; for prints sent as a file ("mode": "cloud_file") they are always 0/0,
    e.g. {"ams": 2, "amsId": 0, "slotId": 0} for a print that ran from A3.
    """
    flat = entry.get("ams")
    try:
        if flat is not None:
            slot = slot_from_flat(int(flat))
            if slot:
                return slot
    except (TypeError, ValueError):
        pass
    ams_id = entry.get("amsId")
    slot_id = entry.get("slotId")
    try:
        if ams_id is not None and int(ams_id) >= 0:
            ams_id = int(ams_id)
            if ams_id in (254, 255):
                return f"ext:{ams_id}"
            if 0 <= ams_id < 4 or 128 <= ams_id < 136:
                return f"ams:{ams_id}:{int(slot_id or 0) if ams_id < 128 else 0}"
    except (TypeError, ValueError):
        pass
    return None


def slots_from_mapping(mapping) -> list[str]:
    """Slots used by the current job, from the printer's `mapping` list (one value per slicer filament).

    65535 = filament not used. Values >= 256 are (unit << 8) | tray (dual-nozzle printers such as H2D/X2D);
    smaller values are the classic flat index. For AMS unit 0 both encodings give the same number.
    """
    out: list[str] = []
    for raw in mapping or []:
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        if v < 0 or v == 0xFFFF:
            continue
        key = None
        if v >= 256:
            unit, tray = v >> 8, v & 0xFF
            if unit in (254, 255):
                key = f"ext:{unit}"
            elif 128 <= unit < 136:
                key = f"ams:{unit}:0"
            elif unit < 4 and tray < 4:
                key = f"ams:{unit}:{tray}"
        else:
            key = slot_from_flat(v)
        if key and key not in out:
            out.append(key)
    return out


def _hex6(color: str | None) -> str:
    c = (color or "").strip().lstrip("#").upper()
    if len(c) >= 6:
        return c[:6]
    return ""


def _tray_weight(tray: dict) -> int:
    """Net weight of a full spool as the AMS reports it (Bambu RFID: '1000', '500'), 0 when unknown."""
    try:
        w = int(float(tray.get("tray_weight") or 0))
    except (TypeError, ValueError):
        return 0
    return w if 50 <= w <= 10000 else 0


def _tray_present(tray: dict) -> bool:
    if "state" in tray:
        try:
            st = int(tray["state"])
        except (TypeError, ValueError):
            st = None
        if st is not None:
            # Bit 0 = spool physically in the slot (both legacy and new encodings).
            # Other bits (motion/steady/rfid) flicker while filament is fed, so they are ignored here.
            return bool(st & 0x01)
    return bool(tray.get("tray_type")) or bool(tray.get("tray_info_idx"))


def _clean_uuid(v: str | None, zero: str) -> str:
    v = (v or "").strip().upper()
    return "" if not v or v == zero or set(v) == {"0"} else v


def active_slots(state: dict) -> set[str]:
    """Slots currently feeding a nozzle."""
    result: set[str] = set()
    extruders = (state.get("device") or {}).get("extruder", {}).get("info")
    if isinstance(extruders, list):
        for e in extruders:
            snow = e.get("snow")
            if not isinstance(snow, int) or snow == 0xFFFF:
                continue
            unit, tray = snow >> 8, snow & 0xFF
            if unit in (254, 255):
                result.add(f"ext:{unit}")
            elif unit >= 128:
                result.add(f"ams:{unit}:0")
            else:
                result.add(f"ams:{unit}:{tray & 0x3}")
        return result
    tray_now = (state.get("ams") or {}).get("tray_now")
    try:
        tn = int(tray_now)
    except (TypeError, ValueError):
        return result
    if tn == 254:
        result.add("ext:254")
    elif tn < 16:
        result.add(f"ams:{tn >> 2}:{tn & 0x3}")
    elif 128 <= tn < 136:
        result.add(f"ams:{tn}:0")
    return result


def parse_slots(state: dict, modules: dict[str, dict] | None = None) -> list[SlotInfo]:
    modules = modules or {}
    active = active_slots(state)
    slots: list[SlotInfo] = []
    units = (state.get("ams") or {}).get("ams") or []
    for unit in sorted(units, key=lambda u: int(u.get("id", 0))):
        try:
            uid = int(unit.get("id"))
        except (TypeError, ValueError):
            continue
        is_ht = uid >= 128
        model = (modules.get(str(uid)) or {}).get("model") or ("AMS HT" if is_ht else "AMS")
        trays = {int(t.get("id", -1)): t for t in (unit.get("tray") or []) if str(t.get("id", "")).isdigit()}
        tray_ids = [0] if is_ht else [0, 1, 2, 3]
        for tid in tray_ids:
            t = trays.get(tid, {})
            key = f"ams:{uid}:{tid}"
            present = _tray_present(t) if t else False
            slots.append(SlotInfo(
                key=key, label=slot_label(key), kind="ht" if is_ht else "ams", unit=uid, tray=tid,
                present=present,
                material=(t.get("tray_type") or "") if present else "",
                color=_hex6(t.get("tray_color")) if present else "",
                colors=[_hex6(c) for c in (t.get("cols") or [])] if present else [],
                sub_brand=(t.get("tray_sub_brands") or "") if present else "",
                filament_id=(t.get("tray_info_idx") or "") if present else "",
                tray_uuid=_clean_uuid(t.get("tray_uuid"), ZERO_UUID) if present else "",
                tag_uid=_clean_uuid(t.get("tag_uid"), ZERO_TAG) if present else "",
                remain=int(t.get("remain", -1)) if present and str(t.get("remain", "")).lstrip("-").isdigit() else -1,
                active=key in active, unit_model=model, weight=_tray_weight(t) if present else 0,
            ))
    ext_list = state.get("vir_slot")
    if not isinstance(ext_list, list) or not ext_list:
        vt = state.get("vt_tray")
        ext_list = [vt] if isinstance(vt, dict) and vt else []
    for t in sorted(ext_list, key=lambda x: -int(x.get("id", 0) or 0)):
        try:
            eid = int(t.get("id", 254))
        except (TypeError, ValueError):
            continue
        if eid not in (254, 255):
            continue
        key = f"ext:{eid}"
        present = bool(t.get("tray_type"))
        slots.append(SlotInfo(
            key=key, label=slot_label(key), kind="ext", unit=eid, tray=0, present=present,
            material=t.get("tray_type") or "", color=_hex6(t.get("tray_color")) if present else "",
            colors=[_hex6(c) for c in (t.get("cols") or [])] if present else [],
            sub_brand=t.get("tray_sub_brands") or "", filament_id=t.get("tray_info_idx") or "",
            tray_uuid=_clean_uuid(t.get("tray_uuid"), ZERO_UUID), tag_uid=_clean_uuid(t.get("tag_uid"), ZERO_TAG),
            remain=-1, active=key in active, unit_model="Ekstern",
        ))
    return slots


def parse_modules(info: dict) -> dict[str, dict]:
    """AMS modules from a get_version reply: {unit_id: {model, serial}}."""
    result: dict[str, dict] = {}
    for m in info.get("module") or []:
        name = str(m.get("name", ""))
        prefix_models = {"ams/": "AMS", "ams_f1/": "AMS Lite", "n3f/": "AMS 2 Pro", "n3s/": "AMS HT"}
        for prefix, model in prefix_models.items():
            if name.startswith(prefix):
                try:
                    idx = int(name[len(prefix):])
                except ValueError:
                    continue
                if model == "AMS HT" and idx < 128:
                    idx += 128
                result[str(idx)] = {"model": model, "serial": m.get("sn", "")}
    return result


def print_summary(state: dict) -> dict:
    def _int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    task_id = str(state.get("task_id") or "")
    return {
        "state": state.get("gcode_state") or "UNKNOWN",
        "percent": _int(state.get("mc_percent")),
        "remaining_min": _int(state.get("mc_remaining_time")),
        "name": state.get("subtask_name") or "",
        "task_id": task_id if task_id not in ("", "0") else "",
        "layer": _int(state.get("layer_num")),
        "total_layers": _int(state.get("total_layer_num")),
        "print_type": state.get("print_type") or "",
    }


def color_distance(a: str, b: str) -> float:
    """Simple RGB distance 0..441 between two RRGGBB strings."""
    try:
        ra, ga, ba = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
        rb, gb, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    except (ValueError, IndexError):
        return 999.0
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5
