"""Tests for template detection and registry."""
from prowl_bench.templates import (
    get_template, get_all_templates, detect_template_from_metadata,
    TEMPLATE_REGISTRY,
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
