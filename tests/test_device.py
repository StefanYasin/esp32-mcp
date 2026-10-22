"""Tests for the HTTP client that talks to the board."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from esp32_mcp.device import DEFAULT_URL, DeviceError, Esp32Device


class _PlainHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html>not json</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_health(client):
    payload = client.health()
    assert payload["ok"] is True
    assert payload["relays"][0]["name"] == "relay1"


def test_sensors(client):
    payload = client.sensors()
    assert len(payload["sensors"]) == 3


def test_relay_roundtrip(client, fake_device):
    payload = client.relay(3, "on")
    assert payload["state"] == "on"
    assert fake_device.relay_states[2] == "on"
    client.relay(3, "off")
    assert fake_device.relay_states[2] == "off"


def test_relay_rejects_bad_state(client):
    with pytest.raises(ValueError):
        client.relay(1, "toggle")


def test_relay_unknown_index(client):
    with pytest.raises(DeviceError) as excinfo:
        client.relay(9, "on")
    assert excinfo.value.status == 404
    assert "does not exist" in str(excinfo.value)


def test_gpio_read_write(client):
    written = client.gpio_write(2, value=1)
    assert written["value"] == 1
    read = client.gpio_read(2)
    assert read["mode"] == "output"
    assert read["value"] == 1


def test_gpio_mode_only(client):
    payload = client.gpio_write(5, mode="input_pullup")
    assert payload["mode"] == "input_pullup"


def test_gpio_disallowed_pin(client):
    with pytest.raises(DeviceError) as excinfo:
        client.gpio_read(6)
    assert excinfo.value.status == 403


def test_unreachable_device():
    device = Esp32Device("http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(DeviceError) as excinfo:
        device.health()
    assert "cannot reach ESP32" in str(excinfo.value)


def test_non_json_response():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PlainHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        device = Esp32Device("http://%s:%d" % (host, port), timeout=2.0)
        with pytest.raises(DeviceError) as excinfo:
            device.health()
        assert "non JSON" in str(excinfo.value)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_token_header_sent():
    from esp32_mcp.fake_device import FakeEsp32

    board = FakeEsp32(token="abc123").start()
    try:
        with pytest.raises(DeviceError):
            Esp32Device(board.url, timeout=2.0).health()
        payload = Esp32Device(board.url, timeout=2.0, token="abc123").health()
        assert payload["ok"] is True
    finally:
        board.stop()


def test_base_url_trailing_slash():
    device = Esp32Device("http://example.test/", timeout=1.0)
    assert device.base_url == "http://example.test"


def test_base_url_required():
    with pytest.raises(ValueError):
        Esp32Device("")


def test_from_env(monkeypatch):
    monkeypatch.setenv("ESP32_MCP_URL", "http://10.0.0.5")
    monkeypatch.setenv("ESP32_MCP_TOKEN", "tok")
    monkeypatch.setenv("ESP32_MCP_TIMEOUT", "1.5")
    device = Esp32Device.from_env()
    assert device.base_url == "http://10.0.0.5"
    assert device.token == "tok"
    assert device.timeout == 1.5


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("ESP32_MCP_URL", raising=False)
    monkeypatch.delenv("ESP32_MCP_TOKEN", raising=False)
    monkeypatch.delenv("ESP32_MCP_TIMEOUT", raising=False)
    device = Esp32Device.from_env()
    assert device.base_url == DEFAULT_URL
    assert device.token is None


def test_from_env_bad_timeout(monkeypatch):
    monkeypatch.setenv("ESP32_MCP_TIMEOUT", "soon")
    assert Esp32Device.from_env().timeout == 5.0


def test_repr_hides_token():
    device = Esp32Device("http://x", token="secret")
    assert "secret" not in repr(device)
