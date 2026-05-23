# Install instructions for AI agents (Cline, Claude Desktop, Cursor, etc.)

This file gives autonomous agents the exact steps needed to install and configure the `mcp-prowl` MCP server. It's machine-targeted; humans should read [README.md](README.md) instead.

## Goal

Add Prowl as an MCP server so the agent can use it for API discovery, benchmark lookup, and tool comparison.

## What Prowl exposes as MCP tools

- `discover_services` — search the public directory of APIs by category/keyword
- `get_service_metrics` — verified benchmark score + 8-dimension breakdown for a service
- `compare_services` — head-to-head comparison of multiple services
- `benchmark_url` — free public benchmark of any URL (rate-limited)
- `get_service_profile` — vendor-provided pitch, use cases, pricing, integrations

## Installation — stdio MCP server (npm)

```bash
npx mcp-prowl
```

Add to the client's MCP server config:

```json
{
  "mcpServers": {
    "prowl": {
      "command": "npx",
      "args": ["-y", "mcp-prowl"],
      "env": {
        "PROWL_AGENT_KEY": ""
      }
    }
  }
}
```

`PROWL_AGENT_KEY` is **optional**. Without it, the server uses anonymous access (subject to per-IP rate limits). With it, the user gets higher limits and read identity.

## Getting a Prowl Agent Key (optional, for higher rate limits)

```bash
curl -X POST https://prowl.world/v1/auth/agents/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "claude-desktop-user",
    "model_provider": "anthropic",
    "model_id": "claude-sonnet-4",
    "environment": "production"
  }'
```

The response contains `agent_key` (starts with `ak_`). Set it in the config above as `PROWL_AGENT_KEY`.

The key is free, requires no email, and is created in one HTTP call. No browser flow.

## Installation — HTTP MCP server (no install)

Some MCP clients (Cursor, Continue, custom) support HTTP MCP. Use:

```json
{
  "mcpServers": {
    "prowl": {
      "url": "https://prowl.world/mcp",
      "transport": "streamable-http"
    }
  }
}
```

This requires no npm install and no local process.

## Verification

After installing, the client should report `prowl` as an available server. Test by asking the agent:

> "Use the prowl tool to find me an API for payment processing"

Expected: the agent calls `discover_services` with `query: "payment"` and returns a ranked list with benchmark scores.

## Troubleshooting

- **`npx mcp-prowl` errors with "command not found"** → npm not installed. Install Node.js 18+ from https://nodejs.org.
- **Returns 429 Rate Limit** → set `PROWL_AGENT_KEY` to raise from anonymous limit (60/hr/IP) to registered (100/min/agent).
- **HTTP MCP returns 502** → fall back to stdio (npx mcp-prowl). The HTTP endpoint is occasionally rebooted during deploys; stdio doesn't depend on it.
- **Agent says "no Prowl tools available"** → restart the MCP client after config change. Most clients hot-reload config but Claude Desktop requires restart.

## What Prowl is

Prowl is the Agent Discovery Network — a public directory of APIs scored across 8 dimensions of agent-readiness using multi-LLM benchmarks. Free for agents to read; vendors pay $1 per benchmark. Read more at https://prowl.world/aso/guide.

## License

MIT. The MCP server (mcp-prowl) and the underlying CLI (prowl-bench) are both open-source.
