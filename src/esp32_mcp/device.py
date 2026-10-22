"""HTTP client for the esp32-mcp firmware.

Only urllib from the standard library is used, so the whole server can run
from a bare Python install. The firmware exposes a small JSON API on port 80:

    GET  /health
    GET  /sensors
    GET  /relay/{n}/on    POST /relay/{n}/on
    GET  /relay/{n}/off   POST /relay/{n}/off
    GET  /gpio?pin=N
    POST /gpio?pin=N&value=1
    POST /gpio?pin=N&mode=output
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_URL = "http://esp32-mcp.local"
DEFAULT_TIMEOUT = 5.0
TOKEN_ENV = "ESP32_MCP_TOKEN"
URL_ENV = "ESP32_MCP_URL"
TIMEOUT_ENV = "ESP32_MCP_TIMEOUT"


class DeviceError(Exception):
    """The ESP32 could not be reached or answered with an error."""

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


class Esp32Device:
    """Thin wrapper around the firmware HTTP API."""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        timeout: float = DEFAULT_TIMEOUT,
        token: Optional[str] = None,
    ) -> None:
        if not base_url or not str(base_url).strip():
            raise ValueError("base_url is required")
        self.base_url = str(base_url).strip().rstrip("/")
        self.timeout = float(timeout)
        self.token = token or None

    @classmethod
    def from_env(cls) -> "Esp32Device":
        """Build a device from ESP32_MCP_URL, ESP32_MCP_TOKEN and ESP32_MCP_TIMEOUT."""
        timeout_raw = os.environ.get(TIMEOUT_ENV)
        timeout = DEFAULT_TIMEOUT
        if timeout_raw:
            try:
                timeout = float(timeout_raw)
            except ValueError:
                timeout = DEFAULT_TIMEOUT
        return cls(
            base_url=os.environ.get(URL_ENV, DEFAULT_URL),
            timeout=timeout,
            token=os.environ.get(TOKEN_ENV),
        )

    def __repr__(self) -> str:
        return "Esp32Device(%r, timeout=%r, token=%r)" % (
            self.base_url,
            self.timeout,
            "set" if self.token else None,
        )

    def _build_url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        url = self.base_url + path
        if params:
            query = urllib.parse.urlencode(
                [(key, value) for key, value in params.items() if value is not None]
            )
            url = url + "?" + query
        return url

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self._build_url(path, params)
        data = b"" if method == "POST" else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Length", "0")
        if self.token:
            request.add_header("X-Api-Token", self.token)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            payload = _loads(body)
            detail = body.strip()
            if isinstance(payload, dict) and payload.get("error"):
                detail = str(payload["error"])
            raise DeviceError(
                "device returned HTTP %d: %s" % (exc.code, detail or exc.reason),
                status=exc.code,
                payload=payload,
            )
        except urllib.error.URLError as exc:
            raise DeviceError(
                "cannot reach ESP32 at %s: %s" % (self.base_url, exc.reason)
            )
        except OSError as exc:
            raise DeviceError("cannot reach ESP32 at %s: %s" % (self.base_url, exc))

        payload = _loads(raw)
        if not isinstance(payload, dict):
            raise DeviceError(
                "device returned a non JSON response: %s" % raw.strip()[:200]
            )
        if payload.get("ok") is False:
            raise DeviceError(
                "device reported failure: %s" % payload.get("error", "unknown error"),
                status=None,
                payload=payload,
            )
        return payload

    def health(self) -> Dict[str, Any]:
        """Read firmware metadata, uptime and the relay table."""
        return self._request("GET", "/health")

    def sensors(self) -> Dict[str, Any]:
        """Read every sensor the firmware knows about."""
        return self._request("GET", "/sensors")

    def relay(self, relay: int, state: str) -> Dict[str, Any]:
        """Switch one relay. state is "on" or "off"."""
        state = str(state).strip().lower()
        if state not in ("on", "off"):
            raise ValueError('state must be "on" or "off"')
        return self._request("POST", "/relay/%d/%s" % (int(relay), state))

    def gpio_read(self, pin: int) -> Dict[str, Any]:
        """Read a pin mode and digital level."""
        return self._request("GET", "/gpio", {"pin": int(pin)})

    def gpio_write(
        self,
        pin: int,
        value: Optional[int] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write a digital level, change a pin mode, or both."""
        params: Dict[str, Any] = {"pin": int(pin)}
        if value is not None:
            params["value"] = int(value)
        if mode is not None:
            params["mode"] = mode
        return self._request("POST", "/gpio", params)
