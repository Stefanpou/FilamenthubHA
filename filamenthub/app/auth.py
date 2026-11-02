"""App login: one password, long-lived signed session cookie. Setup only from the local network."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from . import config
from .db import Database

COOKIE = "spoolsync_session"
SESSION_MAX_AGE = 365 * 24 * 3600
_failures: dict[str, deque] = defaultdict(deque)


def _private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def client_ip(request: Request) -> str:
    """Best guess of the real client IP (Cloudflare tunnel / reverse proxy aware).

    Walks the proxy chain from the nearest hop: proxies on our own network append the address they saw, so the
    first address that is not on our network is the client. Anything further left - and headers like
    CF-Connecting-IP / X-Real-IP, which most reverse proxies pass through unchanged - can be forged by the client.
    """
    peer = request.client.host if request.client else ""
    chain = _header_values(request, "x-forwarded-for") + [peer]
    for addr in reversed(chain):
        if addr and not _private(addr):
            return addr
    # The whole chain is on our network: a LAN client, or a tunnel (cloudflared) that only sets its own header.
    cf = _header_values(request, "cf-connecting-ip")
    if len(cf) == 1 and _private(peer):
        return cf[0]
    return chain[0] or peer


def _header_values(request: Request, name: str) -> list[str]:
    """Every comma-separated value of every line of a header (a proxy may add its own line instead of appending)."""
    return [x.strip() for line in request.headers.getlist(name) for x in line.split(",") if x.strip()]


def _host_name(value: str) -> str:
    v = value.strip().lower()
    if v.startswith("["):
        return v[1:v.find("]")] if "]" in v else v[1:]
    return (v.rsplit(":", 1)[0] if v.count(":") == 1 else v).rstrip(".")


def host_ok(request: Request) -> bool:
    """DNS-rebinding guard: the Host header must be an IP literal, localhost, *.local or in FILAMENTHUB_ALLOWED_HOSTS,
    and an Origin header (if any) must name that same host. (A missing Host - HTTP/1.0 - is fine: browsers send one.)"""
    host = request.headers.get("host")
    if host is None:
        return request.headers.get("origin") is None
    name = _host_name(host)
    ok = name == "localhost" or name.endswith(".local") or name in config.ALLOWED_HOSTS
    if not ok:
        try:
            ipaddress.ip_address(name)
            ok = True
        except ValueError:
            pass
    if not ok:
        return False
    origin = request.headers.get("origin")
    if origin is not None:
        netloc = origin.split("://", 1)[1] if "://" in origin else ""
        if not netloc or netloc.rstrip("/").lower() != host.strip().lower():
            return False
    return True


INGRESS_PATH_RE = re.compile(r"^/api/hassio_ingress/[A-Za-z0-9_\-]+$")


def ingress_path(request: Request) -> str | None:
    """The validated X-Ingress-Path of a Home Assistant Ingress request (add-on mode only), else None."""
    if not config.ADDON:
        return None
    v = request.headers.get("x-ingress-path", "")
    return v if INGRESS_PATH_RE.match(v) else None


def is_ingress(request: Request) -> bool:
    """Add-on mode: the request comes from the HA Supervisor proxy, which has already authenticated the user."""
    peer = request.client.host if request.client else ""
    return peer == config.INGRESS_IP and ingress_path(request) is not None


def is_local(request: Request) -> bool:
    """True only if the peer *and* every forwarded address are on a private network and the Host/Origin are ours."""
    if is_ingress(request):
        return True
    return _private_chain(request) and host_ok(request)


def _private_chain(request: Request) -> bool:
    """The peer *and* every forwarded address are on a private network.

    A proxy always adds the real (public) address, so a forged private header cannot hide it.
    """
    candidates = [request.client.host if request.client else ""]
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        candidates += _header_values(request, header)
    for c in candidates:
        try:
            ip = ipaddress.ip_address(c)
        except ValueError:
            return False
        if not (ip.is_private or ip.is_loopback or ip.is_link_local):
            return False
    return True


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8", "surrogatepass"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def password_set(db: Database) -> bool:
    return bool(db.get("app_password"))


def login_required(db: Database) -> bool:
    """The app login can be switched off in Settings (e.g. behind Cloudflare Access). On by default."""
    return db.get("login_required", True) is not False


def bump_sessions(db: Database) -> None:
    """Revoke every issued session (they carry the generation they were created in)."""
    db.set("session_gen", int(db.get("session_gen") or 0) + 1)


def set_password(db: Database, password: str) -> None:
    salt = secrets.token_bytes(16)
    db.set("app_password", {"salt": salt.hex(), "hash": _hash(password, salt)})
    db.set("session_secret", secrets.token_hex(32))  # rotating the secret logs out all devices
    bump_sessions(db)


def check_password(db: Database, password: str) -> bool:
    rec = db.get("app_password")
    if not rec:
        return False
    return hmac.compare_digest(rec["hash"], _hash(password, bytes.fromhex(rec["salt"])))


def _secret(db: Database) -> bytes:
    s = db.get("session_secret")
    if not s:
        s = secrets.token_hex(32)
        db.set("session_secret", s)
    return s.encode()


def _gen(db: Database) -> int:
    try:
        return int(db.get("session_gen") or 0)
    except (TypeError, ValueError):
        return 0


def make_session(db: Database) -> str:
    issued, gen = str(int(time.time())), _gen(db)
    sig = hmac.new(_secret(db), f"{issued}.{gen}".encode(), hashlib.sha256).hexdigest()
    return f"{issued}.{gen}.{sig}"


def valid_session(db: Database, token: str | None) -> bool:
    if not token or token.count(".") != 2:
        return False
    issued, gen, sig = token.split(".")
    if not (issued.isascii() and gen.isascii() and sig.isascii()):
        return False   # (hmac.compare_digest refuses non-ASCII strings with a TypeError)
    expected = hmac.new(_secret(db), f"{issued}.{gen}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(gen) == _gen(db) and time.time() - int(issued) < SESSION_MAX_AGE
    except ValueError:
        return False


GLOBAL = "*internet*"   # failed logins from outside the LAN, all addresses together


def _recent(key: str, now: float) -> int:
    q = _failures.get(key)
    if not q:
        return 0
    while q and now - q[0] > 900:
        q.popleft()
    return len(q)


def _lan_peer(request: Request) -> str | None:
    """Bucket for a request whose whole chain is on our network: the TCP peer. Such a client can put any private
    address into the forwarded headers, so they must not pick its bucket alone."""
    return f"lan:{request.client.host if request.client else ''}" if _private_chain(request) else None


def rate_limited(request: Request) -> bool:
    """10 failed logins per address and 15 minutes; 30 in total from the internet (rotating addresses) and 30 per
    LAN device (rotating forged private addresses)."""
    now = time.time()
    if _recent(client_ip(request), now) >= 10:
        return True
    lan = _lan_peer(request)
    return _recent(lan, now) >= 30 if lan else _recent(GLOBAL, now) >= 30


def record_failure(request: Request) -> None:
    now = time.time()
    if len(_failures) > 1000:   # bounded: drop addresses with no recent failures
        for k in [k for k in _failures if not _recent(k, now)]:
            del _failures[k]
    _failures[client_ip(request)].append(now)
    _failures[_lan_peer(request) or GLOBAL].append(now)


_attempt_lock = threading.Lock()


def begin_attempt(request: Request) -> bool:
    """Count a login attempt BEFORE the (slow) password check, atomically with the limit check, so a parallel burst
    cannot get more guesses than the limit. False = limited. A success is taken back with `forgive`."""
    with _attempt_lock:
        if rate_limited(request):
            return False
        record_failure(request)
        return True


def forgive(request: Request) -> None:
    with _attempt_lock:
        for k in (client_ip(request), _lan_peer(request) or GLOBAL):
            q = _failures.get(k)
            if q:
                q.pop()


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https" \
        or bool(request.headers.get("cf-visitor", "").find("https") >= 0)


# ---- Spoolman-compatible API (/api/v1): no session cookie for Home Assistant / OrcaSlicer ---------------------------
API_PREFIX = "/api/v1"
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "cf-connecting-ip", "cf-ray", "forwarded", "x-forwarded-host")


def new_api_token() -> str:
    return secrets.token_urlsafe(32)


def api_token(db: Database) -> str:
    """The API token (shown in the settings); generated on first use."""
    tok = db.get("api_token")
    if not tok:
        tok = new_api_token()
        db.set("api_token", tok)
    return tok


def rotate_api_token(db: Database) -> str:
    tok = new_api_token()
    db.set("api_token", tok)
    return tok


def has_valid_api_token(db: Database, request: Request) -> bool:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    given, real = header[7:].strip(), db.get("api_token")
    return bool(real) and given.isascii() and hmac.compare_digest(given, real)


def is_direct_lan(request: Request) -> bool:
    """A direct request from a private address: no proxy header at all (a reverse proxy or the Cloudflare tunnel
    always adds one, so anything that went through one needs a session or the API token)."""
    if any(request.headers.get(h) for h in _PROXY_HEADERS):
        return False
    return is_local(request)
