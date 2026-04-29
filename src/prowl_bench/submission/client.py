"""Prowl submission client — posts benchmark results to the Prowl API."""
from __future__ import annotations

import logging

import httpx

from prowl_bench.config import get_config
from prowl_bench.core.types import BenchmarkReport

log = logging.getLogger("prowl_bench.submission")


async def _resolve_service_id(client: httpx.AsyncClient, base_url: str, name: str, url: str) -> str | None:
    """Find the Prowl service_id for a benchmarked target.

    Tries discovery by name first, then by URL host. Returns None if no
    matching service exists in the catalog.
    """
    resp = await client.get(
        f"{base_url}/v1/discover",
        params={"q": name, "limit": 5},
    )
    if resp.status_code == 200:
        for r in resp.json().get("results", []) or []:
            if r.get("name", "").lower() == name.lower():
                return r["id"]
            if url and r.get("website_url", "").rstrip("/") == url.rstrip("/"):
                return r["id"]
        # Fall back to first match if any
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]
    return None


def _execution_to_test_results(report: BenchmarkReport) -> list[dict]:
    """Map BenchmarkExecutionResult entries into the protocol's TestResultEntry shape."""
    out: list[dict] = []
    for r in report.execution_results:
        out.append({
            "test_case_id": r.test_name or r.endpoint or "unnamed",
            "passed": bool(r.status_code and 200 <= r.status_code < 300),
            "actual_status": r.status_code,
            "latency_ms": r.latency_ms,
            "error": r.error,
        })
    return out


def _build_notes(report: BenchmarkReport) -> str:
    """Pack supplementary metadata that the protocol schema doesn't have first-class fields for."""
    lines = [f"runner: {report.runner_version}"]
    if report.llm_providers_used:
        lines.append(f"llms: {', '.join(report.llm_providers_used)}")
    if report.started_at and report.completed_at:
        lines.append(f"window: {report.started_at} → {report.completed_at}")
    if report.issues:
        lines.append(f"issues ({len(report.issues)}):")
        for i in report.issues[:10]:
            if isinstance(i, dict):
                lines.append(f"  [{i.get('severity', '?')}] {i.get('detail', '')}")
            else:
                lines.append(f"  - {i}")
    if report.recommendations:
        lines.append("recommendations:")
        for r in report.recommendations[:10]:
            lines.append(f"  - {r}")
    return "\n".join(lines)


async def submit_to_prowl(report: BenchmarkReport) -> dict:
    """Submit benchmark results to the Prowl Open Benchmark Protocol.

    Requires PROWL_AGENT_KEY to be set. The target service must already
    exist in the Prowl catalog (register at prowl.world first if not).
    """
    cfg = get_config()
    if not cfg.prowl_agent_key:
        raise RuntimeError("PROWL_AGENT_KEY not set. Register at: prowl-bench register")

    elapsed_ms: int | None = None
    try:
        from datetime import datetime
        if report.started_at and report.completed_at:
            t0 = datetime.fromisoformat(report.started_at.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(report.completed_at.replace("Z", "+00:00"))
            elapsed_ms = max(0, int((t1 - t0).total_seconds() * 1000))
    except Exception:
        elapsed_ms = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        service_id = await _resolve_service_id(client, cfg.prowl_base_url, report.name, report.url)
        if not service_id:
            raise RuntimeError(
                f"Service '{report.name}' ({report.url}) was not found in the Prowl "
                "catalog. Open-protocol submissions need a registered service. "
                "Register it at https://prowl.world or use --provide to land-grab."
            )

        payload = {
            "service_id": service_id,
            "dimensions": report.dimensions,
            "overall_score": report.overall_score,
            "test_results": _execution_to_test_results(report),
            "runner_id": f"prowl-bench/{report.runner_version}",
            "execution_time_ms": elapsed_ms,
            "notes": _build_notes(report),
        }

        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/benchmark/protocol/submit",
            headers={
                "X-Agent-Key": cfg.prowl_agent_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            log.info(
                "Submitted to Prowl: %s (accepted: %s, recorded_score: %s)",
                data.get("submission_id"), data.get("accepted"), data.get("recorded_score"),
            )
            return data
        elif resp.status_code == 401:
            raise RuntimeError("Invalid or expired PROWL_AGENT_KEY. Re-run: prowl-bench register")
        elif resp.status_code == 404:
            raise RuntimeError(f"Service {service_id} no longer exists in the Prowl catalog.")
        elif resp.status_code == 422:
            raise RuntimeError(f"Submission rejected by Prowl (validation): {resp.text[:300]}")
        else:
            log.error("Submission failed: %s %s", resp.status_code, resp.text[:500])
            raise RuntimeError(f"Prowl submission failed: {resp.status_code} {resp.text[:200]}")
