"""API Benchmark template — full LLM-orchestrated REST API benchmark."""
from __future__ import annotations

from prowl_bench.templates.base import BaseBenchmarkTemplate, TemplateConfig
from prowl_bench.core.types import ServiceAnalysis, BenchmarkPlan, BenchmarkExecutionResult, NormalizedScore
from prowl_bench.core.pipeline import (
    analyze_service, plan_benchmark, execute_benchmark, interpret_results_multi,
)


class ApiBenchmarkTemplate(BaseBenchmarkTemplate):
    config = TemplateConfig(
        slug="api_benchmark",
        name="API Benchmark",
        description="Full LLM-orchestrated benchmark for REST APIs and LLM providers. "
                    "Calls real endpoints with credentials, measures latency, accuracy, error handling.",
        requires_credentials=True,
        category_hints=["ai", "llm", "api", "search", "email", "payments", "analytics",
                        "data", "cloud", "monitoring", "testing", "translation"],
    )

    async def analyze(self, url, name, spec_content, docs_content):
        return await analyze_service(spec_content, name, docs_content)

    async def plan(self, analysis):
        return await plan_benchmark(analysis)

    async def execute(self, plan, analysis, raw_credential):
        return await execute_benchmark(plan, analysis, raw_credential)

    async def interpret(self, analysis, results):
        return await interpret_results_multi(analysis, results)
