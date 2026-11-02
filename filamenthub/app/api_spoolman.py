"""Spoolman-compatible REST API at /api/v1 (same JSON shapes as Spoolman 0.26).

Used by Home Assistant's Spoolman integration, OrcaSlicer/PrusaSlicer, and the FilamentHub UI for plain CRUD.
Access (see `guard` in main.py): a session cookie, `Authorization: Bearer <api token>`, or a direct request from a
private LAN address (never through a reverse proxy / the Cloudflare tunnel).

Errors are Spoolman style: `{"message": "..."}` with the HTTP status (404 unknown id, 422 bad value, 409 conflict).
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import auth
from .spoolman import SpoolmanError

log = logging.getLogger("spoolsync.api_v1")
router = APIRouter()
V1 = "/api/v1"
SPOOLMAN_VERSION = "0.26.1"


def _main():
    from . import main   # (late import: main imports this module)
    return main


def _store():
    return _main().spoolman


def _msg(message: str, status: int) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


def _body(request: Request) -> dict:
    """The JSON body (already read by `_guard`, so the handlers can be plain `def` and run in the threadpool)."""
    raw = getattr(request.state, "raw", b"")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise SpoolmanError("Ugyldig JSON", status=422) from None
    if not isinstance(data, dict):
        raise SpoolmanError("Forventede et JSON-objekt", status=422)
    return data


def _guard(fn):
    """Turn SpoolmanError into a Spoolman-style error response."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*a, **k):
        req = k.get("request")
        try:
            if req is not None and req.method not in ("GET", "HEAD"):
                req.state.raw = await req.body()
            if inspect.iscoroutinefunction(fn):
                return await fn(*a, **k)
            return await run_in_threadpool(fn, *a, **k)   # sqlite work must not block the event loop
        except SpoolmanError as e:
            return _msg(str(e), e.status or (503 if e.retryable else 500))
        except (TypeError, ValueError) as e:
            return _msg(str(e), 422)
    return wrapper


# ---- list helpers (filters / sort / paging like Spoolman) ---------------------------------------------------------
def _get(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def _match(value: Any, wanted: str) -> bool:
    """Spoolman filter: comma separated alternatives, case-insensitive exact; an empty alternative matches null."""
    if value is None:
        return any(w == "" for w in wanted.split(","))
    v = str(value).strip().lower()
    return any(w.strip().lower() == v for w in wanted.split(","))


def _filter(rows: list[dict], request: Request, fields: tuple[str, ...], aliases: dict[str, str] | None = None) -> list[dict]:
    q = request.query_params
    for name in fields:
        if name in q:
            rows = [r for r in rows if _match(_get(r, name), q[name])]
    for alias, target in (aliases or {}).items():
        if alias in q:
            rows = [r for r in rows if _match(_get(r, target), q[alias])]
    return rows


def _sort_page(rows: list[dict], request: Request, default: str = "id:asc"):
    q = request.query_params
    for spec in reversed((q.get("sort") or default).split(",")):
        field, _, direction = spec.partition(":")
        desc = direction.lower() == "desc"
        have = [r for r in rows if _get(r, field.strip()) is not None]
        none = [r for r in rows if _get(r, field.strip()) is None]
        have.sort(key=lambda r: (str(_get(r, field.strip())).lower() if isinstance(_get(r, field.strip()), str)
                                 else _get(r, field.strip())), reverse=desc)
        rows = have + none
    total = len(rows)
    try:
        offset = max(int(q.get("offset", 0)), 0)
        limit = int(q["limit"]) if "limit" in q else None
    except ValueError:
        raise SpoolmanError("limit/offset skal være tal", status=422) from None
    rows = rows[offset:] if limit is None else rows[offset:offset + max(limit, 0)]
    return rows, total


def _listing(rows: list[dict], total: int) -> JSONResponse:
    return JSONResponse(rows, headers={"X-Total-Count": str(total), "Access-Control-Expose-Headers": "X-Total-Count"})


def _after_change() -> None:
    try:
        _main().fm.trigger()
    except Exception:   # never let a background nudge break an API call
        log.debug("fm.trigger failed", exc_info=True)


# ---- info / health ------------------------------------------------------------------------------------------------
@router.get(V1 + "/info")
def info():
    return {"version": SPOOLMAN_VERSION, "debug_mode": False, "automatic_backups": False,
            "data_dir": "", "logs_dir": "", "backups_dir": "",   # (no server paths)
            "db_type": "sqlite", "git_commit": "filamenthub", "build_date": "2026-01-01T00:00:00Z",
            "server": "FilamentHub"}


@router.get(V1 + "/health")
def health():
    return {"status": "healthy" if _store().health() else "unhealthy"}


# ---- vendors ------------------------------------------------------------------------------------------------------
@router.get(V1 + "/vendor")
@_guard
def list_vendors(request: Request):
    rows = _filter(_store().vendors(), request, ("name", "external_id"))
    rows, total = _sort_page(rows, request)
    return _listing(rows, total)


@router.post(V1 + "/vendor")
@_guard
def create_vendor(request: Request):
    v = _store().create_vendor(**_body(request))
    _after_change()
    return v


@router.get(V1 + "/vendor/{vendor_id}")
@_guard
def get_vendor(vendor_id: int):
    return _store().vendor(vendor_id)


@router.patch(V1 + "/vendor/{vendor_id}")
@_guard
def patch_vendor(vendor_id: int, request: Request):
    v = _store().update_vendor(vendor_id, **_body(request))
    _after_change()
    return v


@router.delete(V1 + "/vendor/{vendor_id}")
@_guard
def delete_vendor(vendor_id: int):
    _store().delete_vendor(vendor_id)
    return {"message": "Success!"}


# ---- filaments ----------------------------------------------------------------------------------------------------
FILAMENT_FILTERS = ("id", "name", "material", "article_number", "external_id", "vendor.name", "vendor.id")


@router.get(V1 + "/filament")
@_guard
def list_filaments(request: Request):
    rows = _filter(_store().filaments(), request, FILAMENT_FILTERS,
                   {"vendor_name": "vendor.name", "vendor_id": "vendor.id"})
    rows, total = _sort_page(rows, request)
    return _listing(rows, total)


@router.post(V1 + "/filament")
@_guard
def create_filament(request: Request):
    f = _store().create_filament(**_body(request))
    _after_change()
    return f


@router.get(V1 + "/filament/{filament_id}")
@_guard
def get_filament(filament_id: int):
    return _store().filament(filament_id)


@router.patch(V1 + "/filament/{filament_id}")
@_guard
def patch_filament(filament_id: int, request: Request):
    f = _store().update_filament(filament_id, **_body(request))
    _after_change()
    return f


@router.delete(V1 + "/filament/{filament_id}")
@_guard
def delete_filament(filament_id: int):
    _store().delete_filament(filament_id)
    return {"message": "Success!"}


# ---- spools -------------------------------------------------------------------------------------------------------
SPOOL_FILTERS = ("id", "location", "lot_nr", "filament.id", "filament.name", "filament.material",
                 "filament.article_number", "filament.external_id", "filament.vendor.name", "filament.vendor.id")


def _flag(request: Request, name: str) -> bool:
    return request.query_params.get(name, "false").strip().lower() in ("1", "true", "yes")


@router.get(V1 + "/spool")
@_guard
def list_spools(request: Request):
    rows = _store().spools(allow_archived=_flag(request, "allow_archived"))
    rows = _filter(rows, request, SPOOL_FILTERS,
                   {"filament_name": "filament.name", "filament_id": "filament.id", "filament_material": "filament.material",
                    "vendor_name": "filament.vendor.name", "vendor_id": "filament.vendor.id"})
    rows, total = _sort_page(rows, request)
    return _listing(rows, total)


def _unassign_slots(spool_id: int) -> None:
    m = _main()
    for slot, a in m.db.current_assignments().items():
        if a["spool_id"] == spool_id:
            m.engine.manual_unassign(slot)


@router.post(V1 + "/spool")
@_guard
def create_spool(request: Request):
    s = _store().create_spool(**_body(request))
    _after_change()
    return s


@router.get(V1 + "/spool/{spool_id}")
@_guard
def get_spool(spool_id: int):
    return _store().spool(spool_id)


@router.patch(V1 + "/spool/{spool_id}")
@_guard
def patch_spool(spool_id: int, request: Request):
    body = _body(request)
    s = _store().update_spool(spool_id, **body)
    if body.get("archived"):
        _unassign_slots(spool_id)
    if any(k in body for k in ("remaining_weight", "used_weight", "initial_weight")):
        _main().engine.note_weighed(spool_id)
    _after_change()
    return s


@router.delete(V1 + "/spool/{spool_id}")
@_guard
def delete_spool(spool_id: int):
    _store().delete_spool(spool_id)
    _unassign_slots(spool_id)
    _after_change()
    return {"message": "Success!"}


@router.put(V1 + "/spool/{spool_id}/use")
@_guard
def use_spool(spool_id: int, request: Request):
    body = _body(request)
    st = _store()
    if body.get("use_weight") is not None:
        return st.use_weight(spool_id, float(body["use_weight"]), "api")
    if body.get("use_length") is not None:
        return st.use_length(spool_id, float(body["use_length"]), "api")
    raise SpoolmanError("use_weight eller use_length mangler", status=400)


@router.put(V1 + "/spool/{spool_id}/measure")
@_guard
def measure_spool(spool_id: int, request: Request):
    body = _body(request)
    if body.get("weight") is None:
        raise SpoolmanError("weight mangler", status=400)
    s = _store().measure(spool_id, float(body["weight"]))
    _main().engine.note_weighed(spool_id)
    return s


# ---- extra fields -------------------------------------------------------------------------------------------------
@router.get(V1 + "/field/{entity}")
@_guard
def get_fields(entity: str):
    return _store().fields(entity)


@router.post(V1 + "/field/{entity}/{key}")
@_guard
def add_field(entity: str, key: str, request: Request):
    body = _body(request)
    if not body.get("name"):
        raise SpoolmanError("name mangler", status=422)
    return _store().add_field(entity, key, name=body["name"], field_type=body.get("field_type", "text"),
                              order=int(body.get("order") or 0), unit=body.get("unit"),
                              default_value=body.get("default_value"), choices=body.get("choices"),
                              multi_choice=body.get("multi_choice"))


@router.delete(V1 + "/field/{entity}/{key}")
@_guard
def delete_field(entity: str, key: str):
    return _store().delete_field(entity, key)


# ---- SpoolmanDB ---------------------------------------------------------------------------------------------------
@router.get(V1 + "/external/filament")
@_guard
def external_filaments():
    return _store().external_filaments()


@router.get(V1 + "/external/material")
@_guard
def external_materials():
    return _store().external_materials()


# ---- locations ----------------------------------------------------------------------------------------------------
@router.get(V1 + "/location")
@_guard
def locations():
    return _store().locations()


@router.patch(V1 + "/location/{location}")
@_guard
def rename_location(location: str, request: Request):
    body = _body(request)
    if not body.get("name"):
        raise SpoolmanError("name mangler", status=422)
    return _store().rename_location(location, str(body["name"]))


# ---- API token (session only: /api/settings/* is never open without a session) -------------------------------------
def _token_info(token: str) -> dict:
    return {"token": token, "header": f"Authorization: Bearer {token}", "base_path": V1}


@router.get("/api/settings/api-token")
def get_api_token():
    return _token_info(auth.api_token(_main().db))


@router.post("/api/settings/api-token")   # (the settings UI regenerates with a plain POST)
@router.post("/api/settings/api-token/rotate")
def rotate_api_token():
    db = _main().db
    tok = auth.rotate_api_token(db)
    db.event("info", "API-token til Spoolman-API'et (/api/v1) fornyet")
    return _token_info(tok)
