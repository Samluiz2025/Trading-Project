from __future__ import annotations

from trading_bot.core.weekly_outlook_engine import run_weekly_outlook_engine


def run_weekly_outlook_job(
    *,
    symbols: list[str] | None = None,
    source: str = "auto",
    timezone_name: str = "Europe/Vienna",
) -> dict:
    """Run the weekly outlook engine and return report metadata for schedulers."""

    report, markdown_report, saved_paths = run_weekly_outlook_engine(
        symbols=symbols,
        source=source,
        timezone_name=timezone_name,
    )
    return {
        "status": "ok",
        "report": report,
        "markdown_report": markdown_report,
        "saved_paths": saved_paths,
    }
