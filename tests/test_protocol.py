"""Unit tests for the hand written JSON-RPC layer."""

from __future__ import annotations

import json

import pytest

from esp32_mcp import protocol


def test_encode_is_one_line():
    line = protocol.encode({"jsonrpc": "2.0", "id": 1, "result": {}})
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert json.loads(line)["id"] == 1


def test_encode_is_compact():
    line = protocol.encode({"jsonrpc": "2.0", "id": 1, "result": {"a": 1}})
    assert " " not in line.strip()


def test_decode_roundtrip():
    message = {"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {"x": 1}}
    assert protocol.decode(protocol.encode(message)) == message


def test_decode_rejects_bad_json():
    with pytest.raises(protocol.RpcError) as excinfo:
        protocol.decode("{not json}")
    assert excinfo.value.code == protocol.PARSE_ERROR


def test_decode_rejects_non_object():
    with pytest.raises(protocol.RpcError) as excinfo:
        protocol.decode("[1, 2, 3]")
    assert excinfo.value.code == protocol.INVALID_REQUEST


def test_validate_accepts_request():
    protocol.validate_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


def test_validate_accepts_notification():
    protocol.validate_request({"jsonrpc": "2.0", "method": "notifications/initialized"})


def test_validate_requires_jsonrpc_version():
    with pytest.raises(protocol.RpcError) as excinfo:
        protocol.validate_request({"id": 1, "method": "ping"})
    assert excinfo.value.code == protocol.INVALID_REQUEST
    assert excinfo.value.request_id == 1


def test_validate_requires_method():
    with pytest.raises(protocol.RpcError) as excinfo:
        protocol.validate_request({"jsonrpc": "2.0", "id": 1})
    assert excinfo.value.code == protocol.INVALID_REQUEST


def test_validate_rejects_boolean_id():
    with pytest.raises(protocol.RpcError):
        protocol.validate_request({"jsonrpc": "2.0", "id": True, "method": "ping"})


def test_validate_rejects_list_params():
    with pytest.raises(protocol.RpcError) as excinfo:
        protocol.validate_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}
        )
    assert excinfo.value.code == protocol.INVALID_PARAMS


def test_validate_allows_missing_params():
    protocol.validate_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


def test_is_notification():
    assert protocol.is_notification({"jsonrpc": "2.0", "method": "x"})
    assert not protocol.is_notification({"jsonrpc": "2.0", "id": 0, "method": "x"})


def test_result_response_shape():
    response = protocol.result_response(3, {"ok": True})
    assert response == {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}


def test_error_response_omits_null_data():
    response = protocol.error_response(1, -32601, "Method not found: x")
    assert response["error"] == {"code": -32601, "message": "Method not found: x"}


def test_error_response_includes_data():
    response = protocol.error_response(1, -32602, "bad", {"field": "relay"})
    assert response["error"]["data"] == {"field": "relay"}


def test_rpc_error_to_response_keeps_id():
    error = protocol.RpcError(-32601, "nope", request_id=4)
    assert error.to_response()["id"] == 4


def test_rpc_error_to_response_drops_bad_id():
    error = protocol.RpcError(-32601, "nope", request_id={"bad": True})
    assert error.to_response()["id"] is None
