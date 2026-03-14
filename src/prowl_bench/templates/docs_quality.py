"""Docs Quality template — pure documentation assessment."""
from __future__ import annotations

import json
import time

import httpx

from prowl_bench.config import get_config
from prowl_bench.templates.base import BaseBenchmarkTemplate, TemplateConfig
from prowl_bench.core.types import ServiceAnalysis, BenchmarkPlan, BenchmarkExecutionResult, NormalizedScore
from prowl_bench.core.json_utils import extract_json
from prowl_bench.llm.router import call_llm
from prowl_bench.llm.prompts import ANALYZE_SYSTEM, DOCS_INTERPRET_SYSTEM


class DocsQualityTemplate(BaseBenchmarkTemplate):
    config = TemplateConfig(
        slug="docs_quality", name="Documentation Quality",
        description="Assesses API documentation completeness: OpenAPI spec, llms.txt, examples.",
        requires_credentials=False, category_hints=[],
    )

    async def analyze(self, url, name, spec_content, docs_content):
        user_msg = f"Service: {name}\n\n## Documentation\n```\n{spec_content[:15000]}\n```\n"
        if docs_content:
            user_msg += f"\n## Additional Docs\n```\n{docs_content[:10000]}\n```\n"
        raw = await call_llm(ANALYZE_SYSTEM, user_msg, max_tokens=2048)
        parsed = extract_json(raw)
        return ServiceAnalysis(
            service_id="", service_type=parsed.get("service_type", "rest_api"),
            base_url=parsed.get("base_url", url),
            auth_method=parsed.get("auth_method", "none"), auth_config=parsed.get("auth_config", {}),
            endpoints=parsed.get("endpoints", []), pricing_model=parsed.get("pricing_model", {}),
            rate_limits=parsed.get("rate_limits", {}), capabilities=parsed.get("capabilities", []),
            raw_analysis=raw,
        )

    async def plan(self, analysis):
        tests = [
            {"name": "openapi_spec", "endpoint": "/openapi.json", "method": "GET"},
            {"name": "llms_txt", "endpoint": "/llms.txt", "method": "GET"},
            {"name": "docs_page", "endpoint": "/docs", "method": "GET"},
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
                endpoint = test["endpoint"]
                url = endpoint if endpoint.startswith("http") else f"{base}{endpoint}"
                start = time.monotonic()
                try:
                    resp = await client.get(url)
                    latency_ms = int((time.monotonic() - start) * 1000)
                    try:
                        body = resp.json()
                    except Exception:
                        body = resp.text[:2000]
                    results.append(BenchmarkExecutionResult(
                        test_name=test["name"], endpoint=endpoint, method="GET",
                        request_payload=None, status_code=resp.status_code,
                        response_body=body, response_headers=dict(resp.headers), latency_ms=latency_ms,
                    ))
                except Exception as exc:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    results.append(BenchmarkExecutionResult(
                        test_name=test["name"], endpoint=endpoint, method="GET",
                        request_payload=None, status_code=None, response_body=None,
                        response_headers={}, latency_ms=latency_ms, error=str(exc),
                    ))
        return results

    async def interpret(self, analysis, results):
        results_data = [{
            "test_name": r.test_name, "status_code": r.status_code,
            "latency_ms": r.latency_ms, "error": r.error,
            "response_preview": str(r.response_body)[:1000] if r.response_body else None,
        } for r in results]
        user_msg = (
            f"## Service: {analysis.raw_analysis[:2000]}\n\n"
            f"## Doc Checks\n```json\n{json.dumps(results_data, default=str)}\n```"
        )
        raw = await call_llm(DOCS_INTERPRET_SYSTEM, user_msg, max_tokens=2048)
        parsed = extract_json(raw)
        return NormalizedScore(
            service_id=analysis.service_id, overall=parsed.get("overall", 0),
            dimensions=parsed.get("dimensions", {}),
            pricing_normalized=parsed.get("pricing_normalized", {}),
            issues=parsed.get("issues", []), recommendations=parsed.get("recommendations", []),
            raw_interpretation=raw,
        )
