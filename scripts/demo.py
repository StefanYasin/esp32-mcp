#!/usr/bin/env python3
"""Drive the esp32-mcp server over stdio and print the whole exchange.

The demo spawns the server exactly like an MCP client would, performs the
handshake, lists the tools and calls three of them.

    python scripts/demo.py --fake          # built in simulator, no hardware
    python scripts/demo.py                 # board at ESP32_MCP_URL
    python scripts/demo.py --url http://192.168.1.42

Exit code 0 means every request got a valid JSON-RPC response.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from esp32_mcp.device import DEFAULT_URL, Esp32Device  # noqa: E402
from esp32_mcp.fake_device import FakeEsp32  # noqa: E402


class DemoError(RuntimeError):
    pass


def spawn_server(url: str, token: Optional[str] = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, "-u", "-m", "esp32_mcp", "--url", url]
    if token:
        command.extend(["--token", token])
    return subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )


class Session:
    """One JSON-RPC request per line, one response per line."""

    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process
        self.next_id = 0
        self.lines: List[str] = []

    def send(self, message: Dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.next_id += 1
        message: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        print("--> %s" % method)
        self.send(message)
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            stderr = ""
            if self.process.stderr is not None:
                stderr = self.process.stderr.read()
            raise DemoError("server closed the stream. stderr:\n%s" % stderr.strip())
        self.lines.append(line.strip())
        response = json.loads(line)
        if "error" in response:
            print("<-- error %s: %s" % (response["error"]["code"], response["error"]["message"]))
        else:
            print("<-- %s ok" % method)
        return response

    def notify(self, method: str) -> None:
        print("--> %s (notification)" % method)
        self.send({"jsonrpc": "2.0", "method": method})

    def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise DemoError("server did not exit after stdin closed")


def show_tool_result(response: Dict[str, Any]) -> None:
    result = response.get("result", {})
    content = result.get("content") or []
    for block in content:
        print(block.get("text", ""))
    if result.get("isError"):
        raise DemoError("tool call reported an error")


def run(url: str, token: Optional[str]) -> int:
    process = spawn_server(url, token)
    session = Session(process)
    try:
        init = session.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "esp32-mcp-demo", "version": "0.1.0"},
            },
        )
        info = init.get("result", {})
        print(
            "    protocol=%s server=%s %s"
            % (
                info.get("protocolVersion"),
                info.get("serverInfo", {}).get("name"),
                info.get("serverInfo", {}).get("version"),
            )
        )
        session.notify("notifications/initialized")

        listing = session.request("tools/list")
        tools = listing.get("result", {}).get("tools", [])
        print("    %d tools: %s" % (len(tools), ", ".join(t["name"] for t in tools)))

        print()
        print("== esp32_health ==")
        show_tool_result(session.request("tools/call", {"name": "esp32_health", "arguments": {}}))

        print()
        print("== esp32_read_sensors ==")
        show_tool_result(
            session.request("tools/call", {"name": "esp32_read_sensors", "arguments": {}})
        )

        print()
        print("== esp32_set_relay(relay=1, state=on) ==")
        show_tool_result(
            session.request(
                "tools/call",
                {"name": "esp32_set_relay", "arguments": {"relay": 1, "state": "on"}},
            )
        )

        print()
        print("== esp32_gpio_write(pin=2, value=1) ==")
        show_tool_result(
            session.request(
                "tools/call",
                {"name": "esp32_gpio_write", "arguments": {"pin": 2, "value": 1}},
            )
        )

        print()
        print("== esp32_gpio_read(pin=2) ==")
        show_tool_result(
            session.request("tools/call", {"name": "esp32_gpio_read", "arguments": {"pin": 2}})
        )
    finally:
        try:
            session.close()
        except DemoError:
            raise
        finally:
            if process.poll() is None:
                process.kill()

    if process.returncode not in (0, None):
        raise DemoError("server exited with code %s" % process.returncode)
    print()
    print("demo finished: %d JSON-RPC responses, all well formed" % len(session.lines))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Demo of the esp32-mcp stdio server.")
    parser.add_argument("--url", default=None, help="ESP32 base URL (default %s)" % DEFAULT_URL)
    parser.add_argument("--token", default=None, help="API token for the board")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="start the built in simulator instead of using real hardware",
    )
    args = parser.parse_args(argv)

    simulator = None
    url = args.url or os.environ.get("ESP32_MCP_URL", DEFAULT_URL)
    if args.fake:
        simulator = FakeEsp32(relays=4)
        simulator.start()
        url = simulator.url
        print("simulated ESP32 at %s" % url)

    try:
        return run(url, args.token)
    except DemoError as exc:
        print("demo failed: %s" % exc, file=sys.stderr)
        return 1
    finally:
        if simulator is not None:
            simulator.stop()


if __name__ == "__main__":
    raise SystemExit(main())
