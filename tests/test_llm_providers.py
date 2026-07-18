"""Tests for the multi-LLM provider router — DeepSeek support."""
import pytest

from prowl_bench import config as config_mod
from prowl_bench.config import BenchConfig
from prowl_bench.llm import router
from prowl_bench.llm.providers import LLM_PROVIDERS


@pytest.fixture
def set_config(monkeypatch):
    """Swap the cached global config for a controlled one."""
    def _apply(**kwargs):
        # Explicit empty defaults so ambient env keys don't leak into the test.
        base = {
            "anthropic_api_key": "",
            "openai_api_key": "",
            "google_api_key": "",
            "deepseek_api_key": "",
        }
        base.update(kwargs)
        cfg = BenchConfig(**base)
        monkeypatch.setattr(config_mod, "_config", cfg)
        return cfg
    return _apply


def test_deepseek_in_provider_registry():
    assert "deepseek" in LLM_PROVIDERS
    assert LLM_PROVIDERS["deepseek"]["model"] == "deepseek-chat"


def test_available_providers_includes_deepseek(set_config):
    set_config(deepseek_api_key="sk-deepseek")
    assert router.get_available_providers() == ["deepseek"]


def test_available_providers_omits_deepseek_when_unset(set_config):
    set_config(anthropic_api_key="sk-ant")
    assert "deepseek" not in router.get_available_providers()


def test_no_keys_falls_back_to_cli(set_config):
    set_config()
    assert router.get_available_providers() == ["claude_cli"]


async def test_call_llm_dispatches_deepseek(set_config, monkeypatch):
    set_config(deepseek_api_key="sk-deepseek")

    captured = {}
    async def fake_deepseek(system, user, max_tokens=4096):
        captured["hit"] = True
        return "scored-by-deepseek"
    monkeypatch.setattr(router, "call_deepseek_api", fake_deepseek)

    out = await router.call_llm("sys", "user", provider="deepseek")
    assert captured.get("hit") is True
    assert out == "scored-by-deepseek"
