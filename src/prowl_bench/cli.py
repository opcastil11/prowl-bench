"""CLI interface for prowl-bench."""
from __future__ import annotations

import asyncio
import json
import sys
import time

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="prowl-bench",
    help="Benchmark any API with standardized, multi-LLM scoring. Earn revenue as a provider.",
    no_args_is_help=True,
)
provide_app = typer.Typer(help="Provider network — benchmark services and earn revenue.")
bot_app = typer.Typer(help="Autonomous provider bot — auto-claim, benchmark, and submit directives.")
design_app = typer.Typer(
    help="Prowl Design — generate, score, and publish Mycelio manifests for your API.",
    no_args_is_help=True,
)
app.add_typer(provide_app, name="provide")
app.add_typer(bot_app, name="bot")
app.add_typer(design_app, name="design")

console = Console()


@app.command()
def run(
    url: str = typer.Argument(help="URL of the service to benchmark"),
    template: str | None = typer.Option(None, "--template", "-t", help="Template slug (auto-detected if omitted)"),
    credential: str | None = typer.Option(None, "--credential", "-c", help="API credential for the service"),
    credential_type: str = typer.Option("bearer_token", "--credential-type", help="Credential type: bearer_token, api_key, query_param"),
    spec_file: str | None = typer.Option(None, "--spec-file", help="Path to OpenAPI spec file"),
    name: str | None = typer.Option(None, "--name", "-n", help="Service name (derived from URL if omitted)"),
    categories: str | None = typer.Option(None, "--categories", help="Comma-separated categories"),
    output: str = typer.Option("terminal", "--output", "-o", help="Output format: terminal, json"),
    submit: bool = typer.Option(False, "--submit", "-s", help="Submit as community benchmark (does not change the official score)"),
    provide: bool = typer.Option(False, "--provide", "-p", help="Submit as provider benchmark (earn revenue, land-grab)"),
    vendor_submit: bool = typer.Option(False, "--vendor-submit", help="Submit as the verified service owner (changes the official score). Requires PROWL_VENDOR_JWT."),
    min_score: int | None = typer.Option(None, "--min-score", help="Exit non-zero if score below threshold (CI mode)"),
):
    """Benchmark a service URL."""
    asyncio.run(_run_benchmark(
        url=url, template=template, credential=credential, credential_type=credential_type,
        spec_file=spec_file, name=name, categories=categories, output=output,
        submit=submit, provide=provide, vendor_submit=vendor_submit, min_score=min_score,
    ))


async def _run_benchmark(
    url: str, template: str | None, credential: str | None, credential_type: str,
    spec_file: str | None, name: str | None, categories: str | None, output: str,
    submit: bool, provide: bool, vendor_submit: bool, min_score: int | None,
):
    from prowl_bench.core.pipeline import run_benchmark, fetch_spec, fetch_llms_txt
    from prowl_bench.output.terminal import print_report, print_phase
    from prowl_bench.output.json_export import report_to_json
    from prowl_bench.llm.router import get_available_providers

    providers = get_available_providers()
    if providers == ["claude_cli"]:
        console.print("[yellow]No LLM API keys found. Using Claude CLI fallback (slower).[/yellow]")
        console.print("[dim]Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY for faster results.[/dim]\n")

    # Derive name from URL if not provided
    if not name:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        name = parsed.hostname or url

    # Fetch spec content
    spec_content = ""
    if spec_file:
        with open(spec_file) as f:
            spec_content = f.read()
        print_phase("SPEC", f"Loaded from {spec_file}", "OK", 0)
    else:
        console.print(f"\n[dim]Benchmarking {url} ...[/dim]\n")
        start = time.monotonic()
        try:
            spec_content = await fetch_llms_txt(url) or ""
            if spec_content:
                print_phase("SPEC", "Fetched llms.txt", "OK", time.monotonic() - start)
        except Exception:
            pass

        if not spec_content:
            try:
                spec_content = await fetch_spec(url)
                print_phase("SPEC", "Fetched from URL", "OK", time.monotonic() - start)
            except Exception:
                spec_content = f"Service at {url}"
                print_phase("SPEC", "No spec found, using URL only", "OK", time.monotonic() - start)

    # Parse categories
    cat_list = [c.strip() for c in categories.split(",")] if categories else []

    # Run benchmark
    try:
        report = await run_benchmark(
            url=url, name=name, spec_content=spec_content,
            credential=credential, template_slug=template, categories=cat_list,
        )
    except Exception as exc:
        console.print(f"\n[red]Benchmark failed: {exc}[/red]")
        raise typer.Exit(1)

    # Output
    if output == "json":
        print(report_to_json(report))
    else:
        print_report(report)

    # Submit to Prowl
    if submit:
        try:
            from prowl_bench.submission.client import submit_to_prowl
            result = await submit_to_prowl(report)
            console.print(f"[green]Submitted to Prowl[/green] (trust weight: {result.get('trust_weight', '?')})")
            if result.get("profile_url"):
                console.print(f"[dim]Profile: {result['profile_url']}[/dim]")
        except Exception as exc:
            console.print(f"[red]Submission failed: {exc}[/red]")

    # Submit as provider benchmark
    if provide:
        try:
            from prowl_bench.submission.provider import submit_benchmark
            # Look up service ID from Prowl
            import httpx
            from prowl_bench.config import get_config
            cfg = get_config()
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{cfg.prowl_base_url}/v1/discover",
                    params={"q": name, "limit": 1},
                )
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if results:
                        service_id = results[0]["id"]
                        result = await submit_benchmark(service_id, report)
                        console.print(
                            f"[green]Provider benchmark accepted[/green] "
                            f"(quality: {result.get('quality_score', '?')}/100)"
                        )
                    else:
                        console.print("[yellow]Service not found in Prowl catalog. Use --submit first.[/yellow]")
                else:
                    console.print(f"[red]Could not look up service: {resp.status_code}[/red]")
        except Exception as exc:
            console.print(f"[red]Provider submission failed: {exc}[/red]")

    # Submit as the verified service owner (vendor self-attest)
    if vendor_submit:
        try:
            from prowl_bench.submission.client import submit_as_vendor
            result = await submit_as_vendor(report)
            console.print(
                f"[green]Vendor benchmark accepted[/green] "
                f"(score: {result.get('current_score', '?')}/100, "
                f"source: {result.get('benchmark_source', 'external')})"
            )
            if result.get("message"):
                console.print(f"[dim]{result['message']}[/dim]")
            if result.get("profile_url"):
                console.print(f"[dim]{result['profile_url']}[/dim]")
        except Exception as exc:
            console.print(f"[red]Vendor submission failed: {exc}[/red]")

    # CI mode: exit non-zero if below threshold
    if min_score is not None and report.overall_score < min_score:
        console.print(f"\n[red]Score {report.overall_score} is below threshold {min_score}[/red]")
        raise typer.Exit(1)


@app.command()
def templates():
    """List available benchmark templates."""
    from prowl_bench.templates import get_all_templates

    console.print("\n[bold]Available Templates[/bold]\n")
    for t in get_all_templates():
        creds = "[yellow]requires credentials[/yellow]" if t.requires_credentials else "[green]no credentials needed[/green]"
        console.print(f"  [bold]{t.slug}[/bold] -- {t.name}")
        console.print(f"    {t.description}")
        console.print(f"    {creds}")
        if t.category_hints:
            console.print(f"    [dim]Categories: {', '.join(t.category_hints)}[/dim]")
        console.print()


@app.command("mcp")
def mcp_check(
    url: str = typer.Argument(help="MCP server URL (the endpoint itself, or a base URL to search)"),
    output: str = typer.Option("terminal", "--output", "-o", help="Output format: terminal, json"),
    min_score: int | None = typer.Option(None, "--min-score", help="Exit non-zero if score below threshold (CI mode)"),
    timeout: float = typer.Option(20.0, "--timeout", help="Per-request timeout in seconds"),
    show_tools: bool = typer.Option(False, "--tools", help="List every discovered tool"),
):
    """Score an MCP server's agent-readiness. No LLM key required.

    Handshakes, lists tools, and grades what can be counted: schemas,
    descriptions, per-property docs, latency, `instructions`. Read-only — it
    never calls a tool, because `tools/call` has side effects that belong to
    whoever runs the server.

    The score is deterministic, so it is diffable in CI. Run
    `prowl-bench run` when you want the LLM's judgement instead.
    """
    asyncio.run(_mcp_check(url, output, min_score, timeout, show_tools))


async def _mcp_check(
    url: str, output: str, min_score: int | None, timeout: float, show_tools: bool
):
    from prowl_bench.mcp import resolve_endpoint, score_probe

    started = time.time()
    if output != "json":
        console.print(f"\n[bold]MCP conformance[/bold] — {url}\n")

    probe, attempts = await resolve_endpoint(url, timeout=timeout)
    report = score_probe(probe, attempts)

    if output == "json":
        print(json.dumps(report.as_dict(), indent=2))
    else:
        _print_mcp_report(report, probe, attempts, show_tools, time.time() - started)

    if min_score is not None and report.overall < min_score:
        console.print(
            f"[red]Score {report.overall} is below the --min-score threshold of {min_score}[/red]"
        )
        raise typer.Exit(1)
    if not report.reachable:
        raise typer.Exit(1)


def _print_mcp_report(report, probe, attempts, show_tools: bool, elapsed: float):
    # Resolution first: if we ended up somewhere other than where the caller
    # pointed us, that is the single most useful line in the output. Each line
    # carries the status code only — the full body excerpt is the same on every
    # failed guess, so repeating it five times buries the one that matters.
    if len(attempts) > 1:
        for attempt in attempts:
            hit = attempt.ok or attempt.status == "auth_required"
            mark = "[green]->[/green]" if hit else "[dim] x[/dim]"
            detail = f"http {attempt.http_status}" if attempt.http_status else (
                (attempt.error or "no answer").split(":")[-1].strip()[:60]
            )
            console.print(f"  {mark} {attempt.endpoint} [dim]({detail})[/dim]")
        console.print()

    if not report.reachable:
        console.print(f"[red]No MCP server answered at {report.endpoint}[/red]")
        console.print(f"[dim]{probe.error}[/dim]\n")
        for f in report.findings:
            if f.fix:
                console.print(f"  [dim]{f.fix}[/dim]")
        console.print()
        return

    server = report.server_name or "unnamed"
    version = f" v{report.server_version}" if report.server_version else ""
    console.print(f"  [bold]{server}{version}[/bold]  [dim]{report.endpoint}[/dim]")
    console.print(
        f"  [dim]protocol {report.protocol_version or '?'} · {report.framing} framing · "
        f"{'stateful' if report.stateful else 'stateless'} · {report.latency_ms}ms[/dim]\n"
    )

    color = "green" if report.overall >= 70 else "yellow" if report.overall >= 40 else "red"
    console.print(f"  Score: [{color}][bold]{report.overall}[/bold]/100[/{color}]\n")

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 2))
    for dim, value in report.dimensions.items():
        bar = "#" * int(value) + "." * (10 - int(value))
        table.add_row(dim.replace("_", " "), f"[dim]{bar}[/dim]", f"{value:.1f}/10")
    console.print(table)

    console.print(
        f"\n  Tools: [bold]{report.tool_count}[/bold] · "
        f"{report.documented_tools} documented · {report.schema_tools} with a schema"
    )

    if show_tools and probe.tools:
        console.print()
        for tool in probe.tools:
            props = (tool.input_schema or {}).get("properties") or {}
            flags = []
            if not tool.input_schema:
                flags.append("[red]no schema[/red]")
            if not (tool.description or "").strip():
                flags.append("[red]no description[/red]")
            suffix = f"  {' '.join(flags)}" if flags else ""
            console.print(f"    [bold]{tool.name}[/bold] [dim]({len(props)} args)[/dim]{suffix}")
            if tool.description:
                console.print(f"      [dim]{tool.description.strip()[:110]}[/dim]")

    if report.findings:
        console.print("\n  [bold]Findings[/bold]")
        for f in report.findings:
            sev_color = {"critical": "red", "high": "red", "medium": "yellow"}.get(f.severity, "dim")
            console.print(f"    [{sev_color}]{f.severity:<8}[/{sev_color}] {f.detail}")
            if f.fix:
                console.print(f"             [dim]{f.fix}[/dim]")

    console.print(f"\n  [dim]{elapsed:.1f}s · no LLM used[/dim]\n")


@app.command()
def register(
    name: str = typer.Option("prowl-bench-runner", help="Agent name"),
    model_provider: str = typer.Option("anthropic", help="Model provider"),
    model_id: str = typer.Option("claude-sonnet-4-20250514", help="Model ID"),
):
    """Register for a Prowl agent key (needed for --submit and provide)."""
    asyncio.run(_register(name, model_provider, model_id))


async def _register(name: str, model_provider: str, model_id: str):
    import httpx
    from prowl_bench.config import get_config

    cfg = get_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/auth/agents/register",
            json={
                "name": name,
                "model_provider": model_provider,
                "model_id": model_id,
                "environment": "production",
            },
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            key = data.get("agent_key", data.get("key"))
            console.print(f"\n[green]Agent registered![/green]")
            console.print(f"\n  Agent key: [bold]{key}[/bold]")
            console.print(f"\n  Add to your environment:")
            console.print(f"    export PROWL_AGENT_KEY={key}")
            console.print(f"\n  Then use [bold]prowl-bench run --submit[/bold] to submit results.")
            console.print(f"  Or [bold]prowl-bench provide register-provider[/bold] to earn revenue.\n")
        else:
            console.print(f"\n[red]Registration failed: {resp.status_code} {resp.text[:200]}[/red]")


# ── Provider subcommands ──────────────────────────────────────────────


@provide_app.command("register-provider")
def provide_register(
    wallet: str = typer.Option(..., "--wallet", "-w", help="Wallet address (e.g. sol:ABC123...)"),
    wallet_type: str = typer.Option("solana", "--wallet-type", help="Wallet type: solana, stellar, evm"),
):
    """Register as a benchmark provider to earn revenue."""
    asyncio.run(_provide_register(wallet, wallet_type))


async def _provide_register(wallet: str, wallet_type: str):
    from prowl_bench.submission.provider import register_provider
    try:
        data = await register_provider(wallet, wallet_type)
        console.print(f"\n[green]Provider registered![/green]")
        console.print(f"  Provider ID: {data['provider_id']}")
        console.print(f"  Revenue share: {data['revenue_share_pct']}%")
        console.print(f"  Wallet: {data['wallet_address']}")
        console.print(f"\n  Start earning: [bold]prowl-bench run https://api.example.com --provide[/bold]\n")
    except Exception as exc:
        console.print(f"\n[red]{exc}[/red]")


@provide_app.command("dashboard")
def provide_dashboard():
    """Show provider dashboard — earnings, benchmarks, contributions."""
    asyncio.run(_provide_dashboard())


async def _provide_dashboard():
    from prowl_bench.submission.provider import get_dashboard
    try:
        d = await get_dashboard()
        console.print(f"\n[bold]Provider Dashboard[/bold]\n")
        console.print(f"  Status:          {d['status']}")
        console.print(f"  Total benchmarks: {d['total_benchmarks']}")
        console.print(f"  Total earned:    ${d['total_earned_usd']:.2f}")
        console.print(f"  Pending payout:  ${d['pending_payout_usd']:.2f}")
        console.print(f"  Active work:     {d['active_directives']} directives")

        if d.get("recent_contributions"):
            console.print(f"\n  [bold]Recent Contributions[/bold]")
            table = Table(show_header=True, padding=(0, 1))
            table.add_column("Service", style="cyan")
            table.add_column("Quality", justify="right")
            table.add_column("Revenue", justify="right")
            table.add_column("Status")
            for c in d["recent_contributions"][:10]:
                table.add_row(
                    c.get("service_name", "?"),
                    str(c.get("quality_score", "?")),
                    f"${c.get('revenue_usd', 0):.2f}",
                    c.get("status", "?"),
                )
            console.print(table)
        console.print()
    except Exception as exc:
        console.print(f"\n[red]{exc}[/red]")


@provide_app.command("directives")
def provide_directives():
    """List available benchmark work orders."""
    asyncio.run(_provide_directives())


async def _provide_directives():
    from prowl_bench.submission.provider import get_directives
    try:
        directives = await get_directives()
        if not directives:
            console.print("\n[dim]No open directives right now. Use --provide on any benchmark to earn proactively.[/dim]\n")
            return
        console.print(f"\n[bold]Available Directives ({len(directives)})[/bold]\n")
        table = Table(show_header=True, padding=(0, 1))
        table.add_column("ID", style="dim", max_width=8)
        table.add_column("Priority", style="bold")
        table.add_column("Type")
        table.add_column("Reward", justify="right", style="green")
        table.add_column("Expires")
        for d in directives:
            table.add_row(
                d["id"][:8] + "...",
                d.get("priority", "?"),
                d.get("directive_type", "?"),
                f"${d.get('reward_usd', 0):.2f}",
                d.get("expires_at", "?")[:16] if d.get("expires_at") else "?",
            )
        console.print(table)
        console.print(f"\n  Claim: [bold]prowl-bench provide claim <directive-id>[/bold]\n")
    except Exception as exc:
        console.print(f"\n[red]{exc}[/red]")


@provide_app.command("claim")
def provide_claim(
    directive_id: str = typer.Argument(help="Directive ID to claim"),
):
    """Claim a benchmark directive."""
    asyncio.run(_provide_claim(directive_id))


async def _provide_claim(directive_id: str):
    from prowl_bench.submission.provider import claim_directive
    try:
        data = await claim_directive(directive_id)
        console.print(f"\n[green]Directive claimed![/green]")
        console.print(f"  Status: {data['status']}")
        console.print(f"\n  Now benchmark the service and submit with --provide\n")
    except Exception as exc:
        console.print(f"\n[red]{exc}[/red]")


@provide_app.command("earnings")
def provide_earnings():
    """Show detailed earnings breakdown."""
    asyncio.run(_provide_earnings())


async def _provide_earnings():
    from prowl_bench.submission.provider import get_earnings
    try:
        d = await get_earnings()
        console.print(f"\n[bold]Earnings[/bold]\n")
        console.print(f"  Revenue share: {d['revenue_share_pct']}%")
        console.print(f"  Total earned:  ${d['total_earned_usd']:.2f}")
        console.print(f"  Pending:       ${d['pending_payout_usd']:.2f}")

        if d.get("contributions"):
            console.print(f"\n  [bold]Contributions[/bold]")
            for c in d["contributions"][:10]:
                console.print(
                    f"    {c.get('service_name', '?'):30s} "
                    f"quality={c.get('quality_score', '?')} "
                    f"revenue=${c.get('revenue_generated_usd', 0):.2f} "
                    f"payout=${c.get('agent_payout_usd', 0):.2f}"
                )
        console.print()
    except Exception as exc:
        console.print(f"\n[red]{exc}[/red]")


@provide_app.command("withdraw")
def provide_withdraw(
    amount: float = typer.Argument(help="Amount in USD to withdraw"),
):
    """Withdraw earnings to your wallet."""
    asyncio.run(_provide_withdraw(amount))


async def _provide_withdraw(amount: float):
    from prowl_bench.submission.provider import withdraw
    try:
        data = await withdraw(amount)
        console.print(f"\n[green]Withdrawal queued![/green]")
        console.print(f"  Amount: ${data['requested_amount_usd']:.2f}")
        console.print(f"  To: {data['wallet_address']}")
        console.print(f"  Status: {data['status']}")
        console.print(f"  Remaining: ${data['remaining_pending_usd']:.2f}\n")
    except Exception as exc:
        console.print(f"\n[red]{exc}[/red]")


@provide_app.command("guide")
def provide_guide():
    """Show the provider handbook."""
    asyncio.run(_provide_guide())


async def _provide_guide():
    import httpx
    from prowl_bench.config import get_config
    cfg = get_config()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{cfg.prowl_base_url}/v1/provider/guide")
        if resp.status_code == 200:
            d = resp.json()
            console.print(f"\n[bold]{d['title']}[/bold]\n")
            console.print(f"  {d['overview']}\n")
            console.print(f"  [bold]Earning Structure[/bold]")
            for k, v in d.get("earning_structure", {}).items():
                console.print(f"    {k}: {v}")
            console.print(f"\n  [bold]Quality Criteria[/bold]")
            for item in d.get("quality_criteria", {}).get("scoring_factors", []):
                console.print(f"    - {item}")
            console.print(f"\n  [bold]Tips[/bold]")
            for tip in d.get("tips", []):
                console.print(f"    - {tip}")
            console.print()
        else:
            console.print(f"\n[red]Failed to fetch guide: {resp.status_code}[/red]")


# ── Bot subcommands ──────────────────────────────────────────────────


@bot_app.command("start")
def bot_start(
    poll_interval: int = typer.Option(60, "--poll-interval", "-i", help="Seconds between polls"),
    max_workers: int = typer.Option(1, "--max-workers", "-w", help="Max concurrent benchmarks per cycle"),
):
    """Start the autonomous provider bot.

    The bot polls Prowl for benchmark directives, claims them, runs the full
    benchmark pipeline, and submits results. Earn revenue when vendors pay
    for benchmarks on services you've covered.

    Requires PROWL_AGENT_KEY and provider registration.

    Example:
        prowl-bench bot start
        prowl-bench bot start --poll-interval 90 --max-workers 3
    """
    from prowl_bench.config import get_config
    cfg = get_config()
    if not cfg.prowl_agent_key:
        console.print("[red]PROWL_AGENT_KEY not set. Run: prowl-bench register[/red]")
        raise typer.Exit(1)

    from prowl_bench.bot import run_bot
    asyncio.run(run_bot(poll_interval=poll_interval, max_workers=max_workers))


@bot_app.command("status")
def bot_status():
    """Check provider status and available work."""
    asyncio.run(_bot_status())


async def _bot_status():
    from prowl_bench.submission.provider import get_dashboard, get_directives
    try:
        dashboard = await get_dashboard()
        claimed = await get_directives(status="claimed")
        open_dirs = await get_directives(status="open")

        console.print(f"\n[bold]Bot Status[/bold]\n")
        console.print(f"  Provider:       {dashboard['status']}")
        console.print(f"  Benchmarks:     {dashboard['total_benchmarks']}")
        console.print(f"  Earned:         ${dashboard['total_earned_usd']:.2f}")
        console.print(f"  Pending payout: ${dashboard['pending_payout_usd']:.2f}")
        console.print(f"  Claimed work:   {len(claimed)}")
        console.print(f"  Open work:      {len(open_dirs)}")
        console.print()
    except Exception as exc:
        console.print(f"\n[red]{exc}[/red]")


# ---------------------------------------------------------------------------
# `prowl-bench design` — Prowl Design subcommands
# ---------------------------------------------------------------------------


def _require_mycelio():
    """Import mycelio lazily and exit with install instructions if missing."""
    try:
        import mycelio  # noqa: F401
    except ImportError:
        console.print("[red]mycelio is not installed.[/red]")
        console.print("Install with: [cyan]pip install 'prowl-bench[design]'[/cyan]")
        raise typer.Exit(1)


@design_app.command()
def ui(
    port: int = typer.Option(
        4711,
        "--port",
        "-p",
        help="Local port to serve the UI on. Auto-bumps to the next free port if taken.",
    ),
    no_open: bool = typer.Option(
        False,
        "--no-open",
        help="Don't auto-launch a browser. Useful when running over SSH / headless.",
    ),
):
    """Launch the Prowl Design web UI locally.

    Serves the same Postman-for-LLMs UI you'd find at design.prowl.world,
    but from your machine. Your collection stays local; only the
    "Score this endpoint" button calls out (to Prowl's hosted multi-LLM
    scorer, with your daily free-tier credits).
    """
    from prowl_bench.design.server import find_open_port, serve

    try:
        actual_port = find_open_port(start=port)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    if actual_port != port:
        console.print(f"[yellow]Port {port} taken — using {actual_port} instead.[/yellow]")

    try:
        serve(port=actual_port, open_browser=not no_open)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


def _parse_hex_pubkey(flag: str, value: str) -> bytes:
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        console.print(f"[red]{flag} must be hex (64 chars).[/red]")
        raise typer.Exit(1)
    if len(raw) != 32:
        console.print(f"[red]{flag} must be 32 bytes ({len(raw)} given).[/red]")
        raise typer.Exit(1)
    return raw


@design_app.command()
def manifest(
    source: str = typer.Argument(
        ..., help="OpenAPI 3.x spec: an https:// URL or a local file path."
    ),
    vendor_pubkey: str | None = typer.Option(
        None,
        "--vendor-pubkey",
        help="Vendor Ed25519 public key as hex (64 chars). If omitted, an "
        "ephemeral keypair is generated and the seed is written next to the "
        "manifest. Ephemeral keys are for testing only — production manifests "
        "should use your real offline-protected key.",
    ),
    directory_pubkey: str | None = typer.Option(
        None,
        "--directory-pubkey",
        envvar="PROWL_DIRECTORY_PUBKEY",
        help="Directory Ed25519 pubkey (hex). Defaults to $PROWL_DIRECTORY_PUBKEY. "
        "If neither is set, an ephemeral key is used — the resulting manifest "
        "will NOT be valid against the real Prowl directory.",
    ),
    slug: str | None = typer.Option(
        None,
        "--slug",
        help="Override the manifest slug. Default: derived from info.title.",
    ),
    output: str = typer.Option(
        "-",
        "--output",
        "-o",
        help="Path to write the unsigned manifest binary. '-' (default) prints "
        "a hex-encoded preview to stdout instead.",
    ),
    sign: bool = typer.Option(
        False,
        "--sign",
        help="Vendor-sign the manifest with the generated ephemeral key. "
        "Only valid when --vendor-pubkey is NOT given. For real signing, "
        "encode your manifest unsigned (default), then sign the bytes "
        "offline with your own Ed25519 implementation.",
    ),
):
    """Generate a Mycelio manifest from an OpenAPI 3.x spec.

    By default emits an UNSIGNED manifest. The vendor's normal flow is:

    \b
        prowl-bench design manifest stripe.json --vendor-pubkey <hex> -o stripe.myc.unsigned
        # ... sign stripe.myc.unsigned offline with your Ed25519 key ...
        # ... submit the signed bytes to prowl.world for co-signing ...

    For testing, omit --vendor-pubkey to have an ephemeral keypair generated
    for you. Add --sign to also vendor-sign with that ephemeral key.
    """
    _require_mycelio()
    from mycelio.codegen import CodegenError, manifest_from_openapi
    from mycelio.crypto import generate_keypair
    from mycelio.manifest import (
        encode_unsigned_manifest,
        encode_vendor_signed_manifest,
        sign_vendor,
    )

    # Vendor pubkey: real or ephemeral
    vendor_seed: bytes | None = None
    if vendor_pubkey:
        vendor_pub = _parse_hex_pubkey("--vendor-pubkey", vendor_pubkey)
        if sign:
            console.print(
                "[red]--sign can only be used when generating an ephemeral keypair "
                "(don't pass --vendor-pubkey).[/red]"
            )
            raise typer.Exit(1)
    else:
        console.print(
            "[yellow]No --vendor-pubkey given — generating an ephemeral keypair "
            "(testing only).[/yellow]"
        )
        vendor_seed, vendor_pub = generate_keypair()

    # Directory pubkey: real, env, or ephemeral
    if directory_pubkey:
        dir_pub = _parse_hex_pubkey("--directory-pubkey", directory_pubkey)
    else:
        console.print(
            "[yellow]No --directory-pubkey given — generating an ephemeral key. "
            "The manifest's service_id will NOT match the real Prowl directory.[/yellow]"
        )
        _, dir_pub = generate_keypair()

    # Codegen
    try:
        m = manifest_from_openapi(
            source,
            vendor_pubkey=vendor_pub,
            directory_pubkey=dir_pub,
            slug=slug,
        )
    except CodegenError as exc:
        console.print(f"[red]Codegen failed: {exc}[/red]")
        raise typer.Exit(1)

    # Optional vendor signing (only with the ephemeral key)
    if sign:
        assert vendor_seed is not None  # checked above
        sign_vendor(m, vendor_seed)
        body = encode_vendor_signed_manifest(m)
        body_label = "vendor-signed (no directory countersignature yet)"
    else:
        body = encode_unsigned_manifest(m)
        body_label = "unsigned"

    # Summary table
    table = Table(title=f"Mycelio manifest ({body_label})", show_header=False)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("slug", m.slug)
    table.add_row("service_id", m.service_id.hex())
    table.add_row("backend_url", m.backend_url)
    table.add_row("auth_header", m.auth_header or "(none)")
    table.add_row("auth_prefix", m.auth_prefix or "(none)")
    table.add_row("ops", str(len(m.ops)))
    table.add_row("bytes", str(len(body)))
    console.print(table)

    # Output the bytes
    if output == "-":
        console.print(f"\n[dim]hex ({len(body)} bytes):[/dim]\n{body.hex()}")
    else:
        from pathlib import Path

        out_path = Path(output)
        out_path.write_bytes(body)
        console.print(f"\n[green]✓ Wrote {len(body)} bytes to {out_path}[/green]")

    # If we generated keys, write the seeds so the user can recover
    if vendor_seed is not None:
        seed_path = (
            output + ".vendor_seed"
            if output != "-"
            else "mycelio-vendor-seed.bin"
        )
        from pathlib import Path

        Path(seed_path).write_bytes(vendor_seed)
        console.print(
            f"[yellow]Saved ephemeral vendor seed to {seed_path} — keep this "
            f"if you want to re-sign or recover the manifest.[/yellow]"
        )


def _load_inline_spec(source: str) -> tuple[dict, str]:
    """Load a spec from URL or file path. Returns (parsed_dict, raw_text)."""
    import httpx
    from pathlib import Path

    if source.startswith(("http://", "https://")):
        with httpx.Client(timeout=15.0, follow_redirects=True) as c:
            r = c.get(source, headers={"Accept": "application/json, application/yaml, */*"})
            r.raise_for_status()
            text = r.text
    else:
        text = Path(source).read_text(encoding="utf-8")
    try:
        return json.loads(text), text
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:
            raise typer.BadParameter(
                "Spec is not valid JSON and PyYAML is not installed. "
                "Run: pip install pyyaml"
            ) from exc
        out = yaml.safe_load(text)
        if not isinstance(out, dict):
            raise typer.BadParameter("Spec must parse to a top-level object")
        return out, text


def _heuristic_review(spec: dict) -> dict:
    """Pure local, no LLM. Counts ops, picks the first auth scheme, eyeballs
    the spec for the obvious red flags Prowl's hosted scorer would catch
    in detail. Returns a small dict the CLI prints as a Table."""
    methods = ("get", "post", "put", "patch", "delete", "head", "options")
    op_count = 0
    deep_response_shapes = 0
    for item in (spec.get("paths") or {}).values():
        if not isinstance(item, dict):
            continue
        for m in methods:
            if m in item:
                op_count += 1
                responses = (item[m] or {}).get("responses") or {}
                # Cheap nesting check: any 200 response with 3+ levels of
                # nested schema properties is flagged as bloat-prone.
                ok = responses.get("200") or responses.get("default") or {}
                content = (ok.get("content") or {}).get("application/json") or {}
                schema = content.get("schema") or {}

                def _depth(node, d=0):
                    if not isinstance(node, dict) or d > 5:
                        return d
                    props = node.get("properties") or {}
                    return max((_depth(v, d + 1) for v in props.values()), default=d)

                if _depth(schema) >= 3:
                    deep_response_shapes += 1

    schemes = (spec.get("components") or {}).get("securitySchemes") or {}
    auth_str = "(none declared)"
    if schemes:
        first = next(iter(schemes.values()))
        if isinstance(first, dict):
            t = first.get("type")
            if t == "http":
                auth_str = f"http {first.get('scheme', '?')}"
            elif t == "apiKey":
                auth_str = f"apiKey ({first.get('in', '?')}: {first.get('name', '?')})"
            elif t == "oauth2":
                auth_str = f"oauth2 ({', '.join((first.get('flows') or {}).keys()) or 'flow?'})"
            else:
                auth_str = str(t)

    servers = spec.get("servers") or []
    backend = servers[0]["url"] if servers and isinstance(servers[0], dict) else "(none)"

    return {
        "ops": op_count,
        "auth": auth_str,
        "backend": backend,
        "deep_response_shapes": deep_response_shapes,
        "title": (spec.get("info") or {}).get("title") or "(untitled)",
    }


@design_app.command()
def review(
    source: str = typer.Argument(..., help="OpenAPI 3.x spec: an https:// URL or a local file path."),
    cloud: bool = typer.Option(
        False, "--cloud",
        help="Score via Prowl's hosted multi-LLM endpoint (POST /v1/endpoint/review). "
             "Falls into the 3/day anon free tier without auth; 10/day with PROWL_VENDOR_JWT.",
    ),
    target: str | None = typer.Option(
        None, "--target", help="Focus the cloud review on one operation: '<METHOD> <path>'.",
    ),
    api_base: str | None = typer.Option(
        None, "--api-base", envvar="PROWL_BASE_URL",
        help="Override the Prowl API base URL (default: https://prowl.world).",
    ),
    vendor_jwt: str | None = typer.Option(
        None, "--vendor-jwt", envvar="PROWL_VENDOR_JWT",
        help="Vendor JWT for the logged-in free tier (10/day). From localStorage.prowl_jwt in the dashboard.",
    ),
):
    """Score an OpenAPI spec for agent-readiness.

    \b
    Local mode (default): pure heuristic — op count, auth shape, response
      nesting depth. No LLM, no network call. Free, fast, offline.
    --cloud: send the spec to Prowl's hosted scorer (multi-LLM static
      review). Returns dimension scores + concrete rewrite suggestions.
      3 anon reviews/day per IP for free; 10/day with --vendor-jwt.

    Examples:
        prowl-bench design review stripe.json
        prowl-bench design review https://api.you/openapi.json --cloud
        prowl-bench design review spec.yaml --cloud --target "POST /charges"
    """
    spec, raw_text = _load_inline_spec(source)
    summary = _heuristic_review(spec)

    table = Table(title=f"Heuristic review — {summary['title']}", show_header=False)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("ops", str(summary["ops"]))
    table.add_row("backend", summary["backend"])
    table.add_row("auth", summary["auth"])
    table.add_row("deeply-nested responses", str(summary["deep_response_shapes"]))
    console.print(table)

    if not cloud:
        console.print("\n[dim]Pass --cloud for a multi-LLM scorecard ($0.02, 3/day free anon).[/dim]")
        return

    # Cloud path.
    import httpx
    base = (api_base or "https://prowl.world").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if vendor_jwt:
        headers["Authorization"] = f"Bearer {vendor_jwt}"
    payload: dict = {"spec_inline": raw_text}
    if target:
        payload["target_endpoint"] = target

    console.print(f"\n[dim]POST {base}/v1/endpoint/review — multi-LLM scoring, ~3-8s...[/dim]")
    try:
        with httpx.Client(timeout=60.0) as c:
            r = c.post(f"{base}/v1/endpoint/review", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        console.print(f"[red]Network error: {exc}[/red]")
        raise typer.Exit(1)
    if r.status_code == 402:
        tier = "logged-in (10/day)" if vendor_jwt else "anonymous (3/day)"
        challenge = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        usd = float(challenge.get("accepts", [{}])[0].get("maxAmountRequired", 0)) / 1e6
        console.print(
            f"[red]Free tier exhausted ({tier}). Re-run via x402 (${usd:.2f}) or wait until UTC midnight.[/red]"
        )
        raise typer.Exit(2)
    if not r.is_success:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        console.print(f"[red]Review failed (HTTP {r.status_code}): {detail}[/red]")
        raise typer.Exit(1)

    data = r.json()
    score_table = Table(title=f"Cloud review · {data.get('tier', '?')} · ${data.get('cost_usd', 0):.2f}")
    score_table.add_column("dimension", style="cyan")
    score_table.add_column("score", justify="right")
    score_table.add_row("[bold]overall[/bold]", f"[bold]{data.get('overall', 0):.1f}/10[/bold]")
    for k in ("parseability", "auth_simplicity", "error_clarity", "schema_gotchas", "token_bloat"):
        v = (data.get("dimensions") or {}).get(k)
        score_table.add_row(k, f"{v:.1f}" if isinstance(v, (int, float)) else "—")
    console.print(score_table)

    suggestions = data.get("suggestions") or []
    if suggestions:
        console.print(f"\n[cyan]Suggestions ({len(suggestions)}):[/cyan]")
        for i, s in enumerate(suggestions, 1):
            tag = s.get("tag", "OTHER")
            op = s.get("op", "")
            title = s.get("title", "")
            detail = s.get("detail")
            savings = s.get("token_savings_pct")
            console.print(f"  {i}. [yellow]{tag}[/yellow] {op}  [white]{title}[/white]")
            if detail:
                console.print(f"     [dim]{detail}[/dim]")
            if savings:
                console.print(f"     [green]~{savings:.0f}% token savings[/green]")
    else:
        console.print("\n[green]No rewrites flagged — spec reads cleanly to both LLMs.[/green]")


@design_app.command()
def test(
    target_url: str = typer.Argument(..., help="Live URL to hit (the actual endpoint, e.g. https://api.example.com/v1/items)."),
    spec: str | None = typer.Option(
        None, "--spec", help="OpenAPI 3.x spec for context (URL or file path). Required with --cloud.",
    ),
    method: str = typer.Option("GET", "--method", "-X", help="HTTP method."),
    cloud: bool = typer.Option(
        False, "--cloud",
        help="Score via Prowl's hosted live-test endpoint (POST /v1/endpoint/test, $0.05). "
             "No anon free tier — requires --vendor-jwt or x402.",
    ),
    api_base: str | None = typer.Option(
        None, "--api-base", envvar="PROWL_BASE_URL",
        help="Override the Prowl API base URL (default: https://prowl.world).",
    ),
    vendor_jwt: str | None = typer.Option(
        None, "--vendor-jwt", envvar="PROWL_VENDOR_JWT", help="Vendor JWT for billing.",
    ),
):
    """Hit a URL and (optionally) score the response against its spec.

    \b
    Local mode (default): one HTTP GET, prints status + latency + size +
      a short body preview. Free, no LLM, no network call to Prowl.
    --cloud: send target_url + spec to Prowl's hosted live-test scorer.
      Multi-LLM compares declared response shape vs. what came back.

    Examples:
        prowl-bench design test https://api.you/v1/items
        prowl-bench design test https://api.you/v1/items --spec stripe.json --cloud
    """
    import httpx
    if not method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        console.print(f"[red]Unsupported method: {method}[/red]")
        raise typer.Exit(1)

    # Local probe.
    t0 = time.time()
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as c:
            r = c.request(method.upper(), target_url)
    except httpx.HTTPError as exc:
        console.print(f"[red]Probe failed: {exc}[/red]")
        raise typer.Exit(1)
    dur_ms = int((time.time() - t0) * 1000)
    body_bytes = len(r.content or b"")

    color = "green" if 200 <= r.status_code < 300 else ("yellow" if 300 <= r.status_code < 500 else "red")
    table = Table(title=f"Live probe — {method.upper()} {target_url}", show_header=False)
    table.add_column(style="cyan")
    table.add_column()
    table.add_row("status", f"[{color}]{r.status_code} {r.reason_phrase}[/{color}]")
    table.add_row("latency", f"{dur_ms} ms")
    table.add_row("size", f"{body_bytes} B")
    table.add_row("content-type", r.headers.get("content-type", "(none)"))
    console.print(table)

    preview = (r.text or "")[:400]
    if preview:
        console.print(f"\n[dim]body preview ({len(preview)} chars):[/dim]\n{preview}")

    if not cloud:
        console.print("\n[dim]Pass --cloud --spec <spec> for a multi-LLM scored test ($0.05).[/dim]")
        return

    if not spec:
        console.print("[red]--cloud requires --spec (URL or file path of the OpenAPI spec).[/red]")
        raise typer.Exit(1)
    _, raw_text = _load_inline_spec(spec)

    base = (api_base or "https://prowl.world").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if vendor_jwt:
        headers["Authorization"] = f"Bearer {vendor_jwt}"
    payload = {
        "spec_inline": raw_text,
        "target_url": target_url,
        "method": method.upper(),
    }
    console.print(f"\n[dim]POST {base}/v1/endpoint/test — multi-LLM scored test, ~4-10s...[/dim]")
    try:
        with httpx.Client(timeout=120.0) as c:
            cr = c.post(f"{base}/v1/endpoint/test", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        console.print(f"[red]Network error: {exc}[/red]")
        raise typer.Exit(1)
    if cr.status_code == 402:
        console.print("[red]Live test requires payment — no free tier on /v1/endpoint/test. Pass --vendor-jwt or pay via x402.[/red]")
        raise typer.Exit(2)
    if not cr.is_success:
        try:
            detail = cr.json().get("detail", cr.text)
        except Exception:
            detail = cr.text
        console.print(f"[red]Test failed (HTTP {cr.status_code}): {detail}[/red]")
        raise typer.Exit(1)

    data = cr.json()
    out = Table(title=f"Cloud live-test · ${data.get('cost_usd', 0):.2f}")
    out.add_column("dimension", style="cyan")
    out.add_column("score", justify="right")
    out.add_row("[bold]overall[/bold]", f"[bold]{data.get('overall', 0):.1f}/10[/bold]")
    for k in ("parseability", "auth_simplicity", "error_clarity", "schema_gotchas", "token_bloat"):
        v = (data.get("dimensions") or {}).get(k)
        out.add_row(k, f"{v:.1f}" if isinstance(v, (int, float)) else "—")
    out.add_row("probe_status", str(data.get("probe_status", "?")))
    console.print(out)


@design_app.command()
def run(
    source: str = typer.Argument(..., help="OpenAPI spec: an https:// URL or a local file path."),
    target_base_url: str | None = typer.Option(
        None, "--base-url",
        help="Override the spec's servers[0].url (required for specs without one).",
    ),
    bearer: str | None = typer.Option(
        None, "--bearer", envvar="PROWL_TARGET_BEARER",
        help="Bearer token forwarded to the target server for auth.",
    ),
    api_key_name: str = typer.Option(
        "X-API-Key", "--api-key-name",
        help="Header name for apiKey auth (default X-API-Key).",
    ),
    api_key_value: str | None = typer.Option(
        None, "--api-key", envvar="PROWL_TARGET_API_KEY",
        help="API key value forwarded to the target server.",
    ),
    include_destructive: bool = typer.Option(
        False, "--destructive",
        help="Also execute POST/PUT/PATCH/DELETE ops. Default false for safety.",
    ),
    max_ops: int = typer.Option(50, "--max-ops", min=1, max=50, help="Hard-capped at 50 server-side."),
    parallelism: int = typer.Option(5, "--parallelism", min=1, max=10),
    api_base: str | None = typer.Option(
        None, "--api-base", envvar="PROWL_BASE_URL",
        help="Override the Prowl API base URL (default: https://prowl.world).",
    ),
    vendor_jwt: str | None = typer.Option(
        None, "--vendor-jwt", envvar="PROWL_VENDOR_JWT",
        help="Vendor JWT for the logged-in free tier (10/day).",
    ),
    payment_proof: str | None = typer.Option(
        None, "--payment-proof", envvar="PROWL_PAYMENT_PROOF",
        help="x402 proof (sol:<sig>) when paying instead of using free tier.",
    ),
):
    """Run a third-party LLM evaluation against your live API.

    \b
    Smart runner. Reads your spec, classifies every operation
    (runnable / needs-creds / destructive / unrunnable), executes the
    safe ones against the real target, an LLM evaluates each
    endpoint's live behavior, and produces a cross-cutting synthesis
    with prioritized P0/P1/P2 fixes.

    Streams events live as they happen — same SSE source as
    design.prowl.world. $0.50 hosted; free 10/day with --vendor-jwt.

    Examples:
        prowl-bench design run https://api.you/openapi.json --bearer sk_live_abc
        prowl-bench design run spec.json --base-url https://staging.api.you --destructive
        prowl-bench design run spec.json --api-key tk_xyz --max-ops 20
    """
    asyncio.run(_run_design(
        source, target_base_url, bearer, api_key_name, api_key_value,
        include_destructive, max_ops, parallelism,
        api_base, vendor_jwt, payment_proof,
    ))


async def _run_design(
    source: str,
    target_base_url: str | None,
    bearer: str | None,
    api_key_name: str,
    api_key_value: str | None,
    include_destructive: bool,
    max_ops: int,
    parallelism: int,
    api_base: str | None,
    vendor_jwt: str | None,
    payment_proof: str | None,
) -> None:
    """Open the SSE stream, parse events, render to terminal."""
    import httpx

    _spec_obj, raw_text = _load_inline_spec(source)
    base = (api_base or "https://prowl.world").rstrip("/")
    url = f"{base}/v1/design/run"

    payload: dict = {"spec_inline": raw_text, "max_ops": max_ops, "parallelism": parallelism}
    if target_base_url:
        payload["target_base_url"] = target_base_url
    if include_destructive:
        payload["include_destructive"] = True
    creds: dict = {}
    if bearer:
        creds["bearer_token"] = bearer
    if api_key_value:
        creds["api_key"] = {"header_name": api_key_name, "value": api_key_value}
    if creds:
        payload["credentials"] = creds

    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if vendor_jwt:
        headers["Authorization"] = f"Bearer {vendor_jwt}"
    if payment_proof:
        headers["X-Payment-Proof"] = payment_proof

    # State accumulated as events stream in — kept so we can print
    # a final summary table at the end without re-fetching.
    state = {
        "format_detected": None,
        "ops_count": 0,
        "buckets": {},
        "ops_done": 0,
        "ops_total_runnable": 0,
        "verdicts": [],
        "synthesis": {"headline": "", "paragraphs": [], "actions": [], "tags": [], "overall": None},
        "cost_usd": 0.0,
    }

    console.print(f"\n[dim]POST {url} (text/event-stream)...[/dim]\n")

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code == 402:
                    tier = "logged-in (10/day)" if vendor_jwt else "anonymous (3/day)"
                    console.print(
                        f"[red]Free tier exhausted ({tier}). Pass --payment-proof or wait until UTC midnight.[/red]"
                    )
                    raise typer.Exit(2)
                if not resp.is_success:
                    body = await resp.aread()
                    console.print(f"[red]HTTP {resp.status_code}: {body.decode(errors='ignore')[:200]}[/red]")
                    raise typer.Exit(1)

                event = "message"
                buf_data = ""
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        if event and buf_data:
                            try:
                                payload_obj = json.loads(buf_data)
                            except Exception:
                                payload_obj = {"_raw": buf_data}
                            _render_design_event(event, payload_obj, state, console)
                        event = "message"
                        buf_data = ""
                        continue
                    if raw_line.startswith("event:"):
                        event = raw_line[6:].strip()
                    elif raw_line.startswith("data:"):
                        buf_data += raw_line[5:].strip()
    except httpx.HTTPError as exc:
        console.print(f"[red]Network error: {exc}[/red]")
        raise typer.Exit(1)


def _render_design_event(event: str, data: dict, state: dict, c: Console) -> None:
    """One terminal line (or block) per SSE event. Same UX as the
    browser landing's Live tab, just in your shell."""
    if event == "normalize.done":
        state["format_detected"] = data.get("format_detected")
        state["ops_count"] = data.get("ops_count", 0)
        c.print(f"[dim]→[/dim] format: [cyan]{state['format_detected']}[/cyan]  ops: [cyan]{state['ops_count']}[/cyan]")
    elif event == "inspect.done":
        b = data.get("buckets") or {}
        state["buckets"] = b
        state["ops_total_runnable"] = b.get("runnable_now", 0)
        c.print(
            f"[dim]→[/dim] buckets: "
            f"[green]{b.get('runnable_now',0)} runnable[/green]"
            + (f"  [yellow]{b.get('needs_creds',0)} need-creds[/yellow]" if b.get('needs_creds') else "")
            + (f"  [yellow]{b.get('destructive',0)} destructive[/yellow]" if b.get('destructive') else "")
            + (f"  [dim]{b.get('unrunnable',0)} unrunnable[/dim]" if b.get('unrunnable') else "")
        )
        c.print()
    elif event == "op.start":
        c.print(f"  [dim]···[/dim] {data.get('op','')}", end="\r")
    elif event == "op.done":
        status = data.get("status")
        lat = data.get("latency_ms", 0)
        size = data.get("response_bytes", 0)
        if status is None:
            color = "red"
            status_str = "×"
        elif status < 300:
            color = "green"
            status_str = str(status)
        elif status < 400:
            color = "white"
            status_str = str(status)
        else:
            color = "red"
            status_str = str(status)
        state["ops_done"] += 1
        c.print(f"  [{color}]{status_str:>3}[/{color}] {data.get('op','')}  [dim]{lat}ms · {size/1024:.1f}KB[/dim]")
        warns = data.get("schema_warnings") or []
        if warns:
            c.print(f"        [yellow]⚠ {'; '.join(warns)}[/yellow]")
        err = data.get("error")
        if err:
            c.print(f"        [red]{err}[/red]")
    elif event == "op.evaluated":
        sc = data.get("score") or 0
        verdict = data.get("verdict_text") or ""
        tags = [t for t in (data.get("issue_tags") or []) if t and t != "OK"]
        suggs = data.get("suggestions") or []
        sc_color = "green" if sc >= 8 else ("yellow" if sc >= 6 else "red")
        tag_str = "  ".join(f"[{sc_color}]{t}[/{sc_color}]" for t in tags) if tags else ""
        c.print(f"        [{sc_color}]{sc:.1f}/10[/{sc_color}]  {tag_str}")
        c.print(f"        [dim italic]{verdict}[/dim italic]")
        for s in suggs:
            c.print(f"        [dim]→[/dim] {s}")
        state["verdicts"].append(data)
        c.print()
    elif event == "chain.discovered":
        keys = list((data.get("values") or {}).keys())
        c.print(f"  [magenta]⛓ chain discovered {data.get('count', 0)} values:[/magenta] [dim]{', '.join(keys[:6])}{'...' if len(keys)>6 else ''}[/dim]\n")
    elif event == "chain.retry_planned":
        ops = data.get("ops") or []
        via = data.get("via_accumulator") or []
        c.print(f"  [magenta]⛓ retrying {len(ops)} previously-unrunnable ops via {', '.join(via[:3])}[/magenta]\n")
    elif event == "execution.done":
        ran = data.get("ran", 0)
        ok = data.get("succeeded", 0)
        fail = data.get("failed", 0)
        p50 = data.get("p50_latency_ms")
        p95 = data.get("p95_latency_ms")
        err_rate = data.get("error_rate", 0.0)
        c.print()
        c.print(f"[dim]→[/dim] execution: [green]{ok}[/green]/{ran} ok  [red]{fail}[/red] failed  "
                f"p50 [cyan]{p50}ms[/cyan]  p95 [cyan]{p95}ms[/cyan]  err [yellow]{err_rate*100:.0f}%[/yellow]")
        if data.get("chained"):
            c.print(f"[dim]   chained: {data['chained']} ops ran via response-derived values[/dim]")
    elif event == "audit.done":
        c.print(f"[dim]→[/dim] static audit: [cyan]{(data.get('overall') or 0):.1f}/10[/cyan]  "
                f"{len(data.get('suggestions') or [])} suggestions")
    elif event == "agent.synthesis.start":
        c.print()
        c.print("[bold cyan]── Third-party verdict ──[/bold cyan]")
    elif event == "agent.synthesis.headline":
        c.print(f"\n[italic white]{data.get('text','')}[/italic white]\n")
    elif event == "agent.synthesis.paragraph":
        c.print(f"  {data.get('text','')}\n")
    elif event == "agent.synthesis.action":
        prio = data.get("priority", "P?")
        op = data.get("op", "")
        act = data.get("action", "")
        color = {"P0": "red", "P1": "yellow", "P2": "white"}.get(prio, "white")
        op_str = f"[dim]({op})[/dim] " if op and op != "(spec-wide)" else ""
        c.print(f"  [{color}][{prio}][/{color}]  {op_str}{act}")
        state["synthesis"]["actions"].append(data)
    elif event == "agent.synthesis.tag":
        state["synthesis"]["tags"].append(data.get("name", ""))
    elif event == "agent.synthesis.overall":
        sc = data.get("score")
        if sc is not None:
            color = "green" if sc >= 8 else ("yellow" if sc >= 6 else "red")
            c.print(f"\n[bold {color}]Overall: {sc:.1f}/10[/bold {color}]")
            if state["synthesis"]["tags"]:
                c.print(f"[dim]recurring: {', '.join(state['synthesis']['tags'])}[/dim]")
    elif event == "agent.summary":
        # Streaming variant already painted this — only the final
        # snapshot info (cost, evaluated count) is added here.
        pass
    elif event == "persist.done":
        state["cost_usd"] = data.get("cost_usd", 0.0)
        c.print(f"\n[dim]review_id: {data.get('review_id','')}  ·  ${data.get('cost_usd',0):.2f}  ·  {data.get('duration_ms',0)}ms[/dim]\n")
    elif event == "error":
        c.print(f"\n[red]stream error: {data.get('message','')}[/red]")


if __name__ == "__main__":
    app()
