"""Autonomous provider bot — polls for directives, benchmarks services, submits results.

Usage:
    prowl-bench bot start                    # run the bot
    prowl-bench bot start --max-workers 3    # allow 3 concurrent benchmarks
    prowl-bench bot start --poll-interval 90 # poll every 90 seconds
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone

from rich.console import Console

from prowl_bench.config import get_config

log = logging.getLogger("prowl_bench.bot")
console = Console()

PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}

_running = True


def _shutdown(sig, frame):
    global _running
    _running = False
    console.print("\n[yellow]Shutting down after current work completes...[/yellow]")


async def _fetch_spec_for_directive(directive: dict) -> str:
    """Build spec content from directive instructions for the benchmark pipeline."""
    instructions = directive.get("instructions", {})
    service_name = instructions.get("service_name", "Unknown")
    service_url = instructions.get("service_url", "")
    category = instructions.get("category", [])
    template = instructions.get("template", "platform_profile")
    auth_type = instructions.get("auth_type", "")

    parts = [
        f"Service: {service_name}",
        f"URL: {service_url}",
        f"Category: {', '.join(category) if category else 'unknown'}",
        f"Template: {template}",
    ]
    if auth_type:
        parts.append(f"Auth: {auth_type}")

    # Include spec URLs if available
    spec_urls = instructions.get("spec_urls", {})
    for label, url in spec_urls.items():
        parts.append(f"{label}: {url}")

    # Include benchmark guide if vendor submitted one
    guide = instructions.get("benchmark_guide", {})
    if guide:
        parts.append("\n# Vendor Benchmark Guide")
        if guide.get("base_url"):
            parts.append(f"Base URL: {guide['base_url']}")
        if guide.get("auth_instructions"):
            parts.append(f"Auth: {guide['auth_instructions']}")
        if guide.get("endpoints_to_test"):
            parts.append(f"Endpoints: {len(guide['endpoints_to_test'])} specified")
        if guide.get("notes"):
            parts.append(f"Notes: {guide['notes']}")

    # Try to fetch actual spec content
    spec_content = "\n".join(parts)

    if spec_urls.get("openapi_spec") or spec_urls.get("llms_txt"):
        try:
            from prowl_bench.core.pipeline import fetch_spec, fetch_llms_txt
            if spec_urls.get("llms_txt"):
                fetched = await fetch_llms_txt(service_url)
                if fetched:
                    spec_content = fetched
            elif spec_urls.get("openapi_spec"):
                fetched = await fetch_spec(spec_urls["openapi_spec"])
                if fetched:
                    spec_content = fetched
        except Exception:
            pass  # fall back to metadata-based spec

    return spec_content


async def _process_directive(directive: dict) -> bool:
    """Claim, benchmark, and submit a single directive. Returns True on success."""
    from prowl_bench.submission.provider import (
        claim_directive, release_directive, submit_directive,
    )
    from prowl_bench.core.pipeline import run_benchmark

    directive_id = directive["id"]
    instructions = directive.get("instructions", {})
    service_name = instructions.get("service_name", "Unknown")
    service_url = instructions.get("service_url", "")
    template = instructions.get("template")
    is_already_claimed = directive.get("status") == "claimed"

    # Step 1: Claim (skip if already claimed by us)
    if not is_already_claimed:
        try:
            await claim_directive(directive_id)
            console.print(f"  [green]Claimed[/green] {service_name}")
        except RuntimeError as exc:
            if "429" in str(exc) or "Max concurrent" in str(exc):
                console.print(f"  [yellow]At max concurrent claims, skipping[/yellow]")
                return False
            console.print(f"  [red]Claim failed: {exc}[/red]")
            return False

    # Step 2: Benchmark
    if not service_url:
        console.print(f"  [yellow]No service URL, releasing {service_name}[/yellow]")
        try:
            await release_directive(directive_id)
        except Exception:
            pass
        return False

    try:
        console.print(f"  [dim]Benchmarking {service_url}...[/dim]")
        spec_content = await _fetch_spec_for_directive(directive)
        report = await run_benchmark(
            url=service_url,
            name=service_name,
            spec_content=spec_content,
            template_slug=template,
            categories=instructions.get("category", []),
        )
    except Exception as exc:
        console.print(f"  [red]Benchmark failed: {exc}[/red]")
        try:
            await release_directive(directive_id)
            console.print(f"  [dim]Released directive back to queue[/dim]")
        except Exception:
            pass
        return False

    # Step 2.5: Detect bot-blocked targets (Cloudflare/WAF) — releasing the
    # directive instead of submitting noise. Mirrors the backend orchestrator's
    # BotBlocked check: 2+ probes, every one returning the same non-2xx >=400.
    probes = report.execution_results or []
    real_probes = [r for r in probes if r.status_code is not None]
    if len(real_probes) >= 2:
        statuses = {r.status_code for r in real_probes}
        if len(statuses) == 1 and next(iter(statuses)) >= 400:
            blocked_status = next(iter(statuses))
            console.print(
                f"  [yellow]Bot-blocked ({blocked_status} on every probe), "
                f"releasing {service_name}[/yellow]"
            )
            try:
                await release_directive(directive_id)
            except Exception:
                pass
            return False

    # Step 3: Submit
    try:
        result = await submit_directive(directive_id, report)
        quality = result.get("quality_score", "?")
        status = result.get("status", "?")
        console.print(
            f"  [green]Submitted[/green] {service_name} — "
            f"score={report.overall_score}, quality={quality}/100, status={status}"
        )
        return True
    except Exception as exc:
        console.print(f"  [red]Submit failed: {exc}[/red]")
        return False


async def run_bot(
    poll_interval: int = 60,
    max_workers: int = 1,
):
    """Main bot loop — polls for directives, benchmarks, submits."""
    global _running

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    cfg = get_config()
    if not cfg.prowl_agent_key:
        console.print("[red]PROWL_AGENT_KEY not set. Run: prowl-bench register[/red]")
        return

    from prowl_bench.submission.provider import get_directives, get_earnings

    cycle = 0
    total_submitted = 0

    console.print(f"\n[bold]Prowl Provider Bot[/bold]")
    console.print(f"  Poll interval: {poll_interval}s")
    console.print(f"  Max workers:   {max_workers}")
    console.print(f"  Press Ctrl+C to stop\n")

    while _running:
        cycle += 1
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")

        try:
            # Fetch claimed directives first (resume in-progress work)
            claimed = await get_directives(status="claimed")
            open_dirs = await get_directives(status="open")

            # Merge and sort by priority
            all_directives = claimed + open_dirs
            all_directives.sort(key=lambda d: PRIORITY_ORDER.get(d.get("priority", "low"), 4))

            if all_directives:
                console.print(
                    f"[dim][{now}][/dim] Cycle {cycle}: "
                    f"{len(claimed)} claimed, {len(open_dirs)} open"
                )

                # Process up to max_workers directives
                batch = all_directives[:max_workers]
                for directive in batch:
                    if not _running:
                        break
                    name = directive.get("instructions", {}).get("service_name", "?")
                    priority = directive.get("priority", "?")
                    reward = directive.get("reward_usd", 0)
                    console.print(
                        f"\n  [{priority}] {name} "
                        f"({'$' + f'{reward:.2f}' if reward else 'catalog build'})"
                    )
                    success = await _process_directive(directive)
                    if success:
                        total_submitted += 1
            else:
                if cycle % 5 == 1:  # only print "waiting" occasionally
                    console.print(f"[dim][{now}][/dim] Cycle {cycle}: no directives available")

            # Show earnings every 10 cycles
            if cycle % 10 == 0:
                try:
                    earnings = await get_earnings()
                    console.print(
                        f"\n[dim]  Earnings: ${earnings['total_earned_usd']:.2f} total, "
                        f"${earnings['pending_payout_usd']:.2f} pending, "
                        f"{total_submitted} submitted this session[/dim]\n"
                    )
                except Exception:
                    pass

        except Exception as exc:
            console.print(f"[red]Error in cycle {cycle}: {exc}[/red]")

        # Sleep in small increments so shutdown is responsive
        for _ in range(poll_interval):
            if not _running:
                break
            await asyncio.sleep(1)

    console.print(f"\n[bold]Bot stopped.[/bold] Submitted {total_submitted} benchmarks this session.\n")
