"""Read-only, projected VPS paper observations. No broker mutation or settings writes."""

import json
from datetime import UTC, datetime
from pathlib import Path

from vps_access import run

SQL = """SELECT json_build_object(
'counts',(SELECT json_build_object('decisions',(SELECT count(*) FROM pilot_decisions),'signals',(SELECT count(*) FROM pilot_strategy_signals),'intents',(SELECT count(*) FROM pilot_intents),'order_versions',(SELECT count(*) FROM pilot_order_versions))),
'signals',(SELECT coalesce(json_agg(t),'[]') FROM (SELECT session_date,symbol,strategy,role,action,payload->'reasons' AS reasons,payload->>'entry_policy' AS entry_policy FROM pilot_strategy_signals ORDER BY issued_at DESC LIMIT 60)t),
'intents',(SELECT coalesce(json_agg(t),'[]') FROM (SELECT symbol,side,session_date,state,notional,quantity FROM pilot_intents ORDER BY created_at DESC LIMIT 60)t),
'snapshot',(SELECT json_build_object('observed_at',observed_at,'positions',payload->'positions','realized_pnl',payload->'realized_pnl','unrealized_pnl',payload->'unrealized_pnl','cash_consistent',payload->'cash_consistent','daily_pnl',payload->'daily_pnl') FROM pilot_snapshots ORDER BY id DESC LIMIT 1),
'recent_events',(SELECT coalesce(json_agg(t),'[]') FROM (SELECT created_at,kind,payload->>'reason' AS reason FROM pilot_events WHERE kind NOT IN ('price_refresh','company_information_refresh') ORDER BY id DESC LIMIT 30)t))"""


def main():
    result = run(
        'docker exec keftrade-intelligence-paper-postgres-1 psql -U paperpilot -d intelligence_paper -Atc "'
        + SQL
        + '"'
    )
    if result["exit_code"]:
        raise RuntimeError("Read-only observation failed")
    value = {
        "observed_at": datetime.now(UTC).isoformat(),
        "evidence_type": "ALPACA_PAPER_APPLICATION_LEDGER",
        "broker_history_independently_queried": False,
        "data": json.loads(result["stdout"]),
    }
    folder = Path(__file__).resolve().parents[1] / "reports/phase7"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (folder / f"paper-observation-{stamp}.json").write_text(
        json.dumps(value, indent=2), encoding="utf-8"
    )
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
