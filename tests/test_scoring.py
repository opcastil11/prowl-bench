"""Tests for the scoring engine."""
from prowl_bench.core.scoring import compute_score, WEIGHTS


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 0.001


def test_perfect_score():
    dims = {k: 10.0 for k in WEIGHTS}
    overall, breakdown = compute_score(dims)
    assert overall == 100
    assert all(v == 10 for v in breakdown.values())


def test_zero_score():
    dims = {k: 0.0 for k in WEIGHTS}
    overall, breakdown = compute_score(dims)
    assert overall == 0
    assert all(v == 0 for v in breakdown.values())


def test_clamping():
    dims = {"token_efficiency": 15.0, "first_try_success": -5.0}
    overall, breakdown = compute_score(dims)
    assert breakdown["token_efficiency"] == 10
    assert breakdown["first_try_success"] == 0


def test_partial_dimensions():
    dims = {"token_efficiency": 8.0, "first_try_success": 7.0}
    overall, breakdown = compute_score(dims)
    assert overall > 0
    assert breakdown["token_efficiency"] == 8
    assert breakdown["first_try_success"] == 7
    # An unmeasured dimension is absent, not 0 — reporting 0 would claim we
    # tested latency and found it terrible.
    assert "latency" not in breakdown


def test_unmeasured_dimensions_do_not_drag_the_average_down():
    """The regression: mcp_compliance can't observe every dimension without
    calling somebody's tools, and lost points for the ones it left blank.

    Both operational dimensions are supplied here so the 85 cap stays out of
    the way — this test is about the average, not the cap.
    """
    measured = {
        "token_efficiency": 9.0, "first_try_success": 9.0,
        "latency": 9.0, "consistency": 9.0,
    }
    partial, _ = compute_score(measured)
    full, _ = compute_score({
        **measured, "error_clarity": 9.0, "doc_quality": 9.0,
        "response_parseability": 9.0, "auth_simplicity": 9.0,
    })
    assert partial == full == 90


def test_the_cap_still_bites_when_an_operational_dimension_is_missing():
    """Renormalising must not become a way to launder away the 85 cap."""
    overall, _ = compute_score({
        "token_efficiency": 9.0, "first_try_success": 9.0,
        "response_parseability": 9.0, "doc_quality": 9.0,
        "auth_simplicity": 9.0, "latency": 9.0,  # no `consistency`
    })
    assert overall == 85


def test_missing_operational_dimensions_cap_at_85():
    """Skipping a dimension must not be a way to reach 100 cheaply."""
    overall, _ = compute_score({"token_efficiency": 10.0, "doc_quality": 10.0})
    assert overall == 85


def test_all_dimensions_measured_can_still_reach_100():
    overall, _ = compute_score({k: 10.0 for k in WEIGHTS})
    assert overall == 100


def test_no_dimensions_at_all_is_zero_not_a_crash():
    overall, breakdown = compute_score({})
    assert overall == 0
    assert breakdown == {}
