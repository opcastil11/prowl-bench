"""Local web server for `prowl-bench design ui`.

Serves the static UI bundle (HTML + JS, no backend) from a localhost
port. The UI itself fetches from https://prowl.world/v1/endpoint/review
for paid scoring — no LLM keys are needed locally.

Why stdlib http.server instead of FastAPI/Hono? Zero new deps in the
prowl-bench install footprint. The UI is a single static HTML file;
nothing dynamic to do server-side.
"""
from __future__ import annotations

import functools
import http.server
import socket
import socketserver
import sys
import webbrowser
from pathlib import Path

UI_DIR = Path(__file__).parent / "ui"


class _SilentRequestHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that suppresses noisy per-request logs."""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — match parent signature
        # Only log non-200s, so stdout stays clean in normal use.
        try:
            status = int(args[1]) if len(args) >= 2 else 200
        except (TypeError, ValueError):
            status = 200
        if status >= 400:
            sys.stderr.write(f"prowl-design-ui: {format % args}\n")


def _port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_open_port(start: int = 4711, host: str = "127.0.0.1", attempts: int = 20) -> int:
    """Find an available port starting at ``start``; raise if none free."""
    for offset in range(attempts):
        port = start + offset
        if _port_available(port, host):
            return port
    raise RuntimeError(
        f"No open port in {start}-{start + attempts - 1} on {host}. "
        f"Pass --port to override."
    )


def serve(port: int = 4711, open_browser: bool = True, host: str = "127.0.0.1") -> None:
    """Start the local UI server. Blocks until Ctrl-C.

    Raises ``FileNotFoundError`` if the UI bundle wasn't installed
    (e.g. running from a sdist that excluded the design assets).
    """
    if not (UI_DIR / "index.html").exists():
        raise FileNotFoundError(
            f"UI bundle not found at {UI_DIR}. Reinstall with "
            f"'pip install --upgrade prowl-bench[design]'."
        )

    handler = functools.partial(_SilentRequestHandler, directory=str(UI_DIR))
    # ThreadingTCPServer so a slow tab doesn't block another browser request.
    with socketserver.ThreadingTCPServer((host, port), handler) as httpd:
        httpd.allow_reuse_address = True
        url = f"http://{host}:{port}/"
        print(f"\n  Prowl Design — local UI")
        print(f"  ▸ serving {UI_DIR}")
        print(f"  ▸ at {url}")
        print(f"  ▸ Ctrl-C to stop.\n")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception as exc:
                print(f"  (couldn't auto-open browser: {exc})\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Stopped.")
