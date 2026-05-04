"""
run_bot.py – Entry point for the market scanner
Usage:
    python run_bot.py --mode multi --universe all --source auto --poll-seconds 30
"""
import argparse
import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_bot")


def main():
    parser = argparse.ArgumentParser(description="Trading Intelligence Platform Scanner")
    parser.add_argument("--mode",         default="multi",  choices=["single","multi"])
    parser.add_argument("--symbol",       default="GBPUSD")
    parser.add_argument("--universe",     default="all")
    parser.add_argument("--source",       default="auto")
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()

    from trading_bot.core.market_monitor import MarketMonitor
    from trading_bot.core.alert_system   import send_setup_alert
    from trading_bot.core.journal        import append_journal

    monitor = MarketMonitor(source=args.source, poll_seconds=args.poll_seconds)

    def on_setup(result):
        send_setup_alert(result)
        append_journal({
            "symbol":  result.symbol,
            "bias":    result.bias,
            "entry":   result.entry,
            "sl":      result.sl,
            "tp":      result.tp,
            "rr":      result.rr,
            "score":   result.quality_score,
            "confidence": result.confidence,
            "confluences": result.confluences,
            "timestamp": result.timestamp,
            "outcome": "OPEN",
        })

    monitor.on_valid_setup(on_setup)

    token   = os.getenv("TELEGRAM_VALID_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_VALID_CHAT_ID")   or os.getenv("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        logger.info("Telegram alerts: ENABLED")
    else:
        logger.info("Telegram alerts: DISABLED (set TELEGRAM_VALID_BOT_TOKEN + TELEGRAM_VALID_CHAT_ID)")

    logger.info("Scanner started | Source: %s | Poll: %ds", args.source, args.poll_seconds)

    if args.mode == "single":
        result = monitor.scan_symbol(args.symbol.upper())
        logger.info("Result: %s", result.to_dict())
    else:
        monitor.run()


if __name__ == "__main__":
    main()
