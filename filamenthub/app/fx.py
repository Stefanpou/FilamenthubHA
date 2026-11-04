"""EUR -> DKK exchange rate (ECB reference rate, fetched at most twice a day)."""
from __future__ import annotations

import logging
import re
import threading
import time

import requests

log = logging.getLogger("spoolsync.fx")

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FALLBACK = 7.46   # the krone is pegged to the euro (central rate 7.46038)
MAX_AGE = 12 * 3600
_lock = threading.Lock()


def eur_dkk(db, fetch=None) -> dict:
    """{rate, date, source}: the cached ECB rate, refreshed when older than MAX_AGE; the peg when nothing is known."""
    with _lock:
        cached = db.get("fx_eur_dkk")
        if cached and time.time() - cached.get("fetched", 0) < MAX_AGE:
            return {k: cached[k] for k in ("rate", "date", "source")}
        try:
            text = (fetch or _fetch)()
            rate = float(re.search(r"currency=['\"]DKK['\"]\s+rate=['\"]([\d.]+)", text).group(1))
            m = re.search(r"time=['\"](\d{4}-\d\d-\d\d)", text)
            if not 6 < rate < 9:
                raise ValueError(f"implausible rate {rate}")
            cached = {"rate": rate, "date": m.group(1) if m else "", "source": "ECB", "fetched": time.time()}
            db.set("fx_eur_dkk", cached)
        except (requests.RequestException, AttributeError, ValueError) as e:
            log.info("EUR/DKK rate not fetched: %s", e)
            if cached:   # stale, but better than the peg; try again in an hour
                db.set("fx_eur_dkk", {**cached, "fetched": time.time() - MAX_AGE + 3600})
            else:
                return {"rate": FALLBACK, "date": "", "source": "fast"}
        return {k: cached[k] for k in ("rate", "date", "source")}


def _fetch() -> str:
    r = requests.get(ECB_URL, timeout=8)
    r.raise_for_status()
    return r.text
