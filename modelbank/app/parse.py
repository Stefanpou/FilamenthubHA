"""Reads STL and 3MF files: facts for the card (size, plates, filament grams, colours, print time), the embedded
thumbnail and a decimated mesh in FilamentHub's FHM1 format for the 3D view."""
from __future__ import annotations

import io
import json
import logging
import re
import struct
import zipfile

from . import fh_model3d as m3d

log = logging.getLogger("modelbank.parse")

MAX_THUMB = 5 * 1024 * 1024
THUMBS = ("Metadata/plate_1.png", "Metadata/thumbnail.png", "Auxiliaries/.thumbnails/thumbnail_3mf.png",
          "Auxiliaries/.thumbnails/thumbnail_middle.png", "Metadata/plate_1_small.png")
PARSE_VERSION = 1


class Parsed:
    def __init__(self):
        self.meta: dict = {"v": PARSE_VERSION}
        self.thumb: bytes | None = None      # PNG from the file itself
        self.mesh: bytes | None = None       # FHM1 blob


def parse_file(path, data: bytes | None = None) -> Parsed:
    data = data if data is not None else path.read_bytes()
    name = str(path).lower()
    out = Parsed()
    try:
        if name.endswith(".stl"):
            _stl(data, out)
        else:
            _3mf(data, out)
    except Exception as e:   # a broken file still gets a card
        log.info("parse %s: %s", path, e)
        out.meta["error"] = str(e) if isinstance(e, (m3d.ModelError, ValueError)) else type(e).__name__
    return out


# ----------------------------------------------------------------------------- STL
def stl_triangles(data: bytes) -> tuple[list[float], list[int]]:
    """Positions (deduplicated) and indices of an ASCII or binary STL."""
    if len(data) >= 84:
        (n,) = struct.unpack_from("<I", data, 80)
        if 84 + n * 50 == len(data):
            if n > m3d.MAX_RAW_TRIANGLES:
                raise m3d.ModelError("STL har for mange trekanter")
            return _dedupe(data[84 + 12 + i * 50: 84 + 48 + i * 50] for i in range(n))
    text = data.decode("utf-8", "replace")
    if "facet" not in text[:2000] and "vertex" not in text:
        raise m3d.ModelError("ikke en STL")
    nums = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
    if len(nums) % 3 or not nums:
        raise m3d.ModelError("STL uden trekanter")
    if len(nums) // 3 > m3d.MAX_RAW_TRIANGLES:
        raise m3d.ModelError("STL har for mange trekanter")
    tris = (struct.pack("<9f", *map(float, (c for v in nums[i:i + 3] for c in v))) for i in range(0, len(nums), 3))
    return _dedupe(tris)


def _dedupe(tri_blobs) -> tuple[list[float], list[int]]:
    index: dict[bytes, int] = {}
    verts: list[float] = []
    tris: list[int] = []
    for b in tri_blobs:
        for k in (b[0:12], b[12:24], b[24:36]):
            i = index.get(k)
            if i is None:
                i = index[k] = len(index)
                verts += struct.unpack("<3f", k)
            tris.append(i)
    return verts, tris


def _stl(data: bytes, out: Parsed) -> None:
    verts, tris = stl_triangles(data)
    out.meta.update(kind="stl", triangles=len(tris) // 3, filaments=[], plates=[])
    out.mesh, out.meta["dims"] = _mesh(verts, tris)
    out.meta["mesh"] = True


def _mesh(verts: list[float], tris: list[int]) -> tuple[bytes, list[float]]:
    minx, miny, minz = min(verts[0::3]), min(verts[1::3]), min(verts[2::3])
    maxx, maxy = max(verts[0::3]), max(verts[1::3])
    # centre on the 256 mm plate, standing on it
    dx, dy, dz = 128 - (minx + maxx) / 2, 128 - (miny + maxy) / 2, -minz
    v, t = m3d._decimate(verts, tris, m3d.MAX_TRIANGLES)
    v = [c + (dx, dy, dz)[i % 3] for i, c in enumerate(v)]
    bbox = [min(v[0::3]), min(v[1::3]), min(v[2::3]), max(v[0::3]), max(v[1::3]), max(v[2::3])]
    dims = [round(bbox[3] - bbox[0], 1), round(bbox[4] - bbox[1], 1), round(bbox[5] - bbox[2], 1)]
    meta = {"kind": "mesh", "approx": False, "bed": list(m3d.DEFAULT_BED), "bbox": [round(x, 3) for x in bbox],
            "colors": {}, "plate": 1, "name": ""}
    return m3d.pack_model(meta, [{"filament": 1, "positions": v, "indices": t}]), dims


# ----------------------------------------------------------------------------- 3MF
def _3mf(data: bytes, out: Parsed) -> None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise m3d.ModelError("ikke en 3MF") from e
    m3d.check_zip(z)
    names = set(z.namelist())
    meta = out.meta
    meta["kind"] = "3mf"

    def read(n, limit=m3d.MAX_TEXT):
        return m3d.read_limited(z, n, limit) if n in names else None

    model = read("3D/3dmodel.model")
    if model:
        head = model[:200_000].decode("utf-8", "replace")
        for key, field in (("Title", "title"), ("Designer", "designer"), ("Description", "description")):
            mm = re.search(rf'<metadata\s+name="{key}"[^>]*>(.*?)</metadata>', head, re.S)
            if mm and mm.group(1).strip():
                meta[field] = _unxml(mm.group(1).strip())[:2000]

    # plates + filament use: sliced files carry slice_info.config, projects only the filament list
    plates = _slice_info(read("Metadata/slice_info.config"))
    project = _project(read("Metadata/project_settings.config"))
    ext, plate_objs, ranges = m3d._parse_settings(read("Metadata/model_settings.config"))
    used = sorted({e for e in ext.values()} | {r[2] for rs in ranges.values() for r in rs}) or [1]
    if plates:
        meta["sliced"] = True
    else:
        meta["sliced"] = False
        n_plates = max(plate_objs) if plate_objs else 1
        plates = [{"index": i, "seconds": None, "grams": None, "filaments": []} for i in range(1, n_plates + 1)]
    for p in plates:
        for f in p["filaments"]:
            if not f.get("type") and project["types"][f["id"] - 1: f["id"]]:
                f["type"] = project["types"][f["id"] - 1]
    meta["plates"] = plates
    agg: dict[int, dict] = {}
    for p in plates:
        for f in p["filaments"]:
            a = agg.setdefault(f["id"], {"id": f["id"], "type": f.get("type") or "", "color": f.get("color") or "",
                                         "grams": 0.0})
            a["grams"] = round(a["grams"] + (f.get("grams") or 0), 2)
    if not agg:   # not sliced: the project's filaments that objects use, without grams
        for i in used:
            if i - 1 < len(project["types"]) or i - 1 < len(project["colors"]):
                agg[i] = {"id": i, "type": (project["types"][i - 1: i] or [""])[0],
                          "color": (project["colors"][i - 1: i] or [""])[0], "grams": None}
    meta["filaments"] = [agg[k] for k in sorted(agg)]
    secs = [p["seconds"] for p in plates if p.get("seconds")]
    grams = [p["grams"] for p in plates if p.get("grams")]
    meta["seconds"] = round(sum(secs)) if secs else None
    meta["grams"] = round(sum(grams), 2) if grams else None

    for n in THUMBS:
        if n in names:
            try:
                out.thumb = m3d.read_limited(z, n, MAX_THUMB)
                break
            except m3d.ModelError:
                pass

    colors = {str(f["id"]): f["color"] for f in meta["filaments"] if f.get("color")}
    try:
        out.mesh = m3d.mesh_from_3mf(data, plate=1, colors=colors, name=meta.get("title") or "")
        bb = _header(out.mesh)["bbox"]
        meta["dims"] = [round(bb[3] - bb[0], 1), round(bb[4] - bb[1], 1), round(bb[5] - bb[2], 1)]
        meta["mesh"] = True
    except m3d.ModelError as e:
        meta["mesh"] = False
        meta["mesh_error"] = str(e)


def _header(blob: bytes) -> dict:
    (n,) = struct.unpack_from("<I", blob, 4)
    return json.loads(blob[8:8 + n])


def _unxml(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")
            .replace("&#10;", "\n").replace("&amp;", "&"))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _slice_info(data: bytes | None) -> list[dict]:
    if not data:
        return []
    try:
        root = m3d._safe_xml(data)
    except Exception:
        return []
    plates = []
    for p in root.findall("plate"):
        md = {m.get("key"): m.get("value") for m in p.findall("metadata")}
        fil = []
        for f in p.findall("filament"):
            fid = int(_num(f.get("id")) or 1)
            col = (f.get("color") or "").lstrip("#")[:6].upper()
            fil.append({"id": fid, "type": f.get("type") or "", "color": col,
                        "grams": round(_num(f.get("used_g")) or 0, 2)})
        plates.append({"index": int(_num(md.get("index")) or len(plates) + 1),
                       "seconds": _num(md.get("prediction")), "grams": _num(md.get("weight")), "filaments": fil})
    return plates


def _project(data: bytes | None) -> dict:
    out = {"types": [], "colors": []}
    if not data:
        return out
    try:
        js = json.loads(data)
    except ValueError:
        return out
    out["types"] = [str(x) for x in js.get("filament_type") or []]
    out["colors"] = [str(x).lstrip("#")[:6].upper() for x in js.get("filament_colour") or []]
    return out
