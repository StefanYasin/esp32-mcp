"""In-process stand-in for the ESP32 firmware.

Tests and scripts/demo.py use this so nothing needs real hardware. It answers
the same routes with the same JSON shapes as firmware/esp32_mcp/esp32_mcp.ino,
including the optional X-Api-Token check and the GPIO allow list.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

FIRMWARE_VERSION = "0.1.0"
DEFAULT_ALLOWED_PINS = (2, 4, 5, 12, 13, 14, 15, 21, 22, 23, 25, 26, 27, 32, 33, 34, 35)
DEFAULT_SENSORS: List[Dict[str, Any]] = [
    {
        "id": "chip_temp",
        "type": "internal",
        "pin": None,
        "value": 41.5,
        "unit": "c",
    },
    {
        "id": "soil_moisture",
        "type": "analog",
        "pin": 34,
        "raw": 1820,
        "millivolts": 1466,
        "value": 44.4,
        "unit": "percent",
    },
    {
        "id": "light",
        "type": "analog",
        "pin": 35,
        "raw": 2400,
        "millivolts": 1934,
        "value": 58.6,
        "unit": "percent",
    },
]


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeEsp32/0.1"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if getattr(self.server, "verbose", False):
            print("fake-esp32: " + (fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"ok": False, "error": message})

    def _route(self, method: str) -> None:
        device: FakeEsp32 = self.server.device  # type: ignore[attr-defined]
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        device.requests.append((method, parsed.path))

        if device.token and self.headers.get("X-Api-Token") != device.token:
            self._error(401, "missing or invalid X-Api-Token")
            return

        if path == "/health" and method == "GET":
            self._send(200, device.health())
            return

        if path == "/sensors" and method == "GET":
            self._send(200, {"ok": True, "sensors": device.sensor_readings()})
            return

        if path == "/gpio":
            self._handle_gpio(method, query)
            return

        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "relay" and parts[3] in ("on", "off"):
            if method not in ("GET", "POST"):
                self._error(405, "method not allowed")
                return
            try:
                index = int(parts[2])
            except ValueError:
                self._error(400, "relay index must be an integer")
                return
            if index < 1 or index > device.relay_count:
                self._error(404, "relay %d does not exist" % index)
                return
            state = "on" if parts[3] == "on" else "off"
            device.relay_states[index - 1] = state
            self._send(
                200,
                {
                    "ok": True,
                    "relay": index,
                    "name": device.relay_names[index - 1],
                    "pin": device.relay_pins[index - 1],
                    "state": state,
                },
            )
            return

        self._error(404, "no route for %s %s" % (method, parsed.path))

    def _handle_gpio(self, method: str, query: Dict[str, List[str]]) -> None:
        device: FakeEsp32 = self.server.device  # type: ignore[attr-defined]
        raw_pin = query.get("pin", [None])[0]
        if raw_pin is None:
            self._error(400, "missing pin query parameter")
            return
        try:
            pin = int(raw_pin)
        except ValueError:
            self._error(400, "pin must be an integer")
            return
        if pin not in device.pins:
            self._error(403, "pin %d is not in the allowed gpio list" % pin)
            return

        mode = query.get("mode", [None])[0]
        raw_value = query.get("value", [None])[0]

        if mode is None and raw_value is None:
            state = device.pins[pin]
            self._send(
                200,
                {
                    "ok": True,
                    "pin": pin,
                    "mode": state["mode"],
                    "value": state["value"],
                },
            )
            return

        if method != "POST":
            self._error(405, "use POST to change a pin")
            return

        if mode is not None:
            if mode not in ("output", "input", "input_pullup"):
                self._error(400, "mode must be output, input or input_pullup")
                return
            device.pins[pin]["mode"] = mode

        if raw_value is not None:
            try:
                value = int(raw_value)
            except ValueError:
                self._error(400, "value must be 0 or 1")
                return
            if value not in (0, 1):
                self._error(400, "value must be 0 or 1")
                return
            device.pins[pin]["mode"] = "output"
            device.pins[pin]["value"] = value

        state = device.pins[pin]
        self._send(
            200,
            {"ok": True, "pin": pin, "mode": state["mode"], "value": state["value"]},
        )


class FakeEsp32:
    """A simulated board. Start it, point Esp32Device at .url, stop it."""

    def __init__(
        self,
        relays: int = 4,
        token: Optional[str] = None,
        allowed_pins: Optional[Tuple[int, ...]] = None,
        sensors: Optional[List[Dict[str, Any]]] = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if relays < 1:
            raise ValueError("relays must be >= 1")
        self.relay_count = relays
        self.relay_names = ["relay%d" % (index + 1) for index in range(relays)]
        self.relay_pins = [16 + index for index in range(relays)]
        self.relay_states = ["off"] * relays
        self.token = token
        self.sensors = [dict(sensor) for sensor in (sensors or DEFAULT_SENSORS)]
        allowed = allowed_pins or DEFAULT_ALLOWED_PINS
        self.pins: Dict[int, Dict[str, Any]] = {
            pin: {"mode": "input", "value": 0} for pin in allowed
        }
        self.requests: List[Tuple[str, str]] = []
        self.started_at = time.monotonic()
        self._host = host
        self._port = port
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("FakeEsp32 is not running")
        host, port = self._httpd.server_address[:2]
        return "http://%s:%d" % (host, port)

    def start(self) -> "FakeEsp32":
        if self._httpd is not None:
            return self
        self._httpd = ThreadingHTTPServer((self._host, self._port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.device = self  # type: ignore[attr-defined]
        self._httpd.verbose = False  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="fake-esp32", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def __enter__(self) -> "FakeEsp32":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    def health(self) -> Dict[str, Any]:
        relays = []
        for index in range(self.relay_count):
            relays.append(
                {
                    "index": index + 1,
                    "name": self.relay_names[index],
                    "pin": self.relay_pins[index],
                    "state": self.relay_states[index],
                }
            )
        return {
            "ok": True,
            "device": "esp32-mcp",
            "firmware": FIRMWARE_VERSION,
            "hostname": "esp32-mcp",
            "ip": "127.0.0.1",
            "uptime_ms": int((time.monotonic() - self.started_at) * 1000),
            "free_heap": 231456,
            "relays": relays,
        }

    def sensor_readings(self) -> List[Dict[str, Any]]:
        return [dict(sensor) for sensor in self.sensors]
