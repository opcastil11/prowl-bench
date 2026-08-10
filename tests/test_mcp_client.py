"""Tests for the streamable-http MCP client.

No network: every case drives `httpx.MockTransport`. The point of this file is
that the transport is now assertable — the bug it replaces was invisible
precisely because the only consumer of the result was an LLM.
"""
from __future__ import annotations

import json

import httpx
import pytest

from prowl_bench.mcp import client as mc
from prowl_bench.mcp.client import (
    candidate_endpoints,
    decode_body,
    parse_sse,
    parse_tools,
    probe_server,
    resolve_endpoint,
    response_framing,
)

# ---------------------------------------------------------------------------
# SSE framing
# ---------------------------------------------------------------------------

class TestParseSse:
    def test_single_event(self):
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":"1","result":{}}\n\n'
        assert parse_sse(body) == [{"jsonrpc": "2.0", "id": "1", "result": {}}]

    def test_crlf_line_endings(self):
        """A server observed in the wild emits CRLF. Without the \\r strip this
        yields zero messages and the whole probe reads as a dead server."""
        body = 'event: message\r\ndata: {"id":"1","result":{}}\r\n\r\n'
        assert parse_sse(body) == [{"id": "1", "result": {}}]

    def test_multiline_data_concatenates(self):
        body = 'data: {"id":"1",\ndata:  "result":{}}\n\n'
        assert parse_sse(body) == [{"id": "1", "result": {}}]

    def test_comments_and_keepalives_ignored(self):
        body = ': keep-alive\n\ndata: {"id":"1","result":{}}\n\n'
        assert parse_sse(body) == [{"id": "1", "result": {}}]

    def test_malformed_json_is_dropped_not_raised(self):
        assert parse_sse("data: not json\n\n") == []

    def test_missing_trailing_blank_line_still_flushes(self):
        assert parse_sse('data: {"id":"1"}') == [{"id": "1"}]

    def test_empty_body(self):
        assert parse_sse("") == []


class TestFraming:
    def test_detects_sse(self):
        resp = httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"})
        assert response_framing(resp) == "sse"

    def test_defaults_to_json(self):
        assert response_framing(httpx.Response(200)) == "json"

    def test_decode_body_never_raises_on_garbage(self):
        resp = httpx.Response(200, text="<html>nope</html>",
                              headers={"content-type": "text/html"})
        assert isinstance(decode_body(resp), str)

    def test_decode_body_picks_the_response_out_of_an_sse_stream(self):
        resp = httpx.Response(
            200,
            text='data: {"method":"notifications/progress"}\n\ndata: {"id":"1","result":{"ok":1}}\n\n',
            headers={"content-type": "text/event-stream"},
        )
        assert decode_body(resp) == {"id": "1", "result": {"ok": 1}}


# ---------------------------------------------------------------------------
# Endpoint resolution
# ---------------------------------------------------------------------------

class TestCandidateEndpoints:
    def test_url_as_given_comes_first(self):
        """The regression this whole module exists for. `.../api/mcp-commercial`
        answers 200; `.../api/mcp-commercial/mcp` answers 404, and the old
        template only ever built the second one."""
        candidates = candidate_endpoints("https://x.com/api/mcp-commercial")
        assert candidates[0] == "https://x.com/api/mcp-commercial"
        assert "https://x.com/api/mcp-commercial/mcp" in candidates

    def test_suffixes_follow(self):
        candidates = candidate_endpoints("https://x.com")
        assert candidates[1] == "https://x.com/mcp"

    def test_no_duplicates(self):
        assert len(candidate_endpoints("https://x.com/mcp")) == len(
            set(candidate_endpoints("https://x.com/mcp"))
        )

    def test_bare_host_gets_a_scheme(self):
        assert candidate_endpoints("x.com")[0].startswith("https://")

    def test_query_and_fragment_are_dropped_from_guesses(self):
        candidates = candidate_endpoints("https://x.com/base?a=1#f")
        assert "https://x.com/base/mcp" in candidates


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

INIT_RESULT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "demo", "version": "1.2.0"},
    "instructions": "Use this server for demo things.",
}

TOOLS_RESULT = {
    "tools": [
        {"name": "search", "description": "Search the corpus and return matches.",
         "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
    ]
}


def _json_rpc_handler(*, framing="json", session=None, tools=TOOLS_RESULT,
                      seen: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if seen is not None:
            seen.setdefault("requests", []).append((payload, dict(request.headers)))
        method = payload.get("method")
        if method == "notifications/initialized":
            return httpx.Response(202)
        result = INIT_RESULT if method == "initialize" else tools
        msg = {"jsonrpc": "2.0", "id": payload["id"], "result": result}
        headers = {}
        if session and method == "initialize":
            headers["mcp-session-id"] = session
        if framing == "sse":
            return httpx.Response(
                200, text=f"event: message\r\ndata: {json.dumps(msg)}\r\n\r\n",
                headers={**headers, "content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=msg, headers=headers)
    return handler


@pytest.fixture
def patch_client(monkeypatch):
    """Swap the transport, keeping the real client construction path."""
    def _install(handler):
        def build(timeout=mc.DEFAULT_TIMEOUT):
            return httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                timeout=timeout, follow_redirects=True,
            )
        monkeypatch.setattr(mc, "build_client", build)
    return _install


@pytest.mark.asyncio
class TestProbeServer:
    async def test_json_framed_server(self, patch_client):
        patch_client(_json_rpc_handler())
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.ok and probe.status == "live"
        assert probe.framing == "json"
        assert probe.server_name == "demo" and probe.server_version == "1.2.0"
        assert probe.tool_count == 1
        assert probe.instructions

    async def test_sse_framed_server(self, patch_client):
        """The framing the old template could not read at all."""
        patch_client(_json_rpc_handler(framing="sse"))
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.ok
        assert probe.framing == "sse"
        assert probe.tool_count == 1

    async def test_session_id_is_echoed_when_issued(self, patch_client):
        seen: dict = {}
        patch_client(_json_rpc_handler(session="sess-abc", seen=seen))
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.session_id == "sess-abc"
        tools_headers = seen["requests"][-1][1]
        assert tools_headers["mcp-session-id"] == "sess-abc"

    async def test_session_id_is_never_invented(self, patch_client):
        """A stateless server issues none and rejects a header it never sent."""
        seen: dict = {}
        patch_client(_json_rpc_handler(seen=seen))
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.session_id is None
        for _payload, headers in seen["requests"]:
            assert "mcp-session-id" not in {k.lower() for k in headers}

    async def test_handshake_precedes_tools_list(self, patch_client):
        seen: dict = {}
        patch_client(_json_rpc_handler(seen=seen))
        await probe_server("https://demo.example.com/mcp")
        methods = [p.get("method") for p, _ in seen["requests"]]
        assert methods == ["initialize", "notifications/initialized", "tools/list"]

    async def test_accept_header_offers_both_framings(self, patch_client):
        seen: dict = {}
        patch_client(_json_rpc_handler(seen=seen))
        await probe_server("https://demo.example.com/mcp")
        accept = seen["requests"][0][1]["accept"]
        assert "application/json" in accept and "text/event-stream" in accept

    async def test_404_is_a_result_not_an_exception(self, patch_client):
        patch_client(lambda r: httpx.Response(404, text="Not Found"))
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.ok is False
        assert probe.status == "dead"
        assert probe.http_status == 404

    async def test_auth_required_is_distinct_from_dead(self, patch_client):
        patch_client(lambda r: httpx.Response(401, text="unauthorized"))
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.status == "auth_required"
        assert probe.ok is False

    async def test_jsonrpc_error_object(self, patch_client):
        def handler(request):
            payload = json.loads(request.content)
            return httpx.Response(200, json={
                "jsonrpc": "2.0", "id": payload.get("id"),
                "error": {"code": -32601, "message": "Method not found"},
            })
        patch_client(handler)
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.ok is False
        assert "Method not found" in probe.error

    async def test_html_body_does_not_raise(self, patch_client):
        patch_client(lambda r: httpx.Response(
            200, text="<html>hello</html>", headers={"content-type": "text/html"}))
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.ok is False
        assert "neither JSON nor SSE" in probe.error

    async def test_transport_error_does_not_raise(self, patch_client):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)
        patch_client(handler)
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.ok is False and probe.status == "dead"

    async def test_private_address_is_refused_before_any_request(self, patch_client):
        called = {"n": 0}

        def handler(request):
            called["n"] += 1
            return httpx.Response(200, json={})
        patch_client(handler)
        probe = await probe_server("http://169.254.169.254/mcp")
        assert probe.ok is False
        assert called["n"] == 0

    async def test_latency_is_always_recorded(self, patch_client):
        patch_client(lambda r: httpx.Response(500))
        probe = await probe_server("https://demo.example.com/mcp")
        assert probe.latency_ms is not None and probe.latency_ms >= 0


@pytest.mark.asyncio
class TestResolveEndpoint:
    async def test_stops_at_the_url_as_given(self, patch_client):
        seen: dict = {}
        patch_client(_json_rpc_handler(seen=seen))
        best, attempts = await resolve_endpoint("https://x.example.com/api/mcp-commercial")
        assert best.endpoint == "https://x.example.com/api/mcp-commercial"
        assert len(attempts) == 1

    async def test_falls_back_to_a_suffix(self, patch_client):
        live = _json_rpc_handler()

        def handler(request):
            if request.url.path.endswith("/mcp"):
                return live(request)
            return httpx.Response(404, text="nope")
        patch_client(handler)
        best, attempts = await resolve_endpoint("https://x.example.com")
        assert best.ok and best.endpoint.endswith("/mcp")
        assert len(attempts) == 2

    async def test_auth_required_stops_the_search(self, patch_client):
        patch_client(lambda r: httpx.Response(403, text="forbidden"))
        best, attempts = await resolve_endpoint("https://x.example.com")
        assert best.status == "auth_required"
        assert len(attempts) == 1

    async def test_total_failure_reports_the_url_the_caller_asked_about(self, patch_client):
        patch_client(lambda r: httpx.Response(404))
        best, attempts = await resolve_endpoint("https://x.example.com/thing")
        assert best.endpoint == "https://x.example.com/thing"
        assert len(attempts) == len(mc.FALLBACK_PATHS) + 1

    async def test_attempt_count_is_capped(self, patch_client):
        patch_client(lambda r: httpx.Response(404))
        _best, attempts = await resolve_endpoint("https://x.example.com")
        assert len(attempts) <= mc.MAX_RESOLUTION_ATTEMPTS


class TestParseTools:
    def test_accepts_both_schema_spellings(self):
        tools = parse_tools({"tools": [
            {"name": "a", "inputSchema": {"type": "object"}},
            {"name": "b", "input_schema": {"type": "object"}},
        ]})
        assert all(t.input_schema for t in tools)

    def test_drops_nameless_entries(self):
        assert parse_tools({"tools": [{"description": "x"}, {"name": "  "}]}) == []

    def test_tolerates_missing_tools_key(self):
        assert parse_tools({}) == []

    def test_long_descriptions_are_capped(self):
        tools = parse_tools({"tools": [{"name": "a", "description": "x" * 99999}]})
        assert len(tools[0].description) == mc.MAX_DESCRIPTION_CHARS

    def test_non_dict_schema_is_discarded(self):
        tools = parse_tools({"tools": [{"name": "a", "inputSchema": "object"}]})
        assert tools[0].input_schema is None
