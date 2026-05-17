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
