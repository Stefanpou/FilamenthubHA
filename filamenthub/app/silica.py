"""When the silica gel (desiccant) was last dried: one date per AMS unit ("ams:0") and per fixed location such as a
dry box ("loc:Tørreboks"). Kept in the db (kv "silica" = {key: "YYYY-MM-DD"})."""
from __future__ import annotations

import re
import sys
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

KEY = "silica"
_KEY = re.compile(r"^(ams:\d{1,2}|loc:\S.{0,59})$")


def _db():
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "db"):
        from . import main as m
    return m.db


def dates(db) -> dict[str, str]:
    raw = db.get(KEY) or {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(raw, dict) else {}


def rename(db, renames: dict[str, str]) -> None:
    """A renamed fixed location keeps its date."""
    d = dates(db)
    moved = {f"loc:{renames[k[4:]]}" if k.startswith("loc:") and k[4:] in renames else k: v for k, v in d.items()}
    if moved != d:
        db.set(KEY, moved)


class SilicaIn(BaseModel):
    key: str = Field(min_length=5, max_length=64)
    dried: date | None = None          # None clears it


@router.get("/api/silica")
def get_silica():
    return {"dates": dates(_db())}


@router.put("/api/silica")
def put_silica(body: SilicaIn):
    if not _KEY.match(body.key):
        raise HTTPException(422, "Ukendt AMS eller placering")
    if body.dried and body.dried > date.today():
        raise HTTPException(422, "Datoen kan ikke ligge i fremtiden")
    db = _db()
    d = dates(db)
    if body.dried:
        d[body.key] = body.dried.isoformat()
    else:
        d.pop(body.key, None)
    db.set(KEY, d)
    return {"dates": d}
