"""Tests for template detection and registry."""
import pytest

from prowl_bench.templates import (
    get_template, get_all_templates, detect_template_from_metadata,
    looks_like_mcp, TEMPLATE_REGISTRY,
)


def test_all_templates_registered():
    assert len(TEMPLATE_REGISTRY) == 6
    assert "api_benchmark" in TEMPLATE_REGISTRY
    assert "platform_profile" in TEMPLATE_REGISTRY
    assert "mcp_compliance" in TEMPLATE_REGISTRY
    assert "docs_quality" in TEMPLATE_REGISTRY
    assert "defi_yield" in TEMPLATE_REGISTRY
    assert "crypto_app" in TEMPLATE_REGISTRY


def test_get_template():
    t = get_template("api_benchmark")
    assert t.config.slug == "api_benchmark"


def test_get_all_templates():
    templates = get_all_templates()
    assert len(templates) == 6


def test_detect_defi():
    slug = detect_template_from_metadata(categories=["defi", "yield"])
    assert slug == "defi_yield"


def test_detect_crypto():
    slug = detect_template_from_metadata(categories=["exchange", "trading"])
    assert slug == "crypto_app"


def test_detect_mcp():
    slug = detect_template_from_metadata(categories=[], has_mcp=True)
    assert slug == "mcp_compliance"


def test_detect_platform_no_api():
    slug = detect_template_from_metadata(categories=["productivity"], has_openapi=False)
    assert slug == "platform_profile"


def test_detect_api_default():
    slug = detect_template_from_metadata(categories=["payments"], has_openapi=True)
    assert slug == "api_benchmark"


def test_mcp_category_selects_the_mcp_template():
    """`has_mcp` was hardcoded False by the only caller, so the branch above was
    dead. A category of "mcp" is the cheapest way to reach it."""
    assert detect_template_from_metadata(categories=["MCP"]) == "mcp_compliance"


class TestLooksLikeMcp:
    @pytest.mark.parametrize("url", [
        "https://platform.digitalpublic.com/api/mcp-commercial",
        "https://example.com/mcp",
        "https://mcp.aarna.ai/",
        "https://example.com/api/mcp/v1",
        "https://example.com/mcp_v2",
        "example.com/mcp",
    ])
    def test_positive(self, url):
        assert looks_like_mcp(url) is True

    @pytest.mark.parametrize("url", [
        "https://example.com",
        "https://example.com/api/v1/users",
        # The substring trap: a literal `"mcp" in url` check matches these.
        "https://example.com/simcpanel",
        "https://stripe.com/docs/mcpayments",
    ])
    def test_negative(self, url):
        assert looks_like_mcp(url) is False

    def test_spec_naming_the_protocol_counts(self):
        assert looks_like_mcp("https://example.com",
                              '{"$schema":"https://modelcontextprotocol.io/x"}') is True

    def test_spec_naming_a_transport_counts(self):
        assert looks_like_mcp("https://example.com",
                              '{"remotes":[{"type":"streamable-http"}]}') is True

    def test_an_openapi_spec_does_not(self):
        assert looks_like_mcp("https://example.com", '{"openapi":"3.0.0"}') is False
