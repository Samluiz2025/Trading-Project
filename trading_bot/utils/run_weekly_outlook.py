from __future__ import annotations

import argparse
import json

from trading_bot.core.weekly_outlook_job import run_weekly_outlook_job


def parse_args() -> argparse.Namespace:
    """Parse command-line options for manual weekly outlook runs."""

    parser = argparse.ArgumentParser(description="Run the weekly outlook engine manually.")
    parser.add_argument("--symbols", nargs="+", default=None, help="Optional subset of forex symbols to scan.")
    parser.add_argument("--source", default="auto", help="Data source to use for the weekly outlook scan.")
    parser.add_argument("--timezone", default="Europe/Vienna", help="Timezone name used for report timestamps.")
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "both"],
        default="both",
        help="Which output to print to stdout after the report is saved.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the weekly outlook engine and print the chosen output format."""

    args = parse_args()
    result = run_weekly_outlook_job(symbols=args.symbols, source=args.source, timezone_name=args.timezone)
    if args.format in {"json", "both"}:
        print(json.dumps(result["report"], indent=2))
    if args.format in {"markdown", "both"}:
        print()
        print(result["markdown_report"])
    print(json.dumps({"saved_paths": result["saved_paths"]}, indent=2))


if __name__ == "__main__":
    main()
