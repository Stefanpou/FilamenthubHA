"""3D model of a print job, in a compact binary format for the browser.

Two sources (see live.py for how they are fetched from Bambu Cloud):
  * a full 3MF (MakerWorld downloads) -> real mesh, placed where it is printed (`mesh_from_3mf`)
  * the "config" 3MF the cloud keeps for sliced projects, which has NO geometry but the plate preview images and
    plate_N.json (object footprints) -> an extruded silhouette of the top view (`silhouette_from_config`)

Binary layout ("FHM1", little endian):
    4 bytes  magic b"FHM1"
    u32      length of the JSON header (padded with spaces to a multiple of 4)
    JSON     {"kind": "mesh"|"silhouette", "approx": bool, "bed": [x, y, z], "bbox": [minx, miny, minz, maxx, maxy, maxz],
              "colors": {"1": "RRGGBB", ...}, "plate": n, "name": str,
              "parts": [{"filament": 1, "v": vertex_count, "i": index_count}, ...]}
    per part float32[3*v] positions (mm, printer plate coordinates: x right, y back, z up), then uint32[i] indices
"""
from __future__ import annotations

import io
import json
import logging
import re
import struct
import zipfile
import zlib
from xml.etree import ElementTree as ET

log = logging.getLogger("spoolsync.model3d")

MAGIC = b"FHM1"
MAX_TRIANGLES = 90_000          # per model after decimation (phones stay fast)
MAX_UNCOMPRESSED = 400 * 1024 * 1024
MAX_INSTANCES = 20_000          # mesh instances (component expansion) per 3MF
MAX_DEPTH = 8                   # component nesting
MAX_RAW_TRIANGLES = 5_000_000   # before decimation
MAX_RAW_VERTS = 5_000_000
MAX_PNG_PIXELS = 1_000_000
MAX_COVER = 5 * 1024 * 1024
MAX_TEXT = 10 * 1024 * 1024
DEFAULT_BED = (256.0, 256.0, 260.0)

NS_CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
NS_P = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"


class ModelError(Exception):
    pass


# ----------------------------------------------------------------------------- binary container
def pack_model(meta: dict, parts: list[dict]) -> bytes:
    """parts: [{"filament": int, "positions": list[float], "indices": list[int]}]"""
    import array
    head = dict(meta)
    head["parts"] = [{"filament": p["filament"], "v": len(p["positions"]) // 3, "i": len(p["indices"])} for p in parts]
    raw = json.dumps(head, separators=(",", ":")).encode()
    raw += b" " * (-len(raw) % 4)
    out = bytearray(MAGIC + struct.pack("<I", len(raw)) + raw)
    for p in parts:
        out += array.array("f", p["positions"]).tobytes()
        out += array.array("I", p["indices"]).tobytes()
    return bytes(out)


def unpack_model(blob: bytes) -> tuple[dict, list[dict]]:
    import array
    if blob[:4] != MAGIC:
        raise ModelError("not a FHM1 blob")
    (n,) = struct.unpack_from("<I", blob, 4)
    meta = json.loads(blob[8:8 + n])
    pos = 8 + n
    parts = []
    for p in meta["parts"]:
        pa = array.array("f")
        pa.frombytes(blob[pos:pos + 12 * p["v"]])
        pos += 12 * p["v"]
        ia = array.array("I")
        ia.frombytes(blob[pos:pos + 4 * p["i"]])
        pos += 4 * p["i"]
        parts.append({"filament": p["filament"], "positions": list(pa), "indices": list(ia)})
    return meta, parts


# ----------------------------------------------------------------------------- helpers
def read_limited(zf: zipfile.ZipFile, name: str, max_size: int) -> bytes:
    """zf.read(name), refusing members whose declared size exceeds max_size (zip bombs); reads at most max_size + 1."""
    info = zf.getinfo(name)
    if info.file_size > max_size:
        raise ModelError(f"{name} er for stor")
    with zf.open(info) as f:
        data = f.read(max_size + 1)
    if len(data) > max_size:
        raise ModelError(f"{name} er for stor")
    return data


def check_zip(z: zipfile.ZipFile) -> None:
    if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED:
        raise ModelError("3MF er for stor")


def _safe_xml(data: bytes) -> ET.Element:
    head = data[:4096].lower()
    if b"<!entity" in head or b"<!doctype" in head:
        raise ModelError("XML med DOCTYPE afvist")
    return ET.fromstring(data)


def _mat(text: str | None):
    if not text:
        return (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
    try:
        v = [float(x) for x in text.split()]
    except ValueError:
        return (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
    return tuple(v) if len(v) == 12 else (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)


def _compose(a, b):
    """First a, then b (3MF row-vector convention: p' = p*M + t)."""
    out = []
    for r in range(3):
        for c in range(3):
            out.append(a[r * 3 + 0] * b[0 * 3 + c] + a[r * 3 + 1] * b[1 * 3 + c] + a[r * 3 + 2] * b[2 * 3 + c])
    for c in range(3):
        out.append(a[9] * b[c] + a[10] * b[3 + c] + a[11] * b[6 + c] + b[9 + c])
    return tuple(out)


def _apply(m, x, y, z):
    return (x * m[0] + y * m[3] + z * m[6] + m[9],
            x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


def _read_mesh(root: ET.Element):
    """{object id: (verts [x,y,z...], tris [a,b,c...]) | components [(path, objectid, transform)]}"""
    objs = {}
    resources = root.find(f"{NS_CORE}resources")
    if resources is None:
        return objs
    for obj in resources.findall(f"{NS_CORE}object"):
        oid = obj.get("id")
        mesh = obj.find(f"{NS_CORE}mesh")
        if mesh is not None:
            verts: list[float] = []
            for v in mesh.iter(f"{NS_CORE}vertex"):
                verts += [float(v.get("x")), float(v.get("y")), float(v.get("z"))]
            tris: list[int] = []
            for t in mesh.iter(f"{NS_CORE}triangle"):
                tris += [int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))]
            objs[oid] = ("mesh", verts, tris)
            continue
        comps = obj.find(f"{NS_CORE}components")
        if comps is not None:
            lst = []
            for c in comps.findall(f"{NS_CORE}component"):
                lst.append((c.get(f"{NS_P}path") or c.get("path") or "", c.get("objectid"), _mat(c.get("transform"))))
            objs[oid] = ("comps", lst)
    return objs


def _parse_settings(data: bytes | None):
    """model_settings.config -> ({object id: extruder}, {plate index: [object ids]}, {object id: [(first, last, ext)]})"""
    ext: dict[str, int] = {}
    plates: dict[int, list[str]] = {}
    ranges: dict[str, list[tuple[int, int, int]]] = {}
    if not data:
        return ext, plates, ranges
    try:
        root = _safe_xml(data)
    except Exception:
        return ext, plates, ranges
    for o in root.findall("object"):
        oid = o.get("id")
        for m in o.findall("metadata"):
            if m.get("key") == "extruder":
                try:
                    ext[oid] = int(m.get("value"))
                except (TypeError, ValueError):
                    pass
        for p in o.findall("part"):
            pe = None
            for m in p.findall("metadata"):
                if m.get("key") == "extruder":
                    try:
                        pe = int(m.get("value"))
                    except (TypeError, ValueError):
                        pass
            ms = p.find("mesh_stat")
            first = p.get("firstid") or (ms.get("firstid") if ms is not None else None)
            last = p.get("lastid") or (ms.get("lastid") if ms is not None else None)
            if pe is not None and first is not None and last is not None:
                ranges.setdefault(oid, []).append((int(first), int(last), pe))
    for p in root.findall("plate"):
        pid = None
        ids = []
        for m in p.findall("metadata"):
            if m.get("key") == "plater_id":
                try:
                    pid = int(m.get("value"))
                except (TypeError, ValueError):
                    pass
        for inst in p.findall("model_instance"):
            for m in inst.findall("metadata"):
                if m.get("key") == "object_id":
                    ids.append(m.get("value"))
        if pid is not None:
            plates[pid] = ids
    return ext, plates, ranges


def _decimate(verts: list[float], tris: list[int], target: int) -> tuple[list[float], list[int]]:
    """Vertex clustering. Keeps the outline, drops tiny detail."""
    n = len(tris) // 3
    if n <= target:
        return verts, tris
    xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
    mn = (min(xs), min(ys), min(zs))
    ext = max(max(xs) - mn[0], max(ys) - mn[1], max(zs) - mn[2]) or 1.0
    for g in (400, 300, 220, 160, 120, 90, 64, 48, 32):
        cell = ext / g
        cluster: dict[tuple[int, int, int], int] = {}
        acc: list[list[float]] = []
        remap: list[int] = []
        for i in range(0, len(verts), 3):
            key = (int((verts[i] - mn[0]) / cell), int((verts[i + 1] - mn[1]) / cell), int((verts[i + 2] - mn[2]) / cell))
            ci = cluster.get(key)
            if ci is None:
                ci = len(acc)
                cluster[key] = ci
                acc.append([0.0, 0.0, 0.0, 0])
            a = acc[ci]
            a[0] += verts[i]
            a[1] += verts[i + 1]
            a[2] += verts[i + 2]
            a[3] += 1
            remap.append(ci)
        out_t: list[int] = []
        seen = set()
        for i in range(0, len(tris), 3):
            a, b, c = remap[tris[i]], remap[tris[i + 1]], remap[tris[i + 2]]
            if a == b or b == c or a == c:
                continue
            k = (a, b, c) if a < b and a < c else ((b, c, a) if b < c else (c, a, b))
            if k in seen:
                continue
            seen.add(k)
            out_t += [a, b, c]
        if len(out_t) // 3 <= target or g == 32:
            nv: list[float] = []
            for a in acc:
                nv += [a[0] / a[3], a[1] / a[3], a[2] / a[3]]
            return nv, out_t
    return verts, tris  # pragma: no cover


# ----------------------------------------------------------------------------- real mesh from a full 3MF
def mesh_from_3mf(data: bytes, plate: int = 1, colors: dict | None = None, name: str = "",
                  bed=DEFAULT_BED) -> bytes:
    """Mesh of one plate of a Bambu Studio 3MF, in plate coordinates. Raises ModelError if there is no geometry."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ModelError("ikke en 3MF") from e
    check_zip(z)
    names = set(z.namelist())
    if "3D/3dmodel.model" not in names:
        raise ModelError("3MF uden 3D/3dmodel.model")
    root = _safe_xml(read_limited(z, "3D/3dmodel.model", MAX_TEXT))
    own = _read_mesh(root)
    ext, plates, ranges = _parse_settings(
        read_limited(z, "Metadata/model_settings.config", MAX_TEXT) if "Metadata/model_settings.config" in names else None)

    file_cache: dict[str, dict] = {}

    def objects_of(path: str):
        path = path.lstrip("/")
        if not path:
            return own
        if path not in file_cache:
            if path not in names:
                raise ModelError(f"mangler {path}")
            file_cache[path] = _read_mesh(_safe_xml(read_limited(z, path, MAX_TEXT)))
        return file_cache[path]

    build = root.find(f"{NS_CORE}build")
    items = []
    if build is not None:
        for it in build.findall(f"{NS_CORE}item"):
            if it.get("printable") == "0":
                continue
            items.append((it.get("objectid"), _mat(it.get("transform"))))
    if plate in plates and plates[plate]:
        wanted = set(plates[plate])
        sel = [i for i in items if i[0] in wanted]
        if sel:
            items = sel
    elif len(plates) > 1:
        raise ModelError(f"plade {plate} findes ikke i 3MF")

    parts: dict[int, tuple[list[float], list[int]]] = {}

    def add(fil: int, pts, tris):
        v, t = parts.setdefault(fil, ([], []))
        base = len(v) // 3
        v += pts
        t += [base + i for i in tris]

    budget_left = {"inst": MAX_INSTANCES, "tris": MAX_RAW_TRIANGLES, "verts": MAX_RAW_VERTS}

    def emit(objs, oid, mat, top_oid, depth=0):
        o = objs.get(oid)
        if o is None:
            return
        if depth > MAX_DEPTH:
            raise ModelError("3MF for dybt indlejret")
        budget_left["inst"] -= 1
        if budget_left["inst"] < 0:
            raise ModelError("3MF har for mange instanser")
        if o[0] == "mesh":
            _, verts, tris = o
            budget_left["verts"] -= len(verts) // 3
            budget_left["tris"] -= len(tris) // 3
            if budget_left["verts"] < 0 or budget_left["tris"] < 0:
                raise ModelError("3MF har for mange trekanter")
            pts: list[float] = []
            for i in range(0, len(verts), 3):
                pts += _apply(mat, verts[i], verts[i + 1], verts[i + 2])
            rs = ranges.get(top_oid)
            if rs and len(rs) > 1:
                byfil: dict[int, list[int]] = {}
                for fi in range(len(tris) // 3):
                    f = next((r[2] for r in rs if r[0] <= fi <= r[1]), ext.get(top_oid, 1))
                    byfil.setdefault(f, []).extend(tris[fi * 3:fi * 3 + 3])
                for f, tl in byfil.items():
                    add(f, pts, tl)
            else:
                add(ext.get(top_oid, 1), pts, tris)
        else:
            for path, cid, cm in o[1]:
                emit(objects_of(path) if path else objs, cid, _compose(cm, mat), top_oid, depth + 1)

    for oid, mat in items:
        emit(own, oid, mat, oid)
    total = sum(len(t) // 3 for _, t in parts.values())
    if total == 0:
        raise ModelError("3MF uden geometri")

    # Plate alignment: Studio may store plate-relative or global coordinates; trust plate_N.json when present.
    allv = [c for v, _ in parts.values() for c in v]
    minx, miny, minz = min(allv[0::3]), min(allv[1::3]), min(allv[2::3])
    dx = dy = 0.0
    pj = f"Metadata/plate_{plate}.json"
    if pj in names:
        try:
            bb = json.loads(read_limited(z, pj, MAX_TEXT)).get("bbox_all")
            if bb and (abs(bb[0] - minx) > 1.0 or abs(bb[1] - miny) > 1.0):
                dx, dy = bb[0] - minx, bb[1] - miny
        except (ValueError, TypeError, IndexError, AttributeError, ModelError):
            pass
    dz = -minz if abs(minz) > 0.01 else 0.0

    budget = MAX_TRIANGLES
    out_parts = []
    for fil in sorted(parts):
        v, t = parts[fil]
        share = max(2000, int(budget * (len(t) // 3) / total))
        v, t = _decimate(v, t, share)
        v = [c + (dx, dy, dz)[i % 3] for i, c in enumerate(v)]
        out_parts.append({"filament": fil, "positions": v, "indices": t})
    allv = [c for p in out_parts for c in p["positions"]]
    bbox = [min(allv[0::3]), min(allv[1::3]), min(allv[2::3]), max(allv[0::3]), max(allv[1::3]), max(allv[2::3])]
    meta = {"kind": "mesh", "approx": False, "bed": list(bed), "bbox": [round(x, 3) for x in bbox],
            "colors": colors or {}, "plate": plate, "name": name}
    return pack_model(meta, out_parts)


# ----------------------------------------------------------------------------- PNG (stdlib only)
def decode_png(data: bytes) -> tuple[int, int, int, bytes]:
    """(width, height, channels, raw pixel bytes) for non-interlaced 8-bit PNG. Raises ModelError on bad input."""
    try:
        return _decode_png(data)
    except ModelError:
        raise
    except (IndexError, ValueError, zlib.error, struct.error) as e:
        raise ModelError("ugyldig PNG") from e


def _decode_png(data: bytes) -> tuple[int, int, int, bytes]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ModelError("ikke en PNG")
    pos = 8
    idat = bytearray()
    w = h = bd = ct = inter = 0
    plte = b""
    trns = b""
    while pos + 8 <= len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            if len(body) != 13:
                raise ModelError("ugyldig PNG")
            w, h, bd, ct, _, _, inter = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            plte = body
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
    if bd != 8 or inter != 0 or w < 1 or h < 1 or w * h > MAX_PNG_PIXELS:
        raise ModelError("PNG-format ikke understøttet")
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ct)
    if not ch:
        raise ModelError("PNG-farvetype ikke understøttet")
    stride = w * ch
    expect = h * (1 + stride)
    d = zlib.decompressobj()
    raw = d.decompress(bytes(idat), expect + 1)
    if len(raw) != expect or d.unconsumed_tail:
        raise ModelError("ugyldig PNG")
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]
        if ft > 4:
            raise ModelError("ugyldig PNG")
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    if ct == 3:  # palette -> RGBA
        rgba = bytearray()
        for idx in out:
            if idx * 3 + 3 > len(plte):
                raise ModelError("ugyldig PNG")
            r, g, b = plte[idx * 3:idx * 3 + 3]
            a = trns[idx] if idx < len(trns) else 255
            rgba += bytes((r, g, b, a))
        return w, h, 4, bytes(rgba)
    return w, h, ch, bytes(out)


def _mask(w: int, h: int, ch: int, px: bytes) -> list[bytearray]:
    """Silhouette mask rows from a top-view image: alpha if there is one, otherwise 'not near white'."""
    has_alpha = ch in (2, 4)
    rows = []
    for y in range(h):
        row = bytearray(w)
        base = y * w * ch
        for x in range(w):
            i = base + x * ch
            if has_alpha:
                row[x] = 1 if px[i + ch - 1] > 24 else 0
            else:
                row[x] = 1 if min(px[i:i + min(ch, 3)]) < 245 else 0
        rows.append(row)
    return rows


def silhouette_from_config(config_3mf: bytes, plate: int, height_mm: float, colors: dict | None = None,
                           name: str = "", bed=DEFAULT_BED) -> bytes:
    """Extruded top-view silhouette of a plate, from the geometry-less cloud 3MF (top_N.png, 2 px/mm over the bed)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(config_3mf))
    except zipfile.BadZipFile as e:
        raise ModelError("ikke en 3MF") from e
    check_zip(z)
    names = set(z.namelist())
    top = f"Metadata/top_{plate}.png"
    if top not in names:
        raise ModelError("ingen oversigtsbillede")
    w, h, ch, px = decode_png(read_limited(z, top, MAX_COVER))
    rows = _mask(w, h, ch, px)
    if not any(any(r) for r in rows):
        raise ModelError("tomt oversigtsbillede")
    # The image covers the whole bed. Work at 1 px/mm-ish (2x2 blocks) so the mesh stays small.
    scale = w / bed[0]                  # px per mm (2.0 for 512 px / 256 mm)
    step = max(1, int(round(scale)))    # block size in px -> 1 mm cells
    cw, chh = w // step, h // step
    cells = []
    for cy in range(chh):
        line = bytearray(cw)
        for cx in range(cw):
            n = 0
            for dy in range(step):
                r = rows[cy * step + dy]
                for dx in range(step):
                    n += r[cx * step + dx]
            line[cx] = 1 if n * 2 >= step * step else 0
        cells.append(line)
    cell_mm = step / scale
    height = max(0.4, float(height_mm))

    def X(cx):  # image column edge -> mm
        return cx * cell_mm

    def Y(cy):  # image row edge -> mm (image y points to the front of the printer)
        return bed[1] - cy * cell_mm

    verts: list[float] = []
    tris: list[int] = []
    vidx: dict[tuple, int] = {}

    def vid(x, y, zc):
        k = (x, y, zc)
        i = vidx.get(k)
        if i is None:
            i = len(verts) // 3
            vidx[k] = i
            verts.extend((round(x, 3), round(y, 3), zc))
        return i

    def quad(a, b, c, d):
        tris.extend((a, b, c, a, c, d))

    def filled(cx, cy):
        return 0 <= cx < cw and 0 <= cy < chh and cells[cy][cx]

    for cy in range(chh):
        cx = 0
        while cx < cw:
            if not cells[cy][cx]:
                cx += 1
                continue
            x0 = cx
            while cx < cw and cells[cy][cx]:
                cx += 1
            xa, xb, ya, yb = X(x0), X(cx), Y(cy), Y(cy + 1)    # ya > yb
            # top face (normal +z, counter-clockwise seen from above)
            quad(vid(xa, yb, height), vid(xb, yb, height), vid(xb, ya, height), vid(xa, ya, height))
            # bottom face
            quad(vid(xa, yb, 0), vid(xa, ya, 0), vid(xb, ya, 0), vid(xb, yb, 0))
            # walls on the row's front/back
            if not filled_row(cells, cy - 1, x0, cx):   # back side (+y)
                quad(vid(xb, ya, 0), vid(xa, ya, 0), vid(xa, ya, height), vid(xb, ya, height))
            if not filled_row(cells, cy + 1, x0, cx):   # front side (-y)
                quad(vid(xa, yb, 0), vid(xb, yb, 0), vid(xb, yb, height), vid(xa, yb, height))
            quad(vid(xa, ya, 0), vid(xa, yb, 0), vid(xa, yb, height), vid(xa, ya, height))   # left wall
            quad(vid(xb, yb, 0), vid(xb, ya, 0), vid(xb, ya, height), vid(xb, yb, height))   # right wall
    if not tris:
        raise ModelError("tom silhuet")
    bb = [min(verts[0::3]), min(verts[1::3]), 0.0, max(verts[0::3]), max(verts[1::3]), height]
    meta = {"kind": "silhouette", "approx": True, "bed": list(bed), "bbox": [round(x, 3) for x in bb],
            "colors": colors or {}, "plate": plate, "name": name}
    return pack_model(meta, [{"filament": 1, "positions": verts, "indices": tris}])


def filled_row(cells, cy, x0, x1) -> bool:
    """Is the whole span x0..x1 of row cy filled (so the wall between the rows is hidden)?"""
    if cy < 0 or cy >= len(cells):
        return False
    row = cells[cy]
    return all(row[x] for x in range(x0, x1))


# ----------------------------------------------------------------------------- slice metadata
def slice_colors(z_or_bytes) -> dict[str, str]:
    """{filament id (1-based, as in the project): 'RRGGBB'} for the filaments a plate uses, from slice_info.config."""
    try:
        z = zipfile.ZipFile(io.BytesIO(z_or_bytes)) if isinstance(z_or_bytes, (bytes, bytearray)) else z_or_bytes
        data = read_limited(z, "Metadata/slice_info.config", MAX_TEXT)
    except Exception:
        return {}
    out = {}
    for m in re.finditer(rb'<filament\b[^>]*>', data):
        tag = m.group(0).decode("utf-8", "replace")
        fid = re.search(r'\bid="(\d+)"', tag)
        col = re.search(r'\bcolor="#?([0-9A-Fa-f]{6})', tag)
        if fid and col:
            out[fid.group(1)] = col.group(1).upper()
    return out


def plate_info(z_or_bytes, plate: int) -> dict:
    try:
        z = zipfile.ZipFile(io.BytesIO(z_or_bytes)) if isinstance(z_or_bytes, (bytes, bytearray)) else z_or_bytes
        return json.loads(read_limited(z, f"Metadata/plate_{plate}.json", MAX_TEXT))
    except Exception:
        return {}
