"""The demo script has to work end to end with no hardware."""

from __future__ import annotations

import subprocess
import sys


def test_demo_runs_against_the_simulator(repo_root):
    proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "demo.py"), "--fake"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=str(repo_root),
    )
    assert proc.returncode == 0, proc.stderr
    assert "simulated ESP32 at" in proc.stdout
    assert "esp32_set_relay" in proc.stdout
    assert "demo finished" in proc.stdout
    assert "server closed the stream" not in proc.stdout
