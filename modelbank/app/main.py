"""Modelbank: a library of STL/3MF model files for Home Assistant that works with FilamentHub."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import config, filamenthub as fhm, library, prints
from .db import Database

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("modelbank")
STATIC = Path(__file__).parent / "static" / "dist"

db = Database(config.DATA_DIR / "modelbank.db")
fh = fhm.Client(db)
scanner = library.Scanner(db)
print_sync = prints.PrintSync(db, fh)
DEFAULT_MARGIN = {"mode": "pct", "pct": 50, "fixed": None, "kg_price": 120}


@asynccontextmanager
async def lifespan(app):
    scanner.start()
    print_sync.start()
    yield
    scanner.stop()
    print_sync.stop()


app = FastAPI(title="Modelbank", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def guard(request: Request, call_next):
    peer = request.client.host if request.client else ""
    if config.ADDON and peer != config.INGRESS_IP:
        return JSONResponse({"detail": "Kun via Home Assistant"}, status_code=403)
    if request.method not in ("GET", "HEAD"):
        site = request.headers.get("sec-fetch-site")
        if site and site not in ("same-origin", "none"):
            return JSONResponse({"detail": "Forespørgslen kom fra en anden side"}, status_code=403)
    resp = await call_next(request)
    if request.url.path.startswith("/api/") and "cache-control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _err(e: Exception, code: int = 400):
    raise HTTPException(code, str(e))


def _model(mid: int) -> dict:
    m = db.model(mid)
    if not m:
        raise HTTPException(404, "Modellen findes ikke")
    return m


def _folder_of(m: dict) -> str:
    return m["relpath"].rsplit("/", 1)[0] if "/" in m["relpath"] else ""


def _card(m: dict, pc: dict | None = None) -> dict:
    meta = m["meta"] or {}
    pc = pc if pc is not None else db.print_counts().get(m["id"]) or {}
    return {"id": m["id"], "name": m["name"], "file": m["relpath"].rsplit("/", 1)[-1], "folder": _folder_of(m),
            "relpath": m["relpath"], "size": m["size"], "added": m["added"], "mtime": m["mtime"],
            "kind": meta.get("kind"), "tags": m["tags"], "favorite": m["favorite"], "designer": m["designer"],
            "thumb": m["thumb"], "mesh": bool(meta.get("mesh")), "grams": _grams(m), "seconds": _seconds(m),
            "filaments": [{"type": f.get("type"), "color": f.get("color")} for f in meta.get("filaments") or []],
            "plates": len(meta.get("plates") or []) or 1, "dims": meta.get("dims"), "error": meta.get("error"),
            "printed": pc.get("n", 0), "photo": pc.get("photo")}


def _grams(m: dict):
    return (m.get("manual") or {}).get("grams") or (m["meta"] or {}).get("grams")


def _seconds(m: dict):
    man = (m.get("manual") or {}).get("minutes")
    return man * 60 if man else (m["meta"] or {}).get("seconds")


def _library_or_none():
    if not fh.configured():
        return None, None
    try:
        return fh.library(), None
    except fhm.FHError as e:
        return None, str(e)


# ----------------------------------------------------------------------------- models
@app.get("/api/models")
def list_models(q: str = Query("", max_length=200), folder: str | None = Query(None, max_length=500),
                tag: str = Query("", max_length=100), favorite: bool = False,
                sort: Literal["added", "name", "printed", "size"] = "added", sub: bool = True):
    rows = db.models()
    counts = db.print_counts()
    words = [w for w in q.lower().split() if w]
    out = []
    for m in rows:
        f = _folder_of(m)
        if folder is not None and folder != "" and not (f == folder or (sub and f.startswith(folder + "/"))):
            continue
        if folder == "" and not sub and f:
            continue
        if tag and tag not in m["tags"]:
            continue
        if favorite and not m["favorite"]:
            continue
        if words:
            meta = m["meta"] or {}
            text = " ".join([m["name"], m["relpath"], m["designer"], m["notes"], " ".join(m["tags"]),
                             " ".join(x.get("type") or "" for x in meta.get("filaments") or []),
                             meta.get("title") or ""]).lower()
            if not all(w in text for w in words):
                continue
        out.append(_card(m, counts.get(m["id"]) or {}))
    if sort == "name":
        out.sort(key=lambda c: c["name"].lower())
    elif sort == "printed":
        out.sort(key=lambda c: -(c["printed"] or 0))
    elif sort == "size":
        out.sort(key=lambda c: -c["size"])
    return {"items": out, "total": len(out), "fh_error": print_sync.error if fh.configured() else None,
            "last_scan": db.get("last_scan")}


def _margin() -> dict:
    return {**DEFAULT_MARGIN, **(db.get("margin") or {})}


def _detail(m: dict, margin_override: dict | None = None, copies: int = 1) -> dict:
    card = _card(m)
    meta = m["meta"] or {}
    out = {**card, "notes": m["notes"], "source_url": m["source_url"], "manual": m["manual"], "links": m["links"],
           "meta": {k: meta.get(k) for k in ("title", "description", "plates", "filaments", "sliced", "triangles",
                                              "mesh_error", "dims", "kind")},
           "fh": {"configured": fh.configured(), "error": None}, "stock": None, "history": None, "price": None}
    entries: list[dict] = []
    spools = None
    if fh.configured():
        try:
            spools = fh.spools()
            entries = fh.library()
            prints.sync(db, fh, only=m["id"], entries=entries)
            m = db.model(m["id"])
        except fhm.FHError as e:
            out["fh"]["error"] = str(e)
        except OSError as e:
            log.info("sync %s: %s", m["id"], e)
    live = {str(e.get("task_id")): e for e in entries}
    hist = []
    for p in db.prints(m["id"]):
        e = live.get(p["task_id"]) or {}
        hist.append({**p, "energy": e.get("energy"), "spools": e.get("spools") or [], "in_fh": bool(e)})
    out["history"] = hist
    out["links"] = m["links"]
    out["printed"] = len(hist)
    out["photos"] = [{"task_id": h["task_id"], "end": h.get("end"), "title": h.get("title"), "plate": h.get("plate")}
                     for h in hist if h.get("photo")]
    if spools is None:
        return out
    fils = [dict(f) for f in meta.get("filaments") or []]
    man = m["manual"] or {}
    last = next((h for h in hist if h.get("status") == "finish" and h.get("weight")), None)
    grams_src = "file" if any(f.get("grams") for f in fils) else None
    if man.get("grams"):
        total = sum(f.get("grams") or 0 for f in fils)
        if total > 0:
            scale = float(man["grams"]) / total
            for f in fils:
                f["grams"] = round((f.get("grams") or 0) * scale, 2)
        else:
            fils = [{**(fils[0] if fils else {"id": 1, "type": man.get("material") or "", "color": ""}),
                     "grams": float(man["grams"])}] + fils[1:]
        grams_src = "manual"
    elif not grams_src and last:
        # STL / unsliced 3MF: take the weight of the last finished print of it
        if fils:
            share = round(float(last["weight"]) / len(fils), 2)
            for f in fils:
                f["grams"] = share
        else:
            mats = last.get("materials") or []
            fils = [{"id": 1, "type": (mats[0].get("type") if mats else "") or "", "color": (mats[0].get("color") if mats else "") or "",
                     "grams": float(last["weight"])}]
        grams_src = "print"
    if not fils and man.get("material"):
        fils = [{"id": 1, "type": man["material"], "color": "", "grams": None}]
    stock = fhm.stock_for(fils, spools)
    out["stock"] = stock
    out["grams_source"] = grams_src
    seconds = (man.get("minutes") or 0) * 60 or meta.get("seconds") or (last or {}).get("seconds")
    mg = {**_margin(), **(margin_override or {})}
    if grams_src and seconds:
        copies = max(1, min(int(copies or 1), 500))
        lines = [{"grams": (s.get("grams") or 0) * copies, "material": s.get("type") or "",
                  "kg_price": (s.get("spool") or {}).get("kg_price") or mg.get("kg_price")} for s in stock]
        hours = seconds * copies / 3600
        try:
            power, window = fh.power(), fh.price_window(hours)
        except fhm.FHError as e:
            power = window = None
            out["fh"]["error"] = str(e)
        out["price"] = {**fhm.price(lines, hours, power, window, mg), "hours": round(hours, 2), "copies": copies,
                        "margin": mg, "lines": lines}
    else:
        out["price_missing"] = "gram" if not grams_src else "tid"
    return out


@app.get("/api/models/{mid}")
def get_model(mid: int):
    return _detail(_model(mid))


class PriceBody(BaseModel):
    mode: Literal["pct", "fixed"] | None = None
    pct: float | None = Field(None, ge=-100, le=10000)
    fixed: float | None = Field(None, ge=0, le=1_000_000)
    copies: int = Field(1, ge=1, le=500)


@app.post("/api/models/{mid}/price")
def model_price(mid: int, body: PriceBody):
    over = {k: v for k, v in body.model_dump(exclude={"copies"}).items() if v is not None}
    return _detail(_model(mid), over, body.copies)


class ModelPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=300)
    tags: list[str] | None = Field(None, max_length=50)
    notes: str | None = Field(None, max_length=20000)
    designer: str | None = Field(None, max_length=300)
    source_url: str | None = Field(None, max_length=2000)
    favorite: bool | None = None
    folder: str | None = Field(None, max_length=500)
    manual: dict | None = None


@app.patch("/api/models/{mid}")
def patch_model(mid: int, body: ModelPatch):
    _model(mid)
    data = body.model_dump(exclude_unset=True)
    folder = data.pop("folder", None)
    if "tags" in data:
        data["tags"] = sorted({t.strip()[:60] for t in data["tags"] if t and t.strip()}, key=str.lower)
    if "source_url" in data and data["source_url"] and not data["source_url"].startswith(("http://", "https://")):
        raise HTTPException(400, "Kilde-link skal starte med http:// eller https://")
    if "manual" in data:
        man = {}
        for k in ("grams", "minutes"):
            v = (data["manual"] or {}).get(k)
            if v not in (None, ""):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise HTTPException(400, f"Ugyldig værdi for {k}")
                if not 0 < v < 100_000:
                    raise HTTPException(400, f"Ugyldig værdi for {k}")
                man[k] = v
        mat = str((data["manual"] or {}).get("material") or "").strip()[:40]
        if mat:
            man["material"] = mat
        data["manual"] = man
    db.set_fields(mid, data)
    if folder is not None:
        try:
            library.move(db, mid, folder)
        except (library.LibraryError, OSError) as e:
            _err(e)
    return _detail(_model(mid))


@app.delete("/api/models/{mid}")
def delete_model(mid: int):
    _model(mid)
    try:
        library.trash(db, mid)
    except (library.LibraryError, OSError) as e:
        _err(e)
    return {"ok": True}


class LinkBody(BaseModel):
    task_id: str = Field(min_length=1, max_length=100)
    linked: bool = True


@app.post("/api/models/{mid}/link")
def link_print(mid: int, body: LinkBody):
    m = _model(mid)
    links, unlinks = set(m["links"]), set(m["unlinks"])
    if body.linked:
        links.add(body.task_id)
        unlinks.discard(body.task_id)
    else:
        links.discard(body.task_id)
        unlinks.add(body.task_id)
    db.set_fields(mid, {"links": sorted(links), "unlinks": sorted(unlinks)})
    return _detail(_model(mid))   # _detail syncs this model: stores the print + photo, or drops them


@app.get("/api/models/{mid}/file")
def model_file(mid: int):
    m = _model(mid)
    p = library.full(m["relpath"])
    if not p.is_file():
        raise HTTPException(404, "Filen findes ikke længere")
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


@app.get("/api/models/{mid}/mesh")
def model_mesh(mid: int):
    _model(mid)
    p = library.mesh_path(mid)
    if not p.is_file():
        raise HTTPException(404, "Ingen 3D-model")
    return FileResponse(p, media_type="application/octet-stream", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/models/{mid}/thumb")
def model_thumb(mid: int):
    _model(mid)
    p = library.thumb_path(mid)
    if not p.is_file():
        raise HTTPException(404, "Intet billede")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "private, max-age=600"})


@app.get("/api/models/{mid}/photos/{task_id}.jpg")
def model_photo(mid: int, task_id: str):
    _model(mid)
    try:
        p = prints.photo_path(mid, task_id)
    except ValueError:
        raise HTTPException(404, "Findes ikke")
    if not p.is_file():
        raise HTTPException(404, "Intet foto")
    media = "image/png" if p.read_bytes()[:4] == b"\x89PNG" else "image/jpeg"
    return FileResponse(p, media_type=media, headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/models/{mid}/thumb")
async def upload_thumb(mid: int, request: Request):
    if request.headers.get("content-type", "").split(";")[0] != "image/png":
        raise HTTPException(415, "PNG påkrævet")
    data = await request.body()
    try:
        library.set_client_thumb(db, mid, data)
    except library.LibraryError as e:
        _err(e)
    return {"ok": True}


@app.post("/api/upload")
async def upload(request: Request, name: str = Query(min_length=1, max_length=300), folder: str = Query("", max_length=500)):
    if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
        raise HTTPException(415, "Send filen som application/octet-stream")
    try:
        tmp, target = library.begin_upload(folder, name)
    except library.LibraryError as e:
        _err(e)
    total = 0
    try:
        with open(tmp, "wb") as f:
            async for c in request.stream():
                total += len(c)
                if total > config.MAX_UPLOAD:
                    raise HTTPException(413, "Filen er for stor (maks 1 GB)")
                f.write(c)
        mid = await run_in_threadpool(library.finish_upload, db, tmp, target)
    except library.LibraryError as e:
        _err(e)
    finally:
        tmp.unlink(missing_ok=True)
    # a file added after it was printed: link it to those prints (and fetch their photos) right away
    try:
        await run_in_threadpool(prints.sync, db, fh, mid)
    except (fhm.FHError, OSError) as e:
        log.info("upload sync: %s", e)
    return _card(db.model(mid))


@app.post("/api/scan")
def scan_now():
    fh.clear()
    r = library.scan(db)
    print_sync.kick()
    return r


@app.get("/api/folders")
def get_folders():
    counts: dict[str, int] = {}
    for m in db.models():
        f = _folder_of(m)
        while True:
            counts[f] = counts.get(f, 0) + 1
            if not f:
                break
            f = f.rsplit("/", 1)[0] if "/" in f else ""
    return {"folders": [{"path": f, "count": counts.get(f, 0)} for f in library.folders()], "total": counts.get("", 0)}


class FolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=500)


@app.post("/api/folders")
def new_folder(body: FolderBody):
    try:
        return {"path": library.create_folder(body.path)}
    except (library.LibraryError, OSError) as e:
        _err(e)


@app.get("/api/tags")
def tags():
    c: dict[str, int] = {}
    for m in db.models():
        for t in m["tags"]:
            c[t] = c.get(t, 0) + 1
    return {"tags": [{"tag": t, "count": n} for t, n in sorted(c.items(), key=lambda x: x[0].lower())]}


# ----------------------------------------------------------------------------- settings + FilamentHub
class SettingsBody(BaseModel):
    fh_url: str | None = Field(None, max_length=300)
    fh_token: str | None = Field(None, max_length=500)
    margin: dict | None = None


@app.get("/api/settings")
def get_settings():
    s = fh.settings()
    return {"fh_url": s["url"], "fh_url_default": config.default_filamenthub_url(), "fh_token_set": bool(s["token"]),
            "margin": _margin(), "models_dir": str(config.MODELS_DIR), "scan_minutes": config.SCAN_MINUTES}


@app.post("/api/settings")
def set_settings(body: SettingsBody):
    cur = db.get("fh", {}) or {}
    if body.fh_url is not None:
        u = body.fh_url.strip().rstrip("/")
        if u and not u.startswith(("http://", "https://")):
            raise HTTPException(400, "Adressen skal starte med http://")
        cur["url"] = u
    if body.fh_token is not None:
        cur["token"] = body.fh_token.strip()
    db.set("fh", cur)
    if body.margin is not None:
        mg = _margin()
        for k in ("mode", "pct", "fixed", "kg_price"):
            if k in body.margin:
                mg[k] = body.margin[k]
        if mg["mode"] not in ("pct", "fixed"):
            raise HTTPException(400, "Ugyldig avance")
        for k in ("pct", "fixed", "kg_price"):
            if mg[k] not in (None, ""):
                try:
                    mg[k] = float(mg[k])
                except (TypeError, ValueError):
                    raise HTTPException(400, f"Ugyldig værdi: {k}")
            else:
                mg[k] = None
        db.set("margin", mg)
    fh.clear()
    return {**get_settings(), "fh": fh.status()}


@app.get("/api/fh/status")
def fh_status():
    return fh.status()


@app.get("/api/fh/library")
def fh_library(q: str = Query("", max_length=200)):
    entries, err = _library_or_none()
    if err or entries is None:
        raise HTTPException(503, err or "FilamentHub er ikke sat op")
    words = [w for w in q.lower().split() if w]
    items = [{"task_id": str(e.get("task_id")), "title": e.get("title") or "", "end": e.get("end"),
              "weight": e.get("weight"), "has_photo": e.get("has_photo"), "has_cover": e.get("has_cover"),
              "plate": e.get("plate")}
             for e in entries if all(w in (e.get("title") or "").lower() for w in words)]
    return {"items": items[:300]}


@app.get("/api/fh/print/{task_id}/{name}")
def fh_print_file(task_id: str, name: Literal["photo.jpg", "cover.png"]):
    if not task_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(404, "Findes ikke")
    try:
        r = fh.raw(f"/api/library/{task_id}/{name}")
    except fhm.FHError as e:
        raise HTTPException(404, str(e))
    return Response(r.content, media_type=r.headers.get("content-type", "image/jpeg"),
                    headers={"Cache-Control": "private, max-age=86400"})


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ----------------------------------------------------------------------------- frontend
if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/")
def index():
    p = STATIC / "index.html"
    if not p.is_file():
        return Response("Frontend ikke bygget", media_type="text/plain", status_code=503)
    return FileResponse(p, media_type="text/html", headers={"Cache-Control": "no-store"})


def run():
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level=config.LOG_LEVEL.lower(), access_log=False)
