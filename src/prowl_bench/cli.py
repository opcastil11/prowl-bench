"""CLI interface for prowl-bench."""
from __future__ import annotations

import asyncio
import json
import sys
import time

import typer
from rich.console import Console

app = typer.Typer(
    name="prowl-bench",
    help="Benchmark any API with standardized, multi-LLM scoring. Submit results to Prowl.",
    no_args_is_help=True,
)
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
    submit: bool = typer.Option(False, "--submit", "-s", help="Submit results to Prowl"),
    min_score: int | None = typer.Option(None, "--min-score", help="Exit non-zero if score below threshold (CI mode)"),
):
    """Benchmark a service URL."""
    asyncio.run(_run_benchmark(
        url=url, template=template, credential=credential, credential_type=credential_type,
        spec_file=spec_file, name=name, categories=categories, output=output,
        submit=submit, min_score=min_score,
    ))


async def _run_benchmark(
    url: str, template: str | None, credential: str | None, credential_type: str,
    spec_file: str | None, name: str | None, categories: str | None, output: str,
    submit: bool, min_score: int | None,
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
    """Register for a Prowl agent key (needed for --submit)."""
    asyncio.run(_register(name, model_provider, model_id))


async def _register(name: str, model_provider: str, model_id: str):
    import httpx
    from prowl_bench.config import get_config

    cfg = get_config()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{cfg.prowl_base_url}/v1/auth/agents/register",
            json={
                "agent_id": name,
                "model_provider": model_provider,
                "model_id": model_id,
            },
        )

        if resp.status_code == 200:
            data = resp.json()
            key = data.get("agent_key", data.get("key"))
            console.print(f"\n[green]Agent registered![/green]")
            console.print(f"\n  Agent key: [bold]{key}[/bold]")
            console.print(f"\n  Add to your environment:")
            console.print(f"    export PROWL_AGENT_KEY={key}")
            console.print(f"\n  Then use [bold]prowl-bench run --submit[/bold] to submit results.\n")
        else:
            console.print(f"\n[red]Registration failed: {resp.status_code} {resp.text[:200]}[/red]")


if __name__ == "__main__":
    app()
