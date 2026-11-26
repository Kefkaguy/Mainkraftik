"""Read-only performance calculations; optional append-only report evidence."""

import argparse
import math
from datetime import datetime, time, timedelta

import numpy as np

from intelligence.calendar import EASTERN
from intelligence.util import canonical, now
from paperpilot.db import connect, event
from paperpilot.engine import latest_orders, ledger


def quality(rows):
    result = []
    for horizon in (1, 5):
        group = [r for r in rows if r["horizon"] == horizon]
        if not group:
            result.append({"horizon": horizon, "observations": 0, "status": "inconclusive", "metrics": None})
            continue
        mae = brier = logloss = covered = 0
        bins = [[] for _ in range(10)]
        dated = {}
        for r in group:
            f = r["decision"]["horizons"][str(horizon)]
            y = r["outcome"]["return"]
            label = int(y > 0)
            p = max(1e-8, min(1 - 1e-8, f["probability_positive"]))
            mae += abs(y - f["expected_return"])
            brier += (p - label) ** 2
            logloss += -label * math.log(p) - (1 - label) * math.log(1 - p)
            covered += f["lower_return"] <= y <= f["upper_return"]
            bins[min(9, int(p * 10))].append((p, label))
            bucket = dated.setdefault(r["session_date"], np.zeros(3))
            bucket += [abs(y - f["expected_return"]), (p - label) ** 2, 1]
        calibration = [
            {
                "bin": i,
                "n": len(b),
                "mean_probability": sum(p for p, _ in b) / len(b),
                "observed_positive_rate": sum(y for _, y in b) / len(b),
            }
            for i, b in enumerate(bins)
            if b
        ]
        n = len(group)
        uncertainty = None
        if len(dated) >= 126:
            values = np.asarray([dated[d] for d in sorted(dated)])
            size = len(values)
            block = 20
            rng = np.random.default_rng(20261113)
            starts = rng.integers(0, size - block + 1, size=(2000, math.ceil(size / block)))
            indices = (starts[:, :, None] + np.arange(block)).reshape(2000, -1)[:, :size]
            summed = values[indices].sum(axis=1)
            samples = summed[:, :2] / summed[:, 2, None]
            uncertainty = {
                "scope": "Descriptive candidate score intervals, not baseline improvement or independent confirmation",
                "samples": 2000,
                "block_sessions": 20,
                "confidence": 0.9875,
                "unit": "Shared dates, all stocks resampled together",
                "mae_interval": np.quantile(samples[:, 0], [0.00625, 0.99375]).tolist(),
                "brier_interval": np.quantile(samples[:, 1], [0.00625, 0.99375]).tolist(),
            }
        result.append(
            {
                "horizon": horizon,
                "observations": n,
                "distinct_dates": len({r["session_date"] for r in group}),
                "metrics": {
                    "mae": mae / n,
                    "brier": brier / n,
                    "log_loss": logloss / n,
                    "interval_coverage": covered / n,
                    "calibration_bins": calibration,
                    "ece": sum(
                        b["n"] * abs(b["mean_probability"] - b["observed_positive_rate"]) for b in calibration
                    )
                    / n,
                },
                "status": "inconclusive",
                "uncertainty": uncertainty,
                "approximate_20_session_blocks": len(dated) / 20,
                "effective_sample_size": None,
                "effective_sample_size_reason": "Not assumed equal to stock-row count; dependence reflected in shared-date block intervals",
                "reason": "Experimental: correlated stocks and overlapping horizons; no independent validation verdict. Minimum 126 distinct dates remains required.",
            }
        )
    return result


def exposure_integral(snapshots):
    seconds = value_seconds = 0
    for current, following in zip(snapshots, snapshots[1:]):
        p = current["payload"]
        if not p.get("clock", {}).get("is_open") or not p.get("cash_consistent"):
            continue
        elapsed = (following["observed_at"] - current["observed_at"]).total_seconds()
        duration = elapsed if 0 < elapsed <= 60 else 0
        exposure = sum(float(position["market_value"]) for position in p.get("positions", []))
        seconds += duration
        value_seconds += exposure * duration
    return {
        "observed_seconds": seconds,
        "average_deployed_capital": value_seconds / seconds if seconds else None,
        "basis": "Observed regular-session positions, held until next observation; intervals capped at 60 seconds; gaps excluded",
    }


def closed_cycles(trades):
    lots = {}
    closed = []
    for trade in trades:
        lot = lots.setdefault(trade["symbol"], {"qty": 0.0, "pnl": 0.0})
        lot["qty"] += trade["qty"] if trade["side"] == "buy" else -trade["qty"]
        lot["pnl"] += trade.get("realized_pnl") or 0
        if trade["side"] == "sell" and abs(lot["qty"]) < 1e-5:
            closed.append({"symbol": trade["symbol"], "closed_at": trade["time"], "realized_pnl": lot["pnl"]})
            lot["pnl"] = 0
    return closed


def create_report(session=None, source_sha=None, persist=False):
    instant = now()
    session = session or instant.astimezone(EASTERN).date()
    if session != instant.astimezone(EASTERN).date():
        raise ValueError("Reports use the current New York session; read archived reports for earlier dates")
    start = datetime.combine(session, time.min, EASTERN)
    end = start + timedelta(days=1)
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        controls = conn.execute("SELECT * FROM pilot_settings WHERE id=1").fetchone()
        connection = conn.execute("SELECT * FROM pilot_connection WHERE id='paper'").fetchone()
        snapshots = conn.execute(
            "SELECT observed_at,payload FROM pilot_snapshots WHERE observed_at>=%s AND observed_at<%s ORDER BY id",
            (start, end),
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM pilot_decisions WHERE session_date=%s ORDER BY symbol", (session,)
        ).fetchall()
        intents = conn.execute(
            "SELECT * FROM pilot_intents WHERE session_date=%s ORDER BY created_at", (session,)
        ).fetchall()
        orders = latest_orders(conn)
        book = ledger(orders)
        submissions = {
            r["payload"]["client_id"]: r["payload"]
            for r in conn.execute(
                "SELECT payload FROM pilot_events WHERE kind='order_submission_evidence' ORDER BY id"
            ).fetchall()
        }
        benchmark = conn.execute(
            "SELECT * FROM pilot_benchmarks WHERE observed_at<%s ORDER BY id DESC LIMIT 1", (end,)
        ).fetchone()
        matching = (
            conn.execute(
                "SELECT payload FROM pilot_snapshots WHERE observed_at<=%s ORDER BY id DESC LIMIT 1",
                (benchmark["observed_at"],),
            ).fetchone()
            if benchmark
            else None
        )
        outcomes = conn.execute("""SELECT d.symbol,d.session_date,d.payload AS decision,o.horizon,o.payload AS outcome FROM pilot_decisions d
        JOIN LATERAL(SELECT DISTINCT ON(horizon) * FROM pilot_forecast_outcomes WHERE decision_id=d.id ORDER BY horizon,resolved_at DESC,id DESC) o ON true""").fetchall()
        drawdown = conn.execute("""WITH p AS(SELECT (payload->>'strategy_equity')::float AS equity,
        max((payload->>'strategy_equity')::float) OVER(ORDER BY id) AS peak FROM pilot_snapshots
        WHERE (payload->>'cash_consistent')::boolean)
        SELECT min(equity/greatest(600,peak)-1) AS value FROM p""").fetchone()["value"]
    latest = snapshots[-1]["payload"] if snapshots else None
    if matching and not matching["payload"].get("cash_consistent"):
        matching = None
    verified = bool(latest and latest.get("cash_consistent") and latest.get("account_pnl_available"))
    exposure = exposure_integral(snapshots)
    average = exposure["average_deployed_capital"]
    complete = closed_cycles(book["trades"])
    execution = []
    for trade in book["trades"]:
        evidence = submissions.get(trade.get("client_order_id"), {})
        quote = evidence.get("quote")
        if not quote or not evidence.get("submitted_at"):
            continue
        submitted = datetime.fromisoformat(evidence["submitted_at"])
        stamp = datetime.fromisoformat(quote["source_time"])
        if not 0 <= (submitted - stamp).total_seconds() <= 15:
            continue
        midpoint = float(quote["price"])
        adverse = (trade["price"] / midpoint - 1) * (1 if trade["side"] == "buy" else -1)
        execution.append(
            {
                "client_order_id": trade["client_order_id"],
                "broker_order_id": trade["order_id"],
                "filled_quantity": trade["qty"],
                "fill_average_price": trade["price"],
                "notional": trade["value"],
                "submission_iex_midpoint": midpoint,
                "adverse_slippage_bps": adverse * 10000,
            }
        )
    weighted = sum(t["notional"] for t in execution)
    report = {
        "version": "paper-session-report-v1",
        "source_sha256": source_sha,
        "generated_at": instant,
        "session": session,
        "experimental": True,
        "validated": False,
        "account_data_available": verified,
        "last_observed_at": snapshots[-1]["observed_at"] if snapshots else None,
        "snapshot_evidence": latest,
        "controls": controls,
        "connection": connection,
        "strategy_budget": 600,
        "starting_account_equity": latest.get("starting_paper_equity") if verified else None,
        "account_equity": latest["account"].get("equity") if verified else None,
        "cash": latest["account"].get("cash") if verified else None,
        "daily_pnl": latest["daily_pnl"] if verified else None,
        "daily_return_on_budget": latest["daily_pnl"] / 600 if verified else None,
        "realized_pnl": book["realized"] if verified else None,
        "unrealized_pnl": latest["unrealized_pnl"] if verified else None,
        "maximum_observed_drawdown": drawdown,
        "exposure": exposure,
        "exposure_adjusted_daily_return": latest["daily_pnl"] / average
        if verified and average and average > 0
        else None,
        "exposure_adjusted_basis": "Daily gross P/L divided by time-weighted observed regular-session capital; not annualized or a causal alpha estimate",
        "closed_trade_performance": {
            "completed_round_trips": len(complete),
            "wins": sum(t["realized_pnl"] > 0 for t in complete),
            "losses": sum(t["realized_pnl"] < 0 for t in complete),
            "break_even": sum(t["realized_pnl"] == 0 for t in complete),
            "win_rate": sum(t["realized_pnl"] > 0 for t in complete) / len(complete) if complete else None,
            "average_closed_trade_profit": sum(t["realized_pnl"] for t in complete) / len(complete)
            if complete
            else None,
        },
        "execution_quality": {
            "reference": "Actual fresh submission IEX midpoint, not NBBO; average fill prices, fees excluded",
            "fills_with_reference": len(execution),
            "notional_weighted_adverse_bps": sum(t["notional"] * t["adverse_slippage_bps"] for t in execution)
            / weighted
            if weighted
            else None,
            "fills": execution,
        },
        "benchmark": {
            "observed_at": benchmark["observed_at"],
            "payload": benchmark["payload"],
            "strategy_at_or_before_observation": matching["payload"]["strategy_equity"] if matching else None,
            "excess_dollars_vs_spy": matching["payload"]["strategy_equity"]
            - benchmark["payload"]["spy_equity"]
            if matching
            else None,
            "excess_dollars_vs_passive_six": matching["payload"]["strategy_equity"]
            - benchmark["payload"]["passive_six_equity"]
            if matching
            else None,
        }
        if benchmark
        else None,
        "forecast_quality_to_date": quality(outcomes),
        "decisions": decisions,
        "intents": intents,
        "orders": orders,
        "reconciled_fills": book["trades"],
        "closed_round_trips": complete,
        "limitations": [
            "Gross fill/mark P/L; fees excluded. Unexplained cash flows block account performance.",
            "No invented fills, prices or missing-account balances. Null is unavailable.",
            "Exposure/benchmark measurements use observed marks and timestamps; missing intervals are not reconstructed.",
            "No independent predictive improvement demonstrated; frozen research acceptance remains unchanged.",
        ],
    }
    if persist:
        event("session_report_generated", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=lambda s: datetime.fromisoformat(s).date())
    parser.add_argument("--source-sha")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()
    print(canonical(create_report(args.session, args.source_sha, args.persist)))
