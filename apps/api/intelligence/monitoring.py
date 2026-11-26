"""Descriptive risk, freshness and delayed-label health; no invented forecasts."""

from datetime import timedelta

import numpy as np

from intelligence.calendar import schedule
from intelligence.db import connection
from intelligence.forecasting import continuity
from intelligence.research_models import metrics
from intelligence.storage import bars_at, forecast_history
from intelligence.util import now


def risk_summary(bars):
    valid, reason = continuity(bars)
    if not valid or len(bars) < 61:
        return {"available": False, "reason": reason or "61 completed sessions required"}
    closes = np.asarray([float(r["close"]) for r in bars])
    returns = np.diff(np.log(closes[-21:]))
    gaps = [
        float(bars[k]["open"]) / float(bars[k - 1]["close"]) - 1 for k in range(len(bars) - 20, len(bars))
    ]
    return {
        "available": True,
        "as_of_session": bars[-1]["session_date"],
        "return_20": float(closes[-1] / closes[-21] - 1),
        "realized_volatility_20_annualized": float(np.std(returns) * np.sqrt(252)),
        "mean_overnight_gap_20": float(np.mean(gaps)),
        "drawdown_60": float(closes[-1] / np.max(closes[-60:]) - 1),
        "description": "Descriptive past-price measurements; not causal explanations or return predictions",
    }


def reliability(history):
    groups = {}
    for row in history:
        if row["realized_return"] is not None:
            groups.setdefault((row["model_id"], row["horizon"]), []).append(row)
    return [
        {
            "model_id": model,
            "horizon": horizon,
            "observations": len(rows),
            "distinct_dates": len({r["origin_session"] for r in rows}),
            "first_origin": min(r["origin_session"] for r in rows),
            "last_origin": max(r["origin_session"] for r in rows),
            **metrics(
                [r["realized_return"] for r in rows],
                [r["expected_return"] for r in rows],
                [r["probability_positive"] for r in rows],
                [r["lower_return"] for r in rows],
                [r["upper_return"] for r in rows],
            ),
            "evidence": "prospective_descriptive_not_repeated_confirmation_test",
        }
        for (model, horizon), rows in sorted(groups.items())
    ]


def monitor():
    instant = now()
    sessions = schedule(instant.date() - timedelta(days=12), instant.date())
    completed = [d for d, s in sessions.items() if s["close"] + timedelta(minutes=15) <= instant]
    expected = max(completed) if completed else None
    symbols = []
    with connection() as conn:
        for symbol in ("MSI", "AXON", "SPY"):
            bars = bars_at(conn, symbol, instant)
            valid, reason = continuity(bars)
            history = forecast_history(conn, symbol, limit=20000)
            overdue = [
                r
                for r in history
                if r["realized_return"] is None and r["target_session"] < instant.date() - timedelta(days=1)
            ]
            symbols.append(
                {
                    "symbol": symbol,
                    "last_session": bars[-1]["session_date"] if bars else None,
                    "expected_completed_session": expected,
                    "fresh": bool(bars and bars[-1]["session_date"] == expected),
                    "contiguous": valid,
                    "reason": reason,
                    "overdue_outcomes": len(overdue),
                    "risk": risk_summary(bars),
                    "reliability": reliability(history),
                }
            )
        worker = conn.execute("SELECT * FROM worker_status WHERE id='research-worker'").fetchone()
        return {
            "as_of": instant,
            "symbols": symbols,
            "worker": worker,
            "worker_fresh": bool(worker and instant - worker["heartbeat_at"] < timedelta(minutes=5)),
            "failed_jobs": conn.execute(
                "SELECT id,kind,error,completed_at FROM jobs WHERE status='failed' ORDER BY created_at DESC LIMIT 10"
            ).fetchall(),
            "interpretation": "Health and descriptive reliability only; no model promotion or trading authority",
        }
