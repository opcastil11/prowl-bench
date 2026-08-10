"""A real streamable-http MCP client.

`McpComplianceTemplate` could not talk to a spec-compliant MCP server. It made
three mistakes, and they compound:

1. It appended `/mcp` to whatever URL you passed, so pointing it *directly* at
   an endpoint was the one thing guaranteed to fail. Measured against
   `platform.digitalpublic.com/api/mcp-commercial`: the URL as given answers
   **200**, and the URL the template built (`.../api/mcp-commercial/mcp`)
   answers **404**.
2. It sent `Accept: application/json` and called `resp.json()`. streamable-http
   lets a server answer a POST with *either* `application/json` or a
   `text/event-stream` frame — both legal, both in the wild — so an SSE server
   became a parse error.
3. It fired `tools/list` as a bare request with no `initialize` →
   `notifications/initialized` handshake. Stateless servers tolerate that;
   stateful ones reject it.

None of this surfaced as an error, because the template's output is fed to an
LLM and scored. A transport failure degrades into a plausible-looking bad
number, which is indistinguishable from an API that genuinely deserves one.
That is why the fix belongs in a module with its own tests rather than inline
in a template: `probe_server()` returns a measurement you can assert on.

Scope: read-only probing (`initialize` → `tools/list`) of public servers.
Servers demanding auth surface as `auth_required` rather than being retried.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from prowl_bench.sandbox.url_validator import SandboxViolation, validate_url

log = logging.getLogger("prowl_bench.mcp")

# The version we advertise. Servers reply with the version they chose; we echo
# it back in MCP-Protocol-Version on later requests, per the 2025-06-18 spec.
CLIENT_PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "prowl-bench", "version": "0.2.0"}

# streamable-http requires the client to accept both response framings.
ACCEPT = "application/json, text/event-stream"

DEFAULT_TIMEOUT = 20.0
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_DESCRIPTION_CHARS = 4000

# Tried in order when the URL you passed is not itself an MCP endpoint. The
# bare URL is always tried first — see `resolve_endpoint`.
FALLBACK_PATHS = ("/mcp", "/mcp/", "/api/mcp", "/sse")

# Every guess is a POST at somebody else's server. Cap the total so a typo does
# not turn into a small scan.
MAX_RESOLUTION_ATTEMPTS = len(FALLBACK_PATHS) + 1


class McpError(Exception):
    """Transport- or protocol-level failure talking to an MCP server."""


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None


@dataclass
class McpProbe:
    """Outcome of one probe. `ok` means we completed tools/list."""

    endpoint: str
    ok: bool = False
    error: str | None = None
    # 'auth_required' is distinct from a plain failure: the server is alive and
    # speaking MCP, we just cannot see it. Worth reporting, not worth retrying.
    status: str = "dead"  # live | dead | auth_required
    http_status: int | None = None
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    session_id: str | None = None
    instructions: str | None = None
    framing: str | None = None  # 'json' | 'sse'
    tools: list[McpTool] = field(default_factory=list)
    latency_ms: int | None = None

    @property
    def tool_count(self) -> int:
        return len(self.tools)


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def parse_sse(text: str) -> list[dict]:
    """Pull JSON-RPC messages out of an SSE body.

    Per the SSE spec an event ends at a blank line, and multiple `data:` lines
    inside one event concatenate with newlines. Servers emit CRLF, so lines are
    stripped of `\\r` before matching — one observed server does, and dropping
    that strip silently yields zero messages.
    """
    messages: list[dict] = []
    data_lines: list[str] = []

    def flush() -> None:
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        data_lines.clear()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(msg, dict):
            messages.append(msg)

    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    flush()
    return messages


def _snippet(text: str, limit: int = 120) -> str:
    """A body excerpt fit for an error message.

    Wrong-endpoint errors are the common case, and a wrong endpoint usually
    answers with an HTML page — 200 characters of `<!doctype html>...<style>`
    tells the reader nothing and buries the status code that does. Tags are
    stripped and whitespace collapsed so the excerpt is whatever prose the page
    actually carries.
    """
    import re

    # Drop script/style bodies wholesale — stripping only the tags leaves the
    # CSS behind, which is the least informative text on the page.
    cleaned = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    cleaned = re.sub(r"<[^>]{0,200}>", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "..."
    return repr(cleaned) if cleaned else "<empty body>"


def response_framing(resp: httpx.Response) -> str:
    ct = (resp.headers.get("content-type") or "").lower()
    return "sse" if "text/event-stream" in ct else "json"


def _extract_response(resp: httpx.Response, request_id: str) -> dict:
    """Return the JSON-RPC response matching `request_id`, whichever framing
    the server chose."""
    text = resp.text

    if response_framing(resp) == "sse":
        messages = parse_sse(text)
        if not messages:
            raise McpError("SSE body carried no parseable JSON-RPC message")
        for msg in messages:
            if str(msg.get("id")) == request_id:
                return msg
        # Some servers stream notifications ahead of the response; if nothing
        # matched by id, take the last message that looks like one.
        for msg in reversed(messages):
            if "result" in msg or "error" in msg:
                return msg
        raise McpError("SSE body carried no response for our request")

    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        ct = resp.headers.get("content-type")
        raise McpError(
            f"body was neither JSON nor SSE (content-type={ct!r}): {_snippet(text)}"
        )

    if isinstance(body, list):  # batch response
        for msg in body:
            if isinstance(msg, dict) and str(msg.get("id")) == request_id:
                return msg
        raise McpError("batch response carried no reply to our request")
    if not isinstance(body, dict):
        raise McpError(f"unexpected JSON-RPC body type: {type(body).__name__}")
    return body


def decode_body(resp: httpx.Response) -> Any:
    """Best-effort decode, for callers that want to *show* a payload rather
    than route on it. Never raises — a malformed body is a data point the
    benchmark scores, not an error it propagates."""
    if response_framing(resp) == "sse":
        messages = parse_sse(resp.text)
        for msg in reversed(messages):
            if "result" in msg or "error" in msg:
                return msg
        return messages[-1] if messages else resp.text[:2000]
    try:
        return json.loads(resp.text)
    except json.JSONDecodeError:
        return resp.text[:2000]


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

async def _validate_hop(request: httpx.Request) -> None:
    """Re-validate on every redirect hop.

    `validate_url` only guards the URL we chose. With follow_redirects a server
    can answer 302 to anywhere, and httpx obeys it — so the check has to run
    where httpx builds each request, not once at the top.
    """
    validate_url(str(request.url))


def build_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    # follow_redirects: several servers 307 /mcp -> /mcp/ .
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        event_hooks={"request": [_validate_hop]},
    )


async def _rpc(
    client: httpx.AsyncClient,
    endpoint: str,
    method: str,
    params: dict | None,
    *,
    session_id: str | None,
    protocol_version: str | None,
) -> tuple[dict, httpx.Response]:
    """One JSON-RPC call. Returns (result, response) or raises McpError."""
    request_id = uuid.uuid4().hex[:8]
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params

    headers = {"Content-Type": "application/json", "Accept": ACCEPT}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version

    try:
        resp = await client.post(endpoint, json=payload, headers=headers)
    except SandboxViolation:
        raise
    except httpx.HTTPError as e:
        raise McpError(f"{method}: transport error: {e}") from e

    if resp.status_code in (401, 403):
        raise McpError(f"{method}: auth required (HTTP {resp.status_code})")
    if resp.status_code >= 400:
        raise McpError(f"{method}: HTTP {resp.status_code}: {_snippet(resp.text)}")
    if len(resp.content) > MAX_BODY_BYTES:
        raise McpError(f"{method}: response exceeded {MAX_BODY_BYTES} bytes")

    msg = _extract_response(resp, request_id)
    if "error" in msg:
        err = msg["error"] or {}
        raise McpError(f"{method}: server error {err.get('code')}: {err.get('message')}")
    result = msg.get("result")
    if not isinstance(result, dict):
        raise McpError(f"{method}: response had no result object")
    return result, resp


async def _notify(
    client: httpx.AsyncClient,
    endpoint: str,
    method: str,
    *,
    session_id: str | None,
    protocol_version: str | None,
) -> None:
    """Fire a notification (no id, no response). Best-effort: a server that
    rejects it can still serve tools/list, so a failure must not abort."""
    headers = {"Content-Type": "application/json", "Accept": ACCEPT}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if protocol_version:
        headers["MCP-Protocol-Version"] = protocol_version
    try:
        await client.post(endpoint, json={"jsonrpc": "2.0", "method": method}, headers=headers)
    except Exception as e:  # noqa: BLE001 — never let a notification break the probe
        log.debug("mcp: %s notification failed on %s: %s", method, endpoint, e)


def parse_tools(result: dict) -> list[McpTool]:
    tools: list[McpTool] = []
    for raw in result.get("tools") or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        raw_desc = raw.get("description")
        desc = None
        if isinstance(raw_desc, str):
            desc = raw_desc.strip()[:MAX_DESCRIPTION_CHARS] or None
        schema = raw.get("inputSchema") or raw.get("input_schema")
        annotations = raw.get("annotations")
        tools.append(McpTool(
            name=name.strip(),
            description=desc,
            input_schema=schema if isinstance(schema, dict) else None,
            annotations=annotations if isinstance(annotations, dict) else None,
        ))
    return tools


async def _handshake(
    client: httpx.AsyncClient, endpoint: str
) -> tuple[str | None, str, dict, httpx.Response]:
    """initialize + notifications/initialized."""
    result, resp = await _rpc(
        client, endpoint, "initialize",
        {"protocolVersion": CLIENT_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        session_id=None, protocol_version=None,
    )
    # Stateful servers hand back a session id here. Stateless ones never do and
    # reject a header they never issued, so this is read, never invented.
    session_id = resp.headers.get("mcp-session-id")
    negotiated = result.get("protocolVersion") or CLIENT_PROTOCOL_VERSION
    await _notify(client, endpoint, "notifications/initialized",
                  session_id=session_id, protocol_version=negotiated)
    return session_id, negotiated, result, resp


async def probe_server(endpoint: str, *, timeout: float = DEFAULT_TIMEOUT) -> McpProbe:
    """initialize → notifications/initialized → tools/list against one server.

    Never raises: a probe is a measurement, and a dead server is a result. The
    caller reads `.ok` / `.status`.
    """
    probe = McpProbe(endpoint=endpoint)

    try:
        validate_url(endpoint)
    except SandboxViolation as e:
        probe.error = f"blocked: {e}"
        return probe
    except Exception as e:  # noqa: BLE001
        probe.error = f"could not validate endpoint: {e}"
        return probe

    started = time.monotonic()
    try:
        async with build_client(timeout) as client:
            session_id, negotiated, init_result, init_resp = await _handshake(client, endpoint)

            probe.session_id = session_id
            probe.protocol_version = negotiated
            probe.http_status = init_resp.status_code
            probe.framing = response_framing(init_resp)
            info = init_result.get("serverInfo")
            if isinstance(info, dict):
                probe.server_name = info.get("name")
                probe.server_version = info.get("version")
            instructions = init_result.get("instructions")
            if isinstance(instructions, str):
                probe.instructions = instructions.strip()[:MAX_DESCRIPTION_CHARS] or None

            tools_result, _ = await _rpc(
                client, endpoint, "tools/list", {},
                session_id=session_id, protocol_version=negotiated,
            )
            probe.tools = parse_tools(tools_result)
            probe.ok = True
            probe.status = "live"

    except McpError as e:
        probe.error = str(e)
        probe.status = "auth_required" if "auth required" in str(e) else "dead"
        probe.http_status = _status_from_error(str(e))
    except Exception as e:  # noqa: BLE001 — a probe must never take down its caller
        probe.error = f"{type(e).__name__}: {e}"
        probe.status = "dead"

    probe.latency_ms = int((time.monotonic() - started) * 1000)
    return probe


def _status_from_error(msg: str) -> int | None:
    import re
    m = re.search(r"HTTP (\d{3})", msg)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Endpoint resolution
# ---------------------------------------------------------------------------

def _with_path(url: str, suffix: str) -> str:
    p = urlparse(url)
    base = p.path.rstrip("/")
    return urlunparse(p._replace(path=f"{base}{suffix}", query="", fragment=""))


def candidate_endpoints(url: str) -> list[str]:
    """The URL as given, then the usual suffixes.

    The order is the whole point. The template used to skip straight to
    `{url}/mcp`, which means passing the actual endpoint was the one input it
    could not handle — and passing the actual endpoint is what a vendor who
    knows where their server lives will do.
    """
    if "://" not in url:
        url = f"https://{url}"
    candidates = [url.rstrip("/") or url]
    for suffix in FALLBACK_PATHS:
        candidate = _with_path(url, suffix)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


async def resolve_endpoint(
    url: str, *, timeout: float = DEFAULT_TIMEOUT
) -> tuple[McpProbe, list[McpProbe]]:
    """Find the MCP endpoint for `url`.

    Returns (best, attempts). `best` is the first candidate that answered, or —
    if none did — the probe of the URL as given, because that is the answer
    that describes what the user actually asked about rather than the last
    guess we happened to make.
    """
    attempts: list[McpProbe] = []
    for candidate in candidate_endpoints(url):
        if len(attempts) >= MAX_RESOLUTION_ATTEMPTS:
            break
        probe = await probe_server(candidate, timeout=timeout)
        attempts.append(probe)
        if probe.ok:
            return probe, attempts
        # A server that is alive but gated is a real answer; stop guessing.
        if probe.status == "auth_required":
            return probe, attempts
    return attempts[0], attempts
