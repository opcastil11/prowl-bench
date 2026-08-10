"""MCP support: a real streamable-http client and a deterministic scorecard."""
from prowl_bench.mcp.client import (
    McpError,
    McpProbe,
    McpTool,
    candidate_endpoints,
    decode_body,
    parse_sse,
    probe_server,
    resolve_endpoint,
)
from prowl_bench.mcp.conformance import ConformanceReport, Finding, score_probe

__all__ = [
    "ConformanceReport",
    "Finding",
    "McpError",
    "McpProbe",
    "McpTool",
    "candidate_endpoints",
    "decode_body",
    "parse_sse",
    "probe_server",
    "resolve_endpoint",
    "score_probe",
]
