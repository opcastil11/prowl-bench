"""Rich terminal output for benchmark results."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from prowl_bench.core.types import BenchmarkReport

console = Console()


def _score_color(score: float, max_val: float = 10.0) -> str:
    ratio = score / max_val
    if ratio >= 0.8:
        return "green"
    elif ratio >= 0.6:
        return "yellow"
    elif ratio >= 0.4:
        return "dark_orange"
    return "red"


def _bar(value: float, max_val: float = 10.0, width: int = 10) -> str:
    filled = int((value / max_val) * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def print_report(report: BenchmarkReport) -> None:
    """Print a benchmark report to the terminal."""
    # Header
    providers = ", ".join(report.llm_providers_used)
    console.print(f"\n[dim]prowl-bench v{report.runner_version} | Template: {report.template} | LLMs: {providers}[/dim]\n")

    # Score + dimensions
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(width=24)
    table.add_column(width=14)
    table.add_column(width=6, justify="right")

    for dim, val in sorted(report.dimensions.items()):
        color = _score_color(val)
        table.add_row(
            dim.replace("_", " "),
            Text(_bar(val), style=color),
            Text(f"{val:.1f}", style=color),
        )

    score_color = _score_color(report.overall_score, 100)
    header = f"  {report.name}    Score: [{score_color}]{report.overall_score}[/{score_color}]"

    panel = Panel(table, title=header, border_style="dim", width=52)
    console.print(panel)

    # Issues
    if report.issues:
        console.print(f"\n[dim]Issues: {len(report.issues)}[/dim]")
        for issue in report.issues[:5]:
            sev = issue.get("severity", "medium") if isinstance(issue, dict) else "medium"
            detail = issue.get("detail", str(issue)) if isinstance(issue, dict) else str(issue)
            color = {"critical": "red", "high": "red", "medium": "yellow", "low": "dim"}.get(sev, "white")
            console.print(f"  [{color}]- {detail}[/{color}]")

    # Recommendations
    if report.recommendations:
        console.print(f"\n[dim]Recommendations:[/dim]")
        for rec in report.recommendations[:3]:
            console.print(f"  [dim]- {rec}[/dim]")

    console.print()


def print_phase(phase: str, detail: str, status: str = "OK", elapsed: float = 0) -> None:
    """Print a pipeline phase status line."""
    dots = "." * max(1, 50 - len(phase) - len(detail))
    if status == "OK":
        console.print(f"  [bold]{phase}[/bold]  {detail} [dim]{dots}[/dim] [green]{status}[/green] [dim]({elapsed:.1f}s)[/dim]")
    else:
        console.print(f"  [bold]{phase}[/bold]  {detail} [dim]{dots}[/dim] [red]{status}[/red]")
