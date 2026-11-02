"""Bambu Cloud MQTT listener (read-only). Works without LAN/developer mode."""
from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import uuid
from typing import Callable

import paho.mqtt.client as mqtt

log = logging.getLogger("spoolsync.mqtt")


class CloudMqtt:
    def __init__(self, host: str, uid: str, token: str, serial: str,
                 on_report: Callable[[dict], None], on_status: Callable[[str, str], None],
                 pushall_interval: int = 900):
        self.host = host
        self.uid = uid
        self.token = token
        self.serial = serial
        self.on_report = on_report
        self.on_status = on_status
        self.pushall_interval = pushall_interval
        self.status = "stopped"
        self.last_message = 0.0
        self._auth_failures = 0
        self._stop = threading.Event()
        self._client: mqtt.Client | None = None
        self._timer: threading.Thread | None = None
        self._seq = 0

    def _set_status(self, status: str, detail: str = "") -> None:
        self.status = status
        try:
            self.on_status(status, detail)
        except Exception:  # pragma: no cover
            log.exception("status callback failed")

    def start(self) -> None:
        self._stop.clear()
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"spoolsync-{uuid.uuid4().hex[:12]}",
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        client.username_pw_set(f"u_{self.uid}", self.token)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.reconnect_delay_set(min_delay=5, max_delay=300)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self._client = client
        self._set_status("connecting")
        client.connect_async(self.host, 8883, keepalive=60)
        client.loop_start()
        self._timer = threading.Thread(target=self._pushall_loop, name="mqtt-pushall", daemon=True)
        self._timer.start()

    def stop(self, final_status: str = "stopped") -> None:
        self._stop.set()
        client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception:
                pass
        self._set_status(final_status)

    # ---- callbacks -------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            text = str(reason_code)
            log.warning("MQTT connect failed: %s", text)
            if "authori" in text.lower() or "password" in text.lower() or reason_code.value in (4, 5, 134, 135):
                self._auth_failures += 1
                if self._auth_failures >= 3:
                    threading.Thread(target=self.stop, kwargs={"final_status": "auth_failed"}, daemon=True).start()
                    return
            self._set_status("error", text)
            return
        self._auth_failures = 0
        log.info("MQTT connected to %s", self.host)
        client.subscribe(f"device/{self.serial}/report", qos=0)
        self._set_status("connected")
        self.request_version()
        self.request_pushall()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if self._stop.is_set():
            return
        log.warning("MQTT disconnected: %s", reason_code)
        self._set_status("reconnecting", str(reason_code))

    def _on_message(self, client, userdata, msg):
        self.last_message = time.time()
        try:
            payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        except ValueError:
            return
        try:
            self.on_report(payload)
        except Exception:
            log.exception("error handling MQTT report")

    # ---- requests --------------------------------------------------------
    def _publish(self, body: dict) -> None:
        if self._client is None or not self._client.is_connected():
            return
        self._client.publish(f"device/{self.serial}/request", json.dumps(body), qos=0)

    def _next_seq(self) -> str:
        self._seq += 1
        return str(self._seq)

    def request_pushall(self) -> None:
        self._publish({"pushing": {"sequence_id": self._next_seq(), "command": "pushall", "version": 1, "push_target": 1}})

    def request_version(self) -> None:
        self._publish({"info": {"sequence_id": self._next_seq(), "command": "get_version"}})

    def _pushall_loop(self) -> None:
        while not self._stop.wait(self.pushall_interval):
            self.request_pushall()
