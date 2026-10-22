"""Tool registry and dispatch for esp32-mcp.

The schemas are plain JSON Schema objects, which is what MCP clients expect
from tools/list. Validation is done by hand to keep the dependency list empty,
and the error messages are written so an agent can correct itself.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .device import DeviceError, Esp32Device

MAX_RELAY_INDEX = 8
MAX_GPIO_PIN = 39


class ToolArgumentError(Exception):
    """A tool was called with missing or invalid arguments."""


class UnknownTool(Exception):
    """A tool name is not in the registry."""


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "esp32_health",
        "description": (
            "Read board health: firmware version, IP address, hostname, uptime, "
            "free heap and the relay table (index, name, pin, state). Call this "
            "first to learn what the board exposes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "esp32_read_sensors",
        "description": (
            "Read every sensor configured in the firmware: the internal chip "
            "temperature and each analog channel, with raw ADC counts, "
            "millivolts and a scaled value."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "esp32_set_relay",
        "description": (
            "Switch one relay on or off. Relay indexes are 1 based and are "
            "listed by esp32_health. The call is idempotent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "relay": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_RELAY_INDEX,
                    "description": "1 based relay index.",
                },
                "state": {
                    "type": "string",
                    "enum": ["on", "off"],
                    "description": "Target state.",
                },
            },
            "required": ["relay", "state"],
            "additionalProperties": False,
        },
    },
    {
        "name": "esp32_gpio_read",
        "description": (
            "Read the mode and digital level of one GPIO pin. Only pins in the "
            "firmware allow list can be read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pin": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_GPIO_PIN,
                    "description": "GPIO number, for example 2.",
                }
            },
            "required": ["pin"],
            "additionalProperties": False,
        },
    },
    {
        "name": "esp32_gpio_write",
        "description": (
            "Set a GPIO pin mode (output, input, input_pullup) and optionally "
            "drive it high or low. Pass value only, mode only, or both. Only "
            "pins in the firmware allow list can be changed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pin": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": MAX_GPIO_PIN,
                    "description": "GPIO number, for example 2.",
                },
                "value": {
                    "type": "integer",
                    "enum": [0, 1],
                    "description": "Digital level: 1 high, 0 low.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["output", "input", "input_pullup"],
                    "description": "Pin mode to apply before the value.",
                },
            },
            "required": ["pin"],
            "additionalProperties": False,
        },
    },
]


def get_tool(name: str) -> Optional[Dict[str, Any]]:
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return None


def _require_int(
    arguments: Dict[str, Any],
    key: str,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    if key not in arguments:
        raise ToolArgumentError("%s is required" % key)
    value = arguments[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError("%s must be an integer, got %r" % (key, value))
    if minimum is not None and value < minimum:
        raise ToolArgumentError("%s must be >= %d" % (key, minimum))
    if maximum is not None and value > maximum:
        raise ToolArgumentError("%s must be <= %d" % (key, maximum))
    return value


def _optional_int(
    arguments: Dict[str, Any],
    key: str,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> Optional[int]:
    if key not in arguments or arguments[key] is None:
        return None
    return _require_int(arguments, key, minimum, maximum)


def _reject_unknown_keys(arguments: Dict[str, Any], allowed: List[str]) -> None:
    unknown = sorted(set(arguments) - set(allowed))
    if unknown:
        raise ToolArgumentError(
            "unknown argument(s): %s. Allowed: %s"
            % (", ".join(unknown), ", ".join(allowed))
        )


def _dump(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def call_tool(name: str, arguments: Any, device: Esp32Device) -> str:
    """Run one tool and return the text that goes back to the agent."""
    if get_tool(name) is None:
        raise UnknownTool("Unknown tool: %s" % name)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolArgumentError("arguments must be an object")

    if name == "esp32_health":
        _reject_unknown_keys(arguments, [])
        return _dump(device.health())

    if name == "esp32_read_sensors":
        _reject_unknown_keys(arguments, [])
        return _dump(device.sensors())

    if name == "esp32_set_relay":
        _reject_unknown_keys(arguments, ["relay", "state"])
        relay = _require_int(arguments, "relay", 1, MAX_RELAY_INDEX)
        state = arguments.get("state")
        if not isinstance(state, str) or state.lower() not in ("on", "off"):
            raise ToolArgumentError('state must be "on" or "off", got %r' % (state,))
        return _dump(device.relay(relay, state.lower()))

    if name == "esp32_gpio_read":
        _reject_unknown_keys(arguments, ["pin"])
        pin = _require_int(arguments, "pin", 0, MAX_GPIO_PIN)
        return _dump(device.gpio_read(pin))

    if name == "esp32_gpio_write":
        _reject_unknown_keys(arguments, ["pin", "value", "mode"])
        pin = _require_int(arguments, "pin", 0, MAX_GPIO_PIN)
        value = _optional_int(arguments, "value", 0, 1)
        mode = arguments.get("mode")
        if mode is not None:
            if not isinstance(mode, str) or mode not in (
                "output",
                "input",
                "input_pullup",
            ):
                raise ToolArgumentError(
                    "mode must be output, input or input_pullup, got %r" % (mode,)
                )
        if value is None and mode is None:
            raise ToolArgumentError("pass value, mode, or both")
        return _dump(device.gpio_write(pin, value=value, mode=mode))

    raise UnknownTool("Unknown tool: %s" % name)
