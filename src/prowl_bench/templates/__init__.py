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


def looks_like_mcp(url: str, spec_content: str | None = None) -> bool:
    """Cheap, offline guess at whether a target is an MCP server.

    Detection runs before any network call, so this reads the URL and whatever
    spec text we already have — never a probe. It is deliberately narrow: a
    false positive costs a wasted handshake, but a false *negative* is how the
    `mcp_compliance` template ended up unreachable (`has_mcp` was hardcoded to
    False, so `prowl-bench run <mcp-url>` scored MCP servers as web platforms).

    `mcp` must appear as a path/host segment, not merely as a substring — the
    literal check would match `.../simcpanel` and, worse, any docs page that
    happens to spell it.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url if "://" in url else f"https://{url}")
    host_labels = (parsed.hostname or "").split(".")
    path_segments = [s for s in parsed.path.split("/") if s]

    if "mcp" in host_labels:
        return True
    for segment in path_segments:
        # `mcp`, `mcp-commercial`, `mcp_v2` — a segment that starts with the
        # token. `simcpanel` does not.
        if segment.lower() == "mcp" or segment.lower().startswith(("mcp-", "mcp_", "mcp.")):
            return True

    if spec_content:
        head = spec_content[:4000].lower()
        if "modelcontextprotocol" in head:
            return True
        # A manifest naming its own transport, rather than prose mentioning MCP.
        if '"tools/list"' in head or "streamable-http" in head:
            return True
    return False


def detect_template_from_metadata(
    categories: list[str],
    has_openapi: bool = False,
    has_mcp: bool = False,
) -> str:
    """Auto-detect template from service metadata (no DB dependency)."""
    cats = set(c.lower() for c in categories)

    if "mcp" in cats:
        has_mcp = True

    if has_mcp:
        return "mcp_compliance"
    if cats & {"defi", "staking", "yield", "lending", "liquidity"}:
        return "defi_yield"
    if cats & {"crypto", "exchange", "wallet", "bridge", "trading"}:
        return "crypto_app"
    if not has_openapi:
        return "platform_profile"
    return "api_benchmark"
