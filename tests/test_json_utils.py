"""Tests for JSON extraction from LLM responses."""
import pytest

from prowl_bench.core.json_utils import extract_json


def test_plain_json():
    text = '{"overall": 85, "dimensions": {}}'
    result = extract_json(text)
    assert result["overall"] == 85


def test_json_in_code_fence():
    text = 'Here is the result:\n```json\n{"overall": 72}\n```\nDone.'
    result = extract_json(text)
    assert result["overall"] == 72


def test_json_with_preamble():
    text = 'Based on my analysis, the score is:\n\n{"overall": 90, "dimensions": {"latency": 8.5}}'
    result = extract_json(text)
    assert result["overall"] == 90


def test_trailing_comma_repair():
    text = '{"overall": 85, "dims": {"a": 1,}}'
    result = extract_json(text)
    assert result["overall"] == 85


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json("This has no JSON at all")


def test_json_array():
    text = '[{"name": "test1"}, {"name": "test2"}]'
    result = extract_json(text)
    assert len(result) == 2
