"""Prowl submission client — posts benchmark results to the Prowl API."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from prowl_bench.config import get_config
from prowl_bench.core.types import BenchmarkReport

log = logging.getLogger("prowl_bench.submission")


def _sign_payload(agent_key: str, payload: str) -> str:
    """HMAC-SHA256 signature for result integrity."""
    return hmac.new(agent_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


async def submit_to_prowl(report: BenchmarkReport) -> dict:
    """Submit benchmark results to Prowl.

    Requires PROWL_AGENT_KEY to be set.
    Returns the submission response from Prowl.
    """
    cfg = get_config()
    if not cfg.prowl_agent_key:
        raise RuntimeError("PROWL_AGENT_KEY not set. Register at: prowl-bench register")

    payload = {
        "url": report.url,
        "name": report.name,
        "template": report.template,
        "overall_score": report.overall_score,
        "dimensions": report.dimensions,
        "pricing_normalized": report.pricing_normalized,
        "issues": report.issues,
        "recommendations": report.recommendations,
        "execution_results": [
            {
                "test_name": r.test_name, "endpoint": r.endpoint, "method": r.method,
                "status_code": r.status_code, "latency_ms": r.latency_ms,
                "error": r.error,
            }
            for r in report.execution_results
        ],
        "llm_providers_used": report.llm_providers_used,
        "runner_version": report.runner_version,
        "started_at": report.started_at,
        "completed_at": report.completed_at,
    }

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    signature = _sign_payload(cfg.prowl_agent_key, canonical)
    payload["signature"] = signature

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/benchmark/submit",
            headers={
                "X-Agent-Key": cfg.prowl_agent_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if resp.status_code == 200:
            data = resp.json()
            log.info("Submitted to Prowl: %s (trust weight: %s)", data.get("submission_id"), data.get("trust_weight"))
            return data
        else:
            log.error("Submission failed: %s %s", resp.status_code, resp.text[:500])
            raise RuntimeError(f"Prowl submission failed: {resp.status_code} {resp.text[:200]}")
