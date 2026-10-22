"""Tool schema and dispatch tests."""

from __future__ import annotations

import json

import pytest

from esp32_mcp.device import DeviceError, Esp32Device
from esp32_mcp.tools import (
    TOOLS,
    ToolArgumentError,
    UnknownTool,
    call_tool,
    get_tool,
)


def test_tool_names_are_unique():
    names = [tool["name"] for tool in TOOLS]
    assert len(names) == len(set(names))
    assert names == [
        "esp32_health",
        "esp32_read_sensors",
        "esp32_set_relay",
        "esp32_gpio_read",
        "esp32_gpio_write",
    ]


def test_every_tool_has_a_schema():
    for tool in TOOLS:
        assert tool["description"].strip()
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        for field in schema.get("required", []):
            assert field in schema["properties"]


def test_get_tool():
    assert get_tool("esp32_health")["name"] == "esp32_health"
    assert get_tool("nope") is None


def test_health(client):
    payload = json.loads(call_tool("esp32_health", {}, client))
    assert payload["ok"] is True
    assert payload["firmware"] == "0.1.0"


def test_read_sensors(client):
    payload = json.loads(call_tool("esp32_read_sensors", None, client))
    assert payload["sensors"][0]["id"] == "chip_temp"


def test_set_relay(client, fake_device):
    payload = json.loads(
        call_tool("esp32_set_relay", {"relay": 4, "state": "on"}, client)
    )
    assert payload["state"] == "on"
    assert fake_device.relay_states[3] == "on"


def test_set_relay_accepts_uppercase_state(client):
    payload = json.loads(
        call_tool("esp32_set_relay", {"relay": 1, "state": "OFF"}, client)
    )
    assert payload["state"] == "off"


def test_set_relay_requires_state(client):
    with pytest.raises(ToolArgumentError):
        call_tool("esp32_set_relay", {"relay": 1}, client)


def test_set_relay_rejects_bool_index(client):
    with pytest.raises(ToolArgumentError):
        call_tool("esp32_set_relay", {"relay": True, "state": "on"}, client)


def test_set_relay_out_of_range(client):
    with pytest.raises(ToolArgumentError):
        call_tool("esp32_set_relay", {"relay": 0, "state": "on"}, client)
    with pytest.raises(ToolArgumentError):
        call_tool("esp32_set_relay", {"relay": 99, "state": "on"}, client)


def test_gpio_read(client):
    payload = json.loads(call_tool("esp32_gpio_read", {"pin": 2}, client))
    assert payload["pin"] == 2


def test_gpio_write(client):
    payload = json.loads(
        call_tool("esp32_gpio_write", {"pin": 2, "value": 1}, client)
    )
    assert payload["value"] == 1


def test_gpio_write_requires_something_to_do(client):
    with pytest.raises(ToolArgumentError):
        call_tool("esp32_gpio_write", {"pin": 2}, client)


def test_gpio_write_rejects_bad_mode(client):
    with pytest.raises(ToolArgumentError):
        call_tool("esp32_gpio_write", {"pin": 2, "mode": "analog"}, client)


def test_gpio_write_rejects_bad_value(client):
    with pytest.raises(ToolArgumentError):
        call_tool("esp32_gpio_write", {"pin": 2, "value": 2}, client)


def test_unknown_argument_is_rejected(client):
    with pytest.raises(ToolArgumentError) as excinfo:
        call_tool("esp32_gpio_read", {"pin": 2, "speed": "fast"}, client)
    assert "unknown argument" in str(excinfo.value)


def test_arguments_must_be_object(client):
    with pytest.raises(ToolArgumentError):
        call_tool("esp32_gpio_read", [2], client)


def test_unknown_tool(client):
    with pytest.raises(UnknownTool):
        call_tool("esp32_reboot", {}, client)


def test_device_error_propagates(client):
    with pytest.raises(DeviceError):
        call_tool("esp32_gpio_read", {"pin": 6}, client)


def test_unreachable_device_raises_device_error():
    device = Esp32Device("http://127.0.0.1:1", timeout=0.5)
    with pytest.raises(DeviceError):
        call_tool("esp32_health", {}, device)
