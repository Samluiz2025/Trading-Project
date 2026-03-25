from __future__ import annotations

import argparse

from trading_bot.core.alert_engine import MonitorConfig, run_monitoring_loop


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the monitoring bot."""

    parser = argparse.ArgumentParser(description="Run the trading bot monitoring loop.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT"],
        help="One or more symbols to monitor. Example: BTCUSDT EURUSD ETHUSDT",
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
        default=60,
        help="Polling interval in seconds.",
    )
    return parser.parse_args()


def main() -> None:
    """Build monitoring configs and start the real-time alert loop."""

    args = parse_args()
    monitor_configs = [
        MonitorConfig(
            symbol=symbol,
            interval=args.interval,
            limit=args.limit,
            source=args.source,
        )
        for symbol in args.symbols
    ]

    run_monitoring_loop(
        monitor_configs=monitor_configs,
        poll_interval_seconds=args.poll_seconds,
    )


if __name__ == "__main__":
    main()
