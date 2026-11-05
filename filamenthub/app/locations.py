"""Fixed locations: shelves, drawers and dry boxes with a set number of places ("Tørreboks" with 4 places).

A spool in place 2 of "Tørreboks" has the location "Tørreboks - 2", written like the printer slots ("X2D - A2").
Every other location stays free text. The list lives in the db (kv "fixed_locations").
"""
from __future__ import annotations

import re
import sys

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

KEY = "fixed_locations"
MAX_PLACES = 200


def _main():
    m = sys.modules.get(__package__ + ".main")
    if m is None or not hasattr(m, "spoolman"):
        from . import main as m
    return m


def fixed(db) -> list[dict]:
    return [l for l in (db.get(KEY) or []) if isinstance(l, dict) and l.get("name")]


def place_text(name: str, pos: int) -> str:
    return f"{name} - {pos}"


def parse(text: str | None, locs: list[dict]) -> tuple[dict, int | None] | None:
    """(fixed location, place) for a spool's location text; the place is None for the bare name."""
    t = (text or "").strip()
    for loc in locs:
        if t == loc["name"]:
            return loc, None
        m = re.fullmatch(re.escape(loc["name"]) + r" - (\d+)", t)
        if m:
            return loc, int(m.group(1))
    return None


def occupancy(spools: list[dict], locs: list[dict]) -> dict[str, dict[int, int]]:
    """{location name: {place: spool id}} for the spools in stock (archived spools free their place)."""
    out: dict[str, dict[int, int]] = {loc["name"]: {} for loc in locs}
    for s in spools:
        if s.get("archived"):
            continue
        p = parse(s.get("location"), locs)
        if p and p[1] is not None:
            out[p[0]["name"]].setdefault(p[1], s["id"])
    return out


def resolve(store, db, spool_id: int, text: str) -> str:
    """The location to store for `text` on a spool: the bare name of a fixed location takes its first free place
    (or keeps the spool's place there); a taken place or one past the end is refused."""
    locs = fixed(db)
    p = parse(text, locs)
    if not p:
        return text.strip()
    loc, pos = p
    taken = occupancy([s for s in store.spools(allow_archived=False) if s["id"] != spool_id], locs)[loc["name"]]
    if pos is None:
        cur = parse((store.spool(spool_id) or {}).get("location"), locs) if spool_id else None
        if cur and cur[0]["name"] == loc["name"] and cur[1] and cur[1] not in taken and cur[1] <= loc["slots"]:
            return place_text(loc["name"], cur[1])
        pos = next((i for i in range(1, loc["slots"] + 1) if i not in taken), None)
        if pos is None:
            raise HTTPException(409, f"{loc['name']} er fuld ({loc['slots']} pladser)")
        return place_text(loc["name"], pos)
    if not 1 <= pos <= loc["slots"]:
        raise HTTPException(422, f"{loc['name']} har kun {loc['slots']} pladser")
    if pos in taken:
        raise HTTPException(409, f"Plads {pos} i {loc['name']} er optaget af rulle #{taken[pos]}")
    return place_text(loc["name"], pos)


def overview(store, db) -> dict:
    locs = fixed(db)
    spools = store.spools(allow_archived=False)
    occ = occupancy(spools, locs)
    texts = {(s.get("location") or "").strip() for s in spools}
    other = sorted((t for t in texts if t and not parse(t, locs)), key=str.lower)
    return {"fixed": [{"name": l["name"], "slots": l["slots"], "places": {str(k): v for k, v in sorted(occ[l["name"]].items())}}
                      for l in locs],
            "other": other}


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    slots: int = Field(ge=1, le=MAX_PLACES)
    was: str | None = Field(default=None, max_length=60)   # its name before, when it is renamed


class LocationsIn(BaseModel):
    locations: list[LocationIn] = Field(max_length=100)


@router.get("/api/locations")
def get_locations():
    m = _main()
    return overview(m.spoolman, m.db)


@router.put("/api/locations")
def put_locations(body: LocationsIn):
    m = _main()
    store, db = m.spoolman, m.db
    old = fixed(db)
    new = []
    for l in body.locations:
        name = " ".join(l.name.split())
        if re.search(r" - \d+$", name):
            raise HTTPException(422, f"Navnet må ikke ende på et pladsnummer: {name}")
        if any(n["name"].lower() == name.lower() for n in new):
            raise HTTPException(422, f"{name} står der to gange")
        new.append({"name": name, "slots": l.slots, "was": (l.was or "").strip() or None})
    spools = store.spools(allow_archived=False)
    occ = occupancy(spools, old)
    for n in new:   # fewer places than before: the places that go must be empty
        before = n["was"] or n["name"]
        over = sorted(p for p in occ.get(before, {}) if p > n["slots"])
        if over:
            raise HTTPException(409, f"Plads {', '.join(map(str, over))} i {before} er optaget – flyt rullerne først")
    renames = {n["was"]: n["name"] for n in new if n["was"] and n["was"] != n["name"] and any(o["name"] == n["was"] for o in old)}
    if renames:
        for s in store.spools(allow_archived=True):
            p = parse(s.get("location"), old)
            if p and p[0]["name"] in renames:
                to = renames[p[0]["name"]]
                store.update_spool(s["id"], location=to if p[1] is None else place_text(to, p[1]))
    db.set(KEY, [{"name": n["name"], "slots": n["slots"]} for n in new])
    if renames:
        m.fm.trigger()
    return overview(store, db)
