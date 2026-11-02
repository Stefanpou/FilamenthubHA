"""Scanned code -> spool / filament suggestion.

Order:
1. Spoolman label QR (spool / filament)
2. Codes scanned before (local table) and Spoolman filament article numbers  -> exact, one tap
3. Offline barcode index (Open Filament Database + SpoolmanDB-Community, auto-refreshed)
4. Online product lookup (UPCitemdb, 100/day) -> title parsed
Every hit is matched against the user's own filaments and SpoolmanDB so new filaments stay consistent.
Unknown codes return a manufacturer hint from the GS1 prefix.
"""
from __future__ import annotations

import json
import logging
import time

import requests

from .barcode import (gtin_valid, lookup_variants, match_external, normalize_code, parse_spoolman_qr,
                      parse_title)
from .catalog import Catalog, db_item, own_item
from .codedb import CodeIndex
from .db import Database
from .printer_state import color_distance
from .spoolman import Spoolman, SpoolmanError, spool_display
from .sync import _same_time

log = logging.getLogger("spoolsync.lookup")

CACHE_OK_DAYS = 90
CACHE_MISS_DAYS = 7
MATCH_THRESHOLD = 0.6


class TemporaryLookupError(Exception):
    """Provider rate-limited / down: do not cache."""


# ----------------------------------------------------------------------------- online providers
def _upcitemdb(code: str) -> dict | None:
    resp = requests.get("https://api.upcitemdb.com/prod/trial/lookup", params={"upc": code}, timeout=8,
                        headers={"Accept": "application/json", "User-Agent": "SpoolSync/1.0"})
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TemporaryLookupError(f"upcitemdb {resp.status_code}")
    try:
        data = resp.json() or {}
    except ValueError:
        data = {}
    if data.get("code") in ("TOO_FAST", "EXCEED_LIMIT"):
        raise TemporaryLookupError(f"upcitemdb {data.get('code')}")
    if resp.status_code >= 400:
        return None
    items = data.get("items") or []
    if not items:
        return None
    it = items[0]
    brand = it.get("brand") or ""
    if brand.upper() == "NOBRAND":
        brand = ""
    return {"title": it.get("title") or "", "brand": brand, "provider": "upcitemdb.com"}


PROVIDERS = [_upcitemdb]


def online_lookup(db: Database, code: str) -> dict | None:
    """Product title/brand for a GTIN from the cache or online providers. None when unknown."""
    row = db.query_one("SELECT * FROM barcode_cache WHERE code=?", (code,))
    if row:
        result = json.loads(row["result"])
        max_age = (CACHE_OK_DAYS if result else CACHE_MISS_DAYS) * 86400
        if time.time() - row["fetched_ts"] < max_age:
            return result or None
    found, temporary = None, False
    for provider in PROVIDERS:
        try:
            found = provider(code)
        except TemporaryLookupError as e:
            log.info("lookup provider unavailable: %s", e)
            temporary = True
            continue
        except (requests.RequestException, ValueError) as e:
            log.info("lookup provider failed: %s", e)
            temporary = True
            continue
        if found and found.get("title"):
            break
        found = None
    if found or not temporary:
        db.execute("INSERT INTO barcode_cache(code, result, fetched_ts) VALUES(?,?,?) "
                   "ON CONFLICT(code) DO UPDATE SET result=excluded.result, fetched_ts=excluded.fetched_ts",
                   (code, json.dumps(found or {}), time.time()))
    return found


# ----------------------------------------------------------------------------- matching
def _own_items(spoolman: Spoolman) -> list[dict]:
    try:
        return [own_item(f) for f in spoolman.filaments()]
    except SpoolmanError:
        return []


def best_match(parsed: dict, spoolman: Spoolman, catalog: Catalog) -> tuple[dict | None, float]:
    """Best catalogue item for parsed product data; the user's own filaments win close ties."""
    own = _own_items(spoolman)
    best_own, score_own = match_external(parsed, [{**o, "id": o["key"]} for o in own])
    best_db, score_db = match_external(parsed, catalog.external())
    if best_own and score_own >= max(MATCH_THRESHOLD, score_db - 0.05):
        return next(o for o in own if o["key"] == best_own["id"]), score_own
    if best_db and score_db >= MATCH_THRESHOLD:
        return db_item(best_db), score_db
    return None, max(score_own, score_db)


def _enrich_index_hit(hit: dict, spoolman: Spoolman, catalog: Catalog) -> tuple[dict, str]:
    """Prefer an identical filament the user already has, then the matching SpoolmanDB record; else the index data."""
    title = f"{hit['manufacturer']} {hit.get('line') or ''} {hit['material']} {hit.get('color_name') or hit['name']}"
    parsed = parse_title(title, hit["manufacturer"], [hit["manufacturer"]])
    parsed["material"] = hit["material"] or parsed.get("material")
    parsed["weight"] = hit.get("weight") or parsed.get("weight")
    item, score = best_match(parsed, spoolman, catalog)
    same_colour = item and (not hit.get("color_hex") or not item.get("color_hex")
                            or color_distance(hit["color_hex"], item["color_hex"]) <= 60)
    if item and score >= 0.75 and same_colour:
        merged = dict(item)
        for k in ("spool_weight", "density", "extruder_temp", "bed_temp"):
            if merged.get(k) in (None, 0) and hit.get(k):
                merged[k] = hit[k]
        return merged, "Dit filament" if item["source"] == "mine" else "SpoolmanDB"
    return hit, {"ofd": "Open Filament Database", "sdbc": "SpoolmanDB-Community"}.get(hit["source"], hit["source"])


def _vendor_hint(code: str, db: Database, spoolman: Spoolman, codeidx: CodeIndex | None) -> str | None:
    hint = codeidx.brand_hint(code) if codeidx else None
    if hint:
        return hint
    g = code.zfill(13) if len(code) == 12 else code
    for row in db.query("SELECT code, filament_id FROM barcodes"):
        other = row["code"].zfill(13) if len(row["code"]) == 12 else row["code"]
        if len(g) == 13 and other[:7] == g[:7]:
            try:
                return (spoolman.filament(row["filament_id"]).get("vendor") or {}).get("name")
            except SpoolmanError:
                continue
    return None


# ----------------------------------------------------------------------------- main entry
def lookup(code_raw: str, db: Database, spoolman: Spoolman, catalog: Catalog, codeidx: CodeIndex | None = None) -> dict:
    raw = (code_raw or "").strip()
    if not raw:
        return {"kind": "unknown", "code": ""}

    qr = parse_spoolman_qr(raw)
    if qr:
        kind, ident = qr
        try:
            if kind == "spool":
                return {"kind": "spool", "spool": spool_display(spoolman.spool(ident))}
            return {"kind": "known", "code": raw, "item": own_item(spoolman.filament(ident)), "source": "Spoolman-label"}
        except SpoolmanError:
            return {"kind": "unknown", "code": raw, "message": f"Spoolman kender ikke {kind} #{ident}"}

    code = normalize_code(raw)
    variants = lookup_variants(code)

    # 1. Scanned before?
    for v in variants:
        row = db.query_one("SELECT filament_id, filament_registered FROM barcodes WHERE code=?", (v,))
        if row:
            try:
                fil = spoolman.filament(row["filament_id"])
            except SpoolmanError:
                db.execute("DELETE FROM barcodes WHERE code=?", (v,))  # filament deleted in Spoolman
                continue
            if row["filament_registered"] and fil.get("registered") and not _same_time(fil["registered"],
                                                                                        row["filament_registered"]):
                db.execute("DELETE FROM barcodes WHERE code=?", (v,))  # deleted, and its number given to another filament
                continue
            return {"kind": "known", "code": code, "item": own_item(fil), "source": "Scannet før"}
    # 2. Stored as article number on a Spoolman filament (e.g. entered in Spoolman directly)?
    try:
        for f in spoolman.filaments():
            arts = {normalize_code(a) for a in (f.get("article_number") or "").split(",") if a.strip()}
            if arts & set(variants):
                return {"kind": "known", "code": code, "item": own_item(f), "source": "Varenummer i Spoolman"}
    except SpoolmanError:
        pass

    if not gtin_valid(code):
        return {"kind": "unknown", "code": code, "vendor_hint": None,
                "message": "Koden er ikke en gyldig EAN/UPC-stregkode – vælg filamentet i listen, så huskes koden"}

    # 3. Offline index (Open Filament Database / SpoolmanDB-Community)
    hit = codeidx.find(code) if codeidx else None
    if hit:
        item, source = _enrich_index_hit(hit, spoolman, catalog)
        refill = " (refill – uden spole)" if hit.get("refill") else ""
        return {"kind": "match", "code": code, "item": item, "confidence": 0.95, "provider": source,
                "title": f"{hit['manufacturer']} {hit.get('line') or hit['material']} {hit.get('color_name') or ''}{refill}".strip()}

    # 4. Online lookup
    hint = _vendor_hint(code, db, spoolman, codeidx)
    product = online_lookup(db, code)
    if not product:
        return {"kind": "unknown", "code": code, "vendor_hint": hint,
                "message": ("Stregkoden er ikke i databaserne endnu – " + (f"det ligner {hint}. " if hint else "")
                            + "Vælg filamentet i listen, så huskes koden til næste gang")}
    vendors = [m["name"] for m in catalog.manufacturers()]
    parsed = parse_title(product["title"], product.get("brand") or hint, vendors)
    item, score = best_match(parsed, spoolman, catalog)
    if not item and not parsed.get("material"):
        # The barcode belongs to something that does not look like filament (wrong code, box of something else...).
        return {"kind": "unknown", "code": code, "title": product["title"], "vendor_hint": hint,
                "message": f"Stregkoden hører til “{product['title'][:80]}”, som ikke ligner filament – vælg filamentet i listen"}
    return {"kind": "match" if item else "title", "code": code, "title": product["title"], "provider": product["provider"],
            "parsed": parsed, "item": item, "confidence": score, "vendor_hint": hint}
