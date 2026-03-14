"""Prompt injection protection — sanitize user inputs before LLM calls."""
from __future__ import annotations

import logging
import re

log = logging.getLogger("prowl_bench.sandbox")

INJECTION_PATTERNS = [
    re.compile(r"<\|?(system|assistant|user|im_start|im_end)\|?>", re.I),
    re.compile(r"\[INST\]|\[/INST\]|\[\[SYSTEM\]\]", re.I),
    re.compile(r"###\s*(system|instruction|human|assistant)\s*:", re.I),
    re.compile(r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|above|prior)\s+(?:instructions?|prompts?|context)", re.I),
    re.compile(r"(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you\s+are)|new\s+instructions?)\b", re.I),
    re.compile(r"(?:do\s+not\s+follow|override|bypass)\s+(?:the\s+)?(?:system|original|above)\s+(?:prompt|instructions?)", re.I),
    re.compile(r"</?(?:script|iframe|object|embed|form)\b", re.I),
]

MAX_INPUT_LENGTH = 5000


def sanitize_prompt_input(text: str) -> str:
    """Strip prompt injection patterns from user-supplied text."""
    if not text:
        return text

    cleaned = text
    for pattern in INJECTION_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            log.warning("Prompt injection pattern stripped: %s", match.group())
            cleaned = pattern.sub("[BLOCKED]", cleaned)

    if len(cleaned) > MAX_INPUT_LENGTH:
        log.warning("Input truncated from %d to %d chars", len(cleaned), MAX_INPUT_LENGTH)
        cleaned = cleaned[:MAX_INPUT_LENGTH]

    return cleaned
