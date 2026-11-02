"""Barcode handling: code normalisation, product-title parsing and matching against Spoolman's filament database."""
from __future__ import annotations

import re
import unicodedata

# ----------------------------------------------------------------------------- codes
def normalize_code(raw: str) -> str:
    """Trim a scanned value. Numeric product codes keep only digits; anything else is returned stripped."""
    s = (raw or "").strip()
    digits = re.sub(r"[\s-]", "", s)
    if digits.isascii() and digits.isdigit():
        # UPC-A scanned as EAN-13 gets a leading 0; store the 12-digit UPC form for consistency.
        if len(digits) == 13 and digits.startswith("0") and gtin_valid(digits[1:]):
            return digits[1:]
        return digits
    return s


def gtin_valid(code: str) -> bool:
    """Check digit validation for EAN-8, UPC-A (12), EAN-13 and GTIN-14."""
    if not (code.isascii() and code.isdigit()) or len(code) not in (8, 12, 13, 14):
        return False
    body, check = code[:-1], int(code[-1])
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def lookup_variants(code: str) -> list[str]:
    """Equivalent forms of a GTIN (UPC-A <-> EAN-13 with leading zero) used when comparing stored codes."""
    out = [code]
    if code.isascii() and code.isdigit():
        if len(code) == 12:
            out.append("0" + code)
        elif len(code) == 13 and code.startswith("0"):
            out.append(code[1:])
    return out


def parse_spoolman_qr(text: str) -> tuple[str, int] | None:
    """('spool'|'filament', id) for Spoolman label QR codes such as 'web+spoolman:s-12' or '.../spool/show/12'."""
    t = (text or "").strip()
    m = re.match(r"^web\+spoolman:(s|f)-(\d+)$", t, re.I)
    if m:
        return ("spool" if m.group(1).lower() == "s" else "filament", int(m.group(2)))
    m = re.search(r"/(spool|filament)/show/(\d+)", t, re.I)
    if m:
        return (m.group(1).lower(), int(m.group(2)))
    return None


# ----------------------------------------------------------------------------- title parsing
MATERIAL_PATTERNS: list[tuple[str, str]] = [
    # (regex, canonical material) – longest / most specific first
    (r"\bpetg[\s-]*cf\b|\bcf[\s-]*petg\b|carbon[\s-]*fib(?:er|re)\s+petg|petg\s+carbon", "PETG-CF"),
    (r"\bpla[\s-]*cf\b|\bcf[\s-]*pla\b|carbon[\s-]*fib(?:er|re)\s+pla|pla\s+carbon", "PLA-CF"),
    (r"\bpa(?:6|12|-?ht)?[\s-]*cf\b|nylon[\s-]*cf|carbon[\s-]*fib(?:er|re)\s+nylon", "PA-CF"),
    (r"\bpet[\s-]*cf\b", "PET-CF"),
    (r"\basa[\s-]*cf\b", "ASA-CF"),
    (r"\bpetg\b", "PETG"),
    (r"\bpla\s*\+|\bpla[\s-]*plus\b|\bpla[\s-]*pro\b|\bpla\s*\(\+\)", "PLA+"),
    (r"\bpla\b", "PLA"),
    (r"\babs\b", "ABS"),
    (r"\basa\b", "ASA"),
    (r"\btpu\b|\bflexible\b", "TPU"),
    (r"\bpolycarbonate\b|\bpc\b", "PC"),
    (r"\bpa(?:6|12)?\b|\bnylon\b", "PA"),
    (r"\bpva\b", "PVA"),
    (r"\bhips\b", "HIPS"),
    (r"\bpet\b", "PET"),
]

COLOR_WORDS = [
    "black", "white", "grey", "gray", "silver", "gold", "red", "blue", "green", "yellow", "orange", "purple", "violet",
    "pink", "brown", "beige", "ivory", "bone", "cream", "transparent", "clear", "translucent", "natural", "navy", "cyan",
    "magenta", "olive", "mint", "lime", "teal", "turquoise", "bronze", "copper", "charcoal", "jade", "cocoa", "latte",
    "sky", "sakura", "lavender", "maroon", "burgundy", "khaki", "sand", "titan", "titanium", "ash", "iron", "marble",
    "wood", "glow", "rainbow", "dark", "light", "matte", "silk", "sparkle", "galaxy", "metallic", "neon", "pastel",
    "fluorescent", "cold", "warm", "army", "forest", "grass", "ocean", "ice", "space", "jet", "pure", "bambu",
]

FILLER_WORDS = {
    "3d", "printer", "filament", "printing", "print", "spool", "spools", "dimensional", "accuracy", "mm", "pack",
    "cardboard", "refill", "bundle", "for", "fdm", "high", "speed", "hs", "rapid", "tough", "basic", "the", "and",
    "with", "of", "1", "2", "3", "4", "kg", "g", "lbs", "lb", "roll", "rolls", "new", "version", "plastic", "material",
    "compatible", "most", "printers", "vacuum", "packed", "packaging", "reel", "neatly", "wound", "tangle", "free",
    "tolerance", "diameter", "net", "weight", "strength", "impact", "low", "warping", "bubble",
}


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", " ", s).strip()


def parse_weight(title: str) -> float | None:
    t = title.lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|kilo|g|grams?|lbs?|pounds?)\b", t)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2)
    if unit.startswith(("kg", "kilo")):
        grams = val * 1000
    elif unit.startswith(("lb", "pound")):
        grams = val * 453.592
    else:
        grams = val
    if grams < 50 or grams > 20000:
        return None
    # Snap near-round numbers (2.2 lbs = 998 g -> 1000 g).
    for nice in (250, 500, 750, 800, 1000, 2000, 3000, 5000, 10000):
        if abs(grams - nice) / nice < 0.03:
            return float(nice)
    return round(grams)


def parse_diameter(title: str) -> float | None:
    m = re.search(r"\b(1[.,]75|2[.,]85|3[.,]0{1,2})\s*mm\b", title.lower())
    return float(m.group(1).replace(",", ".")) if m else None


def parse_material(title: str) -> str | None:
    t = title.lower()
    for pattern, mat in MATERIAL_PATTERNS:
        if re.search(pattern, t):
            return mat
    return None


def norm_vendor(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (v or "").lower())


def parse_vendor(title: str, brand: str | None, known_vendors: list[str]) -> str | None:
    if brand and brand.strip():
        b = norm_vendor(brand)
        for v in known_vendors:
            if norm_vendor(v) == b or (len(b) >= 4 and norm_vendor(v).startswith(b)) or (len(norm_vendor(v)) >= 4 and b.startswith(norm_vendor(v))):
                return v
        return brand.strip()
    t = norm_vendor(title)
    best = None
    for v in known_vendors:
        nv = norm_vendor(v)
        if len(nv) >= 3 and nv in t and (best is None or len(nv) > len(norm_vendor(best))):
            best = v
    return best


def parse_color_words(title: str, vendor: str | None) -> list[str]:
    t = title.lower()
    if vendor:
        t = re.sub(re.escape(vendor.lower()), " ", t)
    t = re.sub(r"\d+(?:[.,]\d+)?\s*(kg|g|lbs?|mm)\b", " ", t)
    for pattern, _ in MATERIAL_PATTERNS:
        t = re.sub(pattern, " ", t)
    words = re.findall(r"[a-zæøå]+", t)
    return [w for w in words if w in COLOR_WORDS]


def parse_title(title: str, brand: str | None = None, known_vendors: list[str] | None = None) -> dict:
    title = _clean(title)
    vendor = parse_vendor(title, brand, known_vendors or [])
    colors = parse_color_words(title, vendor)
    return {
        "title": title,
        "vendor": vendor,
        "material": parse_material(title),
        "weight": parse_weight(title),
        "diameter": parse_diameter(title),
        "color_words": colors,
    }


# ----------------------------------------------------------------------------- SpoolmanDB matching
def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in FILLER_WORDS}


def match_external(parsed: dict, externals: list[dict]) -> tuple[dict | None, float]:
    """Best SpoolmanDB record for a parsed title. Returns (record, score 0..1)."""
    vendor = norm_vendor(parsed.get("vendor") or "")
    material = (parsed.get("material") or "").upper()
    weight = parsed.get("weight")
    diameter = parsed.get("diameter") or 1.75
    title_tokens = _tokens(parsed.get("title") or "")
    colors = set(parsed.get("color_words") or [])
    best, best_score = None, 0.0
    for ext in externals:
        if vendor and norm_vendor(ext.get("manufacturer") or "") != vendor:
            continue
        ext_mat = (ext.get("material") or "").upper()
        if material and not _material_compatible(material, ext_mat):
            continue
        if abs(float(ext.get("diameter") or 1.75) - diameter) > 0.1:
            continue
        name_tokens = _tokens(ext.get("name") or "")
        if not name_tokens:
            continue
        # How much of the record's name is explained by the title (colour + product line words).
        covered = len(name_tokens & title_tokens) / len(name_tokens)
        color_hit = 1.0 if colors and (name_tokens & colors) else 0.0
        score = 0.6 * covered + 0.25 * color_hit
        if weight and ext.get("weight"):
            score += 0.15 if abs(float(ext["weight"]) - weight) < 1 else -0.2
        if material and ext_mat == material:
            score += 0.05
        if score > best_score:
            best, best_score = ext, score
    return best, round(min(best_score, 1.0), 3)


def _material_compatible(parsed: str, ext: str) -> bool:
    if parsed == ext:
        return True
    base = parsed.replace("+", "")
    return ext.startswith(base) and ("CF" in parsed) == ("CF" in ext)
