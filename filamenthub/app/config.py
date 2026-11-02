"""Runtime configuration, read from environment variables (see deploy/spoolsync.env)."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SPOOLSYNC_DATA_DIR", "/var/lib/spoolsync"))
DB_PATH = DATA_DIR / "spoolsync.db"

# FilamentHub keeps its own inventory (app/store.py); Spoolman is no longer needed. These are kept only so old
# environment files and code that still reads them keep working - the UI never links to Spoolman any more.
SPOOLMAN_URL = os.environ.get("SPOOLMAN_URL", "").rstrip("/")
SPOOLMAN_PUBLIC_URL = ""

# Shadow mode: never write to Bambu's Filament Manager (create/update/delete/slot-mapping sync); reads still happen.
SHADOW = os.environ.get("FILAMENTHUB_SHADOW", "0") == "1"

# Home Assistant add-on mode: running behind HA Ingress (the Supervisor proxy, always 172.30.32.2, already logged the user in).
ADDON = os.environ.get("FILAMENTHUB_ADDON", "0") == "1"
INGRESS_IP = "172.30.32.2"

HOST = os.environ.get("SPOOLSYNC_HOST", "0.0.0.0")
PORT = int(os.environ.get("SPOOLSYNC_PORT", "8080"))

# "global" (bambulab.com) or "china" (bambulab.cn)
BAMBU_REGION = os.environ.get("BAMBU_REGION", "global").lower()

# How often the Bambu Cloud print history is checked for finished prints (seconds).
TASK_POLL_INTERVAL = int(os.environ.get("TASK_POLL_INTERVAL", "120"))
# How often a full status refresh is requested over MQTT (seconds). Bambu asks clients not to spam this.
PUSHALL_INTERVAL = int(os.environ.get("PUSHALL_INTERVAL", "900"))
# A slot must read "empty" this long before its spool is unassigned (seconds).
EMPTY_DEBOUNCE = int(os.environ.get("EMPTY_DEBOUNCE", "45"))
# A spool that is re-inserted into the same slot within this window is re-assigned automatically (seconds).
REINSERT_WINDOW = int(os.environ.get("REINSERT_WINDOW", "900"))
# Warn this many days before the Bambu token expires.
TOKEN_WARN_DAYS = int(os.environ.get("TOKEN_WARN_DAYS", "14"))

# Optional: URL that receives a JSON POST {"title","message"} for important events
# (e.g. a Home Assistant webhook automation that sends a phone notification).
NOTIFY_WEBHOOK = os.environ.get("NOTIFY_WEBHOOK", "").strip()
# Public address of the app (e.g. the Cloudflare tunnel URL), used for links in notifications.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Extra Host header values (besides IP literals, localhost and *.local) a direct LAN request may use, e.g. a DNS name
# of this server on your own network. Comma separated. A DNS-rebinding page always carries its own hostname.
ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get("FILAMENTHUB_ALLOWED_HOSTS", "").split(",") if h.strip()]
