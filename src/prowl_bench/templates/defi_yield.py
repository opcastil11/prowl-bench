"""DeFi Yield template — yield accuracy, staking APY, TVL verification."""
from __future__ import annotations

import json

import httpx

from prowl_bench.templates.base import BaseBenchmarkTemplate, TemplateConfig
from prowl_bench.core.types import ServiceAnalysis, BenchmarkPlan, BenchmarkExecutionResult, NormalizedScore
from prowl_bench.core.json_utils import extract_json
from prowl_bench.core.pipeline import execute_benchmark, plan_benchmark
from prowl_bench.llm.router import call_llm
from prowl_bench.llm.prompts import DEFI_ANALYZE_SYSTEM, DEFI_INTERPRET_SYSTEM, PLAN_SYSTEM


class DefiYieldTemplate(BaseBenchmarkTemplate):
    config = TemplateConfig(
        slug="defi_yield", name="DeFi Yield Benchmark",
        description="Benchmarks DeFi protocols: yield accuracy, staking APY, TVL verification.",
        requires_credentials=True,
        category_hints=["defi", "staking", "yield", "lending", "liquidity"],
    )

    async def analyze(self, url, name, spec_content, docs_content):
        user_msg = f"DeFi Service: {name}\nURL: {url}\n\n"
        user_msg += f"## API Specification\n```\n{spec_content[:15000]}\n```\n"
        if docs_content:
            user_msg += f"\n## Additional Docs\n```\n{docs_content[:10000]}\n```\n"

        raw = await call_llm(DEFI_ANALYZE_SYSTEM, user_msg, max_tokens=4096)
        parsed = extract_json(raw)
        return ServiceAnalysis(
            service_id="", service_type="defi_protocol",
            base_url=parsed.get("base_url", url),
            auth_method=parsed.get("auth_method", "none"), auth_config=parsed.get("auth_config", {}),
            endpoints=parsed.get("endpoints", []), pricing_model=parsed.get("pricing_model", {}),
            rate_limits=parsed.get("rate_limits", {}), capabilities=parsed.get("capabilities", []),
            raw_analysis=raw,
        )

    async def plan(self, analysis):
        user_msg = f"## DeFi Service Analysis\n```json\n{json.dumps(analysis.__dict__, default=str)}\n```"
        plan_prompt = PLAN_SYSTEM + "\n\nDeFi-specific: query yield/APY endpoints, check TVL freshness, test price feeds, verify chain support."
        raw = await call_llm(plan_prompt, user_msg, max_tokens=4096)
        parsed = extract_json(raw)
        return BenchmarkPlan(
            service_id=analysis.service_id, tests=parsed.get("tests", []),
            pricing_probes=parsed.get("pricing_probes", []),
            stress_profile=parsed.get("stress_profile", {"concurrent_requests": 3, "duration_seconds": 10}),
        )

    async def execute(self, plan, analysis, raw_credential):
        results = await execute_benchmark(plan, analysis, raw_credential)

        # Cross-reference TVL from DeFiLlama
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get("https://api.llama.fi/protocols")
                if resp.status_code == 200:
                    protocols = resp.json()
                    name_lower = analysis.raw_analysis[:200].lower()
                    for p in protocols[:500]:
                        if p.get("name", "").lower() in name_lower:
                            results.append(BenchmarkExecutionResult(
                                test_name="defillama_tvl_crossref",
                                endpoint="https://api.llama.fi/protocols", method="GET",
                                request_payload=None, status_code=200,
                                response_body={"protocol": p.get("name"), "tvl": p.get("tvl")},
                                response_headers={}, latency_ms=0,
                            ))
                            break
        except Exception:
            pass
        return results

    async def interpret(self, analysis, results):
        results_data = [{
            "test_name": r.test_name, "endpoint": r.endpoint,
            "status_code": r.status_code, "latency_ms": r.latency_ms, "error": r.error,
            "response_preview": str(r.response_body)[:500] if r.response_body else None,
        } for r in results]
        user_msg = (
            f"## DeFi Analysis\n```json\n{json.dumps({'service_type': analysis.service_type, 'capabilities': analysis.capabilities, 'endpoints': analysis.endpoints[:10]}, default=str)}\n```\n\n"
            f"## Benchmark Results ({len(results)} tests)\n```json\n{json.dumps(results_data, default=str)}\n```"
        )
        raw = await call_llm(DEFI_INTERPRET_SYSTEM, user_msg, max_tokens=4096)
        parsed = extract_json(raw)
        return NormalizedScore(
            service_id=analysis.service_id, overall=parsed.get("overall", 0),
            dimensions=parsed.get("dimensions", {}),
            pricing_normalized=parsed.get("pricing_normalized", {}),
            issues=parsed.get("issues", []), recommendations=parsed.get("recommendations", []),
            raw_interpretation=raw,
        )
