"""Core data structures for the benchmark pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ServiceAnalysis:
    """What the LLM learned from reading the spec."""
    service_id: str
    service_type: str  # "rest_api", "llm_provider", "graphql", "grpc", "mcp_server", "platform", "defi_protocol", "crypto_app"
    base_url: str
    auth_method: str   # "api_key_header", "bearer_token", "query_param", "oauth2", "none"
    auth_config: dict  # {"header": "X-API-Key"} or {"param": "api_key"} etc.
    endpoints: list[dict]
    pricing_model: dict
    rate_limits: dict
    capabilities: list[str]
    raw_analysis: str


@dataclass
class BenchmarkPlan:
    """What the LLM decided to test."""
    service_id: str
    tests: list[dict]
    pricing_probes: list[dict]
    stress_profile: dict


@dataclass
class BenchmarkExecutionResult:
    """Raw results from executing a single test."""
    test_name: str
    endpoint: str
    method: str
    request_payload: dict | None
    status_code: int | None
    response_body: Any
    response_headers: dict
    latency_ms: int
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class NormalizedScore:
    """LLM interpretation — comparable across all services."""
    service_id: str
    overall: int  # 0-100
    dimensions: dict[str, float]  # {"token_efficiency": 8.5, ...}
    pricing_normalized: dict
    issues: list[dict]
    recommendations: list[str]
    raw_interpretation: str


@dataclass
class BenchmarkReport:
    """Complete benchmark output — returned to CLI and submitted to Prowl."""
    url: str
    name: str
    template: str
    overall_score: int
    dimensions: dict[str, float]
    breakdown: dict[str, int]  # weighted 0-100 version
    pricing_normalized: dict
    issues: list[dict]
    recommendations: list[str]
    execution_results: list[BenchmarkExecutionResult]
    analysis: ServiceAnalysis
    raw_interpretation: str
    started_at: str
    completed_at: str
    runner_version: str
    llm_providers_used: list[str]
