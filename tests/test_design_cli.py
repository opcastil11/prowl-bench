"""Smoke tests for `prowl-bench design ...` subcommands.

These are thin tests; the heavy lifting (OpenAPI → Manifest conversion,
signing, encoding) is covered exhaustively in the mycelio test suite.
What this file proves is just that the Typer wiring + CLI ergonomics
work end-to-end on a real spec.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prowl_bench.cli import app

runner = CliRunner()


def _petstore_spec() -> dict:
    return {
        "openapi": "3.0.0",
        "info": {"title": "Petstore", "version": "1.0"},
        "servers": [{"url": "https://petstore.example.com/v1"}],
        "components": {
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}
        },
        "paths": {
            "/pets": {
                "get": {"operationId": "listPets"},
                "post": {"operationId": "createPet"},
            }
        },
    }


def _write_spec(tmp_path: Path) -> Path:
    spec_path = tmp_path / "petstore.json"
    spec_path.write_text(json.dumps(_petstore_spec()), encoding="utf-8")
    return spec_path


def test_design_subapp_appears_in_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "design" in result.stdout.lower()


def test_design_manifest_help_lists_command():
    result = runner.invoke(app, ["design", "--help"])
    assert result.exit_code == 0
    assert "manifest" in result.stdout.lower()


def test_design_manifest_generates_unsigned_file(tmp_path):
    """End-to-end: read OpenAPI, write unsigned manifest binary, succeed."""
    spec = _write_spec(tmp_path)
    output = tmp_path / "petstore.myc"
    result = runner.invoke(
        app,
        ["design", "manifest", str(spec), "--output", str(output)],
    )
    assert result.exit_code == 0, result.stdout

    # File written + non-empty
    assert output.exists()
    assert output.stat().st_size > 50, "manifest should be at least 50 bytes"

    # Ephemeral vendor seed written alongside (since we didn't pass --vendor-pubkey)
    seed_file = Path(str(output) + ".vendor_seed")
    assert seed_file.exists()
    assert seed_file.stat().st_size == 32  # Ed25519 seed is exactly 32 bytes


def test_design_manifest_rejects_invalid_vendor_pubkey(tmp_path):
    spec = _write_spec(tmp_path)
    result = runner.invoke(
        app,
        ["design", "manifest", str(spec), "--vendor-pubkey", "not-hex"],
    )
    assert result.exit_code != 0
    assert "hex" in result.stdout.lower()


def test_design_manifest_rejects_wrong_pubkey_length(tmp_path):
    spec = _write_spec(tmp_path)
    # 16 bytes of hex = 32 chars, too short for an Ed25519 pubkey
    result = runner.invoke(
        app,
        ["design", "manifest", str(spec), "--vendor-pubkey", "00" * 16],
    )
    assert result.exit_code != 0
    assert "32 bytes" in result.stdout


def test_design_manifest_sign_with_real_vendor_pubkey_is_rejected(tmp_path):
    """--sign only works with the generated ephemeral key (we don't have the
    seed for a user-provided pubkey)."""
    spec = _write_spec(tmp_path)
    result = runner.invoke(
        app,
        [
            "design", "manifest", str(spec),
            "--vendor-pubkey", "00" * 32,
            "--sign",
        ],
    )
    assert result.exit_code != 0
    assert "ephemeral" in result.stdout.lower()


def test_design_manifest_sign_mode_emits_larger_payload(tmp_path):
    """Signed output should include the 64-byte vendor signature on top
    of the unsigned core, so it's strictly larger."""
    spec = _write_spec(tmp_path)
    unsigned_out = tmp_path / "petstore.myc"
    signed_out = tmp_path / "petstore-signed.myc"

    r1 = runner.invoke(app, ["design", "manifest", str(spec), "--output", str(unsigned_out)])
    r2 = runner.invoke(app, ["design", "manifest", str(spec), "--output", str(signed_out), "--sign"])
    assert r1.exit_code == 0 and r2.exit_code == 0

    assert signed_out.stat().st_size > unsigned_out.stat().st_size
    # The delta is the vendor signature: ~64 bytes + a few bytes of payload overhead
    delta = signed_out.stat().st_size - unsigned_out.stat().st_size
    assert 64 <= delta <= 80, f"unexpected sig overhead: {delta} bytes"


def test_design_manifest_outputs_summary_table(tmp_path):
    """Summary table should include the key fields so users can sanity-check
    the generated manifest at a glance."""
    spec = _write_spec(tmp_path)
    output = tmp_path / "petstore.myc"
    result = runner.invoke(
        app,
        ["design", "manifest", str(spec), "--output", str(output)],
    )
    assert result.exit_code == 0
    # Strip ANSI escape codes for portable assertions
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "petstore" in plain  # slug
    assert "https://petstore.example.com/v1" in plain  # backend
    assert "Authorization" in plain  # auth header


# ─── design review (local heuristic + --cloud) ─────────────────────


def test_design_review_help_lists_command():
    result = runner.invoke(app, ["design", "review", "--help"])
    assert result.exit_code == 0
    assert "openapi" in result.stdout.lower()
    assert "--cloud" in result.stdout


def test_design_review_local_prints_heuristic(tmp_path):
    spec = _write_spec(tmp_path)
    result = runner.invoke(app, ["design", "review", str(spec)])
    assert result.exit_code == 0, result.stdout
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    # Two ops in the petstore: list + create.
    assert "ops" in plain.lower()
    assert "2" in plain
    # Auth scheme detection: http bearer.
    assert "bearer" in plain.lower()
    # Doesn't try to hit the network.
    assert "POST" not in plain  # no cloud call


def test_design_review_local_handles_yaml_from_file(tmp_path):
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "openapi: 3.0.0\n"
        "info: {title: Hello, version: '1.0'}\n"
        "servers: [{url: https://hello.example}]\n"
        "paths:\n"
        "  /ping:\n"
        "    get: {operationId: ping}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["design", "review", str(spec_path)])
    # PyYAML may or may not be installed; assert either OK exit + recognized
    # 1 op, OR a clean error exit (BadParameter sends to stderr, hence not
    # in stdout — we just check the exit code signals failure cleanly).
    if result.exit_code == 0:
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        assert "Hello" in plain or "1" in plain
    else:
        # Non-zero exit is acceptable when PyYAML is missing.
        assert result.exit_code in (1, 2)


def test_design_review_cloud_posts_to_endpoint(tmp_path, monkeypatch):
    """--cloud forms a POST against /v1/endpoint/review and parses the response."""
    spec = _write_spec(tmp_path)

    captured = {}

    class FakeResponse:
        status_code = 200
        reason_phrase = "OK"
        is_success = True
        headers = {"content-type": "application/json"}

        def json(self):
            return {
                "review_id": "abc",
                "tier": "anon_free",
                "cost_usd": 0.0,
                "duration_ms": 3200,
                "overall": 7.4,
                "dimensions": {
                    "parseability": 8.2, "auth_simplicity": 9.0,
                    "error_clarity": 7.1, "schema_gotchas": 6.8, "token_bloat": 5.4,
                },
                "suggestions": [
                    {"tag": "BODY", "op": "POST /pets", "title": "Tighten request shape", "token_savings_pct": 12},
                ],
                "providers_used": ["claude", "openai"],
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", FakeClient)

    result = runner.invoke(
        app,
        ["design", "review", str(spec), "--cloud", "--api-base", "https://example.test"],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["url"].endswith("/v1/endpoint/review")
    assert "spec_inline" in captured["json"]
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "7.4" in plain
    assert "Tighten" in plain


def test_design_review_cloud_402_shows_friendly_message(tmp_path, monkeypatch):
    spec = _write_spec(tmp_path)

    class FakeResponse:
        status_code = 402
        reason_phrase = "Payment Required"
        is_success = False
        headers = {"content-type": "application/json"}
        text = ""
        def json(self):
            return {"accepts": [{"maxAmountRequired": "20000"}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **k): return FakeResponse()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", FakeClient)

    result = runner.invoke(app, ["design", "review", str(spec), "--cloud"])
    assert result.exit_code == 2
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "Free tier exhausted" in plain
    assert "0.02" in plain  # 20000 micro = $0.02


# ─── design test (local probe + --cloud) ───────────────────────────


def test_design_test_help_lists_command():
    result = runner.invoke(app, ["design", "test", "--help"])
    assert result.exit_code == 0
    assert "--cloud" in result.stdout
    assert "--spec" in result.stdout


def test_design_test_local_probes_url(monkeypatch):
    """Local mode does an HTTP GET, no Prowl involvement."""

    class FakeResponse:
        status_code = 200
        reason_phrase = "OK"
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'
        content = b'{"ok": true}'

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def request(self, method, url):
            self.last = (method, url)
            return FakeResponse()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", FakeClient)

    result = runner.invoke(app, ["design", "test", "https://api.example/ping"])
    assert result.exit_code == 0, result.stdout
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "200" in plain
    # body preview present
    assert '{"ok": true}' in plain


def test_design_test_cloud_requires_spec(monkeypatch):
    """--cloud without --spec should error out — the hosted endpoint
    can't score without the spec."""

    class FakeResponse:
        status_code = 200
        reason_phrase = "OK"
        headers = {}
        text = ""
        content = b""

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def request(self, *a, **k): return FakeResponse()
        def post(self, *a, **k): return FakeResponse()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "Client", FakeClient)

    result = runner.invoke(app, ["design", "test", "https://api.example/x", "--cloud"])
    assert result.exit_code == 1
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "--spec" in plain or "spec" in plain.lower()
