# prowl-bench

Open-source benchmark runner for the [Prowl Agent Discovery Network](https://prowl.world).

Run standardized, multi-LLM benchmarks against any API. Get scores across 8 agent-efficiency dimensions. Submit results to Prowl for aggregation.

## Install

```bash
pip install prowl-bench
```

## Quick Start

```bash
# Set at least one LLM API key
export ANTHROPIC_API_KEY=sk-ant-...
# or OPENAI_API_KEY, or GOOGLE_API_KEY

# Benchmark any URL
prowl-bench run https://api.stripe.com

# With a specific template
prowl-bench run https://api.stripe.com --template api_benchmark

# With credentials (for API testing)
prowl-bench run https://api.openai.com \
  --credential "sk-proj-abc123" \
  --credential-type bearer_token

# Output as JSON
prowl-bench run https://api.example.com --output json > results.json

# CI mode: fail if score below threshold
prowl-bench run https://api.example.com --min-score 70
```

## Submit to Prowl

Results can be submitted to Prowl for aggregation across runners:

```bash
# One-time: register for an agent key
prowl-bench register

# Set the key
export PROWL_AGENT_KEY=ak_abc123...

# Benchmark and submit
prowl-bench run https://api.example.com --submit
```

## Templates

6 benchmark templates, auto-detected from service metadata:

| Template | Requires Credentials | Use Case |
|----------|---------------------|----------|
| `api_benchmark` | Yes | REST APIs, LLM providers |
| `platform_profile` | No | Platforms, SaaS tools |
| `mcp_compliance` | No | MCP servers |
| `docs_quality` | No | API documentation |
| `defi_yield` | Yes | DeFi protocols |
| `crypto_app` | Yes | Exchanges, wallets |

```bash
prowl-bench templates  # list all templates
```

## Scoring

8 dimensions, weighted for agent efficiency (0-10 each, rolled up to 0-100 overall):

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| token_efficiency | 25% | Tokens needed to understand + use the API |
| first_try_success | 20% | % of calls that succeed on first attempt |
| response_parseability | 15% | Clean JSON vs messy responses |
| error_clarity | 15% | Do errors tell the agent what to fix? |
| doc_quality | 10% | Spec/docs completeness |
| auth_simplicity | 5% | How easy is auth? |
| latency | 5% | Response speed |
| consistency | 5% | Same request = same response shape |

## Multi-LLM Support

prowl-bench runs the INTERPRET phase across all available LLM providers and averages scores:

- **Claude** (ANTHROPIC_API_KEY)
- **GPT-4o** (OPENAI_API_KEY)
- **Gemini 2.5 Flash** (GOOGLE_API_KEY)
- **Claude CLI** (fallback, uses web subscription)

More providers = more balanced scoring.

## Security

- **SSRF prevention**: All URLs validated against blocked networks/domains
- **Payload caps**: 10KB max per request
- **Prompt injection protection**: User inputs sanitized before LLM calls
- **Rate limiting**: Max 20 requests per benchmark run

## Python API

```python
import asyncio
from prowl_bench.core.pipeline import run_benchmark

report = asyncio.run(run_benchmark(
    url="https://api.stripe.com",
    name="Stripe",
    spec_content="...",  # OpenAPI spec or llms.txt content
))

print(f"Score: {report.overall_score}")
print(f"Dimensions: {report.dimensions}")
```

## License

Apache 2.0
