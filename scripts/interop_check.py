#!/usr/bin/env python3
"""Check esp32-mcp against the official MCP Python SDK over a real stdio pipe.

This is a compatibility check, not part of the test suite, because it needs a
third party package. The server itself still has no dependencies.

    python -m pip install mcp
    python scripts/interop_check.py            # built in simulator
    python scripts/interop_check.py --url http://192.168.1.42

Exit code 0 means the handshake, tools/list, a successful tool call and a
rejected tool call all behaved the way the SDK expects.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover - depends on the environment
    print("This check needs the official SDK: python -m pip install mcp", file=sys.stderr)
    raise SystemExit(2)


def field(obj: Any, snake: str, camel: str) -> Any:
    """The SDK moved from camelCase to snake_case attributes, support both."""
    if hasattr(obj, snake):
        return getattr(obj, snake)
    return getattr(obj, camel)


async def run(url: str, token: Optional[str]) -> int:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"

    args = ["-m", "esp32_mcp", "--url", url]
    if token:
        args.extend(["--token", token])

    params = StdioServerParameters(command=sys.executable, args=args, env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = field(init, "server_info", "serverInfo")
            print(
                "initialize: server=%s %s protocol=%s"
                % (info.name, info.version, field(init, "protocol_version", "protocolVersion"))
            )

            listing = await session.list_tools()
            names = [tool.name for tool in listing.tools]
            print("tools/list: %d tools %s" % (len(names), ", ".join(names)))

            relay = await session.call_tool(
                "esp32_set_relay", {"relay": 1, "state": "on"}
            )
            relay_error = field(relay, "is_error", "isError")
            print("tools/call relay: isError=%s" % relay_error)
            if relay_error:
                print(relay.content[0].text, file=sys.stderr)
                return 1

            health = await session.call_tool("esp32_health", {})
            print(
                "tools/call health: isError=%s chars=%d"
                % (field(health, "is_error", "isError"), len(health.content[0].text))
            )

            blocked = await session.call_tool("esp32_gpio_read", {"pin": 6})
            blocked_error = field(blocked, "is_error", "isError")
            print("tools/call blocked pin: isError=%s" % blocked_error)
            if not blocked_error:
                print("expected pin 6 to be rejected", file=sys.stderr)
                return 1

    print("interop check passed")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=None, help="ESP32 base URL, defaults to the simulator")
    parser.add_argument("--token", default=None, help="API token for the board")
    args = parser.parse_args(argv)

    board = None
    url = args.url
    if url is None:
        from esp32_mcp.fake_device import FakeEsp32

        board = FakeEsp32(relays=4).start()
        url = board.url
        print("simulated ESP32 at %s" % url)

    try:
        return asyncio.run(run(url, args.token))
    finally:
        if board is not None:
            board.stop()


if __name__ == "__main__":
    raise SystemExit(main())
