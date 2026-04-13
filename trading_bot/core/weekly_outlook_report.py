from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
WEEKLY_OUTLOOK_DIR = BASE_DIR / "data" / "weekly_outlooks"
HISTORY_DIR = WEEKLY_OUTLOOK_DIR / "history"
EXAMPLES_DIR = WEEKLY_OUTLOOK_DIR / "examples"
LOGS_DIR = WEEKLY_OUTLOOK_DIR / "logs"


def ensure_weekly_outlook_dirs() -> None:
    """Create report directories once so the engine can save history safely."""

    for directory in (WEEKLY_OUTLOOK_DIR, HISTORY_DIR, EXAMPLES_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def save_weekly_outlook(report: dict, markdown_report: str, prefix: str = "weekly_outlook") -> dict[str, str]:
    """Persist timestamped JSON and markdown reports and return their file paths."""

    ensure_weekly_outlook_dirs()
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = HISTORY_DIR / f"{prefix}_{stamp}.json"
    markdown_path = HISTORY_DIR / f"{prefix}_{stamp}.md"
    latest_json_path = WEEKLY_OUTLOOK_DIR / f"{prefix}_latest.json"
    latest_markdown_path = WEEKLY_OUTLOOK_DIR / f"{prefix}_latest.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report, encoding="utf-8")
    latest_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest_markdown_path.write_text(markdown_report, encoding="utf-8")
    return {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "latest_json_path": str(latest_json_path),
        "latest_markdown_path": str(latest_markdown_path),
    }


def save_weekly_outlook_example(name: str, report: dict, markdown_report: str) -> dict[str, str]:
    """Persist example outputs separately from history for quick inspection."""

    ensure_weekly_outlook_dirs()
    slug = name.strip().lower().replace(" ", "_")
    json_path = EXAMPLES_DIR / f"{slug}.json"
    markdown_path = EXAMPLES_DIR / f"{slug}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report, encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(markdown_path)}


def render_weekly_outlook_markdown(report: dict) -> str:
    """Build a readable markdown report from the machine-readable outlook payload."""

    lines = [
        "# Weekly Outlook",
        "",
        f"- Scan time: `{report.get('scan_time', '')}`",
        f"- Timezone: `{report.get('timezone', '')}`",
        f"- Review period: `{report.get('week_review_period', {}).get('start', '')}` -> `{report.get('week_review_period', {}).get('end', '')}`",
        f"- Next week: `{report.get('next_week_period', {}).get('start', '')}` -> `{report.get('next_week_period', {}).get('end', '')}`",
        "",
        "## Rankings",
        "",
        f"- Top swing pairs: {', '.join(report.get('rankings', {}).get('top_swing_pairs', [])) or 'None'}",
        f"- Top intraday pairs: {', '.join(report.get('rankings', {}).get('top_intraday_pairs', [])) or 'None'}",
        "",
        "## Summary",
        "",
        f"- Swing theme: {report.get('summary', {}).get('best_overall_swing_theme', 'N/A')}",
        f"- Intraday theme: {report.get('summary', {}).get('best_overall_intraday_theme', 'N/A')}",
        f"- Pairs to avoid: {', '.join(report.get('summary', {}).get('pairs_to_avoid', [])) or 'None'}",
        f"- Market notes: {report.get('summary', {}).get('market_notes', 'N/A')}",
        "",
        "## Pair Outlooks",
        "",
    ]

    for pair in report.get("pairs", []):
        lines.extend(
            [
                f"### {pair.get('symbol', '')}",
                "",
                f"- Previous week condition: {pair.get('previous_week_review', {}).get('condition', 'N/A')}",
                f"- Dominant direction: {pair.get('previous_week_review', {}).get('dominant_direction', 'N/A')}",
                f"- Lesson: {pair.get('previous_week_review', {}).get('lesson', 'N/A')}",
                f"- Outlook alignment: {pair.get('outlook', {}).get('alignment_status', 'N/A')}",
                f"- Market condition: {pair.get('outlook', {}).get('market_condition', 'N/A')}",
                f"- Swing suitability: {pair.get('outlook', {}).get('swing_suitability', 'N/A')}",
                f"- Intraday suitability: {pair.get('outlook', {}).get('intraday_suitability', 'N/A')}",
                f"- Previous week high/low: {pair.get('zones', {}).get('previous_week_high', 'N/A')} / {pair.get('zones', {}).get('previous_week_low', 'N/A')}",
                f"- Swing plan: {pair.get('swing_plan', {}).get('status', 'N/A')} | Bias: {pair.get('swing_plan', {}).get('bias', 'N/A')} | Confidence: {pair.get('swing_plan', {}).get('confidence', 'N/A')}",
                f"- Intraday plan: {pair.get('intraday_plan', {}).get('status', 'N/A')} | Bias: {pair.get('intraday_plan', {}).get('bias', 'N/A')} | Confidence: {pair.get('intraday_plan', {}).get('confidence', 'N/A')}",
                "",
            ]
        )

    return "\n".join(lines).strip() + "\n"
