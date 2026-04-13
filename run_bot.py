from __future__ import annotations

import argparse
import json

from trading_bot.core.alert_engine import MonitorConfig, run_monitoring_loop, run_strict_market_scanner
from trading_bot.core.calibration_mode import apply_calibration, get_recent_calibration_history
from trading_bot.core.instrument_universe import get_instrument_universe
from trading_bot.core.market_monitor import ChallengeModeConfig, run_market_monitor
from trading_bot.core.news_engine import JsonEconomicCalendarProvider
from trading_bot.core.validation_mode import build_validation_snapshot
from trading_bot.core.weekly_outlook_job import run_weekly_outlook_job


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the monitoring bot."""

    parser = argparse.ArgumentParser(description="Run the trading bot monitoring loop.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=None,
        help="One or more symbols to monitor. Example: BTCUSDT EURUSD ETHUSDT",
    )
    parser.add_argument(
        "--universe",
        default="all",
        help="Instrument group for strict scanning: all, forex, indices, crypto",
    )
    parser.add_argument(
        "--interval",
        default="1h",
        help="Candle interval to monitor. Example: 15m, 1h, 4h, 1d",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Number of candles to fetch on each monitoring cycle.",
    )
    parser.add_argument(
        "--source",
        default="auto",
        help="Data source to use. Example: auto, binance, yfinance, oanda, mock",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=5,
        help="Polling interval in seconds.",
    )
    parser.add_argument(
        "--mode",
        default="strict",
        choices=["strict", "classic", "multi", "weekly-outlook", "validation", "calibrate", "calibration-history"],
        help="Use legacy strict scanner, classic monitor loop, or the new multi-strategy monitor.",
    )
    parser.add_argument(
        "--news-calendar",
        default=None,
        help="Optional path to a local economic calendar JSON file.",
    )
    parser.add_argument(
        "--timezone",
        default="Europe/Vienna",
        help="Timezone name used for weekly outlook reporting and scheduling context.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON for report-style modes.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force apply actions when a mode supports it.",
    )
    parser.add_argument(
        "--challenge-mode",
        action="store_true",
        help="Run the multi-strategy scanner in high-grade challenge mode.",
    )
    parser.add_argument(
        "--challenge-name",
        default="Weekly Challenge",
        help="Label used for challenge mode alerts and journal entries.",
    )
    parser.add_argument(
        "--challenge-max-trades",
        type=int,
        default=3,
        help="Maximum number of challenge trades to allow before blocking new ones.",
    )
    parser.add_argument(
        "--challenge-risk",
        type=float,
        default=30.0,
        help="Planned dollar risk per challenge trade for alert labeling.",
    )
    return parser.parse_args()


def main() -> None:
    """Build monitoring configs and start the real-time alert loop."""

    args = parse_args()
    if args.mode == "multi":
        run_market_monitor(
            group=args.universe,
            source=args.source,
            poll_interval_seconds=args.poll_seconds,
            use_ltf_refinement=True,
            challenge_mode=ChallengeModeConfig(
                enabled=bool(args.challenge_mode),
                name=str(args.challenge_name),
                max_trades=int(args.challenge_max_trades),
                risk_per_trade=float(args.challenge_risk),
            ),
        )
        return

    if args.mode == "weekly-outlook":
        report = run_weekly_outlook_job(
            symbols=args.symbols or get_instrument_universe("forex"),
            source=args.source,
            timezone_name=args.timezone,
        )
        print(report["markdown_report"])
        print(report["saved_paths"])
        return

    if args.mode == "validation":
        snapshot = build_validation_snapshot(settings={"timezone": args.timezone})
        _print_report(snapshot, as_json=args.json, renderer=_render_validation_report)
        return

    if args.mode == "calibrate":
        result = apply_calibration(force=args.force)
        _print_report(result, as_json=args.json, renderer=_render_calibration_result)
        return

    if args.mode == "calibration-history":
        payload = {"entries": get_recent_calibration_history(limit=10)}
        _print_report(payload, as_json=args.json, renderer=_render_calibration_history)
        return

    if args.mode == "strict":
        run_strict_market_scanner(
            group=args.universe,
            source=args.source,
            poll_interval_seconds=args.poll_seconds,
        )
        return

    symbols = args.symbols or get_instrument_universe(args.universe)
    monitor_configs = [
        MonitorConfig(
            symbol=symbol,
            interval=args.interval,
            limit=args.limit,
            source=args.source,
        )
        for symbol in symbols
    ]
    news_provider = JsonEconomicCalendarProvider(args.news_calendar) if args.news_calendar else None

    run_monitoring_loop(
        monitor_configs=monitor_configs,
        poll_interval_seconds=args.poll_seconds,
        news_provider=news_provider,
    )

def _print_report(payload: dict, *, as_json: bool, renderer) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(renderer(payload))


def _render_validation_report(snapshot: dict) -> str:
    lines = [
        "Validation Mode",
        f"Validated closed trades: {snapshot.get('validated_closed_trades', 0)}",
        f"Adjusted net R: {snapshot.get('adjusted_net_r', 0.0):.2f}",
        f"Adjusted expectancy: {snapshot.get('adjusted_expectancy_r', 0.0):.2f}R",
        f"Adjusted profit factor: {snapshot.get('adjusted_profit_factor', 0.0):.2f}",
        f"Adjusted win rate: {snapshot.get('adjusted_win_rate', 0.0):.2f}%",
        f"Auto-disabled symbols: {', '.join(snapshot.get('underperforming_symbols', [])) or 'None'}",
    ]
    top_symbols = snapshot.get("top_symbols", [])[:3]
    if top_symbols:
        lines.append("Top validated symbols:")
        lines.extend(
            [
                f"- {item.get('symbol')}: net {float(item.get('adjusted_net_r') or 0.0):.2f}R | expectancy {float(item.get('adjusted_expectancy_r') or 0.0):.2f}R"
                for item in top_symbols
            ]
        )
    bottom_symbols = snapshot.get("bottom_symbols", [])[:3]
    if bottom_symbols:
        lines.append("Weakest validated symbols:")
        lines.extend(
            [
                f"- {item.get('symbol')}: net {float(item.get('adjusted_net_r') or 0.0):.2f}R | expectancy {float(item.get('adjusted_expectancy_r') or 0.0):.2f}R"
                for item in bottom_symbols
            ]
        )
    return "\n".join(lines)


def _render_calibration_result(result: dict) -> str:
    snapshot = result.get("snapshot") or {}
    lines = [
        "Calibration",
        f"Status: {result.get('status', 'UNKNOWN')}",
        result.get("message", ""),
        f"Pending changes: {len(snapshot.get('pending_changes', []))}",
        f"Promoted: {', '.join(snapshot.get('promoted_symbols', [])) or 'None'}",
        f"Demoted: {', '.join(snapshot.get('demoted_symbols', [])) or 'None'}",
        f"Sessions: {', '.join(snapshot.get('recommended_sessions', [])) or 'None'}",
        f"Minimum grade: {snapshot.get('recommended_minimum_setup_grade') or '-'}",
        f"Recent adjusted net R: {float(snapshot.get('recent_adjusted_net_r') or 0.0):.2f}",
        f"Recent adjusted expectancy: {float(snapshot.get('recent_adjusted_expectancy_r') or 0.0):.2f}R",
    ]
    reasons = snapshot.get("reason_log", [])[:4]
    if reasons:
        lines.append("Why:")
        lines.extend([f"- {item}" for item in reasons])
    return "\n".join(line for line in lines if line != "")


def _render_calibration_history(payload: dict) -> str:
    entries = payload.get("entries", [])
    if not entries:
        return "Calibration History\nNo calibration runs recorded yet."
    lines = ["Calibration History"]
    for item in entries[:10]:
        lines.append(
            f"- {item.get('applied_at')}: {', '.join(item.get('changes', [])) or 'No recorded changes'}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
