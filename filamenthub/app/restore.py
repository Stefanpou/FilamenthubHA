"""Applies a data import (app/api_setup.py /api/setup/import) before the app starts. No app imports here: this runs
before the database is opened (add-on run script: `python3 -c "from app.restore import apply; apply('/data')"`)."""
from __future__ import annotations

import shutil
import sys
import tarfile
import time
from pathlib import Path

IMPORT_NAME = "import.tar.gz"
REQUIRED = "spoolsync.db"


def _members(tar: tarfile.TarFile) -> list[str]:
    return [n.lstrip("./") for n in tar.getnames()]


def check(path: Path) -> None:
    """ValueError unless it is a gzip tar of a data directory (spoolsync.db at the top)."""
    try:
        with tarfile.open(path, "r:gz") as t:
            names = _members(t)
    except (tarfile.TarError, OSError, EOFError) as e:
        raise ValueError(f"Ikke en gyldig .tar.gz: {e}") from None
    if REQUIRED not in names:
        raise ValueError(f"{REQUIRED} mangler i backuppen")


def apply(data_dir: str | Path) -> bool:
    """If data_dir/import.tar.gz exists: move the current contents to data_dir/pre-import-<time>/, unpack the
    import, delete the archive. Returns True if something was imported."""
    d = Path(data_dir)
    src = d / IMPORT_NAME
    if not src.exists():
        return False
    try:
        check(src)
    except ValueError as e:
        print(f"[restore] import ignored: {e}", file=sys.stderr)
        src.rename(d / (IMPORT_NAME + ".bad"))
        return False
    keep = d / f"pre-import-{time.strftime('%Y%m%d-%H%M%S')}"
    keep.mkdir()
    for p in d.iterdir():
        if p != src and p != keep and not p.name.startswith("pre-import-"):
            shutil.move(str(p), keep / p.name)
    with tarfile.open(src, "r:gz") as t:
        t.extractall(d, filter="data")       # no absolute paths, no "..", no device files
    src.unlink()
    print(f"[restore] imported data, previous contents kept in {keep.name}", file=sys.stderr)
    return True
