"""Wiring tests for `prowl-bench mcp`.

The scoring itself is covered in test_mcp_conformance.py; what this file proves
is the CLI contract — exit codes, JSON shape, and that a failed resolution is
reported rather than silently succeeding.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from prowl_bench.cli import app
from prowl_bench.mcp.client import McpProbe, McpTool

runner = CliRunner()


def _live(endpoint="https://x.example.com/mcp", tools=None):
    return McpProbe(
        endpoint=endpoint, ok=True, status="live", http_status=200,
        protocol_version="2025-06-18", server_name="demo", server_version="1.0.0",
        instructions="Use me.", framing="json", latency_ms=180,
        tools=tools if tools is not None else [
            McpTool(name="search", description="Search the corpus and rank matches.",
                    input_schema={"type": "object", "properties": {
                        "q": {"type": "string", "description": "The query."}}}),
        ],
    )


def _dead(endpoint="https://x.example.com"):
    return McpProbe(endpoint=endpoint, ok=False, status="dead",
                    http_status=404, error="initialize: HTTP 404", latency_ms=90)


@pytest.fixture
def stub_resolve(monkeypatch):
    """Patch where the CLI looks it up, not where it is defined."""
    def _install(best, attempts=None):
        async def fake(url, *, timeout=20.0):
            return best, attempts or [best]
        import prowl_bench.mcp as pkg
        monkeypatch.setattr(pkg, "resolve_endpoint", fake)
    return _install


def test_live_server_exits_zero(stub_resolve):
    stub_resolve(_live())
    result = runner.invoke(app, ["mcp", "https://x.example.com/mcp"])
    assert result.exit_code == 0
    assert "demo" in result.output


def test_dead_server_exits_nonzero(stub_resolve):
    """A benchmark that cannot reach the server must not look like a pass."""
    stub_resolve(_dead())
    result = runner.invoke(app, ["mcp", "https://x.example.com"])
    assert result.exit_code == 1
    assert "No MCP server answered" in result.output


def test_a_flawless_server_really_does_score_100(stub_resolve):
    """Guards the gate test below: it only means something if the ceiling is
    reachable."""
    stub_resolve(_live())
    result = runner.invoke(app, ["mcp", "https://x.example.com/mcp", "-o", "json"])
    assert json.loads(result.output)["overall"] == 100


def test_min_score_gate_fails_the_build(stub_resolve):
    stub_resolve(_live(tools=[McpTool(name="mystery")]))
    result = runner.invoke(app, ["mcp", "https://x.example.com/mcp", "--min-score", "100"])
    assert result.exit_code == 1
    assert "below the --min-score" in result.output


def test_min_score_gate_passes_when_met(stub_resolve):
    stub_resolve(_live())
    result = runner.invoke(app, ["mcp", "https://x.example.com/mcp", "--min-score", "1"])
    assert result.exit_code == 0


def test_json_output_is_parseable(stub_resolve):
    stub_resolve(_live())
    result = runner.invoke(app, ["mcp", "https://x.example.com/mcp", "-o", "json"])
    payload = json.loads(result.output)
    assert payload["reachable"] is True
    assert payload["tools"]["total"] == 1
    assert 0 <= payload["overall"] <= 100


def test_resolution_chain_is_shown_when_it_took_more_than_one_try(stub_resolve):
    """The vendor's first question is always 'why did you test *that* URL?'."""
    best = _live()
    stub_resolve(best, [_dead("https://x.example.com"), best])
    result = runner.invoke(app, ["mcp", "https://x.example.com"])
    assert "https://x.example.com/mcp" in result.output
    assert "404" in result.output


def test_tools_flag_lists_every_tool(stub_resolve):
    stub_resolve(_live(tools=[
        McpTool(name="alpha", description="Does alpha things properly."),
        McpTool(name="beta"),
    ]))
    result = runner.invoke(app, ["mcp", "https://x.example.com/mcp", "--tools"])
    assert "alpha" in result.output and "beta" in result.output
    assert "no schema" in result.output


def test_findings_carry_their_fix(stub_resolve):
    stub_resolve(_live(tools=[McpTool(name="mystery")]))
    result = runner.invoke(app, ["mcp", "https://x.example.com/mcp"])
    assert "inputSchema" in result.output
