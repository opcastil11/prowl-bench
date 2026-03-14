"""JSON extraction from LLM responses — handles code fences, trailing commas, etc."""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("prowl_bench.json")


def extract_json(text: str) -> dict | list:
    """Extract JSON from an LLM response, handling markdown code fences and minor issues."""
    # Try to find JSON in code fences first
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    candidates = []
    if match:
        candidates.append(match.group(1))

    # Find the first { or [
    for i, ch in enumerate(text):
        if ch in ("{", "["):
            depth = 0
            for j in range(i, len(text)):
                if text[j] == ch:
                    depth += 1
                elif text[j] == ("}" if ch == "{" else "]"):
                    depth -= 1
                if depth == 0:
                    candidates.append(text[i : j + 1])
                    break
            break

    # Try each candidate, with repair on failure
    for raw in candidates:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Repair: strip trailing commas before } or ]
        repaired = re.sub(r",\s*([}\]])", r"\1", raw)
        repaired = repaired.replace("\t", "\\t")
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            continue

    log.warning("JSON extraction failed. Full response (%d chars): %s", len(text), text[:1000])
    raise ValueError(f"No JSON found in response: {text[:200]}")
