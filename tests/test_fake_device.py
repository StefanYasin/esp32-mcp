"""The simulator has to match the firmware contract exactly."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from esp32_mcp.fake_device import FakeEsp32


def get(url, token=None):
    request = urllib.request.Request(url)
    if token:
        request.add_header("X-Api-Token", token)
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def post(url, token=None):
    request = urllib.request.Request(url, data=b"", method="POST")
    if token:
        request.add_header("X-Api-Token", token)
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_health_reports_relays(fake_device):
    status, payload = get(fake_device.url + "/health")
    assert status == 200
    assert payload["ok"] is True
    assert payload["device"] == "esp32-mcp"
    assert len(payload["relays"]) == 4
    assert payload["relays"][0]["index"] == 1
    assert payload["relays"][0]["state"] == "off"


def test_relay_on_then_off(fake_device):
    _, on = post(fake_device.url + "/relay/2/on")
    assert on["state"] == "on"
    assert on["relay"] == 2
    assert fake_device.relay_states[1] == "on"

    _, off = post(fake_device.url + "/relay/2/off")
    assert off["state"] == "off"
    assert fake_device.relay_states[1] == "off"


def test_relay_accepts_get(fake_device):
    status, payload = get(fake_device.url + "/relay/1/on")
    assert status == 200
    assert payload["state"] == "on"


def test_unknown_relay_is_404(fake_device):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(fake_device.url + "/relay/9/on")
    assert excinfo.value.code == 404
    body = json.loads(excinfo.value.read().decode("utf-8"))
    assert body["ok"] is False


def test_gpio_read_default(fake_device):
    status, payload = get(fake_device.url + "/gpio?pin=2")
    assert status == 200
    assert payload == {"ok": True, "pin": 2, "mode": "input", "value": 0}


def test_gpio_write_value(fake_device):
    _, payload = post(fake_device.url + "/gpio?pin=2&value=1")
    assert payload["mode"] == "output"
    assert payload["value"] == 1
    _, again = get(fake_device.url + "/gpio?pin=2")
    assert again["value"] == 1


def test_gpio_write_mode(fake_device):
    _, payload = post(fake_device.url + "/gpio?pin=4&mode=input_pullup")
    assert payload["mode"] == "input_pullup"


def test_gpio_rejects_bad_mode(fake_device):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(fake_device.url + "/gpio?pin=4&mode=analog")
    assert excinfo.value.code == 400


def test_gpio_rejects_bad_value(fake_device):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        post(fake_device.url + "/gpio?pin=4&value=7")
    assert excinfo.value.code == 400


def test_gpio_rejects_unlisted_pin(fake_device):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(fake_device.url + "/gpio?pin=6")
    assert excinfo.value.code == 403


def test_gpio_get_cannot_change_pin(fake_device):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(fake_device.url + "/gpio?pin=4&value=1")
    assert excinfo.value.code == 405


def test_sensors_shape(fake_device):
    status, payload = get(fake_device.url + "/sensors")
    assert status == 200
    ids = [sensor["id"] for sensor in payload["sensors"]]
    assert ids == ["chip_temp", "soil_moisture", "light"]
    for sensor in payload["sensors"]:
        assert "value" in sensor and "unit" in sensor


def test_unknown_route_is_404(fake_device):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(fake_device.url + "/nope")
    assert excinfo.value.code == 404


def test_token_is_enforced():
    device = FakeEsp32(token="s3cret").start()
    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            get(device.url + "/health")
        assert excinfo.value.code == 401
        status, payload = get(device.url + "/health", token="s3cret")
        assert status == 200
        assert payload["ok"] is True
    finally:
        device.stop()


def test_url_requires_start():
    with pytest.raises(RuntimeError):
        _ = FakeEsp32().url
