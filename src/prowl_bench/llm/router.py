"""LLM router — picks the best available provider and calls it."""
from __future__ import annotations

from prowl_bench.config import get_config
from prowl_bench.llm.providers import (
    call_claude_api, call_openai_api, call_gemini_api, call_claude_cli,
)


def get_available_providers() -> list[str]:
    """Return list of providers that have API keys configured."""
    cfg = get_config()
    available = []
    if cfg.anthropic_api_key:
        available.append("claude")
    if cfg.openai_api_key:
        available.append("openai")
    if cfg.google_api_key:
        available.append("gemini")
    if not available:
        available.append("claude_cli")
    return available


async def call_llm(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 4096,
    provider: str | None = None,
) -> str:
    """Call any configured LLM. Tries providers in order of preference.

    Priority: explicit provider > Claude API > OpenAI > Gemini > Claude CLI.
    """
    cfg = get_config()

    if provider == "gemini" and cfg.google_api_key:
        return await call_gemini_api(system_prompt, user_message, max_tokens)
    if provider == "openai" and cfg.openai_api_key:
        return await call_openai_api(system_prompt, user_message, max_tokens)
    if provider == "claude" or (provider is None and cfg.anthropic_api_key):
        if cfg.anthropic_api_key:
            return await call_claude_api(system_prompt, user_message, max_tokens)
    if provider == "openai" or (provider is None and cfg.openai_api_key):
        return await call_openai_api(system_prompt, user_message, max_tokens)
    if provider == "gemini" or (provider is None and cfg.google_api_key):
        return await call_gemini_api(system_prompt, user_message, max_tokens)

    return await call_claude_cli(system_prompt, user_message)
