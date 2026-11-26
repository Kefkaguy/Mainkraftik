from datetime import timedelta
from uuid import uuid4

import numpy as np

from intelligence.calendar import cutoff_for, future_sessions, issuance_window, schedule
from intelligence.db import connection
from intelligence.storage import bars_at, jsonb, save_manifest, watchlist
from intelligence.util import now

MODEL = "historical-baseline-v1"
FEATURE_VERSION = "daily-reference-v1"


def continuity(bars):
    if not bars:
        return False, "No market data"
    feeds = {(r["feed"], r["adjustment"], r["adjustment_asof"]) for r in bars}
    if len(feeds) != 1:
        return False, "Mixed feeds or adjustment bases refused"
    days = [r["session_date"] for r in bars]
    if days != sorted(set(days)):
        return False, "Duplicate or unordered sessions"
    expected = list(schedule(days[0], days[-1]))
    if days != expected:
        return False, "Missing exchange sessions; data is not interpolated"
    return True, None


def historical_samples(bars, horizon):
    valid, reason = continuity(bars)
    if not valid:
        raise ValueError(reason)
    values = [
        float(bars[i + horizon - 1]["close"]) / float(bars[i]["open"]) - 1
        for i in range(len(bars) - horizon + 1)
    ]
    return np.asarray(values, dtype=float)


def baseline(samples):
    samples = np.asarray(samples, dtype=float)
    if len(samples) < 252 or not np.isfinite(samples).all():
        raise ValueError("At least 252 finite completed historical outcomes are required")
    return {
        "expected_return": float(samples.mean()),
        "probability_positive": float(np.mean(samples > 0)),
        "lower_return": float(np.quantile(samples, 0.1)),
        "upper_return": float(np.quantile(samples, 0.9)),
        "sample_count": len(samples),
        "interval_method": "Historical 10thâ€“90th percentiles; not a coverage guarantee",
    }


def issue_forecasts():
    instant = now()  # No API or CLI parameter can backdate a live forecast.
    origin, blocked = issuance_window(instant)
    run_id = uuid4()
    run_date = origin or instant.date()
    issued, skipped = [], []
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194010)")
        conn.execute(
            "INSERT INTO forecast_runs VALUES(%s,%s,%s,NULL,'running',%s)",
            (run_id, run_date, instant, jsonb({"reason": blocked})),
        )
        if blocked:
            conn.execute(
                "UPDATE forecast_runs SET status='skipped',completed_at=now() WHERE id=%s", (run_id,)
            )
            return {"status": "skipped", "reason": blocked, "issued": []}
        cutoff = cutoff_for(origin)
        targets = future_sessions(origin)
        for symbol in [*watchlist(conn)["symbols"], "SPY"]:
            # Both reconstructed market availability and actual acquisition
            # must precede the cutoff for genuinely issued forecasts.
            bars = bars_at(conn, symbol, cutoff, cutoff)
            valid, reason = continuity(bars)
            if not valid or bars[-1]["session_date"] != origin:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "No complete origin-session snapshot was acquired before the 20:00 cutoff"
                        if not bars
                        else reason or "Latest origin session was not acquired by cutoff",
                    }
                )
                continue
            dataset_id = save_manifest(conn, "live_forecast", bars, cutoff=cutoff)
            for horizon in (1, 5):
                try:
                    prediction = baseline(historical_samples(bars, horizon))
                except ValueError as exc:
                    skipped.append({"symbol": symbol, "horizon": horizon, "reason": str(exc)})
                    continue
                forecast_id = uuid4()
                inserted = conn.execute(
                    """INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,
                    data_cutoff,entry_session,target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,
                    feature_version,expected_return,probability_positive,lower_return,upper_return,sample_count,provenance)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'split',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(symbol,origin_session,horizon,model_id) DO NOTHING RETURNING id""",
                    (
                        forecast_id,
                        run_id,
                        symbol,
                        origin,
                        horizon,
                        instant,
                        cutoff,
                        targets[0],
                        targets[horizon - 1],
                        bars[0]["feed"],
                        bars[0]["adjustment_asof"],
                        dataset_id,
                        MODEL,
                        FEATURE_VERSION,
                        prediction["expected_return"],
                        prediction["probability_positive"],
                        prediction["lower_return"],
                        prediction["upper_return"],
                        prediction["sample_count"],
                        jsonb(
                            {
                                "kind": "prospective",
                                "interval_method": prediction["interval_method"],
                                "target": "Split-adjusted next-open to horizon-close price return; dividends excluded",
                                "limitations": "Selected securities, overlapping training outcomes, reference only; no execution assumptions",
                            }
                        ),
                    ),
                ).fetchone()
                if inserted:
                    issued.append({"id": str(inserted["id"]), "symbol": symbol, "horizon": horizon})
        status = "completed" if issued else "skipped"
        conn.execute(
            "UPDATE forecast_runs SET completed_at=now(),status=%s,detail=%s WHERE id=%s",
            (status, jsonb({"issued_count": len(issued), "skipped": skipped}), run_id),
        )
    return {"status": status, "issued": issued, "skipped": skipped}


def resolve_outcomes():
    instant = now()
    resolved = []
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194011)")
        pending = conn.execute(
            "SELECT f.* FROM forecasts f WHERE NOT EXISTS(SELECT 1 FROM forecast_outcomes o WHERE o.forecast_id=f.id) ORDER BY issued_at"
        ).fetchall()
        for f in pending:
            if instant < schedule(f["target_session"], f["target_session"])[f["target_session"]][
                "close"
            ] + timedelta(minutes=15):
                continue
            rows = bars_at(conn, f["symbol"], instant)
            selected = {r["session_date"]: r for r in rows}
            entry, exit_ = selected.get(f["entry_session"]), selected.get(f["target_session"])
            if not entry or not exit_ or entry["feed"] != f["feed"]:
                continue
            spy_rows = {r["session_date"]: r for r in bars_at(conn, "SPY", instant)}
            spy_a, spy_b = spy_rows.get(f["entry_session"]), spy_rows.get(f["target_session"])
            spy_return = float(spy_b["close"] / spy_a["open"] - 1) if spy_a and spy_b else None
            value = float(exit_["close"] / entry["open"] - 1)
            conn.execute(
                "INSERT INTO forecast_outcomes VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NULL)",
                (
                    uuid4(),
                    f["id"],
                    instant,
                    value,
                    spy_return,
                    entry["id"],
                    exit_["id"],
                    jsonb(
                        {
                            "feed": entry["feed"],
                            "adjustment_asof": entry["adjustment_asof"],
                            "entry_hash": entry["content_hash"],
                            "exit_hash": exit_["content_hash"],
                            "dividends": "excluded",
                            "spy_bar_ids": [spy_a["id"], spy_b["id"]] if spy_a and spy_b else [],
                        }
                    ),
                ),
            )
            resolved.append(str(f["id"]))
    return {"resolved": resolved, "count": len(resolved)}
