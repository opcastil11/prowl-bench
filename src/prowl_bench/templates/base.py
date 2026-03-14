"""Base benchmark template — decoupled from Prowl's database."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from prowl_bench.core.types import (
    ServiceAnalysis, BenchmarkPlan, BenchmarkExecutionResult, NormalizedScore,
)


@dataclass
class TemplateConfig:
    slug: str
    name: str
    description: str
    requires_credentials: bool
    category_hints: list[str]


class BaseBenchmarkTemplate(ABC):
    config: TemplateConfig

    @abstractmethod
    async def analyze(self, url: str, name: str, spec_content: str, docs_content: str | None) -> ServiceAnalysis:
        ...

    @abstractmethod
    async def plan(self, analysis: ServiceAnalysis) -> BenchmarkPlan:
        ...

    @abstractmethod
    async def execute(self, plan: BenchmarkPlan, analysis: ServiceAnalysis, raw_credential: str | None) -> list[BenchmarkExecutionResult]:
        ...

    @abstractmethod
    async def interpret(self, analysis: ServiceAnalysis, results: list[BenchmarkExecutionResult]) -> NormalizedScore:
        ...

    async def run(
        self,
        url: str,
        name: str,
        spec_content: str,
        credential: str | None = None,
        docs_content: str | None = None,
    ) -> NormalizedScore:
        """Full pipeline: ANALYZE -> PLAN -> EXECUTE -> INTERPRET."""
        analysis = await self.analyze(url, name, spec_content, docs_content)
        analysis.service_id = ""
        if not analysis.base_url:
            analysis.base_url = url

        plan = await self.plan(analysis)
        exec_results = await self.execute(plan, analysis, credential)
        score = await self.interpret(analysis, exec_results)
        return score
