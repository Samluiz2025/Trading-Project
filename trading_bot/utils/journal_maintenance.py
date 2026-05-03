from __future__ import annotations

import argparse
import json

from trading_bot.core.journal import audit_recent_closed_trade_history, reconcile_open_trade_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean and audit journal state.")
    parser.add_argument("--days", type=int, default=7, help="How many recent days of closed trades to audit.")
    parser.add_argument("--source", default="auto", help="Data source to use for the historical candle audit.")
    parser.add_argument("--no-apply", action="store_true", help="Do not persist audit metadata back into the journal.")
    args = parser.parse_args()

    reconciliation = reconcile_open_trade_state()
    audit = audit_recent_closed_trade_history(days=args.days, apply=not args.no_apply, source=args.source)

    payload = {
        "reconciled": {
            "changed": bool(reconciliation.get("changed")),
            "archived_entries": len(reconciliation.get("archived_entries") or []),
        },
        "audit": {
            "checked": int(audit.get("checked") or 0),
            "summary": audit.get("summary") or {},
            "mismatches": [
                {
                    "symbol": item.get("symbol"),
                    "strategy": item.get("strategy"),
                    "audit_reason": item.get("audit_reason"),
                    "audit_status": item.get("audit_status"),
                }
                for item in (audit.get("mismatches") or [])
            ],
        },
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
