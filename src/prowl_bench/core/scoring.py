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

    A dimension that was never measured is **skipped**, not scored 0, and the
    average is renormalised over the weight actually present. Scoring it 0 says
    "we tested this and it was terrible" about something nobody tested — an
    mcp_compliance run, which cannot observe `error_clarity` or `consistency`
    without calling somebody's tools, lost 16 points to that lie.

    Not measuring is still not free: missing `latency` or `consistency` caps the
    result at 85, so a template can't reach 100 by reporting only the dimensions
    it happens to do well on. Mirrors `compute_score` in the Prowl backend, so a
    number from this CLI means the same thing as a number from prowl.world.

    Returns (overall_score, breakdown_dict). The breakdown carries only the
    dimensions that were measured.
    """
    breakdown: dict[str, int] = {}
    total = 0.0
    total_weight = 0.0
    for dim, weight in WEIGHTS.items():
        raw = dimensions.get(dim)
        if raw is None:
            continue
        clamped = max(0.0, min(10.0, raw))
        breakdown[dim] = round(clamped)
        total += clamped * weight
        total_weight += weight

    if total_weight <= 0:
        return 0, breakdown

    overall = round((total / total_weight) * 10)

    if "latency" not in breakdown or "consistency" not in breakdown:
        overall = min(overall, 85)

    return min(100, max(0, overall)), breakdown
