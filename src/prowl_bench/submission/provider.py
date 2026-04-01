"""Prowl Provider Network client — register, discover, benchmark, earn."""
from __future__ import annotations

import logging

import httpx

from prowl_bench.config import get_config
from prowl_bench.core.types import BenchmarkReport

log = logging.getLogger("prowl_bench.provider")


async def register_provider(wallet_address: str, wallet_type: str, templates: list[str] | None = None) -> dict:
    """Register as a benchmark provider on the Prowl network."""
    cfg = get_config()
    if not cfg.prowl_agent_key:
        raise RuntimeError("PROWL_AGENT_KEY not set. Run: prowl-bench register")

    payload = {
        "wallet_address": wallet_address,
        "wallet_type": wallet_type,
        "capabilities": {
            "max_concurrent": 3,
            "supported_templates": templates or [
                "api_benchmark", "platform_profile", "mcp_compliance",
                "docs_quality", "defi_yield", "crypto_app",
            ],
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/provider/register",
            headers={"X-Agent-Key": cfg.prowl_agent_key},
            json=payload,
        )
        if resp.status_code == 201:
            return resp.json()
        elif resp.status_code == 409:
            raise RuntimeError("Already registered as a provider")
        else:
            raise RuntimeError(f"Provider registration failed: {resp.status_code} {resp.text[:200]}")


async def get_dashboard() -> dict:
    """Get provider dashboard — earnings, benchmarks, contributions."""
    cfg = get_config()
    if not cfg.prowl_agent_key:
        raise RuntimeError("PROWL_AGENT_KEY not set. Run: prowl-bench register")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{cfg.prowl_base_url}/v1/provider/dashboard",
            headers={"X-Agent-Key": cfg.prowl_agent_key},
        )
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 404:
            raise RuntimeError("Not registered as a provider. Run: prowl-bench provide register")
        else:
            raise RuntimeError(f"Dashboard failed: {resp.status_code} {resp.text[:200]}")


async def get_directives() -> list[dict]:
    """List available benchmark directives (work orders)."""
    cfg = get_config()
    if not cfg.prowl_agent_key:
        raise RuntimeError("PROWL_AGENT_KEY not set. Run: prowl-bench register")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{cfg.prowl_base_url}/v1/provider/directives",
            headers={"X-Agent-Key": cfg.prowl_agent_key},
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
            headers={"X-Agent-Key": cfg.prowl_agent_key},
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            raise RuntimeError(f"Claim failed: {resp.status_code} {resp.text[:200]}")


async def submit_benchmark(service_id: str, report: BenchmarkReport) -> dict:
    """Submit a proactive benchmark as a provider."""
    cfg = get_config()
    if not cfg.prowl_agent_key:
        raise RuntimeError("PROWL_AGENT_KEY not set. Run: prowl-bench register")

    payload = {
        "service_id": service_id,
        "results": {
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
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/provider/benchmark",
            headers={
                "X-Agent-Key": cfg.prowl_agent_key,
                "Content-Type": "application/json",
            },
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
            headers={"X-Agent-Key": cfg.prowl_agent_key},
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
            headers={
                "X-Agent-Key": cfg.prowl_agent_key,
                "Content-Type": "application/json",
            },
            json={"amount_usd": amount_usd},
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            raise RuntimeError(f"Withdraw failed: {resp.status_code} {resp.text[:200]}")
