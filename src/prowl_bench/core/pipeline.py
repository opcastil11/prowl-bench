"""4-phase benchmark pipeline: ANALYZE -> PLAN -> EXECUTE -> INTERPRET.

Decoupled from Prowl's database — runs entirely locally with user-provided inputs.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx

from prowl_bench.config import get_config
from prowl_bench.core.types import (
    ServiceAnalysis, BenchmarkPlan, BenchmarkExecutionResult, NormalizedScore, BenchmarkReport,
)
from prowl_bench.core.scoring import compute_score
from prowl_bench.core.json_utils import extract_json
from prowl_bench.llm.router import call_llm, get_available_providers
from prowl_bench.llm.prompts import (
    ANALYZE_SYSTEM, PLAN_SYSTEM, INTERPRET_SYSTEM,
)
from prowl_bench.sandbox.url_validator import validate_url, SandboxViolation
from prowl_bench.sandbox.payload_validator import validate_payload, validate_headers
from prowl_bench.sandbox.prompt_sanitizer import sanitize_prompt_input

import logging

log = logging.getLogger("prowl_bench.pipeline")


# ---------------------------------------------------------------------------
# Phase 1: ANALYZE
# ---------------------------------------------------------------------------

async def analyze_service(
    spec_content: str,
    service_name: str,
    docs_content: str | None = None,
    benchmark_guide: dict | None = None,
) -> ServiceAnalysis:
    """Have the LLM analyze an API spec and extract structured data."""
    service_name = sanitize_prompt_input(service_name)
    spec_content = sanitize_prompt_input(spec_content)
    if docs_content:
        docs_content = sanitize_prompt_input(docs_content)

    user_msg = f"Service: {service_name}\n\n"
    if benchmark_guide:
        guide_str = sanitize_prompt_input(json.dumps(benchmark_guide, indent=2)[:5000])
        user_msg += f"## Vendor Benchmark Guide\n```json\n{guide_str}\n```\n\n"
    user_msg += f"## API Specification\n```\n{spec_content[:15000]}\n```\n"
    if docs_content:
        user_msg += f"\n## Additional Documentation\n```\n{docs_content[:10000]}\n```\n"

    raw_response = await call_llm(ANALYZE_SYSTEM, user_msg, max_tokens=4096)
    parsed = extract_json(raw_response)

    return ServiceAnalysis(
        service_id="",
        service_type=parsed.get("service_type", "rest_api"),
        base_url=parsed.get("base_url", ""),
        auth_method=parsed.get("auth_method", "none"),
        auth_config=parsed.get("auth_config", {}),
        endpoints=parsed.get("endpoints", []),
        pricing_model=parsed.get("pricing_model", {}),
        rate_limits=parsed.get("rate_limits", {}),
        capabilities=parsed.get("capabilities", []),
        raw_analysis=raw_response,
    )


# ---------------------------------------------------------------------------
# Phase 2: PLAN
# ---------------------------------------------------------------------------

async def plan_benchmark(analysis: ServiceAnalysis) -> BenchmarkPlan:
    """Have the LLM plan the benchmark based on the analysis."""
    user_msg = f"## Service Analysis\n```json\n{json.dumps(analysis.__dict__, default=str)}\n```"
    raw_response = await call_llm(PLAN_SYSTEM, user_msg, max_tokens=4096)
    parsed = extract_json(raw_response)

    return BenchmarkPlan(
        service_id=analysis.service_id,
        tests=parsed.get("tests", []),
        pricing_probes=parsed.get("pricing_probes", []),
        stress_profile=parsed.get("stress_profile", {"concurrent_requests": 3, "duration_seconds": 10}),
    )


# ---------------------------------------------------------------------------
# Phase 3: EXECUTE
# ---------------------------------------------------------------------------

def _build_auth_headers(analysis: ServiceAnalysis, raw_credential: str) -> dict[str, str]:
    """Build authentication headers from analysis + credential."""
    headers: dict[str, str] = {}
    method = analysis.auth_method
    config = analysis.auth_config

    if method == "bearer_token":
        header = config.get("header", "Authorization")
        prefix = config.get("prefix", "Bearer")
        headers[header] = f"{prefix} {raw_credential}"
    elif method == "api_key_header":
        header = config.get("header", "X-API-Key")
        headers[header] = raw_credential
    return headers


async def execute_benchmark(
    plan: BenchmarkPlan,
    analysis: ServiceAnalysis,
    raw_credential: str | None,
) -> list[BenchmarkExecutionResult]:
    """Execute the benchmark plan against the real API.

    All URLs are validated through the sandbox to prevent SSRF.
    """
    cfg = get_config()
    results: list[BenchmarkExecutionResult] = []

    auth_headers = {}
    if raw_credential:
        auth_headers = _build_auth_headers(analysis, raw_credential)

    base = analysis.base_url.rstrip("/")
    tests = plan.tests[:cfg.max_requests_per_benchmark]
    if len(plan.tests) > cfg.max_requests_per_benchmark:
        log.warning("Capped benchmark tests from %d to %d", len(plan.tests), cfg.max_requests_per_benchmark)

    async with httpx.AsyncClient(timeout=cfg.request_timeout_seconds) as client:
        for test in tests:
            endpoint = test.get("endpoint", "")
            method = test.get("method", "GET").upper()
            url = f"{base}{endpoint}"
            headers = {**auth_headers, **test.get("headers", {})}
            payload = test.get("payload")

            try:
                url = validate_url(url, service_base_url=base)
                payload = validate_payload(payload)
                headers = validate_headers(headers, raw_credential)
            except SandboxViolation as exc:
                log.warning("Sandbox blocked request: %s -- %s", url, exc)
                results.append(BenchmarkExecutionResult(
                    test_name=test.get("name", endpoint), endpoint=endpoint, method=method,
                    request_payload=payload, status_code=None, response_body=None,
                    response_headers={}, latency_ms=0, error=f"SANDBOX_BLOCKED: {exc}",
                ))
                continue

            params = {}
            if analysis.auth_method == "query_param" and raw_credential:
                param_name = analysis.auth_config.get("param", "api_key")
                params[param_name] = raw_credential

            if "accept" not in {k.lower() for k in headers}:
                headers["Accept"] = "application/json"

            start = time.monotonic()
            try:
                if method == "GET":
                    resp = await client.get(url, headers=headers, params=params)
                elif method == "POST":
                    resp = await client.post(url, headers=headers, params=params, json=payload)
                elif method == "PUT":
                    resp = await client.put(url, headers=headers, params=params, json=payload)
                elif method == "DELETE":
                    resp = await client.delete(url, headers=headers, params=params)
                else:
                    resp = await client.request(method, url, headers=headers, params=params, json=payload)

                latency_ms = int((time.monotonic() - start) * 1000)
                content_type = resp.headers.get("content-type", "")
                got_html = "text/html" in content_type

                try:
                    body = resp.json()
                except Exception:
                    body_text = resp.text[:2000]
                    if got_html:
                        body = {"_error": "GOT_HTML_NOT_JSON", "_preview": body_text[:500]}
                    else:
                        body = body_text

                error_note = None
                if got_html:
                    error_note = (
                        f"CONTENT_TYPE_MISMATCH: Expected JSON but got {content_type}. "
                        "The URL may point to a website page instead of an API endpoint."
                    )

                results.append(BenchmarkExecutionResult(
                    test_name=test.get("name", endpoint), endpoint=endpoint, method=method,
                    request_payload=payload, status_code=resp.status_code,
                    response_body=body, response_headers=dict(resp.headers),
                    latency_ms=latency_ms, error=error_note,
                ))

            except Exception as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                results.append(BenchmarkExecutionResult(
                    test_name=test.get("name", endpoint), endpoint=endpoint, method=method,
                    request_payload=payload, status_code=None, response_body=None,
                    response_headers={}, latency_ms=latency_ms, error=str(exc),
                ))

    return results


# ---------------------------------------------------------------------------
# Phase 4: INTERPRET (multi-model)
# ---------------------------------------------------------------------------

async def interpret_results(
    analysis: ServiceAnalysis,
    results: list[BenchmarkExecutionResult],
    system_prompt: str | None = None,
) -> NormalizedScore:
    """Have the LLM interpret raw results into normalized scores."""
    prompt = system_prompt or INTERPRET_SYSTEM

    results_data = []
    for r in results:
        results_data.append({
            "test_name": r.test_name, "endpoint": r.endpoint, "method": r.method,
            "status_code": r.status_code, "latency_ms": r.latency_ms, "error": r.error,
            "response_preview": str(r.response_body)[:500] if r.response_body else None,
            "response_headers": {k: v for k, v in r.response_headers.items()
                                 if k.lower() in ("content-type", "x-ratelimit-remaining",
                                                   "x-ratelimit-limit", "retry-after", "x-request-id")},
        })

    user_msg = (
        f"## Service Analysis\n```json\n{json.dumps({'service_type': analysis.service_type, 'pricing_model': analysis.pricing_model, 'capabilities': analysis.capabilities, 'endpoints': analysis.endpoints[:10]}, default=str)}\n```\n\n"
        f"## Benchmark Results ({len(results)} tests)\n```json\n{json.dumps(results_data, default=str)}\n```"
    )

    raw_response = await call_llm(prompt, user_msg, max_tokens=4096)
    parsed = extract_json(raw_response)

    return NormalizedScore(
        service_id=analysis.service_id,
        overall=parsed.get("overall", 0),
        dimensions=parsed.get("dimensions", {}),
        pricing_normalized=parsed.get("pricing_normalized", {}),
        issues=parsed.get("issues", []),
        recommendations=parsed.get("recommendations", []),
        raw_interpretation=raw_response,
    )


async def interpret_results_multi(
    analysis: ServiceAnalysis,
    results: list[BenchmarkExecutionResult],
    system_prompt: str | None = None,
) -> NormalizedScore:
    """Run INTERPRET across all available LLMs and average scores."""
    import asyncio

    prompt = system_prompt or INTERPRET_SYSTEM
    providers = get_available_providers()
    if len(providers) <= 1:
        return await interpret_results(analysis, results, prompt)

    results_data = []
    for r in results:
        results_data.append({
            "test_name": r.test_name, "endpoint": r.endpoint, "method": r.method,
            "status_code": r.status_code, "latency_ms": r.latency_ms, "error": r.error,
            "response_preview": str(r.response_body)[:500] if r.response_body else None,
            "response_headers": {k: v for k, v in r.response_headers.items()
                                 if k.lower() in ("content-type", "x-ratelimit-remaining",
                                                   "x-ratelimit-limit", "retry-after", "x-request-id")},
        })

    user_msg = (
        f"## Service Analysis\n```json\n{json.dumps({'service_type': analysis.service_type, 'pricing_model': analysis.pricing_model, 'capabilities': analysis.capabilities, 'endpoints': analysis.endpoints[:10]}, default=str)}\n```\n\n"
        f"## Benchmark Results ({len(results)} tests)\n```json\n{json.dumps(results_data, default=str)}\n```"
    )

    async def _interpret_with(provider: str) -> tuple[str, dict | None]:
        try:
            raw = await call_llm(prompt, user_msg, max_tokens=4096, provider=provider)
            parsed = extract_json(raw)
            log.info("multi_interpret: %s returned overall=%s", provider, parsed.get("overall"))
            return provider, parsed
        except Exception as exc:
            log.warning("multi_interpret: %s failed: %s", provider, exc)
            return provider, None

    tasks = [_interpret_with(p) for p in providers]
    results_by_provider = await asyncio.gather(*tasks)
    valid = [(p, d) for p, d in results_by_provider if d is not None]

    if not valid:
        raise RuntimeError("All LLM providers failed during interpret phase")

    if len(valid) == 1:
        provider, parsed = valid[0]
        return NormalizedScore(
            service_id=analysis.service_id, overall=parsed.get("overall", 0),
            dimensions=parsed.get("dimensions", {}),
            pricing_normalized=parsed.get("pricing_normalized", {}),
            issues=parsed.get("issues", []), recommendations=parsed.get("recommendations", []),
            raw_interpretation=json.dumps({"model": provider, "data": parsed}),
        )

    # Average across models
    avg_overall = round(sum(d.get("overall", 0) for _, d in valid) / len(valid))
    all_dim_keys: set[str] = set()
    for _, d in valid:
        all_dim_keys.update((d.get("dimensions") or {}).keys())

    avg_dimensions = {}
    for key in all_dim_keys:
        vals = [d.get("dimensions", {}).get(key) for _, d in valid if d.get("dimensions", {}).get(key) is not None]
        if vals:
            avg_dimensions[key] = round(sum(vals) / len(vals), 1)

    model_scores = {}
    from prowl_bench.llm.providers import LLM_PROVIDERS
    for provider, d in valid:
        label = LLM_PROVIDERS.get(provider, {}).get("label", provider)
        model_scores[label] = {"overall": d.get("overall", 0), "dimensions": d.get("dimensions", {})}

    # Merge issues + recommendations (deduplicate)
    all_issues, seen_issues = [], set()
    for _, d in valid:
        for issue in (d.get("issues") or []):
            detail = issue if isinstance(issue, str) else issue.get("detail", str(issue))
            if detail not in seen_issues:
                seen_issues.add(detail)
                all_issues.append(issue if isinstance(issue, dict) else {"severity": "medium", "detail": issue})

    all_recs, seen_recs = [], set()
    for _, d in valid:
        for rec in (d.get("recommendations") or []):
            if rec not in seen_recs:
                seen_recs.add(rec)
                all_recs.append(rec)

    pricing = {}
    for _, d in valid:
        if d.get("pricing_normalized"):
            pricing = d["pricing_normalized"]
            break

    return NormalizedScore(
        service_id=analysis.service_id, overall=avg_overall,
        dimensions=avg_dimensions, pricing_normalized=pricing,
        issues=all_issues, recommendations=all_recs,
        raw_interpretation=json.dumps({
            "multi_model": True, "models_used": [p for p, _ in valid],
            "model_scores": model_scores, "averaged": True,
        }),
    )


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

async def run_benchmark(
    url: str,
    name: str,
    spec_content: str,
    credential: str | None = None,
    docs_content: str | None = None,
    template_slug: str | None = None,
    categories: list[str] | None = None,
) -> BenchmarkReport:
    """Run the full 4-phase benchmark pipeline and return a BenchmarkReport.

    This is the main entry point for the OSS runner.
    """
    from prowl_bench.templates import detect_template_from_metadata, get_template

    started_at = datetime.now(timezone.utc).isoformat()

    # Auto-detect template
    slug = template_slug or detect_template_from_metadata(
        categories=categories or [],
        has_openapi=bool(spec_content and "openapi" in spec_content.lower()[:500]),
        has_mcp=False,
    )
    template = get_template(slug)

    # Run the template pipeline
    score = await template.run(url, name, spec_content, credential, docs_content)

    completed_at = datetime.now(timezone.utc).isoformat()
    overall, breakdown = compute_score(score.dimensions)

    return BenchmarkReport(
        url=url, name=name, template=slug,
        overall_score=overall, dimensions=score.dimensions, breakdown=breakdown,
        pricing_normalized=score.pricing_normalized,
        issues=score.issues, recommendations=score.recommendations,
        execution_results=[],  # templates can populate this
        analysis=ServiceAnalysis(
            service_id="", service_type="", base_url=url,
            auth_method="", auth_config={}, endpoints=[], pricing_model={},
            rate_limits={}, capabilities=[], raw_analysis="",
        ),
        raw_interpretation=score.raw_interpretation,
        started_at=started_at, completed_at=completed_at,
        runner_version="0.1.0",
        llm_providers_used=get_available_providers(),
    )


async def fetch_spec(url: str) -> str:
    """Fetch an OpenAPI spec or docs page from a URL."""
    cfg = get_config()
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": cfg.user_agent}, follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def fetch_llms_txt(base_url: str) -> str | None:
    """Try to fetch /llms.txt from a service's base URL."""
    url = f"{base_url.rstrip('/')}/llms.txt"
    try:
        return await fetch_spec(url)
    except Exception:
        return None
