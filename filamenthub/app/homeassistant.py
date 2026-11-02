"""Home Assistant integration: read-only snapshot of the printer + stock, and an optional MQTT discovery publisher.

Nothing here writes to the printer, Bambu Cloud or the stock. `build_snapshot` is the single source for both the
`/api/ha/summary` REST fallback and the MQTT entities.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from urllib.parse import quote
from typing import Any, Callable

from .live import parse_bed, parse_chamber, parse_nozzles
from .bambu_cloud import ams_model_hint, model_name
from .printer_state import print_summary, slot_label

log = logging.getLogger("spoolsync.ha")

SETTINGS_KEY = "ha.settings"
TOKEN_KEY = "ha.kiosk_token"
COVER_TOKEN_KEY = "ha_cover_token"   # only valid for GET /api/printer/cover (goes into the MQTT picture URL)
COVER_PATH = "/api/printer/cover"
DEFAULT_ORIGINS = ["http://homeassistant.local:8123", "https://homeassistant.local:8123"]
DEFAULT_MQTT = {"enabled": False, "host": "", "port": 1883, "user": "", "password": "", "prefix": "homeassistant"}
DEFAULTS: dict[str, Any] = {"ha_url": "", "base_url": "", "low_stock_g": 150, "mqtt": DEFAULT_MQTT}
ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9._\-]+(:\d{1,5})?$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")

STATE_DA = {"IDLE": "Ledig", "RUNNING": "Printer", "PAUSE": "Pauset", "FINISH": "Færdig", "FAILED": "Fejlet",
            "PREPARE": "Forbereder", "SLICING": "Klargør", "INIT": "Starter", "UNKNOWN": "Ukendt"}

LEGACY_SLUG = "x2d"      # entity ids / topics of the original install; kept for X2D (and while no printer is known)
BASE = f"filamenthub/{LEGACY_SLUG}"
STATE_TOPIC = f"{BASE}/state"
AVAIL_TOPIC = f"{BASE}/availability"
COVER_TOPIC = f"{BASE}/cover_url"


def model_slug(model: str | None) -> str:
    """Id fragment for the HA device: "x2d" for an X2D (or unknown printer) so existing entity ids never change,
    otherwise the slugified model ("P1S" -> "p1s", "A1 mini" -> "a1_mini")."""
    m = (model or "").strip()
    if not m or m.upper() == "X2D":
        return LEGACY_SLUG
    return re.sub(r"[^a-z0-9]+", "_", m.lower()).strip("_") or LEGACY_SLUG


def topics(slug: str = LEGACY_SLUG) -> dict[str, str]:
    base = f"filamenthub/{slug}"
    return {"state": f"{base}/state", "avail": f"{base}/availability", "cover": f"{base}/cover_url"}


def device_profile(db, state: dict | None = None) -> dict:
    """{slug, model, ams, name, title} of the connected printer, from the stored device + AMS modules."""
    dev = db.get("device") or {}
    model = model_name(dev.get("model") or dev.get("model_code"), dev.get("serial"))
    mods = db.get("modules") or {}
    names = sorted({(m or {}).get("model") for m in mods.values() if (m or {}).get("model")})
    slug = model_slug(model)
    if names:
        ams = " + ".join("AMS lite" if n.lower() == "ams lite" else n for n in names)
    elif slug == LEGACY_SLUG:
        ams = "AMS 2 Pro"            # the original install (X2D)
    else:
        ams = ams_model_hint(model) if ((state or {}).get("ams") or {}).get("ams") else ""
    label = model or "X2D"
    return {"slug": slug, "model": label, "ams": ams, "name": f"FilamentHub {label}",
            "title": f"{label} + {ams}" if ams else f"{label} (uden AMS)"}


# ------------------------------------------------------------------------------------------- settings
def norm_origin(value: str) -> str | None:
    """'http://ha.local:8123/lovelace' -> 'http://ha.local:8123' (None if it is not a plain http(s) origin)."""
    m = re.match(r"^(https?://[^/\s;,'\"]+)(/[^\s;,'\"]*)?$", (value or "").strip())
    if not m or not ORIGIN_RE.match(m.group(1)):
        return None
    return m.group(1).lower()


def load_settings(db) -> dict:
    stored = db.get(SETTINGS_KEY) or {}
    out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    out["mqtt"] = {**DEFAULT_MQTT, **(stored.get("mqtt") or {})}
    return out


def save_settings(db, s: dict) -> None:
    db.set(SETTINGS_KEY, s)
    _cache.clear()


def allowed_origins(s: dict) -> list[str]:
    seen: list[str] = []
    for o in [s.get("ha_url", "")] + DEFAULT_ORIGINS:
        n = norm_origin(o)
        if n and n not in seen:
            seen.append(n)
    return seen


_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, fn: Callable[[], Any], ttl: float = 5.0):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def frame_ancestors(db) -> str:
    """The Content-Security-Policy value that lets Home Assistant embed us (replaces X-Frame-Options)."""
    origins = _cached("origins", lambda: allowed_origins(load_settings(db)))
    return "frame-ancestors 'self' " + " ".join(origins)


CSP_BASE = ("default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; media-src 'self' blob: mediastream:; connect-src 'self'; "
            "worker-src 'self' blob:; object-src 'none'; base-uri 'none'")


def content_security_policy(db) -> str:
    """The full policy: our own resources only (+ Google Fonts), the zxing wasm of the barcode scanner, and framing
    by ourselves and the configured Home Assistant origins."""
    return f"{CSP_BASE}; {frame_ancestors(db)}"


# ------------------------------------------------------------------------------------------- kiosk token
# The read-only kiosk (/ha in an iframe) cannot rely on the session cookie (third-party cookies), so it carries a
# long random token in the URL. It is valid for these GET endpoints only - never for writes or anything else.
KIOSK_PATHS = {"/api/live", "/api/live/snapshot", "/api/printer/cover", "/api/printer/model.3mf",
               "/api/spools", "/api/ha/summary"}


def kiosk_token(db, create: bool = True) -> str:
    tok = db.get(TOKEN_KEY)
    if not tok and create:
        tok = rotate_token(db)
    return tok or ""


def rotate_token(db) -> str:
    tok = secrets.token_urlsafe(32)
    db.set(TOKEN_KEY, tok)
    db.set(COVER_TOKEN_KEY, secrets.token_urlsafe(32))
    _cache.pop("token", None)
    _cache.pop("cover_token", None)
    return tok


def cover_token(db) -> str:
    """Token for the MQTT cover-picture URL: unlike the kiosk token it opens the cover picture and nothing else."""
    tok = db.get(COVER_TOKEN_KEY)
    if not tok:
        tok = secrets.token_urlsafe(32)
        db.set(COVER_TOKEN_KEY, tok)
    return tok


def kiosk_allows(db, request) -> bool:
    """True when the request is a GET to an allowed read-only path carrying the right ?token=."""
    if request.method not in ("GET", "HEAD") or request.url.path not in KIOSK_PATHS:
        return False
    given = request.query_params.get("token") or ""
    if not given:
        return False
    real = _cached("token", lambda: db.get(TOKEN_KEY) or "", ttl=2.0)
    if real and secrets.compare_digest(given.encode(), real.encode()):
        return True
    if request.url.path == COVER_PATH:
        cover = _cached("cover_token", lambda: db.get(COVER_TOKEN_KEY) or "", ttl=2.0)
        return bool(cover) and secrets.compare_digest(given.encode(), cover.encode())
    return False


# ------------------------------------------------------------------------------------------- snapshot
def _nozzle_temps(state: dict) -> list[dict]:
    """[{temp, target}] per nozzle - same parsing as the live view (packed X2D/H2D values, flat keys as fallback)."""
    out = [{"temp": n["temp"], "target": n["target"]} for n in parse_nozzles(state)]
    while len(out) < 2:
        out.append({"temp": None, "target": None})
    return out[:2]


def slot_id(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def build_snapshot(engine, spoolman, db) -> dict:
    """Printer + AMS + stock as one flat, JSON-safe dict (read-only)."""
    s = load_settings(db)
    low_limit = float(s.get("low_stock_g") or 0)
    with engine.lock:
        state = dict(engine.state or {})
        slots = engine.current_slots()
    summary = print_summary(state) if state else None
    raw = (summary or {}).get("state", "UNKNOWN")
    online = bool(state)
    spools: list[dict] = []
    stock_ok = True
    try:
        spools = [x for x in spoolman.spools() if not x.get("archived")]
    except Exception as e:   # stock backend down: still publish the printer side
        stock_ok = False
        log.debug("stock unavailable for HA snapshot: %s", e)
    by_id = {x["id"]: x for x in spools}
    assignments = db.current_assignments()
    nz = _nozzle_temps(state)
    active = next((x for x in slots if x.active), None)

    out_slots = []
    for sl in slots:
        if sl.kind == "ext" and not (sl.present or sl.active):
            continue   # one physical holder: only list it when it is in use
        a = assignments.get(sl.key)
        sp = by_id.get(a["spool_id"]) if a else None
        f = (sp or {}).get("filament") or {}
        grams = None
        if sp is not None:
            grams = round(float(sp.get("remaining_weight") or 0), 0)
        elif sl.present and sl.remain >= 0 and sl.weight:
            grams = round(sl.weight * sl.remain / 100.0, 0)
        out_slots.append({
            "id": slot_id(sl.key), "key": sl.key, "label": sl.label, "present": sl.present, "active": sl.active,
            "material": (f.get("material") if sp else None) or sl.material or "",
            "color": ((f.get("color_hex") or "")[:6].upper() if sp else "") or sl.color or "",
            "name": f.get("name") or "", "vendor": (f.get("vendor") or {}).get("name") or "",
            "remaining_g": grams,
        })

    totals: dict[str, float] = {}
    low = []
    for x in spools:
        f = x.get("filament") or {}
        mat = f.get("material") or "Ukendt"
        rem = round(float(x.get("remaining_weight") or 0), 0)
        totals[mat] = totals.get(mat, 0) + rem
        if rem < low_limit:
            low.append({"id": x["id"], "name": f"{(f.get('vendor') or {}).get('name') or ''} {f.get('name') or ''}".strip(),
                        "material": mat, "remaining_g": rem})
    low.sort(key=lambda r: r["remaining_g"])
    return {
        "device": device_profile(db, state) | {"nozzle_count": len(parse_nozzles(state))},
        "printer": {
            "online": online, "status_raw": raw,
            "status": STATE_DA.get(raw, raw.title()) if online else "Offline",
            "percent": (summary or {}).get("percent", 0), "remaining_min": (summary or {}).get("remaining_min", 0),
            "layer": (summary or {}).get("layer", 0), "total_layers": (summary or {}).get("total_layers", 0),
            "job": (summary or {}).get("name", ""),
            "nozzle_1": nz[0]["temp"], "nozzle_2": nz[1]["temp"],
            "nozzle_1_target": nz[0]["target"], "nozzle_2_target": nz[1]["target"],
            "bed": parse_bed(state)["temp"], "chamber": parse_chamber(state)["temp"],
            "active_slot": active.label if active else "Ingen",
        },
        "slots": out_slots,
        "inventory": {
            "ok": stock_ok, "spool_count": len(spools), "total_g": round(sum(totals.values()), 0),
            "by_material": {k: round(v, 0) for k, v in sorted(totals.items())},
            "low_stock_limit_g": low_limit, "low_stock_count": len(low), "low_stock": low[:30],
        },
    }


# ------------------------------------------------------------------------------------------- discovery
_LEGACY_DEVICE = {"slug": LEGACY_SLUG, "model": "X2D", "ams": "AMS 2 Pro", "name": "FilamentHub X2D",
                  "title": "X2D + AMS 2 Pro", "nozzle_count": 2}


def _device(dev: dict | None = None) -> dict:
    d = dev or _LEGACY_DEVICE
    return {"identifiers": [f"filamenthub_{d['slug']}"], "name": d["name"], "manufacturer": "Bambu Lab",
            "model": d["title"], "sw_version": "FilamentHub"}


def _ent_for(dev: dict | None, component: str, key: str, name: str, **cfg) -> tuple[str, str, dict]:
    d = dev or _LEGACY_DEVICE
    t = topics(d["slug"])
    base = {"name": name, "unique_id": f"filamenthub_{d['slug']}_{key}",
            "state_topic": t["state"], "availability": [{"topic": t["avail"]}],
            "payload_available": "online", "payload_not_available": "offline", "device": _device(d)}
    base.update(cfg)
    return component, key, base


def discovery_entities(snap: dict, base_url_set: bool = False) -> list[tuple[str, str, dict]]:
    """[(component, key, config)] for the current snapshot (slots and materials are dynamic)."""
    dev = snap.get("device") or _LEGACY_DEVICE
    st = topics(dev["slug"])["state"]

    def _ent(component, key, name, **cfg):      # (shadows the module function: binds this snapshot's device)
        return _ent_for(dev, component, key, name, **cfg)

    two = dev["slug"] == LEGACY_SLUG or dev.get("nozzle_count", 2) >= 2
    e = [
        _ent("sensor", "status", "Printstatus", value_template="{{ value_json.printer.status }}", icon="mdi:printer-3d"),
        _ent("sensor", "progress", "Fremdrift", value_template="{{ value_json.printer.percent }}",
             unit_of_measurement="%", icon="mdi:progress-clock", state_class="measurement"),
        _ent("sensor", "remaining", "Resterende tid", value_template="{{ value_json.printer.remaining_min }}",
             unit_of_measurement="min", device_class="duration", state_class="measurement"),
        _ent("sensor", "layer", "Lag", value_template="{{ value_json.printer.layer }}", icon="mdi:layers"),
        _ent("sensor", "total_layers", "Lag i alt", value_template="{{ value_json.printer.total_layers }}", icon="mdi:layers-triple"),
        _ent("sensor", "job", "Printjob", value_template="{{ value_json.printer.job }}", icon="mdi:file-cad"),
        _ent("sensor", "nozzle_1", "Højre dyse" if two else "Dyse", value_template="{{ value_json.printer.nozzle_1 }}",
             unit_of_measurement="°C", device_class="temperature", state_class="measurement",
             json_attributes_topic=st, json_attributes_template="{{ {'target': value_json.printer.nozzle_1_target} | tojson }}"),
        _ent("sensor", "bed", "Printplade", value_template="{{ value_json.printer.bed }}",
             unit_of_measurement="°C", device_class="temperature", state_class="measurement"),
        _ent("sensor", "chamber", "Kammer", value_template="{{ value_json.printer.chamber }}",
             unit_of_measurement="°C", device_class="temperature", state_class="measurement"),
        _ent("sensor", "active_slot", "Aktiv slot", value_template="{{ value_json.printer.active_slot }}", icon="mdi:printer-3d-nozzle"),
        _ent("sensor", "spool_count", "Antal spoler", value_template="{{ value_json.inventory.spool_count }}",
             icon="mdi:counter", state_class="measurement"),
        _ent("sensor", "total_g", "Filament i alt", value_template="{{ value_json.inventory.total_g }}",
             unit_of_measurement="g", icon="mdi:weight-gram", state_class="measurement",
             json_attributes_topic=st, json_attributes_template="{{ value_json.inventory.by_material | tojson }}"),
        _ent("binary_sensor", "low_stock", "Lav beholdning", value_template="{{ 'ON' if value_json.inventory.low_stock_count > 0 else 'OFF' }}",
             payload_on="ON", payload_off="OFF", icon="mdi:alert-circle-outline",
             json_attributes_topic=st,
             json_attributes_template="{{ {'spools': value_json.inventory.low_stock, 'count': value_json.inventory.low_stock_count, 'limit_g': value_json.inventory.low_stock_limit_g} | tojson }}"),
    ]
    if two:
        e.insert(7, _ent("sensor", "nozzle_2", "Venstre dyse", value_template="{{ value_json.printer.nozzle_2 }}",
             unit_of_measurement="°C", device_class="temperature", state_class="measurement",
             json_attributes_topic=st, json_attributes_template="{{ {'target': value_json.printer.nozzle_2_target} | tojson }}"))
    for mat in snap["inventory"]["by_material"]:
        k = "total_" + re.sub(r"[^a-z0-9]+", "_", mat.lower()).strip("_")
        e.append(_ent("sensor", k, f"Beholdning {mat}", value_template="{{ value_json.inventory.by_material[%s] | default(0) }}" % json.dumps(mat),
                      unit_of_measurement="g", icon="mdi:weight-gram", state_class="measurement"))
    for sl in snap["slots"]:
        sid, lab = sl["id"], sl["label"]
        if not lab.startswith(("AMS", "Ekstern")):
            lab = "AMS " + lab      # entity ids become ..._ams_a1_materiale
        at = "(value_json.slots.get(%s) or {})" % json.dumps(sid)
        e.append(_ent("sensor", f"{sid}_material", f"{lab} materiale", value_template="{{ %s.get('material', '') }}" % at,
                      icon="mdi:printer-3d-nozzle-outline"))
        e.append(_ent("sensor", f"{sid}_color", f"{lab} farve",
                      value_template="{{ ('#' ~ %s.get('color')) if %s.get('color') else '' }}" % (at, at),
                      icon="mdi:palette", json_attributes_topic=st,
                      json_attributes_template="{{ {'hex': %s.get('color', ''), 'name': %s.get('name', ''), 'vendor': %s.get('vendor', ''), 'active': %s.get('active', false)} | tojson }}" % (at, at, at, at)))
        e.append(_ent("sensor", f"{sid}_remaining", f"{lab} resterende",
                      value_template="{{ %s.get('remaining_g', none) }}" % at,
                      unit_of_measurement="g", icon="mdi:weight-gram", state_class="measurement"))
    if base_url_set:
        e.append(("image", "cover", {
            "name": "Printbillede", "unique_id": f"filamenthub_{dev['slug']}_cover",
            "url_topic": topics(dev["slug"])["cover"], "availability": [{"topic": topics(dev["slug"])["avail"]}],
            "payload_available": "online", "payload_not_available": "offline", "device": _device(dev)}))
    return e


def state_payload(snap: dict) -> dict:
    return {"printer": snap["printer"], "inventory": snap["inventory"], "slots": {s["id"]: s for s in snap["slots"]}}


# ------------------------------------------------------------------------------------------- publisher
class Publisher:
    """Publishes discovery + state to HA's MQTT broker. Runs only when enabled in the settings."""

    MIN_INTERVAL = 3.0       # seconds between state publishes
    HEARTBEAT = 120.0        # republish an unchanged state this often

    def __init__(self, db, snapshot: Callable[[], dict], base_url: Callable[[], str] = lambda: "",
                 cover_token: Callable[[], str] = lambda: ""):
        self.db, self.snapshot, self.base_url, self.cover_token = db, snapshot, base_url, cover_token
        self.client = None
        self.thread: threading.Thread | None = None
        self.stop_ev = threading.Event()
        self.status = "disabled"
        self.detail = ""
        self._cfg: dict | None = None
        self._discovered: set[tuple[str, str]] = set()
        self._last_state: str | None = None
        self._last_cover: str | None = None
        self._last_pub = 0.0
        self._connected = threading.Event()
        self._pub_slug: str | None = None

    def _slug(self) -> str:
        try:
            return device_profile(self.db)["slug"]
        except Exception:
            return LEGACY_SLUG

    # -- lifecycle
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_ev.clear()
        self.thread = threading.Thread(target=self._run, name="ha-mqtt", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_ev.set()
        self._disconnect(clean=True)
        if self.thread:
            self.thread.join(timeout=3)

    def _make_client(self, cfg: dict):
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"filamenthub-{self._slug()}", clean_session=True)
        if cfg.get("user"):
            c.username_pw_set(cfg["user"], cfg.get("password") or None)
        c.will_set(topics(self._slug())["avail"], "offline", qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.reconnect_delay_set(2, 60)
        return c

    def _on_connect(self, client, userdata, flags, rc, props=None):
        ok = not getattr(rc, "is_failure", rc != 0)
        if ok:
            self.status, self.detail = "connected", ""
            self._discovered.clear()
            self._last_state = None
            self._connected.set()
        else:
            self.status, self.detail = "refused", str(rc)

    def _on_disconnect(self, client, userdata, *args):
        self._connected.clear()
        if self.status == "connected":
            self.status = "reconnecting"

    def _disconnect(self, clean: bool) -> None:
        c, self.client = self.client, None
        self._connected.clear()
        if c is None:
            return
        try:
            if clean and self.status == "connected":
                c.publish(topics(self._slug())["avail"], "offline", qos=1, retain=True).wait_for_publish(2)
            c.loop_stop()
            c.disconnect()
        except Exception:
            pass

    def _run(self) -> None:
        while not self.stop_ev.wait(2.0):
            try:
                cfg = load_settings(self.db)["mqtt"]
                if not cfg.get("enabled") or not cfg.get("host"):
                    if self.client:
                        self._disconnect(clean=True)
                    self.status, self._cfg = "disabled", None
                    continue
                key = (cfg["host"], cfg["port"], cfg.get("user"), cfg.get("password"))
                if self.client is None or self._cfg != key:
                    self._disconnect(clean=True)
                    self._cfg = key
                    self.status, self.detail = "connecting", ""
                    self.client = self._make_client(cfg)
                    try:
                        self.client.connect_async(cfg["host"], int(cfg["port"]), keepalive=60)
                        self.client.loop_start()
                    except Exception as e:
                        self.status, self.detail = "error", str(e)[:120]
                        self.client = None
                        continue
                if self._connected.is_set():
                    self.publish_once(cfg)
            except Exception:
                log.exception("HA publisher loop failed")

    # -- publishing
    def publish_once(self, cfg: dict | None = None) -> None:
        cfg = cfg or load_settings(self.db)["mqtt"]
        snap = self.snapshot()
        base = self.base_url()
        pre = cfg.get("prefix") or "homeassistant"
        slug = (snap.get("device") or {}).get("slug", LEGACY_SLUG)
        t = topics(slug)
        if self._pub_slug not in (None, slug):       # the printer changed: start discovery over under the new id
            self._discovered.clear()
            self._last_state = self._last_cover = None
        self._pub_slug = slug
        ents = discovery_entities(snap, base_url_set=bool(base))
        new = [(c, k, conf) for c, k, conf in ents if (c, k) not in self._discovered]
        if new:
            for comp, key, conf in new:
                self.client.publish(f"{pre}/{comp}/filamenthub_{slug}/{key}/config", json.dumps(conf), qos=1, retain=True)
                self._discovered.add((comp, key))
            self.client.publish(t["avail"], "online", qos=1, retain=True)
        now = time.time()
        payload = json.dumps(state_payload(snap), sort_keys=True)
        if (payload != self._last_state and now - self._last_pub >= self.MIN_INTERVAL) or now - self._last_pub >= self.HEARTBEAT \
                or self._last_state is None:
            self.client.publish(t["state"], payload, qos=0, retain=True)
            self._last_state, self._last_pub = payload, now
        if base:
            cover = f"{base.rstrip('/')}/api/printer/cover?token={self.cover_token()}&job={quote(snap['printer']['job'])}"
            if cover != self._last_cover:
                self.client.publish(t["cover"], cover, qos=0, retain=True)
                self._last_cover = cover
