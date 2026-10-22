"""MCP server over stdio.

Protocol surface implemented here:

    initialize                  handshake, protocol version and capabilities
    notifications/initialized   accepted, no response
    ping                        liveness check
    tools/list                  the tool registry
    tools/call                  run one tool against the ESP32

Everything else returns JSON-RPC -32601, which is what the MCP spec asks for
when a client calls a method the server does not implement. Nothing is ever
written to stdout except JSON-RPC messages; logs go to stderr.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Dict, Optional, TextIO

from . import __version__
from .device import DEFAULT_TIMEOUT, DEFAULT_URL, DeviceError, Esp32Device
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    RpcError,
    decode,
    encode,
    error_response,
    is_notification,
    result_response,
    validate_request,
)
from .tools import TOOLS, ToolArgumentError, UnknownTool, call_tool

SERVER_NAME = "esp32-mcp"
PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION,)

INSTRUCTIONS = (
    "Controls a physical ESP32 board over HTTP. Call esp32_health first to see "
    "which relays exist and whether the board is reachable. Relay indexes are "
    "1 based. Turning a relay on or off changes real hardware, so confirm with "
    "the user before switching anything that could be dangerous."
)

log = logging.getLogger("esp32_mcp")


def _tool_error(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


class McpServer:
    """Turns JSON-RPC messages into device calls."""

    def __init__(
        self,
        device: Esp32Device,
        server_name: str = SERVER_NAME,
        version: str = __version__,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> None:
        self.device = device
        self.server_name = server_name
        self.version = version
        self.protocol_version = protocol_version

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle one message. Returns None for notifications."""
        request_id = message.get("id")
        notification = is_notification(message)
        try:
            validate_request(message)
        except RpcError as exc:
            if notification:
                return None
            return exc.to_response()

        method = message["method"]
        params = message.get("params") or {}
        try:
            result = self._dispatch(method, params)
        except RpcError as exc:
            return None if notification else exc.to_response()
        except Exception:  # noqa: BLE001 - the server must never die mid stream
            log.exception("unhandled error while running %s", method)
            if notification:
                return None
            return error_response(request_id, INTERNAL_ERROR, "Internal error")

        if notification:
            return None
        return result_response(request_id, result)

    def _dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        if method == "initialize":
            requested = params.get("protocolVersion")
            negotiated = (
                requested
                if requested in SUPPORTED_PROTOCOL_VERSIONS
                else self.protocol_version
            )
            return {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.server_name, "version": self.version},
                "instructions": INSTRUCTIONS,
            }
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call_tool(params)
        raise RpcError(METHOD_NOT_FOUND, "Method not found: %s" % method)

    def _call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise RpcError(
                INVALID_PARAMS, "Invalid params: tools/call requires a tool name"
            )
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        try:
            text = call_tool(name, arguments, self.device)
        except UnknownTool as exc:
            raise RpcError(INVALID_PARAMS, str(exc))
        except ToolArgumentError as exc:
            return _tool_error("invalid arguments: %s" % exc)
        except DeviceError as exc:
            return _tool_error("device error: %s" % exc.message)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        """Read newline delimited requests until end of input."""
        log.info("serving MCP on stdio, device=%s", self.device.base_url)
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = decode(line)
            except RpcError as exc:
                self._write(stdout, exc.to_response())
                continue
            response = self.handle(message)
            if response is not None:
                self._write(stdout, response)

    @staticmethod
    def _write(stdout: TextIO, message: Dict[str, Any]) -> None:
        try:
            stdout.write(encode(message))
            stdout.flush()
        except (BrokenPipeError, ValueError):
            raise SystemExit(0)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="esp32-mcp",
        description="MCP server that controls an ESP32 over HTTP.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="base URL of the ESP32 (default: %s or $ESP32_MCP_URL)" % DEFAULT_URL,
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout in seconds (default: %s)" % DEFAULT_TIMEOUT,
    )
    parser.add_argument(
        "--token",
        default=None,
        help="API token, sent as X-Api-Token (or $ESP32_MCP_TOKEN)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="log requests to stderr"
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = parser.parse_args(argv)

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="esp32-mcp: %(levelname)s %(message)s",
    )

    from_env = Esp32Device.from_env()
    device = Esp32Device(
        base_url=args.url or from_env.base_url,
        timeout=args.timeout if args.timeout is not None else from_env.timeout,
        token=args.token or from_env.token,
    )

    for stream, encoding in ((sys.stdin, "utf-8"), (sys.stdout, "utf-8")):
        try:
            stream.reconfigure(encoding=encoding, newline="\n")
        except (AttributeError, ValueError):
            pass

    server = McpServer(device)
    try:
        server.serve(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
