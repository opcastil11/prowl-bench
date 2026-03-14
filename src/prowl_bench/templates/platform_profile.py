"""Platform Profile template — websites and tools without public APIs."""
from __future__ import annotations

import json
import time

import httpx

from prowl_bench.config import get_config
from prowl_bench.templates.base import BaseBenchmarkTemplate, TemplateConfig
from prowl_bench.core.types import ServiceAnalysis, BenchmarkPlan, BenchmarkExecutionResult, NormalizedScore
from prowl_bench.core.json_utils import extract_json
from prowl_bench.llm.router import call_llm
from prowl_bench.llm.prompts import PLATFORM_ANALYZE_SYSTEM, PLATFORM_INTERPRET_SYSTEM
from prowl_bench.sandbox.url_validator import validate_url


class PlatformProfileTemplate(BaseBenchmarkTemplate):
    config = TemplateConfig(
        slug="platform_profile",
        name="Platform Profile",
        description="Assessment for platforms and tools without public APIs.",
        requires_credentials=False,
        category_hints=["collaboration", "project-management", "devtools", "productivity"],
    )

    async def analyze(self, url, name, spec_content, docs_content):
        user_msg = f"Service: {name}\nURL: {url}\n\n"
        user_msg += f"## Description\n{spec_content[:10000] if spec_content else 'No description'}\n"

        raw = await call_llm(PLATFORM_ANALYZE_SYSTEM, user_msg, max_tokens=2048)
        parsed = extract_json(raw)

        return ServiceAnalysis(
            service_id="", service_type="platform",
            base_url=parsed.get("base_url", url),
            auth_method="none", auth_config={}, endpoints=[],
            pricing_model=parsed.get("pricing_model", {}),
            rate_limits={}, capabilities=parsed.get("capabilities", []),
            raw_analysis=raw,
        )

    async def plan(self, analysis):
        tests = [
            {"name": "website_uptime", "endpoint": "/", "method": "GET"},
            {"name": "robots_txt", "endpoint": "/robots.txt", "method": "GET"},
            {"name": "llms_txt", "endpoint": "/llms.txt", "method": "GET"},
        ]
        return BenchmarkPlan(
            service_id=analysis.service_id, tests=tests,
            pricing_probes=[], stress_profile={"concurrent_requests": 1, "duration_seconds": 5},
        )

    async def execute(self, plan, analysis, raw_credential):
        cfg = get_config()
        results: list[BenchmarkExecutionResult] = []
        base = analysis.base_url.rstrip("/")

        async with httpx.AsyncClient(
            timeout=30.0, headers={"User-Agent": cfg.user_agent}, follow_redirects=True,
        ) as client:
            for test in plan.tests:
                url = f"{base}{test['endpoint']}"
                start = time.monotonic()
                try:
                    validate_url(url)
                    resp = await client.get(url)
                    latency_ms = int((time.monotonic() - start) * 1000)
                    try:
                        body = resp.json()
                    except Exception:
                        body = resp.text[:2000]
                    results.append(BenchmarkExecutionResult(
                        test_name=test["name"], endpoint=test["endpoint"], method="GET",
                        request_payload=None, status_code=resp.status_code,
                        response_body=body, response_headers=dict(resp.headers), latency_ms=latency_ms,
                    ))
                except Exception as exc:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    results.append(BenchmarkExecutionResult(
                        test_name=test["name"], endpoint=test["endpoint"], method="GET",
                        request_payload=None, status_code=None, response_body=None,
                        response_headers={}, latency_ms=latency_ms, error=str(exc),
                    ))
        return results

    async def interpret(self, analysis, results):
        results_data = [{
            "test_name": r.test_name, "status_code": r.status_code,
            "latency_ms": r.latency_ms, "error": r.error,
            "has_security_headers": any(h in r.response_headers for h in
                ("strict-transport-security", "x-content-type-options", "x-frame-options")),
            "response_preview": str(r.response_body)[:300] if r.response_body else None,
        } for r in results]

        user_msg = (
            f"## Platform Analysis\n```json\n{json.dumps({'capabilities': analysis.capabilities, 'pricing_model': analysis.pricing_model}, default=str)}\n```\n\n"
            f"## Check Results ({len(results)} checks)\n```json\n{json.dumps(results_data, default=str)}\n```"
        )
        raw = await call_llm(PLATFORM_INTERPRET_SYSTEM, user_msg, max_tokens=2048)
        parsed = extract_json(raw)
        return NormalizedScore(
            service_id=analysis.service_id, overall=parsed.get("overall", 0),
            dimensions=parsed.get("dimensions", {}),
            pricing_normalized=parsed.get("pricing_normalized", {}),
            issues=parsed.get("issues", []), recommendations=parsed.get("recommendations", []),
            raw_interpretation=raw,
        )
