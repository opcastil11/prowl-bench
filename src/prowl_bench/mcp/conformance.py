"""Deterministic agent-readiness scoring for an MCP server.

Everything else in prowl-bench needs an LLM key, because judging whether an
error message is *actionable* is a judgement call. Most of what makes an MCP
server usable by an agent is not: either a tool carries an input schema or it
does not, either the description says what the tool does or it is three words
long. Those are countable, and counting them is reproducible in a way an LLM
score is not — two runs give the same number, and the number can be diffed in
CI.

So this module scores what can be counted, and says so. It does **not** call
any tool: `tools/call` has side effects that belong to whoever owns the server,
and a benchmark that writes to your production database is not a benchmark. It
therefore cannot tell you whether the tools *work* — only whether an agent has
enough information to use them. `prowl-bench run` with an LLM key remains the
judgement-based score; this is the arithmetic one.

The findings are the point, more than the number. A score of 61 tells a vendor
nothing; "9 of 14 tools have no input schema" tells them what to do on Monday.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from prowl_bench.mcp.client import McpProbe, McpTool

# Weights sum to 1.0. Documentation and schema together carry 40% because they
# are what an agent actually consumes when deciding whether it can call a tool.
WEIGHTS = {
    "reachability": 0.20,
    "tool_discovery": 0.15,
    "tool_documentation": 0.20,
    "schema_quality": 0.20,
    "latency": 0.15,
    "agent_guidance": 0.10,
}

# A description shorter than this says the tool's name again and nothing more.
MIN_USEFUL_DESCRIPTION = 25

# Namespaced as `{slug}__{tool}` by the Prowl gateway, and other aggregators do
# something similar. Past this a name risks being dropped rather than routed.
MAX_SAFE_NAME_LEN = 100

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class Finding:
    severity: str
    detail: str
    fix: str | None = None

    def as_dict(self) -> dict:
        d = {"severity": self.severity, "detail": self.detail}
        if self.fix:
            d["fix"] = self.fix
        return d


@dataclass
class ConformanceReport:
    endpoint: str
    reachable: bool
    overall: int
    dimensions: dict[str, float]
    findings: list[Finding] = field(default_factory=list)
    tool_count: int = 0
    documented_tools: int = 0
    schema_tools: int = 0
    latency_ms: int | None = None
    server_name: str | None = None
    server_version: str | None = None
    protocol_version: str | None = None
    framing: str | None = None
    stateful: bool = False
    attempts: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "reachable": self.reachable,
            "overall": self.overall,
            "dimensions": self.dimensions,
            "findings": [f.as_dict() for f in self.findings],
            "tools": {
                "total": self.tool_count,
                "documented": self.documented_tools,
                "with_schema": self.schema_tools,
            },
            "latency_ms": self.latency_ms,
            "server": {
                "name": self.server_name,
                "version": self.server_version,
                "protocol_version": self.protocol_version,
                "framing": self.framing,
                "stateful": self.stateful,
            },
            "attempts": self.attempts,
        }


def _latency_score(ms: int | None) -> float:
    """A handshake plus a tools/list. Under 500ms is unremarkable; past ~5s an
    agent doing this at the start of every session feels it."""
    if ms is None:
        return 0.0
    if ms <= 500:
        return 10.0
    if ms >= 5000:
        return 1.0
    # linear between the two anchors
    return round(10.0 - 9.0 * (ms - 500) / 4500, 1)


def _is_documented(tool: McpTool) -> bool:
    return bool(tool.description and len(tool.description.strip()) >= MIN_USEFUL_DESCRIPTION)


def _has_schema(tool: McpTool) -> bool:
    schema = tool.input_schema
    if not isinstance(schema, dict):
        return False
    # A schema of `{"type": "object"}` with no properties declares nothing. It
    # is only meaningful for a tool that genuinely takes no arguments, which we
    # cannot distinguish here — so it counts, but `_described_properties` will
    # not credit it.
    return bool(schema.get("type") or schema.get("properties"))


def _property_documentation(tools: list[McpTool]) -> tuple[int, int]:
    """(described properties, total properties) across every tool schema."""
    described = total = 0
    for tool in tools:
        props = (tool.input_schema or {}).get("properties")
        if not isinstance(props, dict):
            continue
        for spec in props.values():
            total += 1
            if isinstance(spec, dict) and str(spec.get("description") or "").strip():
                described += 1
    return described, total


def score_probe(probe: McpProbe, attempts: list[McpProbe] | None = None) -> ConformanceReport:
    """Turn a probe into a scorecard. Pure — no I/O, so it is trivially testable."""
    report = ConformanceReport(
        endpoint=probe.endpoint,
        reachable=probe.ok,
        overall=0,
        dimensions={},
        latency_ms=probe.latency_ms,
        server_name=probe.server_name,
        server_version=probe.server_version,
        protocol_version=probe.protocol_version,
        framing=probe.framing,
        stateful=bool(probe.session_id),
        attempts=[
            {"endpoint": a.endpoint, "status": a.status, "http_status": a.http_status,
             "error": a.error}
            for a in (attempts or [])
        ],
    )

    if not probe.ok:
        if probe.status == "auth_required":
            report.findings.append(Finding(
                "high",
                f"Server requires authentication ({probe.error})",
                "Agents cannot discover a gated server. Expose tools/list "
                "unauthenticated, or document the auth flow in your llms.txt.",
            ))
            # Alive and speaking MCP, just not to us. Reachability is the only
            # thing we can honestly score, and it is not zero.
            report.dimensions = {"reachability": 4.0}
        else:
            report.findings.append(Finding(
                "critical",
                f"No MCP server answered at {probe.endpoint}: {probe.error}",
                "Check the endpoint path. prowl-bench tried the URL you gave "
                "first, then the usual suffixes — see `attempts`.",
            ))
            report.dimensions = {"reachability": 0.0}
        report.overall = round(
            sum(report.dimensions.get(k, 0.0) * w for k, w in WEIGHTS.items()) * 10
        )
        return report

    tools = probe.tools
    report.tool_count = len(tools)
    report.documented_tools = sum(1 for t in tools if _is_documented(t))
    report.schema_tools = sum(1 for t in tools if _has_schema(t))

    dims: dict[str, float] = {"reachability": 10.0}

    # --- tool discovery -----------------------------------------------------
    if not tools:
        dims["tool_discovery"] = 2.0
        report.findings.append(Finding(
            "critical",
            "Server handshakes but advertises no tools",
            "An agent has nothing to call. Check that tools/list returns your "
            "registered tools.",
        ))
    else:
        dims["tool_discovery"] = 10.0

    # --- documentation ------------------------------------------------------
    if tools:
        ratio = report.documented_tools / len(tools)
        dims["tool_documentation"] = round(ratio * 10, 1)
        undocumented = [t.name for t in tools if not _is_documented(t)]
        if undocumented:
            shown = ", ".join(undocumented[:5])
            more = f" (+{len(undocumented) - 5} more)" if len(undocumented) > 5 else ""
            report.findings.append(Finding(
                "high" if ratio < 0.5 else "medium",
                f"{len(undocumented)} of {len(tools)} tools lack a useful description: {shown}{more}",
                "An agent picks a tool by reading its description. Say what it "
                "does, what it returns, and when not to use it.",
            ))
    else:
        dims["tool_documentation"] = 0.0

    # --- schema quality -----------------------------------------------------
    if tools:
        schema_ratio = report.schema_tools / len(tools)
        described, total_props = _property_documentation(tools)
        prop_ratio = (described / total_props) if total_props else 0.0
        # Having a schema at all matters more than annotating every field.
        dims["schema_quality"] = round((schema_ratio * 0.7 + prop_ratio * 0.3) * 10, 1)

        missing = [t.name for t in tools if not _has_schema(t)]
        if missing:
            shown = ", ".join(missing[:5])
            more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
            report.findings.append(Finding(
                "critical" if schema_ratio < 0.5 else "high",
                f"{len(missing)} of {len(tools)} tools declare no inputSchema: {shown}{more}",
                "Without a schema an agent has to guess argument names, which "
                "it will get wrong. Declare inputSchema with typed properties.",
            ))
        if total_props and prop_ratio < 0.8:
            report.findings.append(Finding(
                "medium",
                f"{total_props - described} of {total_props} schema properties have no description",
                "Per-property descriptions are what stop an agent passing a "
                "plausible value in the wrong format.",
            ))
    else:
        dims["schema_quality"] = 0.0

    # --- latency ------------------------------------------------------------
    dims["latency"] = _latency_score(probe.latency_ms)
    if probe.latency_ms and probe.latency_ms > 3000:
        report.findings.append(Finding(
            "medium",
            f"Handshake + tools/list took {probe.latency_ms}ms",
            "Agents pay this on every session start.",
        ))

    # --- agent guidance -----------------------------------------------------
    guidance = 0.0
    if probe.instructions:
        guidance += 6.0
    else:
        report.findings.append(Finding(
            "low",
            "Server sends no `instructions` in its initialize result",
            "`instructions` is free context every agent reads before choosing "
            "a tool. Use it to say what this server is for.",
        ))
    if probe.server_name:
        guidance += 2.0
    if probe.server_version:
        guidance += 2.0
    else:
        report.findings.append(Finding(
            "low",
            "serverInfo declares no version",
            "Agents and registries use it to detect changes.",
        ))
    dims["agent_guidance"] = guidance

    # --- name safety (a finding, not a dimension) ---------------------------
    overlong = [t.name for t in tools if len(t.name) > MAX_SAFE_NAME_LEN]
    if overlong:
        report.findings.append(Finding(
            "medium",
            f"{len(overlong)} tool names exceed {MAX_SAFE_NAME_LEN} chars: {', '.join(overlong[:3])}",
            "Aggregating gateways namespace tool names and drop ones that no "
            "longer fit — a truncated name is a name that does not route.",
        ))

    report.dimensions = dims
    report.overall = round(sum(dims.get(k, 0.0) * w for k, w in WEIGHTS.items()) * 10)
    report.findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    return report
