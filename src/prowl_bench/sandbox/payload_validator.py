"""Payload and header validation for benchmark execution."""
from __future__ import annotations

import json
import logging

from prowl_bench.sandbox.url_validator import SandboxViolation

log = logging.getLogger("prowl_bench.sandbox")

MAX_PAYLOAD_SIZE_BYTES = 10_000


def validate_payload(payload: dict | None) -> dict | None:
    """Validate request payload size."""
    if payload is None:
        return None
    serialized = json.dumps(payload)
    if len(serialized) > MAX_PAYLOAD_SIZE_BYTES:
        raise SandboxViolation(f"Payload too large: {len(serialized)} bytes (max {MAX_PAYLOAD_SIZE_BYTES})")
    return payload


def validate_headers(headers: dict, raw_credential: str | None = None) -> dict:
    """Validate outbound headers."""
    sanitized = {}
    for key, value in headers.items():
        key_lower = key.lower()
        if key_lower in ("content-type", "accept", "user-agent"):
            sanitized[key] = value
        elif key_lower in ("authorization", "x-api-key"):
            sanitized[key] = value
        elif key_lower in ("host", "x-forwarded-for", "x-real-ip"):
            log.warning("Stripped dangerous header: %s", key)
        else:
            sanitized[key] = value
    return sanitized
