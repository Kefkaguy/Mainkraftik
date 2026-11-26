"""Compact, authenticated session evidence for the Mainkraft dashboard."""

from collections import Counter
from datetime import datetime, time, timedelta

from intelligence.calendar import EASTERN, schedule
from intelligence.util import now
from paperpilot.db import connect, jb
from paperpilot.engine import ledger
from paperpilot.risk import TERMINAL
from paperpilot.strategy import SWING_EXPECTANCY_TRIAL


def session_report(session, instant=None):
    instant = instant or now()
    start = datetime.combine(session, time.min, EASTERN)
    end = min(start + timedelta(days=1), instant)
    calendar = schedule(session, session).get(session)
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        frozen = conn.execute(
            "SELECT * FROM pilot_strategy_sessions WHERE session_date=%s", (session,)
        ).fetchone()
        signals = conn.execute(
            """SELECT s.id,s.symbol,s.strategy,s.role,s.action,s.payload,o.payload AS outcome,v.payload AS virtual_outcome
            FROM pilot_strategy_signals s LEFT JOIN pilot_strategy_outcomes o ON o.signal_id=s.id
            LEFT JOIN pilot_virtual_outcomes v ON v.signal_id=s.id
            WHERE s.session_date=%s ORDER BY s.role,s.strategy,s.symbol""",
            (session,),
        ).fetchall()
        legacy = (
            conn.execute(
                "SELECT symbol,action,payload->>'reason' AS reason FROM pilot_decisions WHERE session_date=%s ORDER BY symbol",
                (session,),
            ).fetchall()
            if not frozen
            else []
        )
        notes = conn.execute(
            """SELECT kind,payload->>'reason' AS reason,payload->>'signal_id' AS signal_id
            FROM pilot_events WHERE created_at>=%s AND created_at<%s
            AND kind IN ('daily_research_failed','strategy_issuance_failed','strategy_entry_gate',
                         'order_acknowledgement_unknown','strategy_resolution_failed')
            AND (kind<>'strategy_entry_gate' OR payload->>'allowed'='false') ORDER BY id""",
            (start, end),
        ).fetchall()
        selected = conn.execute(
            "SELECT symbols FROM pilot_selections WHERE session_date=%s", (session,)
        ).fetchone()
        virtual = conn.execute(
            "SELECT picks FROM pilot_shadow_sessions WHERE session_date=%s", (session,)
        ).fetchone()
        latest = conn.execute(
            """SELECT observed_at,payload FROM pilot_snapshots WHERE observed_at>=%s
            AND observed_at<=%s ORDER BY observed_at DESC,id DESC LIMIT 1""",
            (start, end),
        ).fetchone()
        opening = conn.execute(
            """SELECT observed_at,payload FROM pilot_snapshots WHERE observed_at>=%s
            AND observed_at<=%s ORDER BY observed_at,id LIMIT 1""",
            (start, end),
        ).fetchone()
        # Use order state as recorded on the requested day, including late updates to older orders.
        orders = conn.execute(
            """SELECT DISTINCT ON(i.client_id) i.client_id,i.symbol,i.side,i.session_date,
            s.strategy, s.payload->>'entry_policy' AS entry_policy, i.notional, v.payload AS order,v.observed_at
            FROM pilot_intents i LEFT JOIN pilot_strategy_signals s ON s.id=i.strategy_signal_id
            LEFT JOIN pilot_order_versions v ON v.client_id=i.client_id AND v.observed_at<=%s
            WHERE i.created_at<=%s ORDER BY i.client_id,v.observed_at DESC,v.id DESC""",
            (end, end),
        ).fetchall()
        before_orders = conn.execute(
            """SELECT DISTINCT ON(client_id) client_id,payload FROM pilot_order_versions
            WHERE observed_at<%s ORDER BY client_id,observed_at DESC,id DESC""",
            (start,),
        ).fetchall()
    before = {row["client_id"]: row["payload"] for row in before_orders}
    fills = []
    submitted = []
    for row in orders:
        order = row["order"] or {}
        previous = before.get(row["client_id"], {})
        qty, old_qty = float(order.get("filled_qty") or 0), float(previous.get("filled_qty") or 0)
        if qty > old_qty:
            value = qty * float(order["filled_avg_price"]) - old_qty * float(
                previous.get("filled_avg_price") or 0
            )
            fills.append(
                {
                    "symbol": row["symbol"],
                    "strategy": row["strategy"],
                    "side": row["side"],
                    "quantity": qty - old_qty,
                    "average_price": value / (qty - old_qty),
                    "notional": value,
                    "observed_at": row["observed_at"],
                }
            )
        if row["session_date"] == session or (row["observed_at"] and row["observed_at"] >= start):
            submitted.append(
                {
                    "symbol": row["symbol"],
                    "strategy": row["strategy"],
                    "side": row["side"],
                    "status": order.get("status", "unconfirmed"),
                    "filled_qty": qty,
                }
            )
    snapshot = latest["payload"] if latest else {}
    first_snapshot = opening["payload"] if opening else {}

    verified = bool(snapshot.get("cash_consistent") and snapshot.get("account_pnl_available"))
    # Broker fills already include their execution prices. Add only the extra latency allowance.
    gross = snapshot.get("daily_pnl") if verified else None
    allowance = 0.0005 * sum(f["notional"] for f in fills)
    owners = {}
    for order in sorted(orders, key=lambda r: (r["session_date"], r["client_id"])):
        if order["side"] == "buy" and float((order["order"] or {}).get("filled_qty") or 0) > 0:
            owners[order["symbol"]] = order["strategy"] or "legacy"
    positions = (
        [
            {
                "symbol": p["symbol"],
                "strategy": owners.get(p["symbol"], "unassigned"),
                "unrealized_pnl": p.get("unrealized_pl"),
                "quantity": p["qty"],
            }
            for p in snapshot.get("positions", [])
        ]
        if verified
        else None
    )
    strategy_pnl = {}
    if verified and first_snapshot.get("cash_consistent"):
        for lane in ("day", "swing"):
            opening_value = sum(
                float(p["market_value"])
                for p in first_snapshot.get("positions", [])
                if owners.get(p["symbol"]) == lane
            )
            closing_value = sum(
                float(p["market_value"])
                for p in snapshot.get("positions", [])
                if owners.get(p["symbol"]) == lane
            )
            lane_fills = [f for f in fills if f["strategy"] == lane]
            traded_cash = sum(f["notional"] * (1 if f["side"] == "sell" else -1) for f in lane_fills)
            paper = closing_value - opening_value + traded_cash
            strategy_pnl[lane] = {
                "broker_paper_pnl": paper,
                "estimated_net_pnl": paper - 0.0005 * sum(f["notional"] for f in lane_fills),
            }
    closed = bool(calendar and instant >= calendar["close"])
    settled = bool(
        closed
        and verified
        and latest["observed_at"] >= calendar["close"]
        and not any(o["status"] not in TERMINAL for o in submitted)
        and not any(p["strategy"] in {"day", "legacy", "unassigned"} for p in positions)
    )
    picks = {p["signal_id"] for p in virtual["picks"]} if virtual else set()
    gate = {}
    for note in notes:
        if note["signal_id"]:
            gate[note["signal_id"]] = note["reason"]
    decisions = [
        {
            "symbol": r["symbol"],
            "strategy": r["strategy"],
            "role": r["role"],
            "action": r["action"],
            "reasons": r["payload"]["reasons"],
            "execution_rejection": gate.get(r["id"]),
            "expected_net_return": r["payload"].get("expected_net_return"),
            "probability_positive": r["payload"].get("probability_positive"),
            "model": r["payload"]["model_family"]
            + (" · Swing expectancy trial" if r["payload"].get("entry_policy") else ""),
            "entry_policy": r["payload"].get("entry_policy"),
            "entry_notional": r["payload"].get("entry_notional"),
            "shadow_selected": r["id"] in picks,
            "virtual_net_return": r["virtual_outcome"]["net_return"] if r["virtual_outcome"] else None,
            "simulated_net_return": r["outcome"]["net_return"] if r["outcome"] else None,
        }
        for r in signals
    ]
    errors = [
        {"kind": n["kind"], "reason": (n["reason"] or n["kind"])[:200]}
        for n in notes
        if n["kind"] != "strategy_entry_gate"
    ]
    if frozen and frozen["payload"].get("shadow_error"):
        errors.append({"kind": "challenger_unavailable", "reason": frozen["payload"]["shadow_error"]})
    if calendar and instant >= calendar["open"] and not frozen and not legacy:
        errors.append(
            {"kind": "no_decision", "reason": "No strategy decisions were recorded for this session"}
        )
    if closed and not settled:
        errors.append(
            {"kind": "reconciliation_pending", "reason": "Closing account or order reconciliation pending"}
        )
    counts = Counter(r["action"] for r in decisions if r["role"] == "paper")
    if legacy:
        counts.update(r["action"] for r in legacy)
    from paperpilot.comparison import compare_latest

    trial_orders = [row for row in orders if row.get("entry_policy") == SWING_EXPECTANCY_TRIAL]
    trial_book = ledger([row["order"] for row in trial_orders if row["order"] is not None])
    return {
        "swing_expectancy_trial": {
            "reconciled_realized_pnl": trial_book["realized"] if verified else None,
            "filled_buy_notional": sum(
                float(row["order"].get("filled_qty") or 0) * float(row["order"].get("filled_avg_price") or 0)
                for row in trial_orders
                if row["side"] == "buy" and row["order"]
            ),
            "open_lots": sum(lot["qty"] > 0 for lot in trial_book["lots"].values()) if verified else None,
            "today_attempts": sum(
                row["session_date"] == session and row["side"] == "buy" for row in trial_orders
            ),
            "cumulative_buys_filled": sum(
                row["side"] == "buy" and float((row["order"] or {}).get("filled_qty") or 0) > 0
                for row in trial_orders
            ),
        },
        "comparison": compare_latest(),
        "session_date": session,
        "generated_at": instant,
        "market_close": calendar["close"] if calendar else None,
        "status": "final"
        if settled
        else "closed_pending"
        if closed
        else "provisional"
        if calendar
        else "no_session",
        "bundle_id": frozen["bundle_id"] if frozen else None,
        "selected": selected["symbols"] if selected else [],
        "counts": dict(counts),
        "decisions": decisions,
        "legacy_decisions": legacy,
        "errors": errors,
        "orders": submitted,
        "fills": fills,
        "positions": positions,
        "strategy_pnl": strategy_pnl,
        "broker_paper_pnl": gross,
        "estimated_net_pnl": gross - allowance if gross is not None else None,
        "estimated_cost_basis": "0.05% additional latency allowance per leg; broker fills already reflect execution prices",
        "shadow_frozen": bool(virtual),
        "shadow_pending": sum(
            r["role"] == "challenger" and r["shadow_selected"] and r["virtual_net_return"] is None
            for r in decisions
        ),
    }


def archive_final(session, report):
    if report["status"] == "final":
        with connect() as conn:
            conn.execute(
                "INSERT INTO pilot_daily_reports VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (session, report["generated_at"], jb(report)),
            )
