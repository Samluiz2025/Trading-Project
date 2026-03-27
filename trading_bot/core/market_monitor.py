from __future__ import annotations

import time
from datetime import UTC, datetime

from trading_bot.core.alert_system import load_telegram_config, send_alert
from trading_bot.core.confluence_engine import evaluate_symbol
from trading_bot.core.data_fetcher import FetchConfig, fetch_ohlc
from trading_bot.core.instrument_universe import get_instrument_universe
from trading_bot.core.journal import ensure_trade_logged, log_rejected_analysis, update_open_trade_outcomes


def run_market_monitor(group: str = "all", source: str = "auto", poll_interval_seconds: int = 5, use_m30_refinement: bool = True) -> None:
    telegram_config = load_telegram_config()
    symbols = get_instrument_universe(group)
    last_alert_context: dict[str, dict] = {}

    print(f"[INFO] Market monitor started for {group.upper()} ({len(symbols)} symbols).")
    if telegram_config is not None:
        send_alert(
            {
                "status": "INFO",
                "type": "monitor_online",
                "pair": group.upper(),
                "message": f"Market monitor online for {group.upper()} universe ({len(symbols)} symbols).",
                "signature": f"monitor_online|{group}|{datetime.now(UTC).isoformat()}",
            },
            telegram_config,
        )

    while True:
        for symbol in symbols:
            try:
                daily_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1d", limit=220, source=source))
                h1_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="1h", limit=320, source=source))
                m30_data = fetch_ohlc(FetchConfig(symbol=symbol, interval="30m", limit=240, source=source)) if use_m30_refinement else None
                result = evaluate_symbol(symbol=symbol, daily_data=daily_data, h1_data=h1_data, m30_data=m30_data)

                if result["status"] == "VALID_TRADE":
                    if _should_send_setup_alert(result, last_alert_context.get(symbol.upper())):
                        signature = _build_setup_signature(result)
                        last_alert_context[symbol.upper()] = _build_alert_context(result)
                        ensure_trade_logged(
                            symbol=symbol,
                            strategy="+".join(result.get("strategies", [])),
                            entry=float(result["entry"]),
                            stop_loss=float(result["sl"]),
                            take_profit=float(result["tp"]),
                            confluences=result.get("confluences", []),
                            confidence=int(result.get("confidence_score", 0)),
                            timeframe="1h",
                            source=source,
                            timeframes_used=["1d", "1h", "30m"] if use_m30_refinement else ["1d", "1h"],
                        )
                        send_alert(
                            {
                                **result,
                                "type": "valid_setup",
                                "signature": signature,
                            },
                            telegram_config,
                        )
                else:
                    log_rejected_analysis(
                        symbol=symbol,
                        strategy="SMC",
                        missing=result.get("missing", []),
                        timeframe="1h",
                        source=source,
                        message=result.get("message", "No valid setup available"),
                    )
                    if symbol.upper() in last_alert_context and _result_bias(result) != last_alert_context[symbol.upper()].get("bias"):
                        last_alert_context.pop(symbol.upper(), None)
            except Exception as exc:
                print(f"[ERROR] Monitor failed for {symbol}: {exc}")

        try:
            for trade in update_open_trade_outcomes(default_source=source):
                send_alert(
                    {
                        "status": trade.get("status"),
                        "type": "trade_closed",
                        "pair": trade.get("symbol"),
                        "bias": "BUY" if float(trade.get("take_profit") or 0) >= float(trade.get("entry") or 0) else "SELL",
                        "entry": trade.get("entry"),
                        "sl": trade.get("stop_loss"),
                        "tp": trade.get("take_profit"),
                        "confidence": trade.get("confidence"),
                        "strategies": [trade.get("strategy")],
                        "confluences": trade.get("confluences", []),
                        "signature": f"trade_closed|{trade.get('signature')}|{trade.get('status')}",
                        "message": f"Trade {trade.get('status')} for {trade.get('symbol')}",
                    },
                    telegram_config,
                )
        except Exception as exc:
            print(f"[ERROR] Outcome tracking failed: {exc}")

        time.sleep(poll_interval_seconds)


def _build_setup_signature(result: dict) -> str:
    return "|".join(
        [
            result["pair"],
            result["bias"],
            f"{float(result['entry']):.4f}",
            f"{float(result['sl']):.4f}",
            f"{float(result['tp']):.4f}",
            ",".join(result.get("strategies", [])),
        ]
    )


def _build_alert_context(result: dict) -> dict:
    smc = result.get("strategy_results", {}).get("smc", {})
    order_block = smc.get("details", {}).get("order_block", {})
    zone = order_block.get("zone", {})
    return {
        "bias": result.get("bias"),
        "entry": float(result.get("entry") or 0),
        "order_block_formed_at": zone.get("formed_at"),
    }


def _should_send_setup_alert(result: dict, previous: dict | None) -> bool:
    if previous is None:
        return True

    current = _build_alert_context(result)
    if current["bias"] != previous.get("bias"):
        return True
    if current["order_block_formed_at"] and current["order_block_formed_at"] != previous.get("order_block_formed_at"):
        return True

    previous_entry = float(previous.get("entry") or 0)
    current_entry = float(current.get("entry") or 0)
    if previous_entry <= 0:
        return True

    entry_shift_ratio = abs(current_entry - previous_entry) / previous_entry
    return entry_shift_ratio >= 0.0015


def _result_bias(result: dict) -> str | None:
    bias = result.get("bias")
    return str(bias).upper() if bias is not None else None
