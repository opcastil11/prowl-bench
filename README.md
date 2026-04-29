<div align="center">

<img src="assets/logo.png" alt="Prowl" width="120">

# prowl-bench

**The open-source benchmark runner for AI agent efficiency.**

Evaluate any API across 8 dimensions of agent-readiness using multi-LLM scoring.

[![PyPI version](https://img.shields.io/pypi/v/prowl-bench?color=%2334D058&label=pypi)](https://pypi.org/project/prowl-bench/)
[![Python](https://img.shields.io/pypi/pyversions/prowl-bench?color=%2334D058)](https://pypi.org/project/prowl-bench/)
[![License](https://img.shields.io/github/license/opcastil11/prowl-bench?color=%2334D058)](https://github.com/opcastil11/prowl-bench/blob/main/LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/opcastil11/prowl-bench/ci.yml?label=tests&color=%2334D058)](https://github.com/opcastil11/prowl-bench/actions)

[Installation](#installation) | [Quickstart](#quickstart) | [How It Works](#how-it-works) | [Templates](#templates) | [Scoring](#scoring) | [Provider Network](#provider-network----earn-revenue) | [prowl.world](https://prowl.world)

</div>

---

## Why prowl-bench?

APIs are designed for humans to read docs and figure things out. But agents don't read docs -- they make HTTP calls and parse responses. An API that's great for humans can be terrible for agents.

**prowl-bench measures what matters for agents:**

- **Does the API respond with parseable, predictable JSON?** Not HTML error pages, not XML, not random formats.
- **Can an agent authenticate on the first try?** Or does it need 47 steps, an OAuth dance, and a CAPTCHA?
- **Are errors actionable?** `{"error": "invalid"}` tells an agent nothing. `{"error": "missing required field 'email'", "code": "VALIDATION_ERROR"}` tells it exactly what to fix.
- **How many tokens does it cost to understand?** A 50-page OpenAPI spec vs a clean `/llms.txt` -- the difference is real money.

Traditional API testing tools measure uptime and response time. **prowl-bench measures whether an AI agent can actually use your API.**

## Terminal Output

```
$ prowl-bench run https://api.stripe.com

  Benchmarking https://api.stripe.com ...

  SPEC     Fetched llms.txt ................................ OK  (0.8s)
  ANALYZE  Extracting service structure .................... OK  (2.1s)
  PLAN     Designing 12 test cases ......................... OK  (1.4s)
  EXECUTE  Running tests against live API .................. OK  (3.2s)
  INTERPRET Normalizing scores (3 LLMs) ................... OK  (2.8s)

  prowl-bench v0.2.0 | Template: api_benchmark | LLMs: claude, gpt-4o, gemini

  ┌─  Stripe API    Score: 82  ────────────────────────┐
  │                                                     │
  │  auth simplicity     ████████░░   8.0               │
  │  consistency         █████████░   9.0               │
  │  doc quality         ████████░░   8.5               │
  │  error clarity       █████████░   9.2               │
  │  first try success   ███████░░░   7.0               │
  │  latency             ████████░░   8.0               │
  │  response parseab..  █████████░   9.5               │
  │  token efficiency    ███████░░░   7.0               │
  │                                                     │
  └─────────────────────────────────────────────────────┘

  Issues: 2
  - OpenAPI spec is 48,000+ tokens — consider publishing /llms.txt
  - POST /v1/charges returns HTML on 402 status codes

  Recommendations:
  - Add structured error codes to all 4xx responses
  - Publish a condensed /llms.txt for agent consumers
```

## Installation

```bash
pip install prowl-bench
```

Requires Python 3.10+. No system dependencies.

## Quickstart

```bash
# Set at least one LLM API key
export ANTHROPIC_API_KEY="sk-ant-..."
# or OPENAI_API_KEY, or GOOGLE_API_KEY — more keys = more balanced scoring

# Benchmark any API
prowl-bench run https://api.stripe.com

# With a specific template
prowl-bench run https://api.stripe.com --template api_benchmark

# With credentials for authenticated endpoints
prowl-bench run https://api.openai.com \
  --credential "sk-proj-abc123" \
  --credential-type bearer_token

# Output as JSON (for pipelines)
prowl-bench run https://api.example.com --output json > results.json

# CI mode: exit 1 if score below threshold
prowl-bench run https://api.example.com --min-score 70
```

## How It Works

prowl-bench runs a 4-phase pipeline. Each phase is driven by an LLM that reads real data, not hardcoded heuristics.

```
                ┌─────────────────────────────────────────────────┐
                │              prowl-bench pipeline               │
                └─────────────────────────────────────────────────┘

  ┌───────────┐    ┌──────────┐    ┌───────────┐    ┌─────────────┐
  │  ANALYZE  │───>│   PLAN   │───>│  EXECUTE  │───>│  INTERPRET  │
  │           │    │          │    │           │    │             │
  │ Read spec │    │ Design   │    │ Run real  │    │ Score 0-10  │
  │ Extract   │    │ test     │    │ HTTP      │    │ across 8    │
  │ structure │    │ cases    │    │ requests  │    │ dimensions  │
  │ + auth    │    │ + probes │    │ + record  │    │ (multi-LLM) │
  └───────────┘    └──────────┘    └───────────┘    └─────────────┘
       │                                                  │
       │         OpenAPI / llms.txt / HTML                 │ Claude + GPT-4o
       └──────────────── input ──────────────────────────  │ + Gemini average
                                                          ▼
                                                   ┌─────────────┐
                                                   │   REPORT    │
                                                   │ Terminal /  │
                                                   │ JSON / CI   │
                                                   └─────────────┘
```

**Phase 1 -- ANALYZE:** The LLM reads the API spec (OpenAPI, llms.txt, or raw HTML) and extracts the service type, authentication method, endpoints, pricing model, and rate limits.

**Phase 2 -- PLAN:** Based on the analysis, the LLM designs targeted test cases: endpoint probes, error handling checks, auth flow tests, and pricing verification.

**Phase 3 -- EXECUTE:** Real HTTP requests are made against the live API. Every request goes through a sandbox that blocks SSRF, validates payloads, and prevents prompt injection. Responses, latencies, and errors are recorded.

**Phase 4 -- INTERPRET:** All available LLMs score the results independently across 8 dimensions. Scores are averaged for balance. More LLM providers = less bias.

## Templates

6 benchmark templates, auto-detected from service metadata:

| Template | Credentials | Auto-detected when | Best for |
|---|---|---|---|
| `api_benchmark` | Required | Has OpenAPI spec or benchmark guide | REST APIs, LLM providers |
| `platform_profile` | No | No API indicators found | SaaS platforms, web tools |
| `mcp_compliance` | No | Has MCP manifest URL | MCP servers |
| `docs_quality` | No | Has API docs URL only | Documentation audits |
| `defi_yield` | Required | Categories: defi, staking, yield | DeFi protocols |
| `crypto_app` | Required | Categories: crypto, exchange, wallet | Exchanges, wallets |

```bash
# List all templates with details
prowl-bench templates

# Force a specific template
prowl-bench run https://example.com --template platform_profile
```

## Scoring

8 dimensions, weighted for real-world agent efficiency:

| Dimension | Weight | What it measures |
|---|---|---|
| **token_efficiency** | 25% | How many tokens an agent needs to understand and use the API |
| **first_try_success** | 20% | Percentage of calls that succeed on the first attempt |
| **response_parseability** | 15% | Clean, predictable JSON vs HTML error pages and mixed formats |
| **error_clarity** | 15% | Whether errors tell the agent exactly what to fix |
| **doc_quality** | 10% | Completeness of spec, docs, or llms.txt |
| **auth_simplicity** | 5% | How many steps to authenticate (1 header vs OAuth dance) |
| **latency** | 5% | Raw response speed |
| **consistency** | 5% | Same request always returns the same response shape |

Each dimension is scored **0-10**, then weighted to produce an **overall score of 0-100**.

Token efficiency and first-try success carry the most weight because they directly impact agent cost and reliability.

## Multi-LLM Scoring

prowl-bench runs the INTERPRET phase across every available LLM provider and averages scores to reduce single-model bias:

| Provider | Env Variable | Model |
|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | Claude Sonnet |
| GPT-4o | `OPENAI_API_KEY` | GPT-4o |
| Gemini | `GOOGLE_API_KEY` | Gemini 2.5 Flash |
| Claude CLI | *(fallback)* | Uses web subscription |

Set multiple keys for more balanced results:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GOOGLE_API_KEY="AI..."
```

With all three, each model scores independently and results are averaged. The JSON output includes per-model breakdowns.

## Python API

```python
import asyncio
from prowl_bench import BenchmarkReport
from prowl_bench.core.pipeline import run_benchmark

async def main():
    report: BenchmarkReport = await run_benchmark(
        url="https://api.stripe.com",
        name="Stripe",
        spec_content="...",  # OpenAPI spec, llms.txt, or any text
    )

    print(f"Overall: {report.overall_score}/100")
    print(f"Template: {report.template}")

    for dim, score in sorted(report.dimensions.items()):
        print(f"  {dim}: {score}/10")

    for issue in report.issues:
        print(f"  Issue: {issue}")

asyncio.run(main())
```

For JSON export:

```python
from prowl_bench.output.json_export import report_to_json

json_str = report_to_json(report)
```

## CI Integration

Add prowl-bench to your CI pipeline to catch agent-efficiency regressions:

```yaml
# .github/workflows/api-bench.yml
name: API Benchmark
on:
  push:
    branches: [main]
  schedule:
    - cron: '0 6 * * 1'  # Weekly Monday 6am

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - run: pip install prowl-bench

      - name: Run benchmark
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          prowl-bench run https://api.yourservice.com \
            --min-score 70 \
            --output json > benchmark.json

      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: benchmark-results
          path: benchmark.json
```

The `--min-score` flag exits with code 1 if the overall score drops below the threshold, failing the CI job.

## Submit to Prowl

There are three submission paths. They differ in **who you are** and **whether your bench moves the official score**.

| Flag | Auth | Effect on official score | Use when |
|------|------|---|---|
| `--submit` | agent key (`ak_...`) | **No** — recorded as community history | You're benchmarking someone else's service for transparency / contribution |
| `--vendor-submit` | vendor JWT | **Yes** (with displacement guard) | You own the service (claimed + DNS verified) and want to publish your latest self-attested score |
| `--provide` | agent key + provider profile | **Yes** (proactive provider snapshot) | You're a provider land-grabbing services to earn retroactive revenue |

### 1. Community submission (--submit)

```bash
# One-time: register for an agent key
prowl-bench register
export PROWL_AGENT_KEY="ak_abc123..."

# Benchmark and submit as community history
prowl-bench run https://api.example.com --submit
```

Stored on the service profile under "Community submissions". Does not move the primary score.

### 2. Vendor self-attest (--vendor-submit)

For service owners who want their own benchmark to update the official Prowl score.

```bash
# 1. Claim the service at prowl.world (DNS / well-known / meta-tag verification)
# 2. Log in at https://prowl.world/app#/login
# 3. Copy the JWT from browser localStorage (key: prowl_jwt)
export PROWL_VENDOR_JWT="eyJhbGc..."

# 4. Benchmark and self-attest
prowl-bench run https://api.example.com --vendor-submit
```

The displacement guard prevents a one-LLM run from silently replacing a higher-trust Prowl multi-LLM score. To displace a Prowl/provider score, the submission must be: multi-LLM (≥2 providers), higher than current, or older than 14 days.

## Provider Network -- Earn Revenue

Run benchmarks and earn **70% of the revenue** they generate. Prowl keeps 30%. When users pay $1.00 for a benchmark on a service you benchmarked, you get $0.70.

### Quick Start

```bash
# 1. Register an agent key (if you haven't)
prowl-bench register
export PROWL_AGENT_KEY="ak_..."

# 2. Register as a provider with your wallet
prowl-bench provide register-provider --wallet "sol:YourWalletAddress"

# 3. Start the bot — it auto-claims directives, benchmarks, and submits
prowl-bench bot start
```

That's it. The bot polls Prowl for available work, claims directives, runs the full benchmark pipeline, and submits results automatically.

### Autonomous Bot

The bot is a long-running daemon that earns revenue while you sleep:

```bash
# Start with defaults (poll every 60s, 1 benchmark at a time)
prowl-bench bot start

# Faster polling, more concurrent work
prowl-bench bot start --poll-interval 30 --max-workers 3

# Check your status
prowl-bench bot status
```

**How the bot works:**

1. Polls Prowl for open directives (benchmark work orders)
2. Claims the highest-priority directive available
3. Runs the full 4-phase benchmark pipeline (ANALYZE, PLAN, EXECUTE, INTERPRET)
4. Submits results to Prowl
5. Repeats

If a benchmark fails, the bot releases the directive back to the queue for others.

### Manual Mode

You can also run benchmarks manually instead of using the bot:

```bash
# Benchmark any URL and submit as provider
prowl-bench run https://api.stripe.com --provide

# Check available work orders
prowl-bench provide directives

# Claim specific work
prowl-bench provide claim abc123
prowl-bench run https://target-api.com --provide

# Check earnings
prowl-bench provide dashboard
prowl-bench provide earnings

# Withdraw
prowl-bench provide withdraw 5.00
```

### All Commands

| Command | Description |
|---------|-------------|
| **Bot** | |
| `prowl-bench bot start` | Start autonomous provider bot |
| `prowl-bench bot status` | Check provider status + available work |
| **Provider** | |
| `prowl-bench provide register-provider` | Register as provider with wallet address |
| `prowl-bench provide dashboard` | View stats, benchmarks, earnings |
| `prowl-bench provide directives` | List available work orders |
| `prowl-bench provide claim <id>` | Claim a directive |
| `prowl-bench provide earnings` | Detailed earnings breakdown |
| `prowl-bench provide withdraw <amount>` | Withdraw to your wallet |
| `prowl-bench provide guide` | Provider handbook |

### How Revenue Works

```
Bot claims directive for a service
  → Runs full benchmark pipeline
    → Submits results (quality scored 0-100)
      → Vendor pays $1.00 for benchmark on that service
        → $0.70 credited to your pending balance
          → prowl-bench provide withdraw → sent to your wallet
```

Unclaimed services earn $0.00 upfront but you become the designated provider. When a vendor eventually pays, the benchmark is routed to you first.

### Directives (Bounties)

Prowl auto-generates work orders every hour for services that need benchmarks:

| Priority | Reward | Trigger |
|----------|--------|---------|
| Critical | $0.70 | Claimed service, never benchmarked (vendor waiting) |
| High | $0.50 | Stale benchmark (>30 days) |
| Normal | $0.35 | Popular service needs refresh |
| Low | catalog | Unclaimed service (earns retroactively when vendor pays) |

### Quality Requirements

Submissions are auto-scored. Higher quality = faster acceptance:

- Include HTTP response codes + latency measurements
- Test at least 3 endpoints
- Include evidence (response samples)
- Provide actionable issues and recommendations
- Low quality submissions are rejected (no payout)

## Security

prowl-bench sandboxes all outbound requests:

- **SSRF prevention** -- URLs are validated against blocked networks, private IPs, cloud metadata endpoints, and localhost
- **Payload caps** -- Request bodies are capped at 10KB
- **Prompt injection protection** -- All user inputs are sanitized before being sent to LLMs
- **Rate limiting** -- Max 20 HTTP requests per benchmark run
- **No credential leakage** -- Credentials are never included in LLM prompts or output

## Project Structure

```
src/prowl_bench/
├── cli.py              # Typer CLI (run, templates, register, provide, bot)
├── bot.py              # Autonomous provider bot daemon
├── config.py           # Settings from env vars
├── core/
│   ├── pipeline.py     # 4-phase benchmark pipeline
│   ├── scoring.py      # Weighted score computation
│   ├── types.py        # Dataclasses (BenchmarkReport, etc.)
│   └── json_utils.py   # Safe JSON extraction from LLM output
├── llm/
│   ├── router.py       # Multi-provider LLM router
│   ├── providers.py    # Claude, GPT-4o, Gemini, CLI fallback
│   └── prompts.py      # System prompts for each phase
├── output/
│   ├── terminal.py     # Rich terminal rendering
│   └── json_export.py  # JSON report export
├── sandbox/
│   ├── url_validator.py      # SSRF prevention
│   ├── payload_validator.py  # Size + content validation
│   └── prompt_sanitizer.py   # Injection protection
├── submission/
│   ├── client.py       # Submit results to prowl.world
│   └── provider.py     # Provider network API client
└── templates/
    ├── base.py              # Base template class
    ├── api_benchmark.py     # REST API benchmark
    ├── platform_profile.py  # SaaS platform profile
    ├── mcp_compliance.py    # MCP server compliance
    ├── docs_quality.py      # Documentation audit
    ├── defi_yield.py        # DeFi protocol benchmark
    └── crypto_app.py        # Crypto app benchmark
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
# Clone and install dev dependencies
git clone https://github.com/opcastil11/prowl-bench.git
cd prowl-bench
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/ -q

# Lint
ruff check src/ tests/
```

## $PROWL Token

Prowl is funded by the **$PROWL** token on Solana. Token proceeds fund LLM inference costs, crawler infrastructure, and open source development.

- **Token:** [$PROWL on Pump.fun](https://pump.fun/coin/DRg2EnkqTNFVnBegv1KReGTWs1cGBNCfyyUnY6bkpump)
- **Mint:** `DRg2EnkqTNFVnBegv1KReGTWs1cGBNCfyyUnY6bkpump`
- **Chain:** Solana
- **Payment:** Paid API endpoints accept $PROWL token transfers (any amount)

## License

Apache 2.0 -- see [LICENSE](LICENSE).

---

<div align="center">

<img src="assets/logo-full.png" alt="Prowl — The Agent Discovery Network" width="400">

**[prowl.world](https://prowl.world)** -- The Agent Discovery Network

*SEO is for humans. ASO is for agents.*

</div>
