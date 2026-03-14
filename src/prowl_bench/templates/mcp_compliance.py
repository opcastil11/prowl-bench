"""MCP Compliance template — JSON-RPC conformance testing."""
from __future__ import annotations

import json
import time

import httpx

from prowl_bench.templates.base import BaseBenchmarkTemplate, TemplateConfig
from prowl_bench.core.types import ServiceAnalysis, BenchmarkPlan, BenchmarkExecutionResult, NormalizedScore
from prowl_bench.core.json_utils import extract_json
from prowl_bench.llm.router import call_llm
from prowl_bench.llm.prompts import MCP_INTERPRET_SYSTEM
from prowl_bench.sandbox.url_validator import validate_url


class McpComplianceTemplate(BaseBenchmarkTemplate):
    config = TemplateConfig(
        slug="mcp_compliance", name="MCP Compliance",
        description="Tests MCP server conformance: tool discovery, JSON-RPC format, execution.",
        requires_credentials=False, category_hints=["mcp"],
    )

    async def analyze(self, url, name, spec_content, docs_content):
        return ServiceAnalysis(
            service_id="", service_type="mcp_server", base_url=url,
            auth_method="none", auth_config={},
            endpoints=[{"path": "/mcp", "method": "POST", "purpose": "JSON-RPC endpoint"}],
            pricing_model={}, rate_limits={}, capabilities=["mcp", "tool_use"],
            raw_analysis=f"MCP server at {url}",
        )

    async def plan(self, analysis):
        tests = [
            {"name": "mcp_initialize", "endpoint": "/mcp", "method": "POST",
             "payload": {"jsonrpc": "2.0", "method": "initialize", "id": 1,
                         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "prowl-bench", "version": "0.1.0"}}}},
            {"name": "mcp_tools_list", "endpoint": "/mcp", "method": "POST",
             "payload": {"jsonrpc": "2.0", "method": "tools/list", "id": 2}},
        ]
        return BenchmarkPlan(
            service_id=analysis.service_id, tests=tests,
            pricing_probes=[], stress_profile={"concurrent_requests": 1, "duration_seconds": 5},
        )

    async def execute(self, plan, analysis, raw_credential):
        results: list[BenchmarkExecutionResult] = []
        base = analysis.base_url.rstrip("/")

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for test in plan.tests:
                url = f"{base}{test.get('endpoint', '/mcp')}"
                start = time.monotonic()
                try:
                    validate_url(url)
                    resp = await client.post(url, json=test.get("payload", {}),
                                             headers={"Content-Type": "application/json"})
                    latency_ms = int((time.monotonic() - start) * 1000)
                    try:
                        body = resp.json()
                    except Exception:
                        body = resp.text[:2000]
                    results.append(BenchmarkExecutionResult(
                        test_name=test["name"], endpoint=test.get("endpoint", "/mcp"),
                        method="POST", request_payload=test.get("payload"),
                        status_code=resp.status_code, response_body=body,
                        response_headers=dict(resp.headers), latency_ms=latency_ms,
                    ))
                except Exception as exc:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    results.append(BenchmarkExecutionResult(
                        test_name=test["name"], endpoint=test.get("endpoint", "/mcp"),
                        method="POST", request_payload=test.get("payload"),
                        status_code=None, response_body=None, response_headers={},
                        latency_ms=latency_ms, error=str(exc),
                    ))
        return results

    async def interpret(self, analysis, results):
        results_data = [{
            "test_name": r.test_name, "status_code": r.status_code,
            "latency_ms": r.latency_ms, "error": r.error,
            "response_preview": str(r.response_body)[:500] if r.response_body else None,
        } for r in results]
        user_msg = f"## MCP Server Tests\n```json\n{json.dumps(results_data, default=str)}\n```"
        raw = await call_llm(MCP_INTERPRET_SYSTEM, user_msg, max_tokens=2048)
        parsed = extract_json(raw)
        return NormalizedScore(
            service_id=analysis.service_id, overall=parsed.get("overall", 0),
            dimensions=parsed.get("dimensions", {}),
            pricing_normalized=parsed.get("pricing_normalized", {}),
            issues=parsed.get("issues", []), recommendations=parsed.get("recommendations", []),
            raw_interpretation=raw,
        )
