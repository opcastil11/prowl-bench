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


if __name__ == "__main__":
    app()
