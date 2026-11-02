"""Minimal Bambu Lab cloud HTTP client (login, devices, print history).

Endpoints and login flow follow the reverse-engineered API used by ha-bambulab / OpenBambuAPI.
No developer/LAN mode is required: everything here is the same API Bambu Studio and Handy use.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

try:  # Optional: browser impersonation if Cloudflare blocks plain requests.
    from curl_cffi import requests as curl_requests  # type: ignore

    CURL_AVAILABLE = True
except Exception:  # pragma: no cover
    curl_requests = None
    CURL_AVAILABLE = False

log = logging.getLogger("spoolsync.cloud")

DEFAULT_TOKEN_LIFETIME = 90 * 24 * 3600


class CloudError(Exception):
    """Generic cloud failure. `message` is shown to the user (Danish)."""


class AuthExpired(CloudError):
    pass


class NeedCode(CloudError):
    """Bambu wants an e-mail verification code."""


class NeedTfa(CloudError):
    """Account has two-factor authentication enabled."""


@dataclass
class LoginResult:
    token: str
    refresh_token: str | None
    expires_at: float


def _hosts(region: str) -> dict[str, str]:
    tld = "cn" if region == "china" else "com"
    return {
        "api": f"https://api.bambulab.{tld}",
        "web": f"https://bambulab.{tld}",
        "mqtt": "cn.mqtt.bambulab.com" if region == "china" else "us.mqtt.bambulab.com",
    }


def _slicer_headers() -> dict[str, str]:
    return {
        "User-Agent": "bambu_network_agent/01.09.05.01",
        "X-BBL-Client-Name": "OrcaSlicer",
        "X-BBL-Client-Type": "slicer",
        "X-BBL-Client-Version": "01.09.05.51",
        "X-BBL-Language": "en-US",
        "X-BBL-OS-Type": "linux",
        "X-BBL-OS-Version": "6.2.0",
        "X-BBL-Agent-Version": "01.09.05.01",
        "X-BBL-Executable-info": "{}",
        "X-BBL-Agent-OS-Type": "linux",
        "accept": "application/json",
        "Content-Type": "application/json",
    }


def _is_cloudflare_block(resp) -> bool:
    return resp.status_code in (403, 429) and "cloudflare" in (resp.text or "").lower()


def decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(b64))
    except Exception:
        return {}


class BambuCloud:
    def __init__(self, region: str = "global", token: str | None = None):
        self.region = region
        self.hosts = _hosts(region)
        self.token = token
        self._pending_email: str | None = None
        self._tfa_key: str | None = None

    # ---- transport -------------------------------------------------------
    def _request(self, method: str, url: str, *, json_body=None, headers=None, auth=True, timeout=20):
        hdrs = dict(_slicer_headers())
        if headers:
            hdrs.update(headers)
        if auth:
            if not self.token:
                raise AuthExpired("Ikke logget ind på Bambu Cloud")
            hdrs["Authorization"] = f"Bearer {self.token}"
        try:
            resp = requests.request(method, url, json=json_body, headers=hdrs, timeout=timeout)
        except requests.RequestException as e:
            raise CloudError(f"Kunne ikke kontakte Bambu Cloud: {e}") from e
        if _is_cloudflare_block(resp) and CURL_AVAILABLE:
            log.info("Cloudflare block on %s, retrying with browser impersonation", url)
            imp_headers = {"Content-Type": "application/json"}
            if auth:
                imp_headers["Authorization"] = f"Bearer {self.token}"
            if headers:
                imp_headers.update(headers)
            resp = curl_requests.request(
                method, url, json=json_body, headers=imp_headers, timeout=timeout, impersonate="chrome"
            )
        if auth and resp.status_code == 401:
            raise AuthExpired("Bambu-login er udløbet. Log ind igen under Indstillinger.")
        return resp

    # ---- login -----------------------------------------------------------
    def _finish_login(self, data: dict) -> LoginResult:
        token = data.get("accessToken") or ""
        if not token:
            raise CloudError("Bambu returnerede intet token")
        expires_in = data.get("expiresIn") or DEFAULT_TOKEN_LIFETIME
        self.token = token
        return LoginResult(token=token, refresh_token=data.get("refreshToken"), expires_at=time.time() + float(expires_in))

    def login_password(self, email: str, password: str) -> LoginResult:
        self._pending_email = email
        resp = self._request(
            "POST",
            f"{self.hosts['api']}/v1/user-service/user/login",
            json_body={"account": email, "password": password, "apiError": ""},
            auth=False,
        )
        if resp.status_code >= 400:
            msg = _error_message(resp)
            raise CloudError(f"Login afvist af Bambu ({resp.status_code}): {msg}")
        data = resp.json()
        if data.get("accessToken"):
            return self._finish_login(data)
        login_type = data.get("loginType")
        if login_type == "verifyCode":
            raise NeedCode("Bambu har sendt en kode til din e-mail")
        if login_type == "tfa":
            self._tfa_key = data.get("tfaKey")
            raise NeedTfa("Indtast koden fra din authenticator-app")
        raise CloudError(f"Uventet svar fra Bambu: {str(data)[:200]}")

    def request_email_code(self, email: str | None = None) -> None:
        email = email or self._pending_email
        if not email:
            raise CloudError("Start med at indtaste e-mail")
        self._pending_email = email
        resp = self._request(
            "POST",
            f"{self.hosts['api']}/v1/user-service/user/sendemail/code",
            json_body={"email": email, "type": "codeLogin"},
            auth=False,
        )
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke sende kode ({resp.status_code}): {_error_message(resp)}")

    def login_code(self, code: str, email: str | None = None) -> LoginResult:
        email = email or self._pending_email
        if not email:
            raise CloudError("Start med at indtaste e-mail")
        resp = self._request(
            "POST",
            f"{self.hosts['api']}/v1/user-service/user/login",
            json_body={"account": email, "code": code.strip()},
            auth=False,
        )
        if resp.status_code == 400:
            try:
                err = resp.json().get("code")
            except Exception:
                err = None
            if err == 1:
                self.request_email_code(email)
                raise CloudError("Koden er udløbet – der er sendt en ny kode til din e-mail")
            if err == 2:
                raise CloudError("Forkert kode – prøv igen")
            raise CloudError(f"Login afvist: {_error_message(resp)}")
        if resp.status_code >= 400:
            raise CloudError(f"Login afvist ({resp.status_code}): {_error_message(resp)}")
        return self._finish_login(resp.json())

    def login_tfa(self, code: str) -> LoginResult:
        if not self._tfa_key:
            raise CloudError("Start forfra med e-mail og adgangskode")
        web = self.hosts["web"]
        csrf = requests.get(f"{web}/api/csrf", headers=_slicer_headers(), timeout=20)
        token = csrf.cookies.get("bbl_csrf_token")
        if not token:
            # The website host sometimes refuses; fall back to e-mail code login like ha-bambulab does.
            self.request_email_code()
            raise NeedCode("2FA blev afvist af Bambu – der er i stedet sendt en kode til din e-mail")
        headers = {"x-bbl-csrf-token": token, "Cookie": f"bbl_csrf_token={token}"}
        resp = self._request(
            "POST", f"{web}/api/sign-in/tfa", json_body={"tfaKey": self._tfa_key, "tfaCode": code.strip()},
            headers=headers, auth=False,
        )
        if resp.status_code == 403 and "missing_cookie" in resp.text:
            self.request_email_code()
            raise NeedCode("2FA blev afvist af Bambu – der er i stedet sendt en kode til din e-mail")
        if resp.status_code >= 400:
            raise CloudError(f"2FA afvist ({resp.status_code}): {_error_message(resp)}")
        tok = resp.cookies.get("token")
        if not tok:
            raise CloudError("Bambu returnerede intet token efter 2FA")
        self.token = tok
        exp = decode_jwt_payload(tok).get("exp")
        return LoginResult(token=tok, refresh_token=None, expires_at=float(exp) if exp else time.time() + DEFAULT_TOKEN_LIFETIME)

    def use_token(self, token: str) -> LoginResult:
        """Use a token copied from the browser (cookie 'token' on bambulab.com) – for Google/Apple logins."""
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self.token = token
        self.get_devices()  # validates
        exp = decode_jwt_payload(token).get("exp")
        return LoginResult(token=token, refresh_token=None, expires_at=float(exp) if exp else time.time() + DEFAULT_TOKEN_LIFETIME)

    # ---- data ------------------------------------------------------------
    def get_uid(self) -> str:
        """Numeric user id used as MQTT username (u_<uid>)."""
        payload = decode_jwt_payload(self.token or "")
        username = payload.get("username")
        if isinstance(username, str) and username.startswith("u_"):
            return username[2:]
        resp = self._request("GET", f"{self.hosts['api']}/v1/design-user-service/my/preference")
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke hente bruger-id ({resp.status_code})")
        uid = resp.json().get("uid")
        if not uid:
            raise CloudError("Bambu returnerede intet bruger-id")
        return str(uid)

    def get_devices(self) -> list[dict]:
        resp = self._request("GET", f"{self.hosts['api']}/v1/iot-service/api/user/bind")
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke hente printere ({resp.status_code}): {_error_message(resp)}")
        return resp.json().get("devices") or []

    def get_tasks(self, device_id: str | None = None, limit: int = 50) -> list[dict]:
        url = f"{self.hosts['api']}/v1/user-service/my/tasks?limit={int(limit)}"
        if device_id:
            url += f"&deviceId={quote(str(device_id), safe='')}"
        resp = self._request("GET", url)
        if resp.status_code == 400:
            # Older API versions rejected unknown query parameters – fall back to the plain call.
            resp = self._request("GET", f"{self.hosts['api']}/v1/user-service/my/tasks")
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke hente printhistorik ({resp.status_code}): {_error_message(resp)}")
        hits = resp.json().get("hits") or []
        if device_id:
            hits = [h for h in hits if str(h.get("deviceId", "")) == device_id]
        return hits

    # ---- model / cover of a print job (used by the live dashboard) -------------------------------
    def get_profile(self, profile_id, model_id: str) -> dict:
        """Cloud project profile of a task. Carries `url` (signed link to the project's config 3MF: plate images,
        plate_N.json, slice_info - NO mesh for projects sliced locally) and `context.plates/materials`."""
        resp = self._request(
            "GET", f"{self.hosts['api']}/v1/iot-service/api/user/profile/{quote(str(profile_id), safe='')}?model_id={quote(str(model_id), safe='')}")
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke hente projektprofil ({resp.status_code}): {_error_message(resp)}")
        return resp.json()

    def get_instance_3mf(self, instance_id) -> dict:
        """MakerWorld model instance download: {"name", "url"} - a complete 3MF WITH mesh (only for MakerWorld prints)."""
        resp = self._request(
            "GET", f"{self.hosts['api']}/v1/design-service/instance/{quote(str(instance_id), safe='')}/f3mf?type=download&fileType=")
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke hente modelfil ({resp.status_code}): {_error_message(resp)}")
        data = resp.json()
        if not data.get("url"):
            raise CloudError("Bambu returnerede intet download-link")
        return data

    # ---- slicer presets (what Bambu Studio syncs: "user presets") ----------
    def slicer_presets(self, kind: str = "filament") -> list[dict]:
        """The account's own presets of one kind: [{setting_id, name, base_id, version, ...}]."""
        resp = self._request("GET", f"{self.hosts['api']}/v1/iot-service/api/slicer/setting?version=02.06.00.03&public=false")
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke hente profiler ({resp.status_code}): {_error_message(resp)}")
        return ((resp.json().get(kind) or {}).get("private")) or []

    def slicer_preset(self, setting_id: str) -> dict:
        resp = self._request("GET", f"{self.hosts['api']}/v1/iot-service/api/slicer/setting/{quote(setting_id, safe='')}")
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke hente profilen ({resp.status_code}): {_error_message(resp)}")
        return resp.json()

    def create_slicer_preset(self, body: dict) -> dict:
        resp = self._request("POST", f"{self.hosts['api']}/v1/iot-service/api/slicer/setting", json_body=body)
        if resp.status_code >= 400:
            raise CloudError(f"Bambu afviste profilen ({resp.status_code}): {_error_message(resp)}")
        return resp.json() if resp.content else {}

    def delete_slicer_preset(self, setting_id: str) -> None:
        resp = self._request("DELETE", f"{self.hosts['api']}/v1/iot-service/api/slicer/setting/{quote(setting_id, safe='')}")
        if resp.status_code >= 400:
            raise CloudError(f"Kunne ikke slette profilen ({resp.status_code}): {_error_message(resp)}")

    @staticmethod
    def download(url: str, max_bytes: int = 60 * 1024 * 1024, timeout: int = 60) -> bytes:
        """Download a pre-signed (S3/CDN) link. NO Authorization header is sent: the links carry their own signature."""
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        if urlparse(url).scheme != "https" or not (host.endswith(".amazonaws.com") or host.endswith(".bblmw.com")
                                                   or host.endswith(".bambulab.com") or host.endswith(".bambulab.cn")):
            raise CloudError("Uventet download-adresse")
        try:
            with requests.get(url, stream=True, timeout=timeout, allow_redirects=False,   # (a redirect could leave the allowed hosts)
                              headers={"User-Agent": _slicer_headers()["User-Agent"]}) as r:
                if r.status_code >= 300:
                    raise CloudError(f"Download fejlede ({r.status_code})")
                buf = bytearray()
                for chunk in r.iter_content(65536):
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise CloudError("Filen er for stor")
                return bytes(buf)
        except requests.RequestException as e:
            raise CloudError(f"Download fejlede ({type(e).__name__})") from None   # (no signed URL in the message)

    @property
    def mqtt_host(self) -> str:
        return self.hosts["mqtt"]


# ---- printer model names -------------------------------------------------------------------------------------------
# Bambu Cloud's device list carries `dev_model_name` (a code) and `dev_product_name` (a marketing name).
MODEL_CODES = {
    "BL-P001": "X1C", "BL-P002": "X1", "C13": "X1E", "C11": "P1P", "C12": "P1S",
    "N1": "A1 mini", "N2S": "A1", "N7": "P2S",
    "O1D": "H2D", "O1E": "H2D Pro", "O1S": "H2S", "O1C": "H2C",
}
# First three characters of the serial number (fallback when the cloud gives neither code nor a known name).
SERIAL_PREFIXES = {
    "00M": "X1C", "00W": "X1", "03W": "X1E", "01S": "P1P", "01P": "P1S", "030": "A1 mini", "039": "A1",
    "093": "H2S", "094": "H2D", "239": "H2D Pro", "22E": "P2S",
}
_MODEL_NAMES = {   # normalised marketing name (lower-case, letters+digits only) -> canonical name
    "x1carbon": "X1C", "x1c": "X1C", "x1carboncombo": "X1C", "x1": "X1", "x1e": "X1E",
    "p1p": "P1P", "p1s": "P1S", "p2s": "P2S", "a1": "A1", "a1mini": "A1 mini",
    "h2d": "H2D", "h2dpro": "H2D Pro", "h2s": "H2S", "h2c": "H2C", "x2d": "X2D",
}


def _norm(v) -> str:
    return "".join(ch for ch in str(v or "").lower() if ch.isalnum())


def model_name(code_or_name, serial: str | None = None) -> str:
    """Canonical printer model ("P1S", "A1 mini", "X2D", ...) from a Bambu model code ("C12"), a product name
    ("X1 Carbon") or - last resort - the serial number prefix. Unknown values come back unchanged (stripped)."""
    raw = str(code_or_name or "").strip()
    if raw:
        if raw.upper() in MODEL_CODES:
            return MODEL_CODES[raw.upper()]
        n = _norm(raw)
        if n in _MODEL_NAMES:
            return _MODEL_NAMES[n]
        for pre in ("bambulab", "bambu"):      # "Bambu Lab P1S"
            if n.startswith(pre) and n[len(pre):] in _MODEL_NAMES:
                return _MODEL_NAMES[n[len(pre):]]
        return raw
    return SERIAL_PREFIXES.get(str(serial or "")[:3].upper(), "")


def device_model(dev: dict) -> str:
    """Model of one entry of the cloud device list: product name if known, else the model code, else the serial."""
    prod = str(dev.get("dev_product_name") or "").strip()
    code = str(dev.get("dev_model_name") or "").strip()
    serial = str(dev.get("dev_id") or dev.get("serial") or "")
    by_prod = model_name(prod)
    if by_prod and (by_prod != prod or _norm(prod) in _MODEL_NAMES):
        return by_prod
    by_code = model_name(code)
    if by_code and by_code != code:
        return by_code
    return by_prod or by_code or model_name("", serial)


def model_family(model, nozzle_count: int = 0) -> str:
    """Drawing family: "h2" (dual nozzle, X2D/H2D style), "x1p1" (enclosed single-nozzle cube) or "a1" (open
    bedslinger). Unknown models are decided by the number of nozzles reported."""
    n = _norm(model)
    if n.startswith("a1"):
        return "a1"
    if n[:2] in ("h2", "x2"):
        return "h2"
    if n[:2] in ("x1", "p1", "p2"):
        return "x1p1"
    return "h2" if nozzle_count >= 2 else "x1p1"


def ams_model_hint(model) -> str:
    """AMS flavour that belongs to a printer when the printer does not say (A1 family only has AMS lite)."""
    return "AMS lite" if model_family(model) == "a1" else "AMS"


def _error_message(resp) -> str:
    try:
        data = resp.json()
        for key in ("message", "error", "msg"):
            if data.get(key):
                return str(data[key])
        return str(data)[:200]
    except Exception:
        return (resp.text or "")[:200]
