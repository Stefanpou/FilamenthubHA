"""Filament catalogue: SpoolmanDB + Open Filament Database + SpoolmanDB-Community barcodes + the user's own filaments.

Search is typo tolerant and understands Danish colour words ("sort" = black, "hvid" = white ...). The external data
is indexed once (inverted index over normalised tokens); a query only touches the vocabulary and the postings of the
matching tokens, so it takes a few milliseconds on ~30k products."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata

from .spoolman import Spoolman, SpoolmanError  # noqa: F401

log = logging.getLogger("spoolsync.catalog")

CACHE_TTL = 3600  # Spoolman re-syncs SpoolmanDB every hour; follow the same rhythm.


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_TYPE_DA = {"plastic": "plast", "cardboard": "pap", "metal": "metal"}


def _bucket(weight) -> float | None:
    """Net filament weight rounded to the common spool sizes (1000, 750, 500, 250 ...)."""
    try:
        w = float(weight)
    except (TypeError, ValueError):
        return None
    if w <= 0:
        return None
    return float(round(w / 50) * 50) if w < 1000 else float(round(w / 250) * 250)


def _median(vals: list[float]) -> int:
    s = sorted(vals)
    return round(s[len(s) // 2])


def _typical(vals: list[float]) -> int:
    """Most common value (ties -> the median of the values), rounded to whole grams."""
    counts: dict[int, int] = {}
    for v in vals:
        counts[round(v)] = counts.get(round(v), 0) + 1
    best = max(counts.values())
    if best > 1:
        return min(v for v, c in counts.items() if c == best)
    s = sorted(vals)
    return round(s[len(s) // 2])


# Words that name a Bambu product line other than "Basic" (SpoolmanDB names are "<qualifier> <colour>" or just
# "<colour>" for Basic, e.g. "Matte Charcoal", "HF Black", "Silk+ Gold", "Alpine Green Sparkle", "Black").
LINE_WORDS = {"matte", "silk", "silk+", "sparkle", "galaxy", "metal", "metallic", "glow", "translucent", "lite", "aero",
              "tough", "tough+", "hf", "support", "marble", "wood", "gradient", "cf", "gf", "for"}
LINE_SYNONYMS = {"metal": {"metal", "metallic"}}


def _words(s: str | None) -> list[str]:
    return re.findall(r"[a-z0-9+]+", (s or "").lower())


def same_product_line(product: str | None, material: str | None, name: str | None) -> bool:
    """Does a catalogue/filament name belong to a Bambu product line ('PLA Basic', 'PETG HF', ...)?"""
    mat = (material or "").upper().replace("+", "")
    mat_words = set(_words(material)) | set(_words(mat)) | {"bambu", "lab"}
    line = [w for w in _words(product) if w not in mat_words]
    if not line or line[0] == "generic":
        return True
    words = set(_words(name))
    if _norm(product) and _norm(product) in _norm(name):
        return True   # full names ("PLA Matte Charcoal")
    if line == ["basic"]:
        return not (words & LINE_WORDS)
    return all(words & LINE_SYNONYMS.get(w, {w}) for w in line if w != "basic")


def db_item(e: dict) -> dict:
    """SpoolmanDB record -> catalogue item."""
    return {
        "key": f"db:{e['id']}",
        "source": "db",
        "external_id": e["id"],
        "filament_id": None,
        "manufacturer": e.get("manufacturer") or "",
        "name": e.get("name") or "",
        "material": e.get("material") or "",
        "color_hex": (e.get("color_hex") or "").upper()[:6],
        "color_hexes": [c.upper()[:6] for c in (e.get("color_hexes") or [])],
        "multi_color_direction": e.get("multi_color_direction"),
        "weight": e.get("weight"),
        "spool_weight": e.get("spool_weight"),
        "spool_type": e.get("spool_type"),
        "density": e.get("density"),
        "diameter": e.get("diameter") or 1.75,
        "extruder_temp": e.get("extruder_temp"),
        "bed_temp": e.get("bed_temp"),
        "extruder_temp_range": e.get("extruder_temp_range"),
        "bed_temp_range": e.get("bed_temp_range"),
        "td": e.get("transmission_distance"),
        "finish": e.get("finish"),
        "translucent": bool(e.get("translucent")),
        "glow": bool(e.get("glow")),
        "vendor": e.get("manufacturer") or "",
        "ean": "",
        "attributes": [a for a in (e.get("finish"), e.get("pattern"), "translucent" if e.get("translucent") else None,
                                   "glow" if e.get("glow") else None) if a],
    }


def _extra(f: dict, key: str) -> str:
    raw = (f.get("extra") or {}).get(key)
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        val = raw
    return str(val) if val else ""


def own_item(f: dict) -> dict:
    """User's own Spoolman filament -> catalogue item."""
    multi = [c.upper()[:6] for c in (f.get("multi_color_hexes") or "").split(",") if c]
    return {
        "key": f"mine:{f['id']}",
        "source": "mine",
        "external_id": f.get("external_id"),
        "filament_id": f["id"],
        "manufacturer": (f.get("vendor") or {}).get("name") or "",
        "name": f.get("name") or "",
        "material": f.get("material") or "",
        "color_hex": (f.get("color_hex") or "").upper()[:6],
        "color_hexes": multi,
        "multi_color_direction": f.get("multi_color_direction"),
        "weight": f.get("weight"),
        "spool_weight": f.get("spool_weight"),
        "spool_type": _extra(f, "spool_type") or None,
        "density": f.get("density"),
        "diameter": f.get("diameter") or 1.75,
        "extruder_temp": f.get("settings_extruder_temp"),
        "bed_temp": f.get("settings_bed_temp"),
        "finish": _extra(f, "finish") or None,
        "color_name": _extra(f, "color_name"),
        "nozzle_range": _extra(f, "nozzle_range"),
        "bed_range": _extra(f, "bed_range"),
        "td": _extra(f, "td"),
        "attributes": [a.strip() for a in _extra(f, "properties").split(",") if a.strip()],
        "translucent": False,
        "glow": False,
        "article_number": f.get("article_number") or "",
        "vendor": (f.get("vendor") or {}).get("name") or "",
        "ean": _extra(f, "ean") or (f.get("article_number") if str(f.get("article_number") or "").isdigit()
                                    and len(str(f.get("article_number"))) >= 8 else ""),
    }


# ----------------------------------------------------------------------------- search
DA_EN = {  # Danish colour words -> English (SpoolmanDB / OFD names are English)
    "sort": "black", "hvid": "white", "hvide": "white", "rod": "red", "bla": "blue", "gron": "green", "gul": "yellow",
    "gra": "grey", "lilla": "purple", "violet": "purple", "brun": "brown", "solv": "silver", "guld": "gold",
    "gennemsigtig": "transparent", "klar": "clear", "natur": "natural", "naturlig": "natural", "mork": "dark",
    "lys": "light", "lyserod": "pink", "rosa": "pink", "turkis": "turquoise", "kobber": "copper", "sand": "sand",
    "marine": "navy", "gradient": "gradient", "traeagtig": "wood", "trae": "wood", "glimmer": "glitter",
    "blank": "silk", "mat": "matte", "metallisk": "metallic", "orange": "orange",
}
CANON = {"gray": "grey", "colour": "color", "metal": "metallic", "transparent": "translucent", "clear": "translucent"}


def fold(text: str) -> str:
    """Lower-case, Danish letters and accents folded to ASCII (ø -> o, å -> a, æ -> ae)."""
    t = (text or "").lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


def tokens_of(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9+]+", fold(text)):
        out.append(CANON.get(w, w))
        if w.endswith("+") and len(w) > 1:
            out.append(w.rstrip("+"))
    return out


def _within(a: str, b: str, k: int) -> bool:
    """Edit distance(a, b) <= k (insert/delete/substitute/adjacent swap), early exit."""
    if abs(len(a) - len(b)) > k:
        return False
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        lo = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            lo = min(lo, v)
        if lo > k:
            return False
        prev2, prev = prev, cur
    return prev[-1] <= k


MAX_QUERY_CHARS = 100
MAX_QUERY_TOKENS = 8


class _Index:
    """Inverted index over a list of catalogue items."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.postings: dict[str, list[int]] = {}
        self.vendors: dict[str, list] = {}   # norm vendor -> [name, count]
        self.materials: dict[str, dict[str, int]] = {}   # norm vendor ("" = all) -> material -> count
        self._cache: dict[str, dict[str, float]] = {}
        for i, it in enumerate(items):
            toks = set(tokens_of(f"{it.get('manufacturer') or ''} {it.get('name') or ''} {it.get('material') or ''} "
                                 f"{it.get('finish') or ''} {' '.join(it.get('attributes') or [])}"))
            w = it.get("weight")
            if w:
                toks.add(f"{int(w)}g")
                if int(w) % 1000 == 0:
                    toks.add(f"{int(w) // 1000}kg")
            for k in ("ean", "article_number"):
                for code in re.findall(r"[0-9a-z]{4,}", fold(str(it.get(k) or ""))):
                    toks.add(code)
                    toks.add(code.lstrip("0"))
            for t in toks:
                self.postings.setdefault(t, []).append(i)
            nv = _norm(it.get("manufacturer"))
            if nv:
                v = self.vendors.setdefault(nv, [it["manufacturer"], 0])
                v[1] += 1
            m = it.get("material") or ""
            for key in ("", nv):
                d = self.materials.setdefault(key, {})
                d[m] = d.get(m, 0) + 1
        self.vocab = list(self.postings)

    def resolve(self, tok: str) -> dict[str, float]:
        """Vocabulary tokens a query token stands for, with a weight (exact > prefix > substring > typo)."""
        hit = self._cache.get(tok)
        if hit is not None:
            return hit
        out: dict[str, float] = {}
        if tok in self.postings:
            out[tok] = 3.0
        n = len(tok)
        if n >= 2:
            for v in self.vocab:
                if v in out:
                    continue
                if v.startswith(tok):
                    out[v] = 2.0 - min(len(v) - n, 10) * 0.05
                elif n >= 3 and tok in v:
                    out[v] = 1.0
        if n >= 4 and not any(w >= 2.0 for w in out.values()):
            k = 1 if n < 7 else 2
            for v in self.vocab:
                if v not in out and abs(len(v) - n) <= k and v[0] == tok[0] and _within(tok, v, k):
                    out[v] = 1.6 - 0.3 * k
            if not out and n >= 4:   # typo in the first letter
                for v in self.vocab:
                    if abs(len(v) - n) <= 1 and _within(tok, v, 1):
                        out[v] = 1.0
        if len(self._cache) > 5000:
            self._cache.clear()
        self._cache[tok] = out
        return out

    def scores(self, qtoks: list[str]) -> dict[int, float]:
        """item index -> score; items must match every query token (a Danish word counts as its English twin)."""
        per: list[dict[int, float]] = []
        for qt in qtoks:
            m: dict[int, float] = {}
            if qt in DA_EN:   # a Danish word means its English twin ("blå" must not match "black" by prefix)
                resolved = dict(self.resolve(CANON.get(DA_EN[qt], DA_EN[qt])))
                if qt in self.postings:
                    resolved[qt] = 3.0
            else:
                resolved = self.resolve(qt)
            for v, wgt in resolved.items():
                for i in self.postings[v]:
                    if wgt > m.get(i, 0):
                        m[i] = wgt
            per.append(m)
        if not per:
            return {}
        per.sort(key=len)
        res = {i: w for i, w in per[0].items()}
        for m in per[1:]:
            res = {i: w + m[i] for i, w in res.items() if i in m}
            if not res:
                break
        if not res and len(per) > 1:   # nothing matches every word: show what matches most of them
            tally: dict[int, list] = {}
            for m in per:
                for i, w in m.items():
                    t = tally.setdefault(i, [0, 0.0])
                    t[0] += 1
                    t[1] += w
            best = max((t[0] for t in tally.values()), default=0)
            res = {i: t[1] * 0.5 for i, t in tally.items() if t[0] == best and best >= len(per) - 1}
        return res


def _dedupe_key(it: dict) -> tuple:
    return (_norm(it.get("manufacturer")), _norm(it.get("material")), _norm(it.get("name")), int(it.get("weight") or 0))


def _sdbc_item(it: dict) -> dict:
    return {**it, "vendor": it.get("manufacturer") or "", "ean": it.get("key", ":").split(":", 1)[1]
            if str(it.get("key", "")).startswith("sdbc:") else "", "attributes": [a for a in [it.get("finish")] if a]}


def _ofd_item(it: dict) -> dict:
    return {**it, "vendor": it.get("manufacturer") or "", "attributes": it.get("attributes") or []}


class Catalog:
    def __init__(self, spoolman: Spoolman, codeidx=None):
        self.spoolman = spoolman
        self.codeidx = codeidx
        self._lock = threading.Lock()
        self._external: list[dict] = []
        self._materials: list[dict] = []
        self._loaded = 0.0
        self.last_error = ""
        self._ext_index: _Index | None = None
        self._ext_sig: tuple = ()

    def external(self, force: bool = False) -> list[dict]:
        """SpoolmanDB records (raw). The store serves its cache at once and refreshes in the background."""
        with self._lock:
            if force or not self._external or time.time() - self._loaded > CACHE_TTL:
                try:
                    data = self.spoolman.external_filaments()
                    mats = self.spoolman.external_materials()
                    if data:
                        self._external, self._materials = data, mats
                        self._loaded = time.time()
                        self.last_error = ""
                except SpoolmanError as e:
                    self.last_error = str(e)
                    log.warning("could not load SpoolmanDB: %s", e)
            return self._external

    def material_defaults(self) -> dict[str, dict]:
        self.external()
        return {m["material"].upper(): m for m in self._materials if m.get("material")}

    def _own(self) -> list[dict]:
        try:
            return [own_item(f) for f in self.spoolman.filaments()]
        except SpoolmanError as e:
            log.warning("could not load own filaments: %s", e)
            return []

    def _external_index(self) -> _Index:
        """Index over SpoolmanDB + OFD + SpoolmanDB-Community (deduplicated), rebuilt only when the data changed."""
        ext = self.external()
        ci = self.codeidx
        sig = (id(ext), len(ext), getattr(ci, "catalog_version", 0), len(getattr(ci, "sdbc", {}) or {}))
        with self._lock:
            if self._ext_index is not None and self._ext_sig == sig:
                return self._ext_index
        items = [db_item(e) for e in ext]
        seen = {_dedupe_key(it): it for it in items}
        if ci is not None:
            extra = [_ofd_item(it) for it in list(getattr(ci, "ofd_catalog", []) or [])]
            extra += [_sdbc_item(it) for it in list((getattr(ci, "sdbc", {}) or {}).values())]
            for it in extra:
                k = _dedupe_key(it)
                prev = seen.get(k)
                if prev is not None:
                    if not prev.get("ean") and it.get("ean"):
                        prev["ean"] = it["ean"]
                    if not prev.get("article_number") and it.get("article_number"):
                        prev["article_number"] = it["article_number"]
                    continue
                seen[k] = it
                items.append(it)
        idx = _Index(items)
        with self._lock:
            self._ext_index, self._ext_sig = idx, sig
        return idx

    # ------------------------------------------------------------------ empty spool weight
    def _tare_stats(self) -> dict:
        """Empty spool weights seen in the databases, by (manufacturer, net weight, spool type), cached per index."""
        idx = self._external_index()
        with self._lock:
            if getattr(self, "_tare_for", None) is idx:
                return self._tare
        stats: dict[tuple, list[float]] = {}
        for it in idx.items:
            w = it.get("spool_weight")
            if not w or w <= 0 or w > 2000:
                continue
            man, net, typ = _norm(it.get("manufacturer")), _bucket(it.get("weight")), it.get("spool_type") or ""
            for key in ((man, net, typ), (man, net, ""), (man, None, ""), ("", net, typ), ("", net, "")):
                stats.setdefault(key, []).append(float(w))
        with self._lock:
            self._tare, self._tare_for = stats, idx
        return stats

    def spool_weight_estimate(self, manufacturer: str = "", weight: float | None = None,
                              spool_type: str | None = None) -> dict | None:
        """Best guess for what the empty spool weighs: the most common value for the same manufacturer and spool size,
        else for the manufacturer, else the typical spool of that size. None when nothing is known."""
        stats = self._tare_stats()
        man, net, typ = _norm(manufacturer), _bucket(weight or 1000), (spool_type or "").lower()
        size = f"{net:g} g" if net < 1000 else f"{net / 1000:g} kg"
        steps = [((man, net, typ), "manufacturer_size", f"{manufacturer} {size}-rulle")] if typ else []
        steps += [((man, net, ""), "manufacturer_size", f"{manufacturer} {size}-rulle"),
                  ((man, None, ""), "manufacturer", f"{manufacturer}s ruller")]
        if typ:
            steps.append((("", net, typ), "typical", f"typisk {size} {_TYPE_DA.get(typ, typ)}rulle"))
        steps.append((("", net, ""), "typical", f"typisk {size}-rulle"))
        for key, source, label in steps:
            if not key[0] and source != "typical":
                continue
            vals = stats.get(key)
            if vals:
                grams = _typical(vals) if source != "typical" else _median(vals)   # across brands: the middle one
                return {"grams": grams, "source": source, "label": label, "samples": len(vals)}
        return None

    def items(self) -> list[dict]:
        """All catalogue items: the user's own filaments first, then the external databases."""
        return self._own() + self._external_index().items

    def manufacturers(self) -> list[dict]:
        counts: dict[str, dict] = {}
        for n, (name, c) in self._external_index().vendors.items():
            counts[n] = {"name": name, "count": c, "mine": 0}
        for it in self._own():
            n = _norm(it["manufacturer"])
            if not n:
                continue
            c = counts.setdefault(n, {"name": it["manufacturer"], "count": 0, "mine": 0})
            c["count"] += 1
            c["mine"] += 1
            c["name"] = it["manufacturer"]  # prefer the user's spelling
        # Manufacturers you already use first, then alphabetical.
        return sorted(counts.values(), key=lambda c: (c["mine"] == 0, c["name"].lower()))

    def search(self, manufacturer: str = "", material: str = "", q: str = "", limit: int = 60, source: str = "") -> dict:
        t0 = time.perf_counter()
        manufacturer, material = (manufacturer or "")[:100], (material or "")[:40]
        q = (q or "")[:MAX_QUERY_CHARS]
        man = _norm(manufacturer)
        mat = (material or "").strip().upper()
        src = (source or "").strip().lower()
        limit = max(1, min(int(limit), 300))
        own = self._own()
        ext = self._external_index()
        own_idx = _Index(own)
        seen_external = {it["external_id"] for it in own if it["external_id"]}
        qtoks = tokens_of(q)[:MAX_QUERY_TOKENS]
        qtoks = [t for t in qtoks if t not in ("g", "mm", "filament")] or qtoks

        scored: list[tuple[float, int, dict]] = []   # (score, source rank, item)
        for rank, idx in ((0, own_idx), (1, ext)):
            hits = idx.scores(qtoks) if qtoks else {i: 0.0 for i in range(len(idx.items))}
            for i, sc in hits.items():
                it = idx.items[i]
                if rank == 1 and it.get("external_id") in seen_external:
                    continue  # already in the user's own filaments
                if man and _norm(it["manufacturer"]) != man:
                    continue
                if src and it["source"] != src:
                    continue
                scored.append((sc, rank, it))
        mats = dict(own_idx.materials.get(man if man else "", {}))
        for m, c in ext.materials.get(man if man else "", {}).items():
            mats[m] = mats.get(m, 0) + c
        if mat:
            scored = [t for t in scored if (t[2]["material"] or "").upper() == mat]
        phrase = "" if any(t in DA_EN for t in qtoks) else " ".join(fold(q).split())
        if qtoks:
            def key(t):
                sc, rank, it = t
                name = fold(it["name"])
                bonus = 0.6 if phrase and phrase in fold(f"{it['manufacturer']} {it['name']}") else 0.0
                if name.startswith(phrase) and phrase:
                    bonus += 0.4
                bonus += 1.5 if rank == 0 else 0.0
                if fold(it["material"]) in qtoks:
                    bonus += 0.3   # "pla" prefers plain PLA over PLA-CF
                return (-(sc + bonus), (it["weight"] or 0) != 1000, len(it["name"]), it["name"].lower())
            scored.sort(key=key)
        else:
            scored.sort(key=lambda t: (t[1], t[2]["material"], t[2]["name"].lower(), -(t[2]["weight"] or 0)))
        top = []
        for sc, rank, it in scored[:limit]:
            top.append({**it, "score": round(sc, 2)} if qtoks else it)
        return {"total": len(scored), "items": top,
                "materials": sorted(mats.items(), key=lambda kv: -kv[1]),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def warm(self) -> None:
        """Build the search index now (a background thread at startup) so the first search is fast."""
        try:
            self._external_index()
        except Exception:
            log.exception("could not build the catalogue index")

    def get(self, key: str) -> dict | None:
        for it in self.items():
            if it["key"] == key:
                return it
        return None
