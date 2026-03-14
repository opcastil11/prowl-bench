"""Benchmark templates — different strategies for different service types."""
from __future__ import annotations

from prowl_bench.templates.base import BaseBenchmarkTemplate, TemplateConfig
from prowl_bench.templates.api_benchmark import ApiBenchmarkTemplate
from prowl_bench.templates.platform_profile import PlatformProfileTemplate
from prowl_bench.templates.mcp_compliance import McpComplianceTemplate
from prowl_bench.templates.docs_quality import DocsQualityTemplate
from prowl_bench.templates.defi_yield import DefiYieldTemplate
from prowl_bench.templates.crypto_app import CryptoAppTemplate

TEMPLATE_REGISTRY: dict[str, BaseBenchmarkTemplate] = {}


def _register(template: BaseBenchmarkTemplate):
    TEMPLATE_REGISTRY[template.config.slug] = template


_register(ApiBenchmarkTemplate())
_register(PlatformProfileTemplate())
_register(McpComplianceTemplate())
_register(DocsQualityTemplate())
_register(DefiYieldTemplate())
_register(CryptoAppTemplate())


def get_template(slug: str) -> BaseBenchmarkTemplate:
    if slug not in TEMPLATE_REGISTRY:
        raise ValueError(f"Unknown template: {slug}. Available: {list(TEMPLATE_REGISTRY.keys())}")
    return TEMPLATE_REGISTRY[slug]


def get_all_templates() -> list[TemplateConfig]:
    return [t.config for t in TEMPLATE_REGISTRY.values()]


def detect_template_from_metadata(
    categories: list[str],
    has_openapi: bool = False,
    has_mcp: bool = False,
) -> str:
    """Auto-detect template from service metadata (no DB dependency)."""
    cats = set(c.lower() for c in categories)

    if has_mcp:
        return "mcp_compliance"
    if cats & {"defi", "staking", "yield", "lending", "liquidity"}:
        return "defi_yield"
    if cats & {"crypto", "exchange", "wallet", "bridge", "trading"}:
        return "crypto_app"
    if not has_openapi:
        return "platform_profile"
    return "api_benchmark"
