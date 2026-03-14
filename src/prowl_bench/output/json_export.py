"""JSON export for benchmark results."""
from __future__ import annotations

import json
from dataclasses import asdict

from prowl_bench.core.types import BenchmarkReport


def report_to_json(report: BenchmarkReport) -> str:
    """Serialize a BenchmarkReport to JSON."""
    data = {
        "url": report.url,
        "name": report.name,
        "template": report.template,
        "overall_score": report.overall_score,
        "dimensions": report.dimensions,
        "breakdown": report.breakdown,
        "pricing_normalized": report.pricing_normalized,
        "issues": report.issues,
        "recommendations": report.recommendations,
        "llm_providers_used": report.llm_providers_used,
        "runner_version": report.runner_version,
        "started_at": report.started_at,
        "completed_at": report.completed_at,
    }
    return json.dumps(data, indent=2, default=str)
