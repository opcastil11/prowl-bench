"""prowl-bench — Open-source benchmark runner for the Prowl Agent Discovery Network.

Run standardized, multi-LLM benchmarks against any API and submit results to Prowl.

Usage:
    prowl-bench run https://api.example.com
    prowl-bench run https://api.example.com --template api_benchmark --submit
"""

__version__ = "0.1.0"

from prowl_bench.core.types import (
    ServiceAnalysis,
    BenchmarkPlan,
    BenchmarkExecutionResult,
    NormalizedScore,
    BenchmarkReport,
)
from prowl_bench.core.scoring import WEIGHTS, compute_score

__all__ = [
    "ServiceAnalysis",
    "BenchmarkPlan",
    "BenchmarkExecutionResult",
    "NormalizedScore",
    "BenchmarkReport",
    "WEIGHTS",
    "compute_score",
]
