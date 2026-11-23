"""Modelbank settings from the environment (the add-on's run script sets them)."""
from __future__ import annotations

import os
import socket
from pathlib import Path

DATA_DIR = Path(os.environ.get("MODELBANK_DATA_DIR", "/data"))
MODELS_DIR = Path(os.environ.get("MODELBANK_MODELS_DIR", "/share/modelbank"))
HOST = os.environ.get("MODELBANK_HOST", "0.0.0.0")
PORT = int(os.environ.get("MODELBANK_PORT", "8099"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
INGRESS_IP = "172.30.32.2"
# Outside Home Assistant (dev/tests) every client is allowed; in the add-on only the Ingress proxy.
ADDON = os.environ.get("MODELBANK_ADDON", "0") == "1"
SCAN_MINUTES = float(os.environ.get("MODELBANK_SCAN_MINUTES", "10"))
MAX_UPLOAD = 1024 * 1024 * 1024          # 1 GB per file
EXTS = (".3mf", ".stl")
TRASH = ".slettet"                          # deleted models are moved here (inside MODELS_DIR), not removed


def default_filamenthub_url() -> str:
    """Both add-ons come from the same repository, so their host names share the repository prefix:
    <prefix>-modelbank -> <prefix>-filamenthub (port 8080)."""
    env = os.environ.get("MODELBANK_FILAMENTHUB_URL")
    if env:
        return env.rstrip("/")
    host = socket.gethostname()
    if host.endswith("-modelbank"):
        return f"http://{host[:-len('-modelbank')]}-filamenthub:8080"
    return "http://127.0.0.1:8080"
