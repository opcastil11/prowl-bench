"""Score computation — 8-dimension weighted scoring for agent efficiency."""
from __future__ import annotations

# Agent-Efficiency Score weights — measures how easy/cheap it is for an LLM agent to use a service
WEIGHTS = {
    "token_efficiency": 0.25,      # How few tokens needed to understand + use the API
    "first_try_success": 0.20,     # Does agent get it right on first attempt?
    "response_parseability": 0.15,  # Clean structured JSON vs messy responses
    "error_clarity": 0.15,         # Do errors tell the agent exactly what to fix?
    "doc_quality": 0.10,           # Is the spec/docs complete enough for self-serve?
    "auth_simplicity": 0.05,       # How easy is auth for an agent?
    "latency": 0.05,               # Response speed
    "consistency": 0.05,           # Repeated requests return consistent shapes
}


def compute_score(dimensions: dict[str, float]) -> tuple[int, dict[str, int]]:
    """Compute overall score (0-100) from dimension scores (0-10 each).

    Returns (overall_score, breakdown_dict).
    """
    breakdown = {}
    total = 0.0
    for dim, weight in WEIGHTS.items():
        raw = dimensions.get(dim, 0.0)
        clamped = max(0.0, min(10.0, raw))
        breakdown[dim] = round(clamped)
        total += clamped * weight

    overall = round(total * 10)  # scale 0-10 weighted avg -> 0-100
    return min(100, max(0, overall)), breakdown
