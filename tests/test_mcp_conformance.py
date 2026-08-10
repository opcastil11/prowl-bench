"""Tests for the deterministic MCP scorecard.

`score_probe` is pure, so these are plain function calls — which is the whole
argument for splitting the scoring out of the template. Nothing here needs a
network, a database, or an LLM key.
"""
from __future__ import annotations

from prowl_bench.mcp.client import McpProbe, McpTool
from prowl_bench.mcp.conformance import (
    MIN_USEFUL_DESCRIPTION,
    WEIGHTS,
    score_probe,
)


def _tool(name="search", desc="Search the corpus and return ranked matches.",
          props=None, has_schema=True):
    schema = None
    if has_schema:
        schema = {"type": "object", "properties": props if props is not None else {
            "q": {"type": "string", "description": "The query string."}
        }}
    return McpTool(name=name, description=desc, input_schema=schema)


def _probe(tools=None, **kw):
    defaults = {
        "endpoint": "https://x.example.com/mcp", "ok": True, "status": "live",
        "http_status": 200, "protocol_version": "2025-06-18", "server_name": "demo",
        "server_version": "1.0.0", "instructions": "Use me for demo things.",
        "framing": "json", "latency_ms": 200,
    }
    defaults.update(kw)
    return McpProbe(tools=tools if tools is not None else [_tool()], **defaults)


class TestWeights:
    def test_weights_sum_to_one(self):
        assert round(sum(WEIGHTS.values()), 6) == 1.0


class TestUnreachable:
    def test_dead_server_scores_zero(self):
        report = score_probe(_probe(ok=False, status="dead", error="HTTP 404", tools=[]))
        assert report.overall == 0
        assert report.reachable is False
        assert report.findings[0].severity == "critical"

    def test_dead_server_finding_points_at_the_attempts(self):
        report = score_probe(_probe(ok=False, status="dead", error="HTTP 404", tools=[]))
        assert "attempts" in report.findings[0].fix

    def test_auth_required_is_not_scored_as_dead(self):
        """The server is alive and speaking MCP; we just cannot see it. Scoring
        that identically to a 404 would tell a vendor to fix the wrong thing."""
        report = score_probe(_probe(ok=False, status="auth_required",
                                    error="auth required (HTTP 401)", tools=[]))
        assert report.overall > 0
        assert report.dimensions["reachability"] == 4.0
        assert report.findings[0].severity == "high"

    def test_attempts_are_carried_into_the_report(self):
        attempts = [
            McpProbe(endpoint="https://x.example.com", status="dead", http_status=404),
            McpProbe(endpoint="https://x.example.com/mcp", status="dead", http_status=404),
        ]
        report = score_probe(_probe(ok=False, tools=[]), attempts)
        assert [a["endpoint"] for a in report.attempts] == [a.endpoint for a in attempts]


class TestHealthyServer:
    def test_a_well_built_server_scores_high(self):
        report = score_probe(_probe(tools=[_tool(), _tool(name="fetch")]))
        assert report.overall >= 85
        assert report.reachable

    def test_counts_are_reported_not_just_the_score(self):
        report = score_probe(_probe(tools=[_tool(), _tool(name="b", desc="hi")]))
        assert report.tool_count == 2
        assert report.documented_tools == 1

    def test_dimension_keys_match_the_weights(self):
        report = score_probe(_probe())
        assert set(report.dimensions) == set(WEIGHTS)


class TestToolDiscovery:
    def test_handshake_without_tools_is_critical(self):
        report = score_probe(_probe(tools=[]))
        assert report.dimensions["tool_discovery"] == 2.0
        assert any(f.severity == "critical" for f in report.findings)

    def test_no_tools_means_no_documentation_credit(self):
        report = score_probe(_probe(tools=[]))
        assert report.dimensions["tool_documentation"] == 0.0
        assert report.dimensions["schema_quality"] == 0.0


class TestDocumentation:
    def test_short_descriptions_do_not_count(self):
        short = "x" * (MIN_USEFUL_DESCRIPTION - 1)
        report = score_probe(_probe(tools=[_tool(desc=short)]))
        assert report.documented_tools == 0
        assert report.dimensions["tool_documentation"] == 0.0

    def test_partial_documentation_scores_proportionally(self):
        tools = [_tool(name="a"), _tool(name="b", desc=None),
                 _tool(name="c"), _tool(name="d")]
        report = score_probe(_probe(tools=tools))
        assert report.dimensions["tool_documentation"] == 7.5

    def test_finding_names_the_offending_tools(self):
        report = score_probe(_probe(tools=[_tool(name="mystery", desc=None)]))
        assert any("mystery" in f.detail for f in report.findings)

    def test_finding_truncates_a_long_list(self):
        tools = [_tool(name=f"t{i}", desc=None) for i in range(9)]
        report = score_probe(_probe(tools=tools))
        detail = next(f.detail for f in report.findings if "lack a useful description" in f.detail)
        assert "+4 more" in detail


class TestSchemaQuality:
    def test_missing_schemas_are_critical_when_widespread(self):
        tools = [_tool(name="a", has_schema=False), _tool(name="b", has_schema=False)]
        report = score_probe(_probe(tools=tools))
        assert any(f.severity == "critical" and "inputSchema" in f.detail
                   for f in report.findings)

    def test_undescribed_properties_are_flagged_separately(self):
        tools = [_tool(props={"q": {"type": "string"}, "n": {"type": "integer"}})]
        report = score_probe(_probe(tools=tools))
        assert any("schema properties have no description" in f.detail
                   for f in report.findings)

    def test_having_a_schema_outweighs_annotating_every_field(self):
        """An agent can call a tool with an unannotated schema; it cannot call
        one with no schema at all."""
        bare = score_probe(_probe(tools=[_tool(props={"q": {"type": "string"}})]))
        none = score_probe(_probe(tools=[_tool(has_schema=False)]))
        assert bare.dimensions["schema_quality"] > none.dimensions["schema_quality"]


class TestLatency:
    def test_fast_server_gets_full_marks(self):
        assert score_probe(_probe(latency_ms=120)).dimensions["latency"] == 10.0

    def test_slow_server_is_flagged(self):
        report = score_probe(_probe(latency_ms=4200))
        assert report.dimensions["latency"] < 5.0
        assert any("4200ms" in f.detail for f in report.findings)

    def test_score_decreases_monotonically(self):
        scores = [score_probe(_probe(latency_ms=ms)).dimensions["latency"]
                  for ms in (400, 1000, 3000, 6000)]
        assert scores == sorted(scores, reverse=True)


class TestAgentGuidance:
    def test_missing_instructions_is_a_low_finding(self):
        report = score_probe(_probe(instructions=None))
        assert any(f.severity == "low" and "instructions" in f.detail
                   for f in report.findings)
        assert report.dimensions["agent_guidance"] < 10.0

    def test_missing_version_is_flagged(self):
        report = score_probe(_probe(server_version=None))
        assert any("no version" in f.detail for f in report.findings)


class TestNameSafety:
    def test_overlong_names_are_flagged(self):
        report = score_probe(_probe(tools=[_tool(name="t" * 140)]))
        assert any("exceed" in f.detail for f in report.findings)

    def test_normal_names_are_not(self):
        report = score_probe(_probe())
        assert not any("exceed" in f.detail for f in report.findings)


class TestReportShape:
    def test_findings_are_sorted_most_severe_first(self):
        tools = [_tool(name="a", desc=None, has_schema=False)]
        report = score_probe(_probe(tools=tools, instructions=None, server_version=None))
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        seq = [order[f.severity] for f in report.findings]
        assert seq == sorted(seq)

    def test_as_dict_is_json_serialisable(self):
        import json
        json.dumps(score_probe(_probe()).as_dict())

    def test_score_is_deterministic(self):
        """The selling point over the LLM path: two runs, same number."""
        probe = _probe()
        assert score_probe(probe).overall == score_probe(probe).overall
