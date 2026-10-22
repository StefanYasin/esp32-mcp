"""JSON-RPC 2.0 framing for the MCP stdio transport.

The MCP stdio transport sends one JSON-RPC 2.0 message per line, UTF-8
encoded. This module parses and validates those messages by hand so the
server needs nothing outside the Python standard library.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcError(Exception):
    """A JSON-RPC error that should be returned to the client."""

    def __init__(
        self,
        code: int,
        message: str,
        data: Any = None,
        request_id: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.request_id = request_id

    def to_response(self) -> Dict[str, Any]:
        request_id = self.request_id
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            request_id = None
        return error_response(request_id, self.code, self.message, self.data)


def encode(message: Dict[str, Any]) -> str:
    """Serialize one message as a single newline terminated line."""
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"


def decode(line: str) -> Dict[str, Any]:
    """Parse one line into a JSON-RPC message object."""
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RpcError(PARSE_ERROR, "Parse error: %s" % exc.msg)
    if not isinstance(message, dict):
        raise RpcError(INVALID_REQUEST, "Invalid Request: expected a JSON object")
    return message


def result_response(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(
    request_id: Any, code: int, message: str, data: Any = None
) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def is_notification(message: Dict[str, Any]) -> bool:
    return "id" not in message


def validate_request(message: Dict[str, Any]) -> None:
    """Raise RpcError when a message is not a valid JSON-RPC request."""
    request_id = message.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        if "id" in message:
            raise RpcError(
                INVALID_REQUEST,
                "Invalid Request: id must be a string or an integer",
            )
    if message.get("jsonrpc") != JSONRPC_VERSION:
        raise RpcError(
            INVALID_REQUEST,
            'Invalid Request: "jsonrpc" must be "2.0"',
            request_id=request_id,
        )
    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise RpcError(
            INVALID_REQUEST,
            "Invalid Request: missing method",
            request_id=request_id,
        )
    params = message.get("params", {})
    if params is not None and not isinstance(params, dict):
        raise RpcError(
            INVALID_PARAMS,
            "Invalid params: expected an object",
            request_id=request_id,
        )
