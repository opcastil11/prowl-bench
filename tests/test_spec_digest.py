"""The URLs a benchmark calls come from the spec, whatever its length."""
import json

import pytest

from prowl_bench.core.spec_digest import (
    align_plan, declared_base_url, parse_spec, resolve_endpoint, spec_digest,
)

# Shaped like a real 90 KB spec: servers early, paths far apart, 90 KB overall.
PADDING = "x" * 2000
WEATHER = {
    "openapi": "3.0.1",
    "info": {"title": "Weather API", "description": PADDING},
    "servers": [{"url": "https://api.weatherapi.com/v1"}],
    "paths": {
        path: {"get": {
            "summary": name, "description": PADDING * 3,
            "parameters": [{"name": "q", "required": True}, {"name": "lang"}],
        }}
        for name, path in {
            "Current": "/current.json", "Forecast": "/forecast.json",
            "History": "/history.json", "Alerts": "/alerts.json",
            "Marine": "/marine.json", "Future": "/future.json",
        }.items()
    },
}
WEATHER_TEXT = json.dumps(WEATHER, indent=1)


def test_every_path_survives_a_spec_longer_than_the_budget():
    assert len(WEATHER_TEXT) > 30000
    digest = spec_digest(WEATHER_TEXT, 15000)
    assert len(digest) <= 15000
    assert "https://api.weatherapi.com/v1" in digest
    for path in WEATHER["paths"]:
        assert f"GET {path}" in digest
    assert "*q" in digest


def test_paths_are_kept_when_the_summaries_no_longer_fit():
    spec = {"paths": {f"/r{i}": {"get": {"summary": "s" * 100}} for i in range(100)}}
    digest = spec_digest(json.dumps(spec), 2500)
    assert all(f"GET /r{i}\n" in digest + "\n" for i in range(100))


def test_something_that_is_not_a_spec_comes_back_unchanged():
    text = "# Docs\nCall GET /things with your key."
    assert spec_digest(text) == text
    assert parse_spec(text) is None


def test_yaml_specs_are_read():
    pytest.importorskip("yaml")
    text = "openapi: 3.0.0\nservers:\n- url: https://a.example.com\npaths:\n  /x:\n    get: {}\n"
    assert "GET /x" in spec_digest(text)


def test_swagger_2_declares_its_server_with_host_and_base_path():
    spec = {"swagger": "2.0", "host": "api.acme.com", "basePath": "/v2", "schemes": ["https"], "paths": {"/a": {}}}
    assert declared_base_url(spec, "https://acme.com") == "https://api.acme.com/v2"


@pytest.mark.parametrize("current,servers,expected", [
    ("https://www.weatherapi.com", ["https://api.weatherapi.com/v1"], "https://api.weatherapi.com/v1"),
    ("https://b.acme.com/", ["https://a.acme.com", "https://b.acme.com"], None),
    ("https://acme.com", ["http://localhost:8080"], None),
    ("https://acme.com", ["http://10.0.0.1"], None),
    ("https://acme.com", ["https://{tenant}.acme.com"], None),
    ("https://acme.com", ["/api"], None),
])
def test_declared_base_url(current, servers, expected):
    spec = {"servers": [{"url": s} for s in servers], "paths": {"/a": {}}}
    assert declared_base_url(spec, current) == expected


PATHS = ["/current.json", "/forecast.json", "/users/{id}", "/users/{id}/orders"]


@pytest.mark.parametrize("endpoint,base_path,expected", [
    ("/current.json", "/v1", "/current.json"),
    ("/v1/current.json", "/v1", "/current.json"),
    ("/v1/current", "/v1", "/current.json"),
    ("/current?q=London", "/v1", "/current.json?q=London"),
    ("/users/42", "", "/users/42"),
    ("/users/42/orders", "", "/users/42/orders"),
    ("/this-path-does-not-exist", "/v1", None),
    ("/v1/historical", "/v1", None),
    ("https://evil.example/x", "", None),
])
def test_resolve_endpoint(endpoint, base_path, expected):
    assert resolve_endpoint(endpoint, PATHS, base_path) == expected


def test_an_ambiguous_extension_is_left_alone():
    assert resolve_endpoint("/a", ["/a.json", "/a.xml"]) is None


def test_align_plan_keeps_a_deliberate_404():
    tests = [{"endpoint": "/v1/current"}, {"endpoint": "/nope"}, {"name": "no endpoint"}]
    assert align_plan(tests, WEATHER, "https://api.weatherapi.com/v1") == 1
    assert tests[0]["endpoint"] == "/current.json"
    assert tests[1]["endpoint"] == "/nope"
