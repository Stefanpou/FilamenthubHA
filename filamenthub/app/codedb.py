"""Offline barcode (GTIN/EAN) index built from open filament databases, refreshed automatically.

Sources (both MIT licensed, no API key):
* Open Filament Database  – https://api.openfilamentdatabase.org/json/all.json.gz  (rebuilt daily, ~3 MB)
  Best coverage for Bambu Lab, Polymaker, Prusament, Hatchbox.
* SpoolmanDB-Community    – https://icezaza2543.github.io/SpoolmanDB-Community/filaments.json (~44 MB, weekly)
  EANs for Polymaker, Spectrum, Fiberlogy, eSun, Formfutura ...

Only the entries that carry a barcode are kept (a few thousand), stored compactly in the data directory.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("spoolsync.codedb")

OFD_URL = "https://api.openfilamentdatabase.org/json/all.json.gz"
SDBC_URL = "https://icezaza2543.github.io/SpoolmanDB-Community/filaments.json"
OFD_MAX_AGE = 24 * 3600
SDBC_MAX_AGE = 7 * 24 * 3600

# GS1 company prefixes seen on real boxes (seed; more are learned from the indexes and the user's own scans).
SEED_PREFIXES = {
    "6975337": "Bambu Lab",
    "6976021": "ELEGOO",
    "6933582": "Sunlu",
    "6938936": "Polymaker",
    "6922572": "eSun",
}


def _gtin13(code: str) -> str:
    code = re.sub(r"\D", "", str(code or ""))
    return code.zfill(13) if len(code) in (12, 13) else code


def _mid(a, b):
    vals = [v for v in (a, b) if isinstance(v, (int, float)) and v > 0]
    return int(round(sum(vals) / len(vals))) if vals else None


def _name_from_line(line: str, material: str, color: str) -> str:
    """'PLA Matte' + 'Charcoal' -> 'Matte Charcoal'; 'PLA Basic' + 'Jade White' -> 'Jade White'."""
    qual = re.sub(re.escape(material or ""), "", line or "", flags=re.I)
    qual = re.sub(r"\bbasic\b", "", qual, flags=re.I)
    qual = re.sub(r"\s+", " ", qual).strip(" -")
    return f"{qual} {color}".strip() if qual else color


def build_ofd_index(data: dict) -> dict[str, dict]:
    brands = {b["id"]: b for b in data.get("brands", [])}
    filaments = {f["id"]: f for f in data.get("filaments", [])}
    variants = {v["id"]: v for v in data.get("variants", [])}
    out: dict[str, dict] = {}
    for s in data.get("sizes", []):
        gtin = _gtin13(s.get("gtin") or "")
        if not gtin:
            continue
        v = variants.get(s.get("variant_id"))
        f = filaments.get(v.get("filament_id")) if v else None
        if not v or not f:
            continue
        brand = (brands.get(f.get("brand_id")) or {}).get("name") or ""
        material = f.get("material") or ""
        color = v.get("name") or ""
        hexes = [h.lstrip("#").upper()[:6] for h in (v.get("color_hex") if isinstance(v.get("color_hex"), list) else [v.get("color_hex")]) if h]
        bambu = ((f.get("slicer_settings") or {}).get("bambustudio") or {}).get("id")
        refill = bool(s.get("spool_refill"))
        prev = out.get(gtin)
        if prev and (refill or not prev["refill"]):
            # Duplicate GTIN (known OFD data errors: refill rows carrying the spool code). A spool row wins.
            continue
        out[gtin] = {
            "key": f"ofd:{gtin}", "source": "ofd", "external_id": None, "filament_id": None,
            "manufacturer": brand, "line": f.get("name") or "", "name": _name_from_line(f.get("name") or "", material, color),
            "color_name": color, "material": material,
            "color_hex": hexes[0] if hexes else "", "color_hexes": hexes if len(hexes) > 1 else [],
            "multi_color_direction": None,
            "weight": s.get("filament_weight"), "spool_weight": s.get("empty_spool_weight"), "spool_type": None,
            "density": f.get("density"), "diameter": s.get("diameter") or 1.75,
            "extruder_temp": _mid(f.get("min_print_temperature"), f.get("max_print_temperature")),
            "bed_temp": _mid(f.get("min_bed_temperature"), f.get("max_bed_temperature")),
            "finish": None, "refill": refill, "article_number": s.get("article_number") or "",
            "bambu_filament_id": bambu,
        }
    return out


def _strip_words(text: str, words: list[str]) -> str:
    for w in words:
        if w:
            text = re.sub(re.escape(w), " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" -")


def build_ofd_catalog(data: dict) -> list[dict]:
    """Every (non-discontinued) colour/size of the Open Filament Database as a flat catalogue list (for search).

    Unlike the barcode index this keeps products without a GTIN, too. Refill rows are kept only when no spool row
    exists for the same colour and weight."""
    brands = {b["id"]: b for b in data.get("brands", [])}
    filaments = {f["id"]: f for f in data.get("filaments", [])}
    sizes_by_variant: dict[str, list[dict]] = {}
    for s in data.get("sizes", []):
        if not s.get("discontinued"):
            sizes_by_variant.setdefault(s.get("variant_id"), []).append(s)
    out: list[dict] = []
    for v in data.get("variants", []):
        if v.get("discontinued"):
            continue
        f = filaments.get(v.get("filament_id"))
        if not f or f.get("discontinued"):
            continue
        brand = (brands.get(f.get("brand_id")) or {}).get("name") or ""
        material = f.get("material") or ""
        color = v.get("name") or ""
        chex = v.get("color_hexes") or ([v.get("color_hex")] if v.get("color_hex") else [])
        hexes = [str(h).lstrip("#").upper()[:6] for h in chex if h]
        line = _strip_words(f.get("name") or "", [brand, material, "filament", "basic"])
        traits = [k for k, val in (v.get("traits") or {}).items() if val is True]
        best: dict[float, dict] = {}
        for s in sizes_by_variant.get(v.get("id"), []):
            w = s.get("filament_weight")
            refill = bool(s.get("spool_refill"))
            prev = best.get(w)
            if prev and not (prev["refill"] and not refill):
                if not prev.get("ean") and s.get("gtin") and refill == prev["refill"]:
                    prev["ean"] = _gtin13(s["gtin"])
                continue
            best[w] = {"weight": w, "refill": refill, "diameter": s.get("diameter") or 1.75,
                       "spool_weight": None if refill else s.get("empty_spool_weight"),
                       "ean": _gtin13(s["gtin"]) if s.get("gtin") else "", "article_number": s.get("article_number") or ""}
        if not best:
            best[None] = {"weight": None, "refill": False, "diameter": 1.75, "spool_weight": None, "ean": "",
                          "article_number": ""}
        for sz in best.values():
            out.append({
                "key": f"ofdc:{v.get('id')}:{int(sz['weight'] or 0)}", "source": "ofd", "external_id": None,
                "filament_id": None, "manufacturer": brand, "vendor": brand,
                "name": f"{line} {color}".strip() if line else color, "color_name": color, "line": f.get("name") or "",
                "material": material, "color_hex": hexes[0] if hexes else "", "color_hexes": hexes if len(hexes) > 1 else [],
                "multi_color_direction": None, "weight": sz["weight"], "spool_weight": sz["spool_weight"],
                "spool_type": None, "density": f.get("density"), "diameter": sz["diameter"],
                "extruder_temp": _mid(f.get("min_print_temperature"), f.get("max_print_temperature")),
                "bed_temp": _mid(f.get("min_bed_temperature"), f.get("max_bed_temperature")),
                "finish": line or None, "attributes": traits, "refill": sz["refill"], "ean": sz["ean"],
                "article_number": sz["article_number"],
            })
    return out


def build_sdbc_index(data: list) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in data:
        for field, refill in (("eans", False), ("eans_refill", True)):
            for code in e.get(field) or []:
                gtin = _gtin13(code)
                if not gtin or gtin in out:
                    continue
                et = e.get("extruder_temp") or _mid(*(e.get("extruder_temp_range") or [None, None])[:2])
                bt = e.get("bed_temp") or _mid(*(e.get("bed_temp_range") or [None, None])[:2])
                hexes = [h.upper()[:6] for h in (e.get("color_hexes") or [])]
                out[gtin] = {
                    "key": f"sdbc:{gtin}", "source": "sdbc", "sdbc_id": e.get("id"), "external_id": None, "filament_id": None,
                    "manufacturer": e.get("manufacturer") or "", "line": "", "name": e.get("name") or "",
                    "color_name": e.get("name") or "", "material": e.get("material") or "",
                    "color_hex": (e.get("color_hex") or "").upper()[:6], "color_hexes": hexes,
                    "multi_color_direction": e.get("multi_color_direction"),
                    "weight": e.get("weight"), "spool_weight": None if refill else e.get("spool_weight"),
                    "spool_type": e.get("spool_type"), "density": e.get("density"), "diameter": e.get("diameter") or 1.75,
                    "extruder_temp": et, "bed_temp": bt, "finish": e.get("finish"), "refill": refill or bool(e.get("is_refill")),
                    "article_number": ",".join(e.get("codes") or [])[:64],
                }
    return out


class CodeIndex:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self._lock = threading.Lock()
        self.ofd: dict[str, dict] = {}
        self.sdbc: dict[str, dict] = {}
        self.ofd_catalog: list[dict] = []   # all OFD products (search), not just the ones with a barcode
        self.catalog_version = 0            # bumped whenever the lists above change (search index rebuild)
        self.prefixes: dict[str, str] = dict(SEED_PREFIXES)
        self.status = {"ofd": None, "sdbc": None, "error": ""}
        self._stop = threading.Event()

    # ---- persistence -----------------------------------------------------
    def _path(self, name: str) -> Path:
        return self.dir / f"codeidx_{name}.json"

    def load(self) -> None:
        for name in ("ofd", "sdbc"):
            p = self._path(name)
            if p.exists():
                try:
                    blob = json.loads(p.read_text("utf-8"))
                    setattr(self, name, blob["index"])
                    self.status[name] = blob["fetched"]
                except (OSError, ValueError, KeyError) as e:
                    log.warning("could not load %s index: %s", name, e)
        p = self._path("ofdcat")
        if p.exists():
            try:
                self.ofd_catalog = json.loads(p.read_text("utf-8"))["index"]
            except (OSError, ValueError, KeyError) as e:
                log.warning("could not load OFD catalogue: %s", e)
        self.catalog_version += 1
        self._rebuild_prefixes()

    def _save(self, name: str, index: dict) -> None:
        tmp = self._path(name).with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched": time.time(), "index": index}), "utf-8")
        os.replace(tmp, self._path(name))

    # ---- refresh ---------------------------------------------------------
    def _age(self, name: str) -> float:
        return time.time() - (self.status.get(name) or 0)

    def refresh(self, force: bool = False) -> None:
        if force or self._age("ofd") > OFD_MAX_AGE or not self.ofd_catalog:
            try:
                resp = requests.get(OFD_URL, timeout=60, headers={"User-Agent": "SpoolSync/1.0"})
                resp.raise_for_status()
                raw = json.loads(gzip.decompress(resp.content))
                idx = build_ofd_index(raw)
                cat = build_ofd_catalog(raw)
                del raw
                if idx:
                    with self._lock:
                        self.ofd = idx
                        self.ofd_catalog = cat
                        self.catalog_version += 1
                        self.status["ofd"] = time.time()
                    self._save("ofd", idx)
                    self._save("ofdcat", cat)
                    log.info("Open Filament Database index: %d barcodes, %d products", len(idx), len(cat))
            except (requests.RequestException, ValueError, OSError) as e:
                self.status["error"] = f"Open Filament Database: {e}"
                log.warning("OFD refresh failed: %s", e)
        if force or self._age("sdbc") > SDBC_MAX_AGE:
            try:
                resp = requests.get(SDBC_URL, timeout=120, headers={"User-Agent": "SpoolSync/1.0"})
                resp.raise_for_status()
                idx = build_sdbc_index(resp.json())
                del resp
                if idx:
                    with self._lock:
                        self.sdbc = idx
                        self.catalog_version += 1
                        self.status["sdbc"] = time.time()
                    self._save("sdbc", idx)
                    log.info("SpoolmanDB-Community index: %d barcodes", len(idx))
            except (requests.RequestException, ValueError, OSError) as e:
                self.status["error"] = f"SpoolmanDB-Community: {e}"
                log.warning("SpoolmanDB-Community refresh failed: %s", e)
        self._rebuild_prefixes()

    def _rebuild_prefixes(self) -> None:
        """GS1 7-digit prefix -> brand, learned from the indexes (only unambiguous prefixes with >= 3 codes)."""
        counts: dict[str, dict[str, int]] = {}
        for idx in (self.ofd, self.sdbc):
            for gtin, it in idx.items():
                if len(gtin) == 13 and it.get("manufacturer"):
                    c = counts.setdefault(gtin[:7], {})
                    c[it["manufacturer"]] = c.get(it["manufacturer"], 0) + 1
        learned = dict(SEED_PREFIXES)
        for prefix, brands in counts.items():
            brand, n = max(brands.items(), key=lambda kv: kv[1])
            if n >= 3 and n / sum(brands.values()) >= 0.9:
                learned.setdefault(prefix, brand)
        self.prefixes = learned

    def start(self) -> None:
        self.load()

        def loop():
            while not self._stop.is_set():
                try:
                    self.refresh()
                except Exception:  # never let the refresher die
                    log.exception("code index refresh crashed")
                self._stop.wait(3600)
        threading.Thread(target=loop, name="codedb", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # ---- queries ---------------------------------------------------------
    def find(self, code: str) -> dict | None:
        g = _gtin13(code)
        with self._lock:
            return self.ofd.get(g) or self.sdbc.get(g)

    def brand_hint(self, code: str) -> str | None:
        g = _gtin13(code)
        for n in (9, 8, 7):
            if g[:n] in self.prefixes:
                return self.prefixes[g[:n]]
        return None

    def learn_prefix(self, code: str, brand: str) -> None:
        g = _gtin13(code)
        if len(g) == 13 and brand and g[:7] not in self.prefixes:
            self.prefixes[g[:7]] = brand

    def summary(self) -> dict:
        return {"ofd_codes": len(self.ofd), "sdbc_codes": len(self.sdbc), "ofd_updated": self.status["ofd"],
                "sdbc_updated": self.status["sdbc"], "ofd_products": len(self.ofd_catalog), "error": self.status["error"]}
