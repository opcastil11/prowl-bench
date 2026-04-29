"""Prowl submission client — posts benchmark results to the Prowl API.

Three submission paths:
  1. submit_to_prowl()    — anonymous community submission, agent key,
                            does NOT change the official score
  2. submit_as_vendor()    — verified service owner, vendor JWT,
                            CAN change the official score (with displacement guard)
  3. provider.* (separate) — provider-network land-grab and directive flow
"""
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


async def submit_as_vendor(report: BenchmarkReport) -> dict:
    """Submit benchmark results as the verified service owner.

    Hits POST /v1/benchmark/submit, which:
      - requires a vendor JWT (PROWL_VENDOR_JWT)
      - requires the vendor to own the service (claimed + DNS verified)
      - CAN change the service's primary score (subject to displacement guard:
        only displaces a Prowl/provider score if multi-LLM, higher, or stale)

    The benchmarked URL must already be a registered + claimed service.
    """
    cfg = get_config()
    if not cfg.prowl_vendor_jwt:
        raise RuntimeError(
            "PROWL_VENDOR_JWT not set. Log in at https://prowl.world/app#/login "
            "and copy the token from localStorage (key: prowl_jwt). Then run "
            "`export PROWL_VENDOR_JWT=eyJ...` and retry --vendor-submit."
        )

    payload = {
        "service_id": "",  # filled in below after lookup
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
        "raw_interpretation": report.raw_interpretation[:5000] if report.raw_interpretation else None,
        "llm_providers_used": report.llm_providers_used,
        "runner_version": report.runner_version,
        "started_at": report.started_at,
        "completed_at": report.completed_at,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Look up the service by name, then by URL host as a fallback.
        service_id = await _resolve_service_id(client, cfg.prowl_base_url, report.name, report.url)
        if not service_id:
            raise RuntimeError(
                f"Service '{report.name}' ({report.url}) was not found in the Prowl "
                "catalog. Vendor self-submissions require a registered + claimed + "
                "DNS-verified service. Register it first at https://prowl.world."
            )
        payload["service_id"] = service_id

        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/benchmark/submit",
            headers={
                "Authorization": f"Bearer {cfg.prowl_vendor_jwt}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            log.info(
                "Vendor benchmark submitted: %s (score: %s, source: %s)",
                data.get("submission_id"), data.get("current_score"), data.get("benchmark_source"),
            )
            return data
        elif resp.status_code == 401:
            raise RuntimeError("Invalid or expired PROWL_VENDOR_JWT. Re-login at https://prowl.world.")
        elif resp.status_code == 403:
            raise RuntimeError(
                f"Permission denied: {resp.text[:300]}. The service must be claimed "
                "by you AND have DNS verification completed."
            )
        elif resp.status_code == 422:
            raise RuntimeError(f"Submission rejected (validation): {resp.text[:300]}")
        else:
            log.error("Vendor submission failed: %s %s", resp.status_code, resp.text[:500])
            raise RuntimeError(f"Vendor submission failed: {resp.status_code} {resp.text[:200]}")
