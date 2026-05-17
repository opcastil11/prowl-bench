"""Smoke tests for `prowl-bench design ui` — the local web UI subcommand.

Covers CLI wiring, port discovery, UI bundle presence, and an actual
serve-and-fetch round trip.
"""
from __future__ import annotations

import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prowl_bench.cli import app
from prowl_bench.design.server import UI_DIR, find_open_port, serve

runner = CliRunner()


def test_design_ui_help_renders():
    result = runner.invoke(app, ["design", "ui", "--help"])
    assert result.exit_code == 0
    assert "port" in result.stdout.lower()
    assert "browser" in result.stdout.lower() or "no-open" in result.stdout.lower()


def test_ui_bundle_is_packaged():
    """The HTML bundle must ship with the package — otherwise `design ui`
    can't serve anything. This catches packaging regressions."""
    index = UI_DIR / "index.html"
    assert index.exists(), f"UI bundle missing at {index}"
    text = index.read_text(encoding="utf-8")
    assert "Prowl Design" in text
    # The local-mode marker we set during the extraction must be present;
    # if someone replaces the file with the hosted version, this test fails.
    assert "running locally" in text


def test_find_open_port_finds_something():
    p = find_open_port(start=50_000, attempts=10)
    assert 50_000 <= p < 50_010


def test_find_open_port_raises_when_all_taken():
    """If every candidate port is held, find_open_port must raise rather
    than silently picking a wrong port."""
    held_sockets = []
    try:
        # Hold a small range of ports so they're all unavailable.
        for offset in range(5):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", 50_500 + offset))
            held_sockets.append(s)
        with pytest.raises(RuntimeError, match="No open port"):
            find_open_port(start=50_500, attempts=5)
    finally:
        for s in held_sockets:
            s.close()


def test_serve_actually_serves_the_index(tmp_path):
    """End-to-end: start the server in a background thread, fetch the
    index, confirm we get the bundled HTML back."""
    port = find_open_port(start=50_700)

    server_thread = threading.Thread(
        target=serve,
        kwargs={"port": port, "open_browser": False},
        daemon=True,
    )
    server_thread.start()

    # Wait up to 2s for the server to be reachable.
    deadline = time.monotonic() + 2.0
    body = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as resp:
                body = resp.read().decode("utf-8")
                status = resp.status
                break
        except (urllib.error.URLError, ConnectionRefusedError):
            time.sleep(0.05)
    assert body is not None, "server never came up within 2s"
    assert status == 200
    assert "Prowl Design" in body
    assert "running locally" in body
    # Daemon thread will die when test process exits; no explicit shutdown
    # to keep this test simple. The OS recycles the port within seconds.
