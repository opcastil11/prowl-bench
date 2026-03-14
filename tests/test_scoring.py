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
    # Missing dimensions should be 0
    assert breakdown["latency"] == 0
