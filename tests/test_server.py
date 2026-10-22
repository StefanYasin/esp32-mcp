"""Protocol level tests for the MCP server, in process and over a real pipe."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

from esp32_mcp.protocol import INVALID_PARAMS, METHOD_NOT_FOUND, PARSE_ERROR
from esp32_mcp.server import McpServer


def call(server, method, params=None, request_id=1, notification=False):
    message = {"jsonrpc": "2.0", "method": method}
    if not notification:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return server.handle(message)


def test_initialize(mcp):
    response = call(
        mcp,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    )
    result = response["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "esp32-mcp"
    assert "esp32_health" in result["instructions"]


def test_initialize_negotiates_unknown_version(mcp):
    response = call(mcp, "initialize", {"protocolVersion": "2099-01-01"})
    assert response["result"]["protocolVersion"] == "2024-11-05"


def test_initialized_notification_has_no_response(mcp):
    assert call(mcp, "notifications/initialized", notification=True) is None


def test_ping(mcp):
    assert call(mcp, "ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_tools_list(mcp):
    tools = call(mcp, "tools/list")["result"]["tools"]
    assert len(tools) == 5
    assert {tool["name"] for tool in tools} >= {"esp32_set_relay", "esp32_gpio_read"}


def test_tools_call_health(mcp):
    response = call(mcp, "tools/call", {"name": "esp32_health", "arguments": {}})
    result = response["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["device"] == "esp32-mcp"


def test_tools_call_without_arguments(mcp):
    response = call(mcp, "tools/call", {"name": "esp32_health"})
    assert response["result"]["isError"] is False


def test_tools_call_unknown_tool(mcp):
    response = call(mcp, "tools/call", {"name": "esp32_reboot", "arguments": {}})
    assert response["error"]["code"] == INVALID_PARAMS
    assert "Unknown tool" in response["error"]["message"]


def test_tools_call_missing_name(mcp):
    response = call(mcp, "tools/call", {"arguments": {}})
    assert response["error"]["code"] == INVALID_PARAMS


def test_tools_call_bad_arguments_returns_tool_error(mcp):
    response = call(
        mcp, "tools/call", {"name": "esp32_set_relay", "arguments": {"relay": 1}}
    )
    result = response["result"]
    assert result["isError"] is True
    assert "invalid arguments" in result["content"][0]["text"]


def test_tools_call_device_failure_returns_tool_error(mcp):
    response = call(
        mcp, "tools/call", {"name": "esp32_gpio_read", "arguments": {"pin": 6}}
    )
    result = response["result"]
    assert result["isError"] is True
    assert "device error" in result["content"][0]["text"]


def test_unknown_method(mcp):
    response = call(mcp, "resources/list")
    assert response["error"]["code"] == METHOD_NOT_FOUND


def test_invalid_jsonrpc_version(mcp):
    response = mcp.handle({"jsonrpc": "1.0", "id": 1, "method": "ping"})
    assert response["error"]["code"] == -32600


def test_blank_and_bad_lines_do_not_kill_the_stream(mcp):
    stdin = io.StringIO(
        "\n".join(
            [
                "   ",
                '{"jsonrpc":"2.0","id":1,"method":"ping"}',
                "not json at all",
                '{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
                "",
            ]
        )
    )
    stdout = io.StringIO()
    mcp.serve(stdin, stdout)
    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["id"] == 1
    assert parsed[1]["error"]["code"] == PARSE_ERROR
    assert parsed[2]["id"] == 2
    assert len(parsed[2]["result"]["tools"]) == 5


def test_subprocess_stdio_roundtrip(fake_device, repo_root, src_dir):
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "esp32_set_relay", "arguments": {"relay": 2, "state": "on"}},
        },
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-u", "-m", "esp32_mcp", "--url", fake_device.url],
        input="".join(json.dumps(message) + "\n" for message in messages),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        cwd=str(repo_root),
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 3
    responses = [json.loads(line) for line in lines]
    assert responses[0]["id"] == 1
    assert responses[1]["id"] == 2
    assert responses[2]["id"] == 3
    assert json.loads(responses[2]["result"]["content"][0]["text"])["state"] == "on"
    assert fake_device.relay_states[1] == "on"


def test_stdio_errors_stay_on_stderr(fake_device, repo_root, src_dir):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-u", "-m", "esp32_mcp", "--url", fake_device.url],
        input="{broken\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        cwd=str(repo_root),
        env=env,
    )
    assert proc.returncode == 0
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["error"]["code"] == PARSE_ERROR


def test_version_cli(fake_device, repo_root, src_dir):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)
    proc = subprocess.run(
        [sys.executable, "-m", "esp32_mcp", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        cwd=str(repo_root),
        env=env,
    )
    assert proc.returncode == 0
    assert "0.1.0" in proc.stdout

