"""Groups versions of the same model (yubikey_5c_nfc_v2, yubikey_5c_nfc_lock, yubikey_noglering ...) so the library
shows one card per model. Two files in the same folder are versions of each other when their names start with the
same words: two or more words, or one distinctive word of 6+ letters; generic words ('bambu', 'holder') do not count.
Groups are transitive (single linkage). Per model the user can override: grp '-' = never grouped,
grp 'with:<id>' = always in the same group as model <id>."""
from __future__ import annotations

import re

STOP = {"bambu", "bambulab", "lab", "test", "print", "prints", "model", "models", "new", "ny", "nye", "the", "my", "min",
        "calibration", "kalibrering", "x1c", "x1e", "x2d", "p1s", "p1p", "p2s", "a1", "h2d", "h2s", "plate", "plade",
        "part", "parts", "del", "dele", "box", "holder", "holders", "case", "cover", "mount", "stand", "spool",
        "filament", "openscad", "remix", "custom", "simple", "small", "large", "mini"}
VERSION = re.compile(r"^(v\d+[a-z]?|\d+|rev\d*|r\d+|final|copy|kopi|fixed|fix|old|gammel|ny|new)$")
MIN_WORD = 6


def tokens(name: str) -> list[str]:
    s = (name or "").lower()
    s = re.sub(r"\.(gcode\.)?(3mf|stl)$", "", s.strip())
    s = re.sub(r"\(\d+\)", " ", s)                       # 'file (2)' duplicates
    return [t for t in re.split(r"[^0-9a-zæøå]+", s) if t and not VERSION.match(t)]


def _common(a: list[str], b: list[str]) -> list[str]:
    out = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return out


def related(a: list[str], b: list[str]) -> bool:
    """Same first words; generic words ('bambu', 'lab', 'x2d', 'holder' ...) do not count."""
    c = [t for t in _common(a, b) if t not in STOP]
    if len(c) >= 2:
        return True
    return len(c) == 1 and len(c[0]) >= MIN_WORD


def file_date(m: dict) -> float:
    """The model's own date: when the 3MF was saved (from inside the file), else the file's date on disk."""
    return ((m.get("meta") or {}).get("saved")) or m.get("mtime") or 0


def folder_of(m: dict) -> str:
    return m["relpath"].rsplit("/", 1)[0] if "/" in m["relpath"] else ""


def stem(m: dict) -> str:
    return m["relpath"].rsplit("/", 1)[-1]


def group(models: list[dict]) -> list[list[dict]]:
    """Groups, each sorted newest first (file_date, then added); groups in the input's order of their newest."""
    ids = [m["id"] for m in models]
    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    toks = {m["id"]: tokens(stem(m)) for m in models}
    by_folder: dict[str, list[dict]] = {}
    for m in models:
        if (m.get("grp") or "") != "-":
            by_folder.setdefault(folder_of(m), []).append(m)
    for ms in by_folder.values():
        for i, a in enumerate(ms):
            for b in ms[i + 1:]:
                if related(toks[a["id"]], toks[b["id"]]):
                    union(a["id"], b["id"])
    for m in models:
        g = m.get("grp") or ""
        if g.startswith("with:"):
            try:
                other = int(g[5:])
            except ValueError:
                continue
            if other in parent:
                union(m["id"], other)
    groups: dict[int, list[dict]] = {}
    for m in models:
        groups.setdefault(find(m["id"]), []).append(m)
    out = [sorted(g, key=lambda m: (file_date(m), m.get("added") or 0), reverse=True) for g in groups.values()]
    order = {m["id"]: i for i, m in enumerate(models)}
    out.sort(key=lambda g: min(order[m["id"]] for m in g))
    return out


def label(members: list[dict]) -> str:
    """Shared words of the group's file names, e.g. 'yubikey' or 'yubikey 5c nfc'."""
    ts = [tokens(stem(m)) for m in members]
    c = ts[0]
    for t in ts[1:]:
        c = _common(c, t)
    return " ".join(c) if c else (ts[0][0] if ts and ts[0] else "")
