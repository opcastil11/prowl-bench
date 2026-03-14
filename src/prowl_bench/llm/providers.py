"""LLM provider clients — Claude, OpenAI, Gemini, Claude CLI fallback."""
from __future__ import annotations

import httpx

from prowl_bench.config import get_config

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"
OPENAI_MODEL = "gpt-4o"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"

LLM_PROVIDERS = {
    "claude": {"model": CLAUDE_MODEL, "label": "Claude Sonnet 4"},
    "openai": {"model": OPENAI_MODEL, "label": "GPT-4o"},
    "gemini": {"model": GEMINI_MODEL, "label": "Gemini 2.5 Flash"},
}


async def call_claude_cli(system_prompt: str, user_message: str) -> str:
    """Call Claude via the CLI (uses web subscription auth)."""
    import asyncio
    import os
    import shutil

    claude_bin = shutil.which("claude")
    if not claude_bin:
        home_bin = os.path.expanduser("~/.local/bin/claude")
        if os.path.exists(home_bin):
            claude_bin = home_bin
        else:
            raise RuntimeError("Claude CLI not found -- install with: npm install -g @anthropic-ai/claude-code")

    prompt = f"{system_prompt}\n\n{user_message}"
    proc = await asyncio.create_subprocess_exec(
        claude_bin, "--print", "--model", CLAUDE_MODEL, prompt,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
    if proc.returncode != 0:
        raise RuntimeError(f"Claude CLI failed (exit {proc.returncode}): {stderr.decode()[:500]}")
    return stdout.decode()


async def call_claude_api(system_prompt: str, user_message: str, max_tokens: int = 4096) -> str:
    """Call Claude via the REST API."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            CLAUDE_API_URL,
            headers={
                "x-api-key": cfg.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL, "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]


async def call_openai_api(system_prompt: str, user_message: str, max_tokens: int = 4096) -> str:
    """Call OpenAI-compatible API (GPT-4o)."""
    cfg = get_config()
    if not cfg.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {cfg.openai_api_key}", "Content-Type": "application/json"},
            json={
                "model": OPENAI_MODEL, "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def call_gemini_api(system_prompt: str, user_message: str, max_tokens: int = 4096) -> str:
    """Call Google Gemini API."""
    cfg = get_config()
    if not cfg.google_api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{GEMINI_API_URL}/models/{GEMINI_MODEL}:generateContent",
            params={"key": cfg.google_api_key},
            headers={"Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": user_message}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
