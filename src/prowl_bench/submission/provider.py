"""Prowl Provider Network client — register, discover, benchmark, earn."""
from __future__ import annotations

import logging

import httpx

from prowl_bench.config import get_config
from prowl_bench.core.types import BenchmarkReport

log = logging.getLogger("prowl_bench.provider")


def _headers() -> dict:
    cfg = get_config()
    if not cfg.prowl_agent_key:
        raise RuntimeError("PROWL_AGENT_KEY not set. Run: prowl-bench register")
    return {"X-Agent-Key": cfg.prowl_agent_key}


async def register_provider(wallet_address: str, wallet_type: str, templates: list[str] | None = None) -> dict:
    """Register as a benchmark provider on the Prowl network.

    Idempotent — re-registering returns existing profile.
    """
    cfg = get_config()
    payload = {
        "wallet_address": wallet_address,
        "wallet_type": wallet_type,
        "capabilities": {
            "max_concurrent": 5,
            "supported_templates": templates or [
                "api_benchmark", "platform_profile", "mcp_compliance",
                "docs_quality", "defi_yield", "crypto_app",
            ],
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/provider/register",
            headers=_headers(),
            json=payload,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        else:
            raise RuntimeError(f"Provider registration failed: {resp.status_code} {resp.text[:200]}")


async def get_dashboard() -> dict:
    """Get provider dashboard — earnings, benchmarks, contributions."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{cfg.prowl_base_url}/v1/provider/dashboard",
            headers=_headers(),
        )
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code in (403, 404):
            raise RuntimeError("Not registered as a provider. Run: prowl-bench provide register-provider")
        else:
            raise RuntimeError(f"Dashboard failed: {resp.status_code} {resp.text[:200]}")


async def get_directives(status: str = "open", limit: int = 50) -> list[dict]:
    """List benchmark directives (work orders)."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{cfg.prowl_base_url}/v1/provider/directives",
            headers=_headers(),
            params={"status": status, "limit": limit},
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            raise RuntimeError(f"Directives failed: {resp.status_code} {resp.text[:200]}")


async def claim_directive(directive_id: str) -> dict:
    """Claim a benchmark directive."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/provider/directives/{directive_id}/claim",
            headers=_headers(),
        )
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            raise RuntimeError("Max concurrent directives reached. Complete or release existing claims first.")
        else:
            raise RuntimeError(f"Claim failed: {resp.status_code} {resp.text[:200]}")


async def release_directive(directive_id: str) -> dict:
    """Release a claimed directive so other providers can pick it up."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/provider/directives/{directive_id}/release",
            headers=_headers(),
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            raise RuntimeError(f"Release failed: {resp.status_code} {resp.text[:200]}")


def _report_to_results(report: BenchmarkReport) -> dict:
    """Convert a BenchmarkReport to the Prowl submission results format."""
    return {
        "overall_score": report.overall_score,
        "dimensions": report.dimensions,
        "issues": [
            {"severity": "medium", "detail": i} if isinstance(i, str) else i
            for i in (report.issues or [])
        ],
        "recommendations": report.recommendations or [],
        "evidence": {
            "http_calls": [
                {
                    "endpoint": r.endpoint,
                    "status": r.status_code,
                    "latency_ms": r.latency_ms,
                }
                for r in report.execution_results
                if r.status_code is not None
            ],
            "total_tests": len(report.execution_results),
            "passed": sum(
                1 for r in report.execution_results
                if r.status_code and 200 <= r.status_code < 300
            ),
        },
    }


async def submit_directive(directive_id: str, report: BenchmarkReport) -> dict:
    """Submit benchmark results for a claimed directive."""
    cfg = get_config()
    payload = {
        "directive_id": directive_id,
        "results": _report_to_results(report),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/provider/submit",
            headers=_headers(),
            json=payload,
        )
        if resp.status_code == 201:
            return resp.json()
        else:
            raise RuntimeError(f"Submit failed: {resp.status_code} {resp.text[:200]}")


async def submit_benchmark(service_id: str, report: BenchmarkReport) -> dict:
    """Submit a proactive benchmark as a provider (no directive needed)."""
    cfg = get_config()
    payload = {
        "service_id": service_id,
        "results": _report_to_results(report),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/provider/benchmark",
            headers=_headers(),
            json=payload,
        )
        if resp.status_code == 201:
            return resp.json()
        else:
            raise RuntimeError(f"Submission failed: {resp.status_code} {resp.text[:200]}")


async def get_earnings() -> dict:
    """Get earnings breakdown."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{cfg.prowl_base_url}/v1/provider/earnings",
            headers=_headers(),
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            raise RuntimeError(f"Earnings failed: {resp.status_code} {resp.text[:200]}")


async def withdraw(amount_usd: float) -> dict:
    """Request a withdrawal."""
    cfg = get_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/provider/withdraw",
            headers=_headers(),
            json={"amount_usd": amount_usd},
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            raise RuntimeError(f"Withdraw failed: {resp.status_code} {resp.text[:200]}")
