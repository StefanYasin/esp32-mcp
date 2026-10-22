"""Shared fixtures. Everything runs against the in-process fake board."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from esp32_mcp.device import Esp32Device  # noqa: E402
from esp32_mcp.fake_device import FakeEsp32  # noqa: E402
from esp32_mcp.server import McpServer  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def src_dir() -> Path:
    return SRC


@pytest.fixture()
def fake_device():
    device = FakeEsp32(relays=4)
    device.start()
    try:
        yield device
    finally:
        device.stop()


@pytest.fixture()
def client(fake_device) -> Esp32Device:
    return Esp32Device(fake_device.url, timeout=2.0)


@pytest.fixture()
def mcp(client) -> McpServer:
    return McpServer(client)
