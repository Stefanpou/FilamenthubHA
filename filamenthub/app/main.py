"""FastAPI web app: JSON API + mobile web UI."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config, fx
from .bambu_cloud import BambuCloud, CloudError, NeedCode, NeedTfa
from .barcode import normalize_code
from .catalog import Catalog, same_product_line
from .codedb import CodeIndex
from .fm import FilamentManager, FmBusy
from .db import Database
from .lookup import lookup as lookup_code
from .printer_state import color_distance, slot_label
from . import slicer
from .spoolman import DRIED_FIELD, FILAMENT_FIELDS, TAG_FIELD, SpoolmanError, extra_value, nominal_weight, spool_display
from .store import LocalStore
from .sync import COLOR_MATCH, Engine, material_match

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("spoolsync")

STATIC = Path(__file__).parent / "static"
DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PA": 1.14, "PC": 1.20,
           "PVA": 1.23, "HIPS": 1.04, "PET": 1.38, "PPS": 1.35, "PP": 0.9}
MATERIALS = ["PLA", "PLA+", "PETG", "ABS", "ASA", "TPU", "PA", "PA-CF", "PC", "PETG-CF", "PLA-CF", "PVA", "HIPS", "PET"]

db = Database(config.DB_PATH)
spoolman = LocalStore(config.DATA_DIR)   # FilamentHub's own inventory (name kept: engine/fm/tests call it "spoolman")
codeidx = CodeIndex(config.DATA_DIR)
catalog = Catalog(spoolman, codeidx)
engine = Engine(db, spoolman)
fm = FilamentManager(db, spoolman, engine)
engine.assignment_hook = fm.on_assignment
engine.deduction_hook = fm.trigger
engine.fm_enabled = lambda: fm.enabled
engine.fm_poke = fm.trigger
_login_lock = threading.Lock()
_pending_login: dict = {"cloud": None, "email": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        spoolman.ensure_extra_field()
        spoolman.ensure_extra_field(DRIED_FIELD, "Tørret", "datetime")
        for key, name in FILAMENT_FIELDS.items():
            spoolman.ensure_extra_field(key, name, entity="filament")
        spoolman.ensure_extra_field(slicer.SLICER_FIELD, "Slicer-indstillinger", entity="filament")
    except SpoolmanError as e:
        log.warning("Inventory not ready at startup: %s", e)
    spoolman.start()
    engine.start()
    codeidx.start()
    threading.Thread(target=catalog.warm, name="catalog-warm", daemon=True).start()
    fm.start()
    yield
    engine.stop()
    codeidx.stop()
    spoolman.stop()
    fm.stop()


app = FastAPI(title="Spool Sync", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

OPEN_PATHS = {"/api/auth/state", "/api/auth/login", "/api/auth/setup", "/healthz"}


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    # Never echo the submitted values (passwords; strings that cannot even be encoded back).
    return JSONResponse({"detail": [{"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", ""))}
                                    for e in exc.errors()]}, status_code=422)


@app.exception_handler(OverflowError)
async def _overflow(request: Request, exc: OverflowError):
    return JSONResponse({"detail": "Ugyldigt tal"}, status_code=400)   # e.g. an id too large for the database


MAX_BODY = 1_000_000   # the largest real request (an FM create of every spool) is far below this


class _BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Bodies are read before the session is checked (login, setup): never buffer more than MAX_BODY bytes."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        if config.ADDON and scope.get("path") == "/api/setup/import":
            return await self.inner(scope, receive, send)   # streamed to disk with its own limit (api_setup.IMPORT_MAX)
        length = dict(scope.get("headers") or []).get(b"content-length", b"")
        if length.isdigit() and int(length) > MAX_BODY:
            return await JSONResponse({"detail": "For stor forespørgsel"}, status_code=413)(scope, receive, send)
        seen = 0

        async def limited():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BODY:
                    raise _BodyTooLarge()   # (a chunked body without a length: FastAPI answers 400)
            return message
        await self.inner(scope, limited, send)


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        # The media type itself must be JSON ("text/plain; charset=application/json" is a CORS-safelisted type that
        # a cross-site form or no-cors fetch can send without a preflight).
        media = request.headers.get("content-type", "").split(";")[0].strip().lower()
        # /api/v1 (Spoolman-compatible API): non-browser clients (HA, OrcaSlicer) may send DELETE/PUT without a body or
        # media type; a browser always sends Sec-Fetch-Site, which is checked below.
        v1 = path == auth.API_PREFIX or path.startswith(auth.API_PREFIX + "/")
        # The add-on data import (/api/setup/import) is a gzip upload; a cross-site page cannot send that type without
        # a CORS preflight, so this keeps the same protection as requiring JSON.
        upload = path == "/api/setup/import" and media == "application/gzip"
        if (request.method not in ("GET", "HEAD") and media != "application/json" and not upload
                and not (v1 and "sec-fetch-site" not in request.headers)):
            return JSONResponse({"detail": "JSON påkrævet"}, status_code=415)
        site = request.headers.get("sec-fetch-site")
        if request.method not in ("GET", "HEAD") and site and site not in ("same-origin", "none"):
            return JSONResponse({"detail": "Forespørgslen kom fra en anden side"}, status_code=403)
        # Home Assistant kiosk: a read-only ?token= is accepted on a few GET paths only (homeassistant.KIOSK_PATHS).
        # /api/v1 also works without a session for a direct LAN client or with the API bearer token (never via a proxy).
        if (path not in OPEN_PATHS and auth.login_required(db) and not auth.is_ingress(request)
                and not auth.valid_session(db, request.cookies.get(auth.COOKIE))
                and not (v1 and (auth.has_valid_api_token(db, request) or auth.is_direct_lan(request)))
                and not api_ha.ha.kiosk_allows(db, request)):
            return JSONResponse({"detail": "Log ind"}, status_code=401)
    response = await call_next(request)
    if path.startswith("/api/") or path in ("/", "/index.html", "/sw.js"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    # Framing: only ourselves + the configured Home Assistant origins (replaces X-Frame-Options; see api_ha/settings).
    csp = api_ha.ha.content_security_policy(db)
    if auth.is_ingress(request):
        # HA Ingress: HA (any origin/URL) frames us and the page carries a <base href> for the Ingress prefix.
        csp = csp.replace("base-uri 'none'", "base-uri 'self'") + " http: https:"
    response.headers["Content-Security-Policy"] = csp
    return response


app.add_middleware(BodyLimit)   # outermost: before anything reads the body


def _err(e: Exception, status: int = 400):
    raise HTTPException(status_code=status, detail=str(e))


# ============================================================================ app auth
class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)


MIN_PASSWORD = 10   # for new passwords (setup / change); an existing shorter one keeps working at login


class ChangePasswordBody(BaseModel):
    current: str
    new: str = Field(min_length=MIN_PASSWORD, max_length=200)


def _login_response(request: Request, payload: dict) -> JSONResponse:
    resp = JSONResponse(payload)
    resp.set_cookie(auth.COOKIE, auth.make_session(db), max_age=auth.SESSION_MAX_AGE, httponly=True,
                    samesite="lax", secure=auth.is_https(request), path="/")
    return resp


@app.get("/api/auth/state")
def auth_state(request: Request):
    return {
        "password_set": auth.password_set(db),
        "login_required": auth.login_required(db),
        "authenticated": (not auth.login_required(db) or auth.is_ingress(request)
                          or auth.valid_session(db, request.cookies.get(auth.COOKIE))),
        "setup_allowed": auth.is_local(request),
        "ingress": auth.is_ingress(request),   # inside Home Assistant: HA does the login, no app password
    }


@app.post("/api/auth/setup")
def auth_setup(body: PasswordBody, request: Request):
    if auth.password_set(db):
        raise HTTPException(409, "Adgangskode er allerede oprettet")
    if not auth.is_local(request):
        raise HTTPException(403, "Første opsætning skal ske fra dit hjemmenetværk")
    if len(body.password) < MIN_PASSWORD:
        raise HTTPException(400, f"Mindst {MIN_PASSWORD} tegn")
    auth.set_password(db, body.password)
    db.event("info", "Adgangskode til appen oprettet")
    return _login_response(request, {"ok": True})


@app.post("/api/auth/login")
def auth_login(body: PasswordBody, request: Request):
    if not auth.begin_attempt(request):   # (counted before the scrypt check: parallel bursts cannot exceed the limit)
        raise HTTPException(429, "For mange forsøg – vent 15 minutter")
    if not auth.check_password(db, body.password):
        raise HTTPException(401, "Forkert adgangskode")
    auth.forgive(request)
    return _login_response(request, {"ok": True})


@app.post("/api/auth/logout")
def auth_logout():
    auth.bump_sessions(db)   # server-side: the cookie of every device stops working, not just this browser's
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.post("/api/auth/password")
def auth_change(body: ChangePasswordBody, request: Request):
    if not auth.check_password(db, body.current):
        raise HTTPException(401, "Nuværende adgangskode er forkert")
    auth.set_password(db, body.new)
    db.event("info", "Adgangskode til appen ændret (alle enheder logget ud)")
    return _login_response(request, {"ok": True})


class LoginRequiredBody(BaseModel):
    required: bool
    password: str = Field(default="", max_length=200)


@app.post("/api/auth/required")
def auth_required(body: LoginRequiredBody, request: Request):
    """Switch the app login off (only from a logged-in session while it is on) or back on (needs the password, so
    nobody locks themselves out with a password they do not know)."""
    if body.required:
        if not auth.password_set(db):
            raise HTTPException(409, "Opret en adgangskode først")
        if not auth.begin_attempt(request):
            raise HTTPException(429, "For mange forsøg – vent 15 minutter")
        if not auth.check_password(db, body.password):
            raise HTTPException(401, "Forkert adgangskode")
        auth.forgive(request)
        db.set("login_required", True)
        db.event("info", "Login til appen slået til")
        return _login_response(request, {"ok": True, "login_required": True})
    db.set("login_required", False)
    db.event("warning", "Login til appen slået fra – alle med adgang til adressen kan bruge appen")
    return {"ok": True, "login_required": False}


# ============================================================================ status
def _spools_by_id(allow_archived: bool = False) -> dict[int, dict]:
    spools = {s["id"]: spool_display(s) for s in spoolman.spools(allow_archived=allow_archived)}
    weighed = db.get("weighed", {}) or {}
    for sid, sp in spools.items():
        sp["weighed"] = weighed.get(str(sid))   # when its weight was last set by hand (epoch), None = never
    try:
        live = engine.live_usage()
    except Exception:
        log.exception("live usage estimate failed")
        live = {}
    for sid, grams in live.items():
        # The print in progress, counted down as it goes (an estimate until the print is processed).
        if sid in spools:
            sp = spools[sid]
            sp["printing"] = round(grams, 1)
            sp["remaining"] = round(max(0.0, sp["remaining"] - grams), 1)
            sp["used"] = round(sp["used"] + grams, 1)
    return spools


@app.get("/api/status")
def status():
    st = engine.status()
    try:
        spools = _spools_by_id()
        st["spoolman_ok"] = True
    except SpoolmanError as e:
        spools = {}
        st["spoolman_ok"] = False
        st["spoolman_error"] = str(e)
    assignments = st.pop("assignments")
    assigned_ids = {a["spool_id"] for a in assignments.values()}
    slot_keys = {s["key"] for s in st["slots"]}
    for s in st["slots"]:
        a = assignments.get(s["key"])
        s["assignment"] = None
        if a:
            sp = spools.get(a["spool_id"])
            if sp is None:
                # missing = deleted/archived in Spoolman; if Spoolman is just down we cannot tell.
                sp = {"id": a["spool_id"], "name": f"Spole #{a['spool_id']}", "missing": st["spoolman_ok"],
                      "unavailable": not st["spoolman_ok"], "vendor": "", "material": "", "color": "", "colors": [],
                      "remaining": 0, "initial": 0}
            s["assignment"] = {"spool": sp, "since": a["start_ts"], "source": a["source"]}
            mismatch = []
            if s["present"] and s["material"] and not material_match(sp.get("material", ""), s["material"]):
                mismatch.append(f"printeren siger {s['material']}, spolen er {sp.get('material')}")
            if s["present"] and s["color"] and sp.get("color") and color_distance(sp["color"], s["color"]) > 120:
                mismatch.append("farven i printeren passer ikke med spolen")
            s["mismatch"] = mismatch
        slot_rfids = {x.upper() for x in (s["tray_uuid"], s["tag_uid"]) if x}
        # Only offer 'Opret i lager' for a Bambu spool no spool in stock carries the tag of (the engine assigns a
        # known one by itself).
        s["unknown_rfid"] = bool(slot_rfids) and s["kind"] != "ext" and not any(
            (sp.get("tag") or "").upper() in slot_rfids for sp in spools.values())
        s["suggestions"] = _suggest(s, spools, assigned_ids) if s["present"] and not a else []
    # Assignments for slots the printer does not currently report (e.g. AMS unplugged).
    st["orphans"] = [
        {"slot": k, "label": slot_label(k), "spool": spools.get(a["spool_id"])}
        for k, a in assignments.items() if k not in slot_keys
    ]
    st["spoolman_url"] = ""   # (no Spoolman any more; key kept so the response shape is unchanged)
    return st


def _suggest(slot: dict, spools: dict[int, dict], assigned_ids: set[int]) -> list[int]:
    scored = []
    for sp in spools.values():
        if sp["id"] in assigned_ids or sp["archived"]:
            continue
        if slot["material"] and not material_match(sp["material"], slot["material"]):
            continue
        dist = color_distance(sp["color"], slot["color"]) if slot["color"] and sp["color"] else 60.0
        if dist > COLOR_MATCH:
            continue
        # Prefer close colour, then spools already in use (lower remaining) over fresh ones.
        scored.append((round(dist / 10), sp["remaining"] >= sp["initial"] - 1, sp["id"]))
    scored.sort()
    return [x[2] for x in scored[:6]]


@app.get("/api/events")
def events(limit: int = 100):
    return db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))


@app.post("/api/sync-now")
def sync_now():
    result = engine.process_tasks()
    if engine.mqtt:
        engine.mqtt.request_pushall()
    return result


# ============================================================================ spools
class NewSpool(BaseModel):
    # Source of the filament definition (at most one is used, in this order):
    filament_id: int | None = None                         # an existing Spoolman filament
    external_id: str | None = Field(default=None, max_length=200)  # a SpoolmanDB record
    # Filament fields (required unless filament_id is given)
    vendor: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=100)  # colour / product name, e.g. "Matte Black"
    material: str | None = Field(default=None, max_length=40)
    color_hex: str | None = Field(default=None, pattern=r"^#?[0-9A-Fa-f]{6}$")
    color_hexes: list[str] | None = Field(default=None, max_length=8)
    multi_color_direction: str | None = Field(default=None, pattern=r"^(coaxial|longitudinal)$")
    weight: float | None = Field(default=None, gt=0, le=20000)  # net filament weight when full
    spool_weight: float | None = Field(default=None, ge=0, le=5000)
    density: float | None = Field(default=None, gt=0, le=5)
    diameter: float | None = Field(default=None, gt=0, le=5)
    extruder_temp: int | None = Field(default=None, ge=0, le=500)
    bed_temp: int | None = Field(default=None, ge=0, le=200)
    # What the filament databases know besides (stored on the filament)
    article_number: str | None = Field(default=None, max_length=100)
    ean: str | None = Field(default=None, max_length=40)
    color_name: str | None = Field(default=None, max_length=100)
    finish: str | None = Field(default=None, max_length=60)
    spool_type: str | None = Field(default=None, max_length=40)
    properties: list[str] | None = Field(default=None, max_length=20)
    nozzle_range: str | None = Field(default=None, max_length=30)   # recommended range, e.g. "230-250"
    bed_range: str | None = Field(default=None, max_length=30)
    td: float | None = Field(default=None, gt=0, le=100)            # HueForge transmission distance
    slicer: dict | None = None   # {"profile": slicer profile name, "settings": {key: value}}; None = best match
    category: str | None = Field(default=None, max_length=40)   # kind by hand ("Basic", "-" = plain); None/"" = automatic
    # Spool fields
    remaining_weight: float | None = Field(default=None, ge=0, le=20000)
    price: float | None = Field(default=None, ge=0)
    comment: str | None = Field(default=None, max_length=500)
    count: int = Field(default=1, ge=1, le=20)
    slot: str | None = None
    barcode: str | None = Field(default=None, max_length=300)   # as long as a scanned code may be (lookup)
    dried: date | None = None                                 # when the spool was dried


class SpoolPatch(BaseModel):
    remaining_weight: float | None = Field(default=None, ge=0, le=20000)
    gross_weight: float | None = Field(default=None, ge=0, le=25000)
    archived: bool | None = None
    comment: str | None = Field(default=None, max_length=500)
    clear_tag: bool | None = None
    dried: date | None = None          # when the spool was dried
    clear_dried: bool | None = None
    spool_weight: float | None = Field(default=None, ge=0, le=5000)   # empty spool (e.g. after respooling)
    price: float | None = Field(default=None, ge=0, le=100000)        # what the spool cost (kr)
    clear_price: bool | None = None
    category: str | None = Field(default=None, max_length=40)        # the filament's kind ("Basic"); "" = automatic
    location: str | None = Field(default=None, max_length=120)      # a fixed location's bare name takes a free place
    reset_usage: bool | None = None    # start over at what is on the spool now (100 % left)


@app.get("/api/spools")
def list_spools(archived: bool = False):
    try:
        return sorted(_spools_by_id(allow_archived=archived).values(),
                      key=lambda s: (s["archived"], s["material"], s["vendor"], s["name"], s["id"]))
    except SpoolmanError as e:
        _err(e, 502)


@app.get("/api/meta")
def meta():
    try:
        vendors = sorted({v["name"] for v in spoolman.vendors() if v.get("name")}, key=str.lower)
        filaments = spoolman.filaments()
    except SpoolmanError as e:
        _err(e, 502)
    vendors = sorted(set(vendors) | {m["name"] for m in catalog.manufacturers()}, key=str.lower)
    materials = sorted({f.get("material") for f in filaments if f.get("material")} | set(MATERIALS)
                       | set(catalog.material_defaults().keys()), key=str.upper)
    templates = [
        {"vendor": (f.get("vendor") or {}).get("name") or "", "name": f.get("name") or "", "material": f.get("material") or "",
         "color_hex": (f.get("color_hex") or "")[:6], "weight": f.get("weight"), "spool_weight": f.get("spool_weight"),
         "density": f.get("density")}
        for f in filaments
    ]
    return {"vendors": vendors, "materials": materials, "templates": templates, "density": DENSITY,
            "spoolman_url": ""}


def _default_density(material: str) -> float:
    mat = (material or "").upper()
    defaults = catalog.material_defaults()
    for key in (mat, mat.split("-")[0], mat.rstrip("+"), mat.split("-")[0].rstrip("+")):
        if key in defaults and defaults[key].get("density"):
            return float(defaults[key]["density"])
        if key in DENSITY:
            return DENSITY[key]
    return 1.24


def _resolve_filament(body: NewSpool, avoid: int | None = None) -> dict:
    """Existing Spoolman filament for the request, creating it (from SpoolmanDB data or manual input) if needed.
    `avoid`: never this filament (a spool split off from the one it shares)."""
    if body.filament_id and body.filament_id != avoid:
        return spoolman.filament(body.filament_id)
    if not (body.vendor and body.name and body.material and body.weight):
        raise ValueError("Udfyld producent, navn/farve, materiale og vægt")
    existing = spoolman.filaments()
    if body.external_id:
        for f in existing:
            if f.get("external_id") == body.external_id and f["id"] != avoid:
                return _fill_filament(f, body)
    vendor = spoolman.get_or_create_vendor(body.vendor)
    color = (body.color_hex or "").lstrip("#").upper()
    multi = [c.lstrip("#").upper()[:6] for c in (body.color_hexes or []) if c]
    for f in existing:  # identical definition already in Spoolman -> reuse it
        if (f["id"] != avoid and (f.get("vendor") or {}).get("id") == vendor["id"]
                and (f.get("name") or "").strip().lower() == body.name.strip().lower()
                and (f.get("material") or "").strip().lower() == body.material.strip().lower()
                and (f.get("color_hex") or "").upper()[:6] == color[:6]
                and abs(float(f.get("weight") or 0) - body.weight) < 0.5):
            return _fill_filament(f, body)
    fields = {
        "vendor_id": vendor["id"], "name": body.name.strip(), "material": body.material.strip(),
        "density": body.density or _default_density(body.material), "diameter": body.diameter or 1.75,
        "weight": body.weight,
    }
    if len(multi) > 1:
        fields["multi_color_hexes"] = ",".join(multi)
        fields["multi_color_direction"] = body.multi_color_direction or "coaxial"
    elif color or multi:
        fields["color_hex"] = color or multi[0]
    if body.spool_weight is not None:
        fields["spool_weight"] = body.spool_weight
    # Temperatures not given: the material's typical ones (SpoolmanDB material list), when it has them.
    try:
        typical = catalog.material_defaults().get(body.material.strip().upper(), {})
    except Exception:   # the database list is not available: leave them empty
        typical = {}
    if body.extruder_temp or typical.get("extruder_temp"):
        fields["settings_extruder_temp"] = body.extruder_temp or typical["extruder_temp"]
    if body.bed_temp or typical.get("bed_temp"):
        fields["settings_bed_temp"] = body.bed_temp or typical["bed_temp"]
    if body.external_id and avoid is None:   # (a split-off copy leaves the database link to the original)
        fields["external_id"] = body.external_id
    if (body.article_number or "").strip():
        fields["article_number"] = body.article_number.strip()
    extra = _filament_extra(body)
    if extra:
        fields["extra"] = extra
    return spoolman.create_filament(**fields)


def _filament_extra(body: NewSpool) -> dict:
    """The database facts beyond Spoolman's columns, as Spoolman extra values (JSON text)."""
    vals = {"ean": (body.ean or "").strip(), "color_name": (body.color_name or "").strip(),
            "finish": (body.finish or "").strip(), "spool_type": (body.spool_type or "").strip(),
            "properties": ", ".join(p.strip() for p in body.properties or [] if p and p.strip()),
            "nozzle_range": (body.nozzle_range or "").strip(), "bed_range": (body.bed_range or "").strip(),
            "td": f"{body.td:g}" if body.td else "", "category": " ".join((body.category or "").split())}
    out = {k: json.dumps(v) for k, v in vals.items() if v}
    choice = _slicer_choice(body)
    if choice:
        out[slicer.SLICER_FIELD] = json.dumps(json.dumps(choice))
    return out


def _edit_filament(fil: dict, body: NewSpool) -> dict:
    """Overwrite a filament with the edit form (every spool of it changes); the slicer choice only when sent."""
    if not (body.vendor and body.name and body.material and body.weight):
        raise ValueError("Udfyld producent, navn/farve, materiale og vægt")
    color = (body.color_hex or "").lstrip("#").upper()[:6]
    multi = [c.lstrip("#").upper()[:6] for c in (body.color_hexes or []) if c]
    fields = {
        "vendor_id": spoolman.get_or_create_vendor(body.vendor)["id"], "name": body.name.strip(),
        "material": body.material.strip(), "weight": body.weight, "diameter": body.diameter or 1.75,
        "density": body.density or fil.get("density") or _default_density(body.material),
        "settings_extruder_temp": body.extruder_temp, "settings_bed_temp": body.bed_temp,
        "article_number": (body.article_number or "").strip() or None,
    }
    if len(multi) > 1:
        fields.update(multi_color_hexes=",".join(multi), multi_color_direction=body.multi_color_direction or "coaxial")
    elif color:
        fields.update(color_hex=color, multi_color_hexes=None)
    if body.spool_weight is not None:
        fields["spool_weight"] = body.spool_weight
    new = _filament_extra(body.model_copy(update={"slicer": body.slicer if body.slicer is not None else {}}))
    fields["extra"] = {k: new.get(k) for k in FILAMENT_FIELDS} | (
        {slicer.SLICER_FIELD: new.get(slicer.SLICER_FIELD)} if body.slicer is not None else {})
    out = spoolman.update_filament(fil["id"], **fields)
    spoolman.invalidate()
    return out


def _printer_model() -> str:
    return slicer.model_code((engine.device or {}).get("model"))


def _slicer_choice(body: NewSpool) -> dict | None:
    """The slicer profile picked in the form (with its edits), else the best match for this filament."""
    prof = slicer.by_name(str((body.slicer or {}).get("profile") or ""))
    if prof:
        edits = {str(k): str(v) for k, v in (body.slicer.get("settings") or {}).items()
                 if k in slicer.OVERRIDE and v not in (None, "")}
        return {"profile": prof["n"], "settings": {**prof["s"], **edits}}
    if body.slicer is not None:   # {} = none wanted
        return None
    hits = slicer.match(body.vendor, body.name, body.material, _printer_model(), limit=1)
    return {"profile": hits[0]["n"], "settings": dict(hits[0]["s"])} if hits else None


def _fill_filament(fil: dict, body: NewSpool) -> dict:
    """A filament that already exists gets what it lacks from this request (never overwrites)."""
    upd: dict = {}
    if (body.article_number or "").strip() and not (fil.get("article_number") or "").strip():
        upd["article_number"] = body.article_number.strip()
    for col, val in (("settings_extruder_temp", body.extruder_temp), ("settings_bed_temp", body.bed_temp),
                     ("spool_weight", body.spool_weight), ("density", body.density)):
        if val and fil.get(col) is None:
            upd[col] = val
    have = fil.get("extra") or {}
    extra = {k: v for k, v in _filament_extra(body).items() if not have.get(k)}
    if extra:
        upd["extra"] = extra
    if not upd:
        return fil
    try:
        return spoolman.update_filament(fil["id"], **upd)
    except SpoolmanError as e:
        log.warning("could not complete filament %s: %s", fil["id"], e)
        return fil


def _remember_barcode(code: str, fil: dict) -> None:
    """Store the product barcode on the filament (Spoolman 'article_number') and locally, for one-tap re-scans."""
    code = normalize_code(code)
    if not code:
        return
    if not (fil.get("article_number") or "").strip():
        try:
            spoolman.update_filament(fil["id"], article_number=code)
        except SpoolmanError as e:
            log.warning("could not store article number: %s", e)
    codeidx.learn_prefix(code, (fil.get("vendor") or {}).get("name") or "")
    db.execute("INSERT INTO barcodes(code, filament_id, created_ts, filament_registered) VALUES(?,?,?,?) "
               "ON CONFLICT(code) DO UPDATE SET filament_id=excluded.filament_id, "
               "filament_registered=excluded.filament_registered",
               (code, fil["id"], time.time(), fil.get("registered")))   # (the stamp tells a reused number)


def _created_anyway(filament_id: int, known: set[int]) -> dict | None:
    """The one new spool of this filament that Spoolman created although its answer never arrived, if any."""
    spoolman.invalidate()
    new = [s for s in spoolman.spools(allow_archived=True)
           if s["id"] not in known and (s.get("filament") or {}).get("id") == filament_id]
    return new[0] if len(new) == 1 else None


@app.post("/api/spools")
def create_spool(body: NewSpool):
    if body.slot:
        _check_slot(body.slot)   # before anything is created
    try:
        fil = _resolve_filament(body)
        weight = body.weight or float(fil.get("weight") or 1000)
        spool_weight = body.spool_weight if body.spool_weight is not None else fil.get("spool_weight")
        if spool_weight is None:   # not given and not in the databases: the typical spool of this vendor / size
            est = catalog.spool_weight_estimate((fil.get("vendor") or {}).get("name") or body.vendor or "", weight)
            spool_weight = est["grams"] if est else None
        created = []
        known = {s["id"] for s in spoolman.spools(allow_archived=True)}
        for _ in range(body.count):
            fields = {"filament_id": fil["id"], "initial_weight": weight}
            if spool_weight is not None:
                fields["spool_weight"] = spool_weight
            if body.remaining_weight is not None:
                fields["remaining_weight"] = body.remaining_weight
            if body.price is not None:
                fields["price"] = body.price
            if body.comment:
                fields["comment"] = body.comment
            if body.dried:
                fields["extra"] = {DRIED_FIELD: json.dumps(_dried_value(body.dried))}
            try:
                sp = spoolman.create_spool(**fields)
            except SpoolmanError as e:
                # The answer was lost (no status): Spoolman may have created the spool anyway - use that one
                # instead of letting a second tap create a twin.
                sp = None
                if not (e.retryable or e.status):
                    try:
                        sp = _created_anyway(fil["id"], known)
                    except SpoolmanError:
                        pass   # cannot tell: report it as not created
                if sp is None:
                    if created:
                        raise ValueError(f"{len(created)} af {body.count} spoler blev oprettet "
                                         f"(#{', #'.join(str(s['id']) for s in created)}), resten fejlede: {e}") from e
                    raise
            created.append(sp)
            known.add(sp["id"])
            if body.remaining_weight is not None:
                # 'Tilbage nu' is a weighing: prints that ended before are already in it.
                engine.note_weighed(created[-1]["id"])
        if body.barcode:
            try:
                _remember_barcode(body.barcode, fil)
            except ValueError as e:   # the spools exist now: a barcode problem must not fail the request
                log.warning("barcode %r not remembered: %s", body.barcode, e)
        vendor_name = (fil.get("vendor") or {}).get("name") or body.vendor or ""
        db.event("info", f"{len(created)} spole(r) oprettet: {vendor_name} {fil.get('material')} {fil.get('name')}")
        if body.slot and created:
            engine.manual_assign(body.slot, created[0]["id"])
        fm.trigger()
        return [spool_display(s) for s in created]
    except (SpoolmanError, ValueError) as e:
        _err(e)


# ============================================================================ catalogue (SpoolmanDB + own)
@app.get("/api/catalog/manufacturers")
def catalog_manufacturers():
    return {"manufacturers": catalog.manufacturers(), "error": catalog.last_error}


@app.get("/api/catalog/search")
def catalog_search(manufacturer: str = "", material: str = "", q: str = "", limit: int = 60, source: str = ""):
    return catalog.search(manufacturer=manufacturer, material=material, q=q, limit=limit, source=source)


@app.get("/api/catalog/spool-weight")
def catalog_spool_weight(manufacturer: str = Query("", max_length=100), weight: float | None = Query(None, gt=0, le=20000),
                         spool_type: str = Query("", max_length=20)):
    """What an empty spool of this manufacturer / size most likely weighs ({grams, source, label} or null)."""
    return {"estimate": catalog.spool_weight_estimate(manufacturer, weight, spool_type)}


@app.get("/api/fx/eur")
def fx_eur():
    """EUR -> DKK rate for typing prices in euro."""
    return fx.eur_dkk(db)


@app.get("/api/catalog/vendors")
def catalog_vendors():
    """Same list as /api/catalog/manufacturers, as {"vendors": [...]}: the user's own vendors first, then A-Z."""
    return {"vendors": catalog.manufacturers(), "error": catalog.last_error}


# ============================================================================ Bambu Filament Manager sync
MAX_ID = 2 ** 63 - 1   # SQLite INTEGER


class FmSettings(BaseModel):
    enabled: bool | None = None
    auto_create: bool | None = None


class FmLink(BaseModel):
    fm_id: int = Field(ge=0, le=MAX_ID)
    spool_id: int | None = Field(default=None, ge=1, le=MAX_ID)
    force: bool = False


class FmCreate(BaseModel):
    spool_ids: list[Annotated[int, Field(ge=1, le=MAX_ID)]] = Field(min_length=1, max_length=5000)   # the spools the user confirmed (all missing ones)


def _fm_error(e: Exception):
    if isinstance(e, FmBusy):
        raise HTTPException(409, str(e))
    if isinstance(e, ValueError):
        raise HTTPException(409, str(e))
    raise HTTPException(400, str(e))


@app.get("/api/fm")
def fm_overview(refresh: bool = False):
    if refresh or (not fm.fm_spools and engine.cloud):
        try:
            fm.fetch()
        except CloudError as e:
            fm.last_error = str(e)
    return {**fm.overview(), "shadow": config.SHADOW}


@app.post("/api/fm/settings")
def fm_settings(body: FmSettings):
    if body.enabled is not None:
        db.set("fm_enabled", body.enabled)
        db.event("info", f"Synkronisering med Bambu Filament Manager {'slået til' if body.enabled else 'slået fra'}")
    if body.auto_create is not None:
        db.set("fm_auto_create", body.auto_create)
    if fm.enabled:
        fm.trigger()
    return fm.overview()


@app.post("/api/fm/sync")
def fm_sync_now():
    result = fm.sync()
    return {"result": result, **fm.overview()}


@app.post("/api/fm/link")
def fm_link(body: FmLink):
    if not body.spool_id:
        raise HTTPException(400, "Vælg en spole")
    try:
        fm.link(body.spool_id, body.fm_id, "manuel", force=body.force)
    except (FmBusy, ValueError) as e:
        _fm_error(e)
    return fm.overview()


@app.post("/api/fm/unlink")
def fm_unlink(body: FmLink):
    try:
        fm.unlink(body.fm_id)
    except FmBusy as e:
        _fm_error(e)
    return fm.overview()


@app.post("/api/fm/ignore")
def fm_ignore(body: FmLink):
    try:
        fm.ignore(body.fm_id)
    except FmBusy as e:
        _fm_error(e)
    return fm.overview()


@app.post("/api/fm/unignore")
def fm_unignore(body: FmLink):
    try:
        fm.unignore(body.fm_id)
    except FmBusy as e:
        _fm_error(e)
    fm.trigger()
    return fm.overview()


@app.post("/api/fm/import")
def fm_import(body: FmLink):
    try:
        sid = fm.import_to_spoolman(body.fm_id, catalog.external())
    except (CloudError, SpoolmanError, FmBusy, ValueError) as e:
        _fm_error(e)
    fm.trigger()
    return {"spool_id": sid, **fm.overview()}


@app.post("/api/fm/create")
def fm_create(body: FmCreate):
    created, errors = 0, []
    for sid in body.spool_ids:
        if sid in fm.links():
            continue
        try:
            fm.create_for(sid)
            created += 1
        except ValueError as e:
            errors.append(f"#{sid}: {e}")   # this spool only (e.g. a similar row exists): go on with the rest
        except (CloudError, SpoolmanError, FmBusy) as e:
            errors.append(f"#{sid}: {e}")
            break
    fm.trigger()
    return {"created": created, "errors": errors, **fm.overview()}


@app.post("/api/fm/remove-created")
def fm_remove_created():
    try:
        n = fm.remove_created()
    except (CloudError, FmBusy) as e:
        _fm_error(e)
    return {"removed": n, **fm.overview()}


@app.post("/api/fm/confirm-empty")
def fm_confirm_empty():
    """The user confirms that every spool really was deleted in Bambu (after a long empty list)."""
    try:
        fm.confirm_empty()
    except (CloudError, FmBusy, ValueError) as e:
        _fm_error(e)
    return fm.overview()


class CodeLookup(BaseModel):
    code: str = Field(min_length=1, max_length=300)


@app.post("/api/barcode/lookup")
def barcode_lookup(body: CodeLookup):
    return lookup_code(body.code, db, spoolman, catalog, codeidx)


def _dried_value(d: date) -> str:
    """Spoolman datetime value for a drying date (noon, so no time zone moves it to another day)."""
    if d > date.today() + timedelta(days=1):
        raise HTTPException(422, "Tørret-datoen kan ikke ligge i fremtiden")
    return f"{d.isoformat()}T12:00:00Z"


@app.patch("/api/spools/{spool_id}")
def patch_spool(spool_id: int, body: SpoolPatch):
    try:
        if body.gross_weight is not None:
            spoolman.measure(spool_id, body.gross_weight)
            engine.note_weighed(spool_id)
            db.event("info", f"Spole #{spool_id} vejet: {body.gross_weight:.0f} g brutto")
        if body.reset_usage:
            sp = spoolman.reset_usage(spool_id)
            db.event("info", f"Spole #{spool_id}: forbrug nulstillet – starter nu på {sp['remaining_weight']:.0f} g")
        fields = {}
        if body.remaining_weight is not None:
            fields["remaining_weight"] = body.remaining_weight
        if body.location is not None:
            fields["location"] = locations.resolve(spoolman, db, spool_id, body.location)
        if body.archived is not None:
            fields["archived"] = body.archived
        if body.comment is not None:
            fields["comment"] = body.comment
        if body.spool_weight is not None:
            fields["spool_weight"] = body.spool_weight
        if body.price is not None or body.clear_price:
            fields["price"] = None if body.clear_price else body.price
        if fields:
            spoolman.update_spool(spool_id, **fields)
            if "remaining_weight" in fields:
                engine.note_weighed(spool_id)   # only once Spoolman really has the new weight
                db.event("info", f"Spole #{spool_id} rettet til {body.remaining_weight:.0f} g tilbage")
        if body.category is not None:
            fil = (spoolman.spool(spool_id) or {}).get("filament") or {}
            cat = " ".join(body.category.split())
            spoolman.update_filament(fil["id"], extra={"category": json.dumps(cat) if cat else None})
            spoolman.invalidate()
        if body.clear_tag:
            spoolman.set_extra(spool_id, TAG_FIELD, None)
        if body.dried is not None or body.clear_dried:
            spoolman.set_extra(spool_id, DRIED_FIELD, None if body.clear_dried else _dried_value(body.dried))
        if body.archived:
            for slot, a in db.current_assignments().items():
                if a["spool_id"] == spool_id:
                    engine.manual_unassign(slot)
        fm.trigger()
        return spool_display(spoolman.spool(spool_id))
    except SpoolmanError as e:
        _err(e, 502)


class FilamentChange(NewSpool):
    only_this: bool = False   # the filament is shared: change just this spool (it gets its own filament)


@app.post("/api/spools/{spool_id}/filament")
def change_filament(spool_id: int, body: FilamentChange):
    """Edit the spool's filament in place (filament_id = its own), or move the spool to another one (picked in the
    database or typed in), which is found or created like when adding a spool. The spool's weights stay.
    only_this: a filament other spools share is left alone - this spool moves to a filament of its own."""
    try:
        cur = (spoolman.spool(spool_id) or {}).get("filament") or {}
        shared = any(s["id"] != spool_id and (s.get("filament") or {}).get("id") == cur.get("id")
                     for s in spoolman.spools(allow_archived=True))
        edited = False
        if body.only_this and shared:
            fil = _resolve_filament(body.model_copy(update={"filament_id": None}), avoid=cur.get("id"))
        elif body.filament_id and body.filament_id == cur.get("id"):
            fil, edited = _edit_filament(cur, body), True
        else:
            fil = _resolve_filament(body)
        if not edited and body.category is not None:   # (a reused filament keeps its fields, but gets the category)
            cat = " ".join(body.category.split())
            fil = spoolman.update_filament(fil["id"], extra={"category": json.dumps(cat) if cat else None})
        if fil["id"] != cur.get("id"):
            spoolman.update_spool(spool_id, filament_id=fil["id"])
        what = "rettet" if fil["id"] == cur.get("id") else "delt fra og sat" if body.only_this and shared else "skiftet"
        db.event("info", f"Spole #{spool_id}: filament {what} til {(fil.get('vendor') or {}).get('name', '')} "
                         f"{fil.get('material', '')} {fil.get('name', '')}".rstrip())
        fm.trigger()
        spoolman.invalidate()
        return spool_display(spoolman.spool(spool_id))
    except (SpoolmanError, ValueError) as e:
        _err(e)


# ============================================================================ slots
class AssignBody(BaseModel):
    spool_id: int
    force: bool = False


@app.post("/api/slots/{slot}/assign")
def assign(slot: str, body: AssignBody):
    _check_slot(slot)
    try:
        engine.manual_assign(slot, body.spool_id, force=body.force)
    except (SpoolmanError, ValueError) as e:
        _err(e)
    return {"ok": True}


@app.post("/api/slots/{slot}/unassign")
def unassign(slot: str):
    _check_slot(slot)
    engine.manual_unassign(slot)
    return {"ok": True}


_tray_lock = threading.Lock()   # one 'Opret i lager' at a time (two devices / a double tap)


@app.post("/api/slots/{slot}/create-from-tray")
def create_from_tray(slot: str):
    """Create a Spoolman spool from the RFID data of a Bambu spool and assign it."""
    s = next((x for x in engine.current_slots() if x.key == slot), None)
    if not s or not s.present:
        raise HTTPException(400, "Der er ingen spole i slottet")
    with _tray_lock:
        try:
            return _create_from_tray(slot, s)
        except (SpoolmanError, ValueError) as e:
            _err(e)


def _create_from_tray(slot: str, s) -> dict:
    net = float(s.weight) if s.weight > 0 else 0.0   # the tray's spool size (0.5 kg for PVA / support)
    rfids = {x.upper() for x in (s.tray_uuid, s.tag_uid) if x}
    if rfids:
        # Already in stock (briefly unassigned, the request sent twice, or archived earlier): use that spool.
        spoolman.invalidate()
        known = next((x for x in spoolman.spools(allow_archived=True) if extra_value(x, TAG_FIELD).upper() in rfids), None)
        if known:
            if known.get("archived"):
                fields = {"archived": False}
                if 0 <= s.remain <= 100:
                    initial = float(known.get("initial_weight") or net or 1000)
                    fields["remaining_weight"] = round(initial * s.remain / 100.0)
                spoolman.update_spool(known["id"], **fields)
                if "remaining_weight" in fields:
                    engine.note_weighed(known["id"])
                db.event("info", f"Arkiveret spole #{known['id']} genbrugt fra {slot_label(slot)} (samme RFID)")
            # (One an earlier tap created although Spoolman's answer was lost: the assignment records its weighing.)
            engine.manual_assign(slot, known["id"])
            return spool_display(spoolman.spool(known["id"]))
    vendor = spoolman.get_or_create_vendor("Bambu Lab")
    name = s.sub_brand or s.material
    color = s.color or "FFFFFF"
    mat = s.material or "PLA"
    fil = None
    for f in spoolman.filaments():
        if ((f.get("vendor") or {}).get("id") == vendor["id"] and (f.get("material") or "").upper() == mat.upper()
                and (f.get("color_hex") or "")[:6].upper() == color
                and same_product_line(s.sub_brand, mat, f.get("name"))
                and (not net or abs(float(f.get("weight") or 0) - net) < 1)):
            fil = f
            break
    if fil is None:
        fil = spoolman.get_or_create_filament(vendor_id=vendor["id"], name=name, material=mat, color_hex=color,
                                              weight=net or 1000, spool_weight=250,
                                              density=DENSITY.get(mat.upper().split("-")[0].split(" ")[0], 1.24))
    weight = net or float(fil.get("weight") or 1000)
    fields = {"filament_id": fil["id"], "initial_weight": weight}
    if fil.get("spool_weight") is not None:
        fields["spool_weight"] = fil["spool_weight"]
    if 0 <= s.remain <= 100:
        fields["remaining_weight"] = round(weight * s.remain / 100.0)
    rfid = s.tray_uuid or s.tag_uid
    if rfid:
        fields["extra"] = {TAG_FIELD: f'"{rfid}"'}
    tag = rfid.upper() if rfid and "remaining_weight" in fields else None
    if tag:
        engine.note_tray_creating(tag, time.time())   # in case Spoolman's answer is lost
    try:
        sp = spoolman.create_spool(**fields)
    except SpoolmanError as e:
        if tag and (e.retryable or e.status):
            engine.note_tray_creating(tag, None)      # not reached, or it answered: nothing was created
        raise
    if "remaining_weight" in fields:
        # The AMS's remaining estimate already contains whatever was printed from the spool so far: count it as a
        # weighing now, so that print is not charged again when it is processed or picked later.
        engine.note_weighed(sp["id"])
    if tag:
        engine.note_tray_creating(tag, None)
    db.event("info", f"Ny Bambu-spole oprettet fra {slot_label(slot)}: {mat} {name}")
    engine.manual_assign(slot, sp["id"])
    return spool_display(spoolman.spool(sp["id"]))


def _check_slot(slot: str) -> None:
    m = re.fullmatch(r"ams:(\d{1,3}):(\d)|ext:25[45]", slot or "", re.ASCII)
    if not m or (m.group(1) is not None and (int(m.group(1)) > 135 or int(m.group(2)) > 3)):
        raise HTTPException(400, "Ugyldigt slot")


# ============================================================================ history
@app.get("/api/stats")
def stats():
    """Filament usage: what Spool Sync deducted (by week, month, material, spool), cost and a run-out forecast,
    plus Spoolman's all-time totals."""
    from datetime import datetime, timedelta
    from .sync import _same_time, parse_time
    now = time.time()
    rows = db.query("SELECT d.task_id, d.spool_id, d.grams, d.material, d.ts, d.spool_registered, p.end_time FROM deductions d "
                    "LEFT JOIN processed_tasks p ON p.task_id=d.task_id WHERE d.state='applied' AND d.grams > 0")
    spoolman_error = None
    try:
        raw = spoolman.spools(allow_archived=True)
    except SpoolmanError as e:
        raw, spoolman_error = [], str(e)
    by_id = {s["id"]: s for s in raw}

    def price_per_gram(s: dict | None) -> float | None:
        if not s:
            return None
        fil = s.get("filament") or {}
        if s.get("price") and float(nominal_weight(s) or 0) > 0:
            return float(s["price"]) / float(nominal_weight(s))
        if fil.get("price") and float(fil.get("weight") or 0) > 0:
            return float(fil["price"]) / float(fil["weight"])
        return None

    today = datetime.now().date()
    week0 = today - timedelta(days=today.weekday())                      # Monday of this week
    weeks = [week0 - timedelta(weeks=i) for i in range(11, -1, -1)]
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.insert(0, (y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    per_week = {w: 0.0 for w in weeks}
    per_month = {mo: 0.0 for mo in months}
    per_material: dict[str, float] = {}
    per_spool: dict[tuple, float] = {}       # (spool id, deleted) - a deleted spool's number may belong to a new one
    material_of: dict[tuple, str] = {}
    last30: dict[int, float] = {}
    first_use: dict[int, float] = {}
    total = cost = recent = 0.0
    priced = 0.0
    tasks = set()
    first = None
    for r in rows:
        when = parse_time(r["end_time"]) or r["ts"]
        if not 1.4e9 < when < now + 86400:
            when = r["ts"]   # a placeholder time from Bambu (year 0001 or 9999) is not a date
        g = float(r["grams"])
        total += g
        if now - when <= 30 * 86400:
            recent += g   # also a deleted spool's usage (it is kept out of the forecast only)
        tasks.add(r["task_id"])
        first = when if first is None else min(first, when)
        d = datetime.fromtimestamp(when).date()
        wk = d - timedelta(days=d.weekday())
        if wk in per_week:
            per_week[wk] += g
        if (d.year, d.month) in per_month:
            per_month[(d.year, d.month)] += g
        sp = by_id.get(r["spool_id"])
        deleted = bool(sp and r["spool_registered"] and sp.get("registered")
                       and not _same_time(sp["registered"], r["spool_registered"]))
        if deleted:
            sp = None   # charged to a spool that was deleted; Spoolman gave its number to another spool since
        mat = ((sp or {}).get("filament") or {}).get("material") or r["material"] or "?"
        per_material[mat] = per_material.get(mat, 0.0) + g
        if r["spool_id"]:
            key = (r["spool_id"], deleted)
            per_spool[key] = per_spool.get(key, 0.0) + g
            material_of.setdefault(key, r["material"] or "")
            if not deleted:
                first_use[r["spool_id"]] = min(first_use.get(r["spool_id"], when), when)
                if now - when <= 30 * 86400:
                    last30[r["spool_id"]] = last30.get(r["spool_id"], 0.0) + g
        ppg = price_per_gram(sp)
        if ppg is not None:
            cost += g * ppg
            priced += g

    def spool_info(sid: int, deleted: bool = False) -> dict:
        s = None if deleted else by_id.get(sid)
        d = spool_display(s) if s else {"name": "", "vendor": "", "material": material_of.get((sid, deleted), ""),
                                         "color": "", "colors": [], "remaining": 0, "archived": True}
        return {"id": sid, "name": d["name"], "vendor": d["vendor"], "material": d["material"], "color": d["color"],
                "colors": d.get("colors") or [], "remaining": d["remaining"], "archived": d.get("archived", False),
                "deleted": deleted or (s is None and not spoolman_error)}

    forecast = []
    for sid, g30 in last30.items():
        info = spool_info(sid)
        # The pace over the time the spool has really been in use (at most 30 days, at least 3).
        days = min(30.0, max(3.0, (now - first_use[sid]) / 86400))
        daily = g30 / days
        if info["archived"] or daily <= 0 or info["remaining"] <= 0:
            continue
        forecast.append({**info, "per_day": round(daily, 1), "days_left": round(info["remaining"] / daily)})
    forecast.sort(key=lambda x: x["days_left"])

    all_used: dict[str, float] = {}
    for s in raw:
        mat = (s.get("filament") or {}).get("material") or "?"
        all_used[mat] = all_used.get(mat, 0.0) + float(s.get("used_weight") or 0)
    return {
        "since": first, "prints": len(tasks), "total": round(total, 1),
        "last30": round(recent, 1),
        "cost": round(cost, 2) if priced else None, "cost_coverage": round(priced / total, 2) if total else 0,
        "weeks": [{"start": w.isoformat(), "grams": round(per_week[w], 1)} for w in weeks],
        "months": [{"month": f"{y:04d}-{m:02d}", "grams": round(per_month[(y, m)], 1)} for y, m in months],
        "materials": [{"material": k, "grams": round(v, 1)} for k, v in sorted(per_material.items(), key=lambda kv: -kv[1])],
        "spools": [{**spool_info(*key), "grams": round(g, 1)} for key, g in sorted(per_spool.items(), key=lambda kv: -kv[1])[:10]],
        "forecast": forecast[:6],
        "spoolman_error": spoolman_error,
        "spoolman": {
            "spools": len(raw), "active": sum(1 for s in raw if not s.get("archived")),
            "used": round(sum(all_used.values()), 1),
            "remaining": round(sum(float(s.get("remaining_weight") or 0) for s in raw if not s.get("archived")), 1),
            "materials": [{"material": k, "grams": round(v, 1)} for k, v in sorted(all_used.items(), key=lambda kv: -kv[1]) if v > 0],
        },
    }


@app.get("/api/history")
def history(limit: int = 50):
    limit = min(max(limit, 1), 300)
    tasks = db.query("SELECT * FROM processed_tasks ORDER BY processed_ts DESC LIMIT ?", (limit,))
    ids = [t["task_id"] for t in tasks]
    rows = db.query(f"SELECT * FROM deductions WHERE task_id IN ({','.join('?' * len(ids))}) ORDER BY id", tuple(ids)) if ids else []
    try:
        spools = _spools_by_id(allow_archived=True)
    except SpoolmanError:
        spools = {}
    from .sync import _same_time

    def spool_of(r: dict) -> dict | None:
        sp = spools.get(r["spool_id"]) if r["spool_id"] else None
        if sp and r.get("spool_registered") and sp.get("registered") and not _same_time(sp["registered"], r["spool_registered"]):
            r["spool_deleted"] = True   # that spool was deleted; Spoolman gave its number to another spool since
            return None
        return sp

    by_task: dict[str, list] = {}
    for r in rows:
        r["label"] = slot_label(r["slot"]) if r["slot"] != "unknown" else "Ukendt slot"
        r["spool"] = spool_of(r)
        by_task.setdefault(r["task_id"], []).append(r)
    from .power import energy_for
    energy = energy_for(db, ids)
    for t in tasks:
        t["deductions"] = by_task.get(t["task_id"], [])
        t["energy"] = energy.get(t["task_id"])
    pending = db.query("SELECT d.*, p.title FROM deductions d LEFT JOIN processed_tasks p ON p.task_id=d.task_id "
                       "WHERE d.state IN ('pending','uncertain') ORDER BY d.id")
    for r in pending:
        r["label"] = slot_label(r["slot"]) if r["slot"] != "unknown" else "Ukendt slot"
        r["spool"] = spool_of(r)
        if r.get("spool_deleted") and r["state"] == "pending":
            r["spool_id"] = None   # never offer the new spool with that number as the one to deduct from
    return {"tasks": tasks, "pending": pending}


class ResolveBody(BaseModel):
    spool_id: int | None = Field(default=None, ge=1, le=MAX_ID)
    grams: float | None = Field(default=None, ge=0, le=20000)
    gross_weight: float | None = Field(default=None, ge=0, le=25000)   # weighed instead of entering grams
    force: bool = False


@app.post("/api/deductions/{deduction_id}/resolve")
def resolve(deduction_id: int, body: ResolveBody):
    try:
        engine.resolve_deduction(deduction_id, body.spool_id, body.grams, gross_weight=body.gross_weight,
                                 force=body.force)
    except (SpoolmanError, ValueError) as e:
        _err(e)
    return {"ok": True}


@app.post("/api/deductions/{deduction_id}/checked")
def checked(deduction_id: int):
    engine.mark_checked(deduction_id)
    return {"ok": True}


# ============================================================================ Bambu login
class BambuLogin(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class CodeBody(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    email: str | None = None


class TokenBody(BaseModel):
    token: str = Field(min_length=20, max_length=5000)


class DeviceBody(BaseModel):
    dev_id: str


def _complete_login(cloud: BambuCloud, result, email: str, method: str) -> dict:
    engine.save_login(cloud, result, email, method)
    _pending_login.update(cloud=None, email=None)
    return {"next": "done"}


@app.post("/api/bambu/login")
def bambu_login(body: BambuLogin):
    with _login_lock:
        cloud = BambuCloud(config.BAMBU_REGION)
        _pending_login.update(cloud=cloud, email=body.email.strip())
        try:
            return _complete_login(cloud, cloud.login_password(body.email.strip(), body.password), body.email.strip(), "password")
        except NeedCode as e:
            return {"next": "code", "message": str(e)}
        except NeedTfa as e:
            return {"next": "tfa", "message": str(e)}
        except CloudError as e:
            _err(e)


class SendCode(BaseModel):
    email: str | None = Field(default=None, max_length=200)


@app.post("/api/bambu/send-code")
def bambu_send_code(body: SendCode = Body(default=SendCode())):
    with _login_lock:
        email = body.email or _pending_login.get("email")
        if not email:
            raise HTTPException(400, "Indtast din e-mail")
        cloud = _pending_login.get("cloud") or BambuCloud(config.BAMBU_REGION)
        _pending_login.update(cloud=cloud, email=email.strip())
        try:
            cloud.request_email_code(email.strip())
        except CloudError as e:
            _err(e)
        return {"next": "code", "message": "Koden er sendt til din e-mail"}


@app.post("/api/bambu/code")
def bambu_code(body: CodeBody):
    with _login_lock:
        email = (body.email or _pending_login.get("email") or "").strip()
        cloud = _pending_login.get("cloud") or BambuCloud(config.BAMBU_REGION)
        try:
            return _complete_login(cloud, cloud.login_code(body.code, email), email, "code")
        except CloudError as e:
            _err(e)


@app.post("/api/bambu/tfa")
def bambu_tfa(body: CodeBody):
    with _login_lock:
        cloud = _pending_login.get("cloud")
        if not cloud:
            raise HTTPException(400, "Start forfra med e-mail og adgangskode")
        try:
            return _complete_login(cloud, cloud.login_tfa(body.code), _pending_login.get("email") or "", "tfa")
        except NeedCode as e:
            return {"next": "code", "message": str(e)}
        except CloudError as e:
            _err(e)


@app.post("/api/bambu/token")
def bambu_token(body: TokenBody):
    with _login_lock:
        cloud = BambuCloud(config.BAMBU_REGION)
        try:
            return _complete_login(cloud, cloud.use_token(body.token), "", "token")
        except CloudError as e:
            _err(e)


@app.post("/api/bambu/logout")
def bambu_logout():
    engine.logout()
    return {"ok": True}


@app.get("/api/bambu/devices")
def bambu_devices():
    if not engine.cloud:
        raise HTTPException(400, "Ikke logget ind på Bambu")
    try:
        devs = engine.cloud.get_devices()
    except CloudError as e:
        _err(e)
    selected = (engine.device or {}).get("serial")
    return [{"dev_id": d.get("dev_id"), "name": d.get("name"), "model": d.get("dev_product_name"),
             "online": d.get("online"), "selected": d.get("dev_id") == selected} for d in devs]


@app.post("/api/bambu/device")
def bambu_device(body: DeviceBody):
    if not engine.cloud:
        raise HTTPException(400, "Ikke logget ind på Bambu")
    try:
        devs = engine.cloud.get_devices()
    except CloudError as e:
        _err(e)
    dev = next((d for d in devs if d.get("dev_id") == body.dev_id), None)
    if not dev:
        raise HTTPException(404, "Printer ikke fundet")
    engine.select_device(dev)
    return {"ok": True}


# ============================================================================ static
@app.get("/healthz")
def healthz():
    return {"ok": True, "mqtt": engine.mqtt_status}


DIST = STATIC / "dist"


@app.get("/")
@app.get("/ha")
def index(request: Request):
    # The new Svelte UI (frontend/ -> app/static/dist); the old UI stays as a fallback until it is built.
    page = DIST / "index.html"
    if not page.exists():
        return FileResponse(STATIC / "index.html")
    html = page.read_text(encoding="utf-8")
    # The build uses relative URLs ("api/...", "static/..."); <base> makes them resolve under the Ingress prefix.
    ingress = auth.ingress_path(request)
    html = html.replace('"./assets/', '"static/dist/assets/')
    if ingress:
        tag = f'<base href="{ingress}/">'
        if config.ADDON:
            tag += '<meta name="fh-addon" content="1">'
        html = html.replace("<head>", "<head>" + tag, 1)
    elif config.ADDON:   # (no <base> needed outside Ingress: the page is at "/" or "/ha", both resolve to "/")
        html = html.replace("<head>", '<head><meta name="fh-addon" content="1">', 1)
    return HTMLResponse(html)


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript")


# ============================================================================ routers
# One line per module. Each module owns its own APIRouter; add yours here and nowhere else in this file.
# (routers)
from . import api_setup; app.include_router(api_setup.router)
from . import live; app.include_router(live.router)
from . import api_spoolman; app.include_router(api_spoolman.router)
from . import api_ha; app.include_router(api_ha.router); api_ha.attach(app)
from . import power; app.include_router(power.router); power.attach(app)
from . import api_slicer; app.include_router(api_slicer.router)
from . import features; app.include_router(features.router); features.attach(app)
from . import locations; app.include_router(locations.router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")


class _NoQueryAccessLog(logging.Filter):
    """uvicorn's access log would write `?token=...` (kiosk / cover links) to the journal: log the path only."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            args[2] = args[2].split("?", 1)[0]
            record.args = tuple(args)
        return True


def make_server(host: str, port: int, log_level: str | None = None):
    import uvicorn
    # proxy_headers stays off: auth.py inspects the forwarded headers itself (and refuses forged ones).
    cfg = uvicorn.Config(app, host=host, port=port, proxy_headers=False, log_level=(log_level or config.LOG_LEVEL).lower())
    logging.getLogger("uvicorn.access").addFilter(_NoQueryAccessLog())   # (after Config: it configures the loggers)
    return uvicorn.Server(cfg)


def run() -> None:
    make_server(config.HOST, config.PORT).run()


if __name__ == "__main__":
    run()
