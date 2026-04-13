from __future__ import annotations

import os
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests

from trading_bot.core.alerts_store import append_alert
from trading_bot.core.monitor_state import record_telegram_delivery


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
PENDING_TELEGRAM_PATH = DATA_DIR / "pending_telegram_alerts.json"


def load_telegram_config() -> TelegramConfig | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None
    return TelegramConfig(bot_token=token, chat_id=chat_id)


def send_alert(alert_payload: dict, telegram_config: TelegramConfig | None = None) -> dict:
    stored = append_alert(alert_payload)
    message = _format_alert_message(stored)
    print(message)
    if telegram_config is not None:
        _flush_pending_telegram_alerts(telegram_config)
        sent = _send_telegram_message(message, telegram_config)
        if not sent:
            _queue_pending_telegram_alert(stored, message)
    return stored


def _format_alert_message(alert_payload: dict) -> str:
    alert_type = str(alert_payload.get("type") or "").lower()
    strategy_label = _primary_strategy_label(alert_payload)
    challenge_line = _format_challenge_line(alert_payload)

    if alert_type == "monitor_online":
        return f"[SCANNER ONLINE]\n{alert_payload.get('message')}"

    if alert_type == "calibration_applied":
        changes = alert_payload.get("changes") or []
        reasons = alert_payload.get("reason_log") or []
        return (
            f"[CALIBRATION APPLIED]\n"
            f"{alert_payload.get('message') or 'Calibration settings updated.'}\n"
            f"Changes: {', '.join(str(item) for item in changes) or 'None'}\n"
            f"Promoted: {', '.join(alert_payload.get('promoted_symbols', [])) or 'None'}\n"
            f"Demoted: {', '.join(alert_payload.get('demoted_symbols', [])) or 'None'}\n"
            f"Sessions: {', '.join(alert_payload.get('recommended_sessions', [])) or 'None'}\n"
            f"Grade: {alert_payload.get('recommended_minimum_setup_grade') or '-'}\n"
            f"Why: {reasons[0] if reasons else 'Validated results triggered a calibration pass.'}"
        )

    if alert_type in {"morning_digest", "evening_summary"}:
        lines = [
            f"[{str(alert_type).replace('_', ' ').upper()}]",
            alert_payload.get("message", "Market summary"),
        ]
        top = alert_payload.get("top_setups", [])
        if top:
            lines.append("Top setups:")
            lines.extend(
                [
                    f"- {item.get('pair')} {item.get('bias')} | Tier {item.get('tier', '-')} | RR 1:{item.get('risk_reward_ratio', '-')}"
                    for item in top[:5]
                ]
            )
        lines.append(f"Scanner health: {alert_payload.get('scanner_health', 'unknown')}")
        twin = alert_payload.get("digital_twin") or {}
        if twin:
            lines.append(
                f"Twin: win rate {twin.get('win_rate', 0)}% | closed {twin.get('closed_count', 0)} | realized ${float(twin.get('realized_pnl', 0) or 0):.2f}"
            )
        return "\n".join(lines)

    if alert_type == "trade_closed":
        title = "CHALLENGE TRADE " + str(alert_payload.get('status', 'CLOSED')) if alert_payload.get("challenge_mode") else f"TRADE {alert_payload.get('status', 'CLOSED')}"
        return (
            f"[{title}]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"TRADEABLE NOW: NO\n"
            f"{challenge_line}"
            f"Bias: {alert_payload.get('bias')}\n"
            f"Entry Zone: {float(alert_payload.get('entry') or 0):.4f}\n"
            f"SL Zone: {float(alert_payload.get('sl') or 0):.4f}\n"
            f"TP Zone: {float(alert_payload.get('tp') or 0):.4f}\n"
            f"Strategies: {', '.join(alert_payload.get('strategies', []))}\n"
            f"Why this matters: {alert_payload.get('message') or 'Trade outcome recorded.'}"
        )

    if alert_type == "trade_activated":
        title = "CHALLENGE ENTRY ACTIVATED" if alert_payload.get("challenge_mode") else "ENTRY ACTIVATED"
        return (
            f"[{title}]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"TRADEABLE NOW: YES\n"
            f"{challenge_line}"
            f"ACTION: Trade is live.\n"
            f"Bias: {alert_payload.get('bias')}\n"
            f"Entry Zone: {float(alert_payload.get('entry') or 0):.4f}\n"
            f"SL Zone: {float(alert_payload.get('sl') or 0):.4f}\n"
            f"TP Zone: {float(alert_payload.get('tp') or 0):.4f}\n"
            f"Strategies: {', '.join(alert_payload.get('strategies', []))}\n"
            f"Why this matters: {alert_payload.get('message') or 'Price has touched the entry and the trade is now active.'}"
        )

    if alert_type == "twin_trade_closed":
        title = "CHALLENGE DIGITAL TWIN " + str(alert_payload.get('status', 'CLOSED')) if alert_payload.get("challenge_mode") else f"DIGITAL TWIN {alert_payload.get('status', 'CLOSED')}"
        return (
            f"[{title}]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"TRADEABLE NOW: NO\n"
            f"{challenge_line}"
            f"Bias: {alert_payload.get('bias')}\n"
            f"Entry Zone: {float(alert_payload.get('entry') or 0):.4f}\n"
            f"SL Zone: {float(alert_payload.get('sl') or 0):.4f}\n"
            f"TP Zone: {float(alert_payload.get('tp') or 0):.4f}\n"
            f"Strategies: {', '.join(alert_payload.get('strategies', []))}\n"
            f"Why this matters: {alert_payload.get('message') or 'Digital twin outcome recorded.'}"
        )

    if alert_type == "setup_invalidated":
        return (
            f"[SETUP INVALIDATED]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"TRADEABLE NOW: NO\n"
            f"{challenge_line}"
            f"Bias: {alert_payload.get('bias')}\n"
            f"Reason: {alert_payload.get('message')}\n"
            f"Missing: {', '.join(_humanize_reason(item) for item in alert_payload.get('missing', []))}"
        )

    if alert_type == "stalker_alert":
        stalker = alert_payload.get("stalker") or {}
        plan_zone = (alert_payload.get("details") or {}).get("plan_zone")
        plan_zone_line = ""
        if isinstance(plan_zone, list) and len(plan_zone) >= 2:
            plan_zone_line = f"Plan Zone: {float(plan_zone[0]):.4f} -> {float(plan_zone[1]):.4f}\n"
        fractal_line = _format_fractal_line(alert_payload)
        return (
            f"[STALKER ALERT]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"TRADEABLE NOW: NO\n"
            f"{challenge_line}"
            f"Setup Model: {strategy_label}\n"
            f"Tier: {alert_payload.get('tier', '-')}\n"
            f"Bias: {alert_payload.get('bias')}\n"
            f"Lifecycle: {alert_payload.get('lifecycle', '-')}\n"
            f"Stalker State: {stalker.get('state', '-')}\n"
            f"Stalker Score: {stalker.get('score', '-')}\n"
            f"{plan_zone_line}"
            f"{fractal_line}"
            f"Missing: {', '.join(_humanize_reason(item) for item in alert_payload.get('missing', [])) or 'None'}\n"
            f"Why this matters: {alert_payload.get('why_this_matters') or 'Pair is close to a valid setup.'}"
        )

    if alert_type in {"confirmation_watch", "zone_decision_wait"}:
        plan_zone = (alert_payload.get("details") or {}).get("plan_zone") or []
        plan_zone_line = ""
        if isinstance(plan_zone, list) and len(plan_zone) >= 2:
            plan_zone_line = f"Plan Zone: {float(plan_zone[0]):.4f} -> {float(plan_zone[1]):.4f}\n"
        requirements = ((alert_payload.get("details") or {}).get("confirmation_entry") or {}).get("required") or []
        requirement_line = f"Confirmation Needed: {', '.join(requirements)}\n" if requirements else ""
        entry_line = f"Projected Entry Zone: {float(alert_payload.get('entry')):.4f}\n" if alert_payload.get("entry") is not None else ""
        sl_line = f"Projected SL Zone: {float(alert_payload.get('sl')):.4f}\n" if alert_payload.get("sl") is not None else ""
        tp_line = f"Projected TP Zone: {float(alert_payload.get('tp')):.4f}\n" if alert_payload.get("tp") is not None else ""
        missing_line = f"Still Missing: {', '.join(_humanize_reason(item) for item in alert_payload.get('missing', []))}\n" if alert_payload.get("missing") else ""
        title = "WAIT FOR ENTRY ZONE" if alert_type == "zone_decision_wait" else "WAIT FOR CONFIRMATION"
        action_line = "ACTION: Wait. Do not enter yet.\n"
        default_why = (
            "Price is not in the trade zone yet."
            if alert_type == "zone_decision_wait"
            else "Price is in the zone, but confirmation is not complete yet."
        )
        return (
            f"[{title}]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"TRADEABLE NOW: NO\n"
            f"{challenge_line}"
            f"{action_line}"
            f"Setup Model: {strategy_label}\n"
            f"Bias: {alert_payload.get('bias')}\n"
            f"Lifecycle: {alert_payload.get('lifecycle', '-')}\n"
            f"{plan_zone_line}"
            f"{entry_line}"
            f"{sl_line}"
            f"{tp_line}"
            f"{requirement_line}"
            f"{missing_line}"
            f"Confluences: {', '.join(alert_payload.get('confluences', []))}\n"
            f"Why this matters: {alert_payload.get('why_this_matters') or alert_payload.get('message') or default_why}"
        )

    if alert_type in {"setup_forming", "zone_watch", "entry_reached", "active_setup", "startup_setup", "valid_setup", "confirmation_entry"}:
        title_map = {
            "setup_forming": "VALID SETUP",
            "zone_watch": "VALID SETUP",
            "entry_reached": "VALID SETUP",
            "active_setup": "VALID SETUP",
            "startup_setup": "STARTUP VALID SETUP",
            "valid_setup": "VALID SETUP",
            "confirmation_entry": "VALID SETUP",
        }
        title = title_map.get(alert_type, "SETUP ALERT")
        if alert_payload.get("challenge_mode"):
            title = f"CHALLENGE {title}"
        return (
            f"[{title}]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"Status: VALID\n"
            f"TRADEABLE NOW: YES\n"
            f"{challenge_line}"
            f"ACTION: Place the trade plan.\n"
            f"Setup Model: {strategy_label}\n"
            f"Setup Grade: {alert_payload.get('setup_grade', '-')}\n"
            f"Daily Bias: {str(alert_payload.get('daily_bias') or '-').upper()}\n"
            f"Setup Type: {alert_payload.get('setup_type') or '-'}\n"
            f"Bias: {alert_payload.get('bias')}\n"
            f"Entry: {float(alert_payload.get('entry') or 0):.4f}\n"
            f"Stop Loss: {float(alert_payload.get('sl') or 0):.4f}\n"
            f"Take Profit: {float(alert_payload.get('tp') or 0):.4f}\n"
            f"Risk-Reward: 1:{alert_payload.get('risk_reward_ratio', '-')}\n"
            f"Session: {str(alert_payload.get('session') or '-').upper()}\n"
            f"Confidence: {alert_payload.get('confidence')}\n"
            f"Invalidation: {float(alert_payload.get('invalidation') or alert_payload.get('sl') or 0):.4f}\n"
            f"Reason: {alert_payload.get('reason') or alert_payload.get('why_this_matters') or 'All rules are aligned.'}"
        )

    if alert_payload.get("status") == "NO TRADE":
        return (
            f"[NO TRADE]\n"
            f"Pair: {alert_payload.get('pair')}\n"
            f"Status: NO TRADE\n"
            f"TRADEABLE NOW: NO\n"
            f"{challenge_line}"
            f"Reason: {alert_payload.get('reason') or alert_payload.get('message') or ', '.join(_humanize_reason(item) for item in alert_payload.get('missing', []))}"
        )

    return (
        f"[{str(alert_type or 'setup_alert').replace('_', ' ').upper()}]\n"
        f"Pair: {alert_payload.get('pair')}\n"
        f"Status: {alert_payload.get('status', '-')}\n"
        f"TRADEABLE NOW: {'YES' if str(alert_payload.get('status', '')).upper() == 'VALID_TRADE' else 'NO'}\n"
        f"{challenge_line}"
        f"Setup Model: {strategy_label}\n"
        f"Bias: {alert_payload.get('bias')}\n"
        f"Entry: {float(alert_payload.get('entry') or 0):.4f}\n"
        f"Stop Loss: {float(alert_payload.get('sl') or 0):.4f}\n"
        f"Take Profit: {float(alert_payload.get('tp') or 0):.4f}\n"
        f"Risk-Reward: 1:{alert_payload.get('risk_reward_ratio', '-')}\n"
        f"Confidence: {alert_payload.get('confidence')}\n"
        f"Reason: {alert_payload.get('reason') or alert_payload.get('why_this_matters') or 'All rules are aligned.'}"
    )


def _humanize_reason(reason: str) -> str:
    mapping = {
        "Bias mismatch": "Timeframes not aligned",
        "Daily/H4 bias mismatch": "Daily and H4 not aligned",
        "Weekly/Daily bias mismatch": "Weekly and Daily not aligned",
        "Unfavorable regime": "Market too choppy / not trending enough",
        "Insufficient target range": "Target too close for minimum RR",
        "No H1 pullback": "No clean H1 pullback yet",
        "No LTF break and retest": "No lower-timeframe confirmation yet",
        "No continuation or reversal zone": "No clean zone to trade from yet",
        "No H4/H1 confirmation": "H4 and H1 have not confirmed yet",
        "H1 not aligned": "H1 not aligned with higher timeframe",
        "Setup invalidated": "Setup no longer valid",
        "News lock": "High-impact news nearby",
    }
    return mapping.get(str(reason), str(reason))


def _format_challenge_line(alert_payload: dict) -> str:
    if not bool(alert_payload.get("challenge_mode")):
        return ""
    challenge_name = str(alert_payload.get("challenge_name") or "Challenge Mode")
    planned_risk = alert_payload.get("planned_risk")
    risk_suffix = f" | Planned Risk: ${float(planned_risk):.2f}" if planned_risk is not None else ""
    return f"CHALLENGE MODE: {challenge_name}{risk_suffix}\n"


def _format_fractal_line(alert_payload: dict) -> str:
    fractal = alert_payload.get("fractal") or {}
    scenario = fractal.get("scenario") or {}
    validation = fractal.get("validation") or {}
    motifs = (fractal.get("current_box") or {}).get("motifs") or []
    if not scenario:
        return ""
    motif_line = ""
    if motifs:
        motif_line = f" | Motif {motifs[0].get('label', '-')}"
    path_line = ""
    if scenario.get("path_family") == "distribution_to_lower_box_then_reversal":
        path_line = " | Path lower box first, reversal later"
    elif scenario.get("path_family") == "equity_corrective_drop_sequence":
        path_line = " | Path corrective rhythm, sharp drop risk"
    phase_line = ""
    if scenario.get("active_phase") not in {None, "", "standard"}:
        phase_line = (
            f" | Active phase {str(scenario.get('active_phase')).replace('_', ' ')}"
            f" ({scenario.get('active_trade_bias', 'neutral')})"
        )
    return (
        f"Fractal: {scenario.get('path_bias', '-')} | "
        f"Down {scenario.get('breakdown_probability', 0)}% / "
        f"Up {scenario.get('breakout_probability', 0)}% | "
        f"Validation {validation.get('directional_accuracy', 0)}%{motif_line}{path_line}{phase_line}\n"
    )


def _format_correlation_line(alert_payload: dict) -> str:
    confirmations = alert_payload.get("correlated_confirmations") or []
    if not confirmations:
        return ""
    peers = ", ".join(
        f"{item.get('pair')} {item.get('bias')} (corr {item.get('rolling_correlation', '-')})"
        for item in confirmations[:3]
    )
    return f"Correlation: {peers}\n"


def _format_cluster_line(alert_payload: dict) -> str:
    cluster = alert_payload.get("cluster_confirmation") or {}
    if not cluster:
        return ""
    return (
        f"Cluster: {cluster.get('name')} {cluster.get('bias')} | "
        f"Peers {cluster.get('member_count')} | Avg corr {cluster.get('average_correlation')}\n"
    )


def _primary_strategy_label(alert_payload: dict) -> str:
    strategies = [str(item) for item in (alert_payload.get("strategies") or []) if item]
    if not strategies:
        strategy = str(alert_payload.get("strategy") or "").strip()
        return strategy or "Strategy"
    preferred = [
        "Sweep Reversal",
        "Trend Pullback Continuation",
        "HTF Zone Reaction",
    ]
    for label in preferred:
        if label in strategies:
            return label
    return strategies[0]


def _send_telegram_message(message: str, telegram_config: TelegramConfig) -> bool:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{telegram_config.bot_token}/sendMessage",
            json={"chat_id": telegram_config.chat_id, "text": message},
            timeout=10,
        )
        response.raise_for_status()
        record_telegram_delivery(True)
        return True
    except requests.RequestException as exc:
        record_telegram_delivery(False, str(exc))
        print(f"[ERROR] Telegram alert failed: {exc}")
        return False


def _load_pending_telegram_alerts() -> list[dict]:
    if not PENDING_TELEGRAM_PATH.exists():
        return []
    try:
        return json.loads(PENDING_TELEGRAM_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []


def _save_pending_telegram_alerts(items: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_TELEGRAM_PATH.write_text(json.dumps(items[-500:], indent=2), encoding="utf-8")


def _queue_pending_telegram_alert(alert_payload: dict, message: str) -> None:
    signature = str(alert_payload.get("signature") or "")
    pending = _load_pending_telegram_alerts()
    if signature and any(str(item.get("signature") or "") == signature for item in pending):
        return
    pending.append(
        {
            "signature": signature,
            "message": message,
            "queued_at": datetime.now(UTC).isoformat(),
            "pair": alert_payload.get("pair"),
            "type": alert_payload.get("type"),
        }
    )
    _save_pending_telegram_alerts(pending)


def _flush_pending_telegram_alerts(telegram_config: TelegramConfig) -> None:
    pending = _load_pending_telegram_alerts()
    if not pending:
        return

    remaining: list[dict] = []
    for index, item in enumerate(pending):
        message = str(item.get("message") or "")
        if not message:
            continue
        sent = _send_telegram_message(message, telegram_config)
        if not sent:
            remaining.append(item)
            remaining.extend(pending[index + 1 :])
            break
    _save_pending_telegram_alerts(remaining)
