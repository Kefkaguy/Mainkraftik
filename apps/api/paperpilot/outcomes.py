"""Append outcome evidence separately from genuine premarket decisions."""

import math
from collections import defaultdict
from datetime import timedelta

from intelligence.calendar import EASTERN, schedule
from intelligence.providers import fetch_bars, normalize_bars
from intelligence.util import digest, now
from paperpilot.config import settings
from paperpilot.data import latest_sources, store
from paperpilot.db import connect, jb


def resolve(revisions=False):
    instant = now()
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.* FROM pilot_decisions d WHERE %s OR EXISTS(SELECT 1 FROM jsonb_object_keys(d.payload->'horizons') h
        WHERE NOT EXISTS(SELECT 1 FROM pilot_forecast_outcomes o WHERE o.decision_id=d.id AND o.horizon=h::int))""",
            (revisions,),
        ).fetchall()
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["symbol"]].append(row)
        cached = {}
        for symbol, items in grouped.items():
            sources = latest_sources("daily_prices", [symbol, "SPY"])
            # Completed outcomes need recent bars, never another five-year
            # archive for every stock encountered by the changing universe.
            mature = []
            for d in items:
                sessions = list(schedule(d["session_date"], d["session_date"] + timedelta(days=20)))
                if any(
                    str(h) in d["payload"].get("horizons", {})
                    and len(sessions) >= h
                    and sessions[h - 1] < instant.astimezone(EASTERN).date()
                    and (
                        revisions
                        or not conn.execute(
                            "SELECT 1 FROM pilot_forecast_outcomes WHERE decision_id=%s AND horizon=%s LIMIT 1",
                            (d["id"], h),
                        ).fetchone()
                    )
                    for h in (1, 5)
                ):
                    mature.append(d)
            if not mature:
                continue
            for name in (symbol, "SPY"):
                latest = sources.get(name)
                if name in cached:
                    sources[name] = cached[name]
                    continue
                if mature and (
                    not latest
                    or latest["payload"]["bars"][-1]["session_date"]
                    < str(instant.astimezone(EASTERN).date() - timedelta(days=1))
                ):
                    try:
                        day = instant.astimezone(EASTERN).date()
                        raw, _, url = fetch_bars(
                            name,
                            min(d["session_date"] for d in mature) - timedelta(days=5),
                            instant - timedelta(minutes=20),
                            day,
                        )
                        bars = normalize_bars(name, raw, settings.feed, day, now())
                        store(
                            name,
                            "outcome_prices",
                            {"bars": bars, "feed": settings.feed, "adjustment": "split", "url": url},
                            {"feed": settings.feed, "day": day, "values": raw},
                        )
                        sources[name] = latest_sources("outcome_prices", [name]).get(name)
                        cached[name] = sources[name]
                    except Exception:
                        pass  # No inferred outcomes; retry on a later scheduled ingestion.
            for d in items:
                _resolve_decision(conn, d, sources, instant, revisions)


def _resolve_decision(conn, d, sources, instant, revisions):
    own, spy = sources.get(d["symbol"]), sources.get("SPY")
    if not own or not spy or own["payload"]["feed"] != spy["payload"]["feed"]:
        return
    bars = {b["session_date"]: b for b in own["payload"]["bars"]}
    market = {b["session_date"]: b for b in spy["payload"]["bars"]}
    sessions = list(schedule(d["session_date"], d["session_date"] + timedelta(days=20)))
    for h in (1, 5):
        if (
            not revisions
            and conn.execute(
                "SELECT 1 FROM pilot_forecast_outcomes WHERE decision_id=%s AND horizon=%s LIMIT 1",
                (d["id"], h),
            ).fetchone()
        ):
            continue
        if str(h) not in d["payload"].get("horizons", {}) or len(sessions) < h:
            continue
        entry, target = str(d["session_date"]), str(sessions[h - 1])
        if any(s not in bars or s not in market for s in (entry, target)):
            continue
        if instant.astimezone(EASTERN).date() <= sessions[h - 1]:
            continue  # Resolve only on a later calendar day with completed bars.
        identity = digest([d["id"], h, own["id"], spy["id"]])
        outcome = {
            "return": float(bars[target]["close"]) / float(bars[entry]["open"]) - 1,
            "spy_return": float(market[target]["close"]) / float(market[entry]["open"]) - 1,
            "entry_session": entry,
            "target_session": target,
            "source_ids": [own["id"], spy["id"]],
            "feed": own["payload"]["feed"],
            "adjustment": "split",
            "basis": "Next actual session open to target close; dividends excluded",
        }
        conn.execute(
            "INSERT INTO pilot_forecast_outcomes VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (identity, d["id"], h, instant, jb(outcome)),
        )


AGGREGATE_SCOPE = "all_model_versions_descriptive_only"


def _metric_rows(conn, model_version=None):
    return conn.execute(
        """SELECT d.symbol,d.session_date,d.payload AS decision,
    COALESCE(NULLIF(d.payload->>'model',''),'unversioned') AS model_version,
    o.horizon,o.payload AS outcome
    FROM pilot_decisions d JOIN LATERAL
    (SELECT DISTINCT ON(horizon) * FROM pilot_forecast_outcomes WHERE decision_id=d.id ORDER BY horizon,resolved_at DESC,id DESC) o ON true
    WHERE %s::text IS NULL OR COALESCE(NULLIF(d.payload->>'model',''),'unversioned')=%s""",
        (model_version, model_version),
    ).fetchall()


def _metric_reports(rows, model_version=None):
    reports = []
    for h in (1, 5):
        group = [r for r in rows if r["horizon"] == h]
        scope = {
            "aggregation": AGGREGATE_SCOPE if model_version is None else "single_model_version",
            "model_version": model_version,
            "models_included": sorted({r["model_version"] for r in group}),
        }
        if not group:
            reports.append(
                {
                    **scope,
                    "horizon": h,
                    "observations": 0,
                    "status": "inconclusive",
                    "reason": "No resolved prospective outcomes",
                }
            )
            continue
        errors = []
        briers = []
        logs = []
        coverage = []
        for r in group:
            f = r["decision"]["horizons"][str(h)]
            y = r["outcome"]["return"]
            p = max(1e-8, min(1 - 1e-8, f["probability_positive"]))
            label = int(y > 0)
            errors.append(abs(y - f["expected_return"]))
            briers.append((p - label) ** 2)
            logs.append(-label * math.log(p) - (1 - label) * math.log(1 - p))
            coverage.append(f["lower_return"] <= y <= f["upper_return"])
        reports.append(
            {
                **scope,
                "horizon": h,
                "observations": len(group),
                "distinct_dates": len({r["session_date"] for r in group}),
                "mae": sum(errors) / len(group),
                "brier": sum(briers) / len(group),
                "log_loss": sum(logs) / len(group),
                "interval_coverage": sum(coverage) / len(group),
                "status": "inconclusive",
                "reason": "Experimental pilot; correlated stocks/overlapping horizons are not independent validation",
            }
        )
    return reports


def metrics(conn, model_version=None):
    """Legacy two-horizon array; optional exact model filter never pools versions."""
    return _metric_reports(_metric_rows(conn, model_version), model_version)


def metric_views(conn):
    """Read outcome evidence once for descriptive totals and isolated version views."""
    rows = _metric_rows(conn)
    versions = [
        r["model_version"]
        for r in conn.execute(
            "SELECT DISTINCT COALESCE(NULLIF(payload->>'model',''),'unversioned') AS model_version FROM pilot_decisions ORDER BY model_version"
        ).fetchall()
    ]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["model_version"]].append(row)
    return _metric_reports(rows), {
        version: _metric_reports(grouped[version], version) for version in versions
    }


def metrics_by_model(conn):
    return metric_views(conn)[1]
