"""MCP Compliance template — JSON-RPC conformance testing.

Rewritten 2026-08-10 on top of `prowl_bench.mcp.client`. The previous version
appended `/mcp` to whatever URL it was given, sent a JSON-only `Accept`, called
`resp.json()` unconditionally, and fired `tools/list` with no handshake. Against
a real server that is four ways to fail, and none of them looked like a failure
— the results went to an LLM, which dutifully scored the wreckage.

What changed that matters to a caller:

* The URL you pass is tried **first**. Passing the actual endpoint used to be
  the one input guaranteed to 404.
* SSE-framed replies are decoded, so half the network stops reading as broken.
* The score has a floor that does not depend on an LLM: `score_probe()` counts
  schemas and descriptions, and if every LLM provider fails the template
  returns that arithmetic rather than raising.
"""
from __future__ import annotations

import json

from prowl_bench.templates.base import BaseBenchmarkTemplate, TemplateConfig
from prowl_bench.core.types import (
    ServiceAnalysis, BenchmarkPlan, BenchmarkExecutionResult, NormalizedScore,
)
from prowl_bench.core.json_utils import extract_json
from prowl_bench.llm.router import call_llm
from prowl_bench.llm.prompts import MCP_INTERPRET_SYSTEM
from prowl_bench.mcp.client import probe_server, resolve_endpoint
from prowl_bench.mcp.conformance import score_probe


class McpComplianceTemplate(BaseBenchmarkTemplate):
    config = TemplateConfig(
        slug="mcp_compliance", name="MCP Compliance",
        description="Tests MCP server conformance: endpoint resolution, handshake, tool discovery, schema quality.",
        requires_credentials=False, category_hints=["mcp"],
    )

    async def analyze(self, url, name, spec_content, docs_content) -> ServiceAnalysis:
        """Resolve where the MCP server actually is.

        This is a network phase, not a spec-reading one — an MCP server's spec
        *is* its handshake. `resolve_endpoint` tries the URL as given before
        any guess, and records what it tried so a vendor can see why we
        concluded their server is somewhere else.
        """
        probe, attempts = await resolve_endpoint(url)
        return ServiceAnalysis(
            service_id="",
            service_type="mcp_server",
            base_url=probe.endpoint,
            auth_method="none" if probe.status != "auth_required" else "unknown",
            auth_config={
                "_resolved_from": url,
                "_attempts": [
                    {"endpoint": a.endpoint, "status": a.status,
                     "http_status": a.http_status, "error": a.error}
                    for a in attempts
                ],
            },
            endpoints=[{"path": probe.endpoint, "method": "POST",
                        "purpose": "MCP JSON-RPC endpoint"}],
            pricing_model={}, rate_limits={},
            capabilities=["mcp", "tool_use"],
            raw_analysis=(
                f"MCP endpoint resolved to {probe.endpoint} "
                f"(status={probe.status}, tools={probe.tool_count})"
            ),
        )

    async def plan(self, analysis: ServiceAnalysis) -> BenchmarkPlan:
        """The plan is fixed, and deliberately read-only.

        `tools/call` has side effects owned by whoever runs the server. A
        benchmark that writes to someone's production database is not a
        benchmark, so conformance stops at discovery.
        """
        return BenchmarkPlan(
            service_id=analysis.service_id,
            tests=[
                {"name": "mcp_initialize", "endpoint": analysis.base_url, "method": "POST"},
                {"name": "mcp_tools_list", "endpoint": analysis.base_url, "method": "POST"},
            ],
            pricing_probes=[],
            stress_profile={"concurrent_requests": 1, "duration_seconds": 5},
        )

    async def execute(self, plan, analysis, raw_credential) -> list[BenchmarkExecutionResult]:
        """One clean probe of the resolved endpoint.

        Deliberately re-probes rather than reusing the resolution result: the
        latency we report should be a single handshake, not the sum of however
        many wrong guesses resolution had to make first.
        """
        endpoint = analysis.base_url
        probe = await probe_server(endpoint)
        report = score_probe(probe)

        # Carry the structured probe through as one result. The pipeline's
        # generic shape wants HTTP-ish rows, and an MCP session is not that —
        # so the JSON-RPC facts go in response_body where interpret can read
        # them, rather than being flattened into two fake HTTP calls.
        return [BenchmarkExecutionResult(
            test_name="mcp_conformance",
            endpoint=endpoint,
            method="POST",
            request_payload={"methods": ["initialize", "notifications/initialized", "tools/list"]},
            status_code=probe.http_status,
            response_body={
                "ok": probe.ok,
                "status": probe.status,
                "framing": probe.framing,
                "stateful": bool(probe.session_id),
                "protocol_version": probe.protocol_version,
                "server": {"name": probe.server_name, "version": probe.server_version},
                "instructions_present": bool(probe.instructions),
                "tool_count": probe.tool_count,
                "tools": [
                    {"name": t.name,
                     "description": (t.description or "")[:300],
                     "has_schema": bool(t.input_schema),
                     "property_count": len((t.input_schema or {}).get("properties") or {})}
                    for t in probe.tools[:40]
                ],
                "deterministic": report.as_dict(),
                "error": probe.error,
            },
            response_headers={},
            latency_ms=probe.latency_ms or 0,
            error=probe.error,
        )]

    async def interpret(self, analysis, results) -> NormalizedScore:
        result = results[0] if results else None
        body = (result.response_body if result else None) or {}
        deterministic = body.get("deterministic") or {}

        user_msg = (
            "## MCP Conformance Probe\n"
            f"Endpoint resolved to `{analysis.base_url}` "
            f"(you were pointed at `{analysis.auth_config.get('_resolved_from')}`).\n\n"
            f"```json\n{json.dumps(body, default=str)[:12000]}\n```\n\n"
            "The `deterministic` block is measured, not inferred: counts of tools "
            "with schemas and descriptions, and the latency of one handshake. "
            "Treat it as ground truth and judge the parts it cannot count."
        )

        try:
            raw = await call_llm(MCP_INTERPRET_SYSTEM, user_msg, max_tokens=2048)
            parsed = extract_json(raw)
        except Exception as exc:
            # The arithmetic score is a real answer, so a dead LLM downgrades
            # the run instead of failing it. This is the difference between
            # "no key configured" being an inconvenience and a hard stop.
            return self._from_deterministic(analysis, deterministic, reason=str(exc))

        if not parsed or not parsed.get("dimensions"):
            return self._from_deterministic(
                analysis, deterministic, reason="LLM returned no dimensions"
            )

        issues = [
            i if isinstance(i, dict) else {"severity": "medium", "detail": str(i)}
            for i in (parsed.get("issues") or [])
        ]
        # The measured findings are not opinions — they survive whatever the
        # model decided to mention.
        seen = {i.get("detail") for i in issues}
        for f in deterministic.get("findings", []):
            if f.get("detail") not in seen:
                issues.append(f)

        return NormalizedScore(
            service_id=analysis.service_id,
            overall=parsed.get("overall", deterministic.get("overall", 0)),
            dimensions=parsed.get("dimensions", {}),
            pricing_normalized=parsed.get("pricing_normalized", {}),
            issues=issues,
            recommendations=parsed.get("recommendations", []),
            raw_interpretation=raw,
        )

    @staticmethod
    def _from_deterministic(
        analysis: ServiceAnalysis, deterministic: dict, *, reason: str
    ) -> NormalizedScore:
        dims = deterministic.get("dimensions") or {}
        return NormalizedScore(
            service_id=analysis.service_id,
            overall=deterministic.get("overall", 0),
            # Map the conformance dimensions onto the names the rest of the
            # pipeline scores, so a fallback report is still comparable.
            dimensions={
                "doc_quality": dims.get("tool_documentation", 0.0),
                "first_try_success": dims.get("schema_quality", 0.0),
                "response_parseability": dims.get("tool_discovery", 0.0),
                "latency": dims.get("latency", 0.0),
                "auth_simplicity": dims.get("reachability", 0.0),
                "token_efficiency": dims.get("agent_guidance", 0.0),
            },
            pricing_normalized={},
            issues=deterministic.get("findings", []),
            recommendations=[
                f.get("fix") for f in deterministic.get("findings", []) if f.get("fix")
            ],
            raw_interpretation=json.dumps({
                "source": "deterministic_fallback",
                "reason": reason,
                "report": deterministic,
            }),
        )
