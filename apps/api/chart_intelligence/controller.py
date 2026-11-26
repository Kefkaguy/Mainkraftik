"""Local research loop: read-only market data, immutable forecasts, no promotion.

SQLite transactions serialize workers and release on process death. Unique
semantic keys prevent late/repeated issuance after restart. No broker imports.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np

from chart_intelligence import store
from chart_intelligence.core import Protocol, evaluate, identity, infer, label, parse_bars, sequence
from intelligence.calendar import EASTERN
from intelligence.config import settings


def connection(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "controller.sqlite", timeout=2)
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS evidence (key TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    for action in ("UPDATE", "DELETE"):
        db.execute(
            f"CREATE TRIGGER IF NOT EXISTS immutable_{action} BEFORE {action} ON evidence BEGIN SELECT RAISE(ABORT,'Research evidence is append-only'); END"
        )
    db.commit()
    return db


def append(db, key, kind, payload):
    db.execute(
        "INSERT OR IGNORE INTO evidence VALUES(?,?,?)",
        (key, kind, json.dumps(payload, sort_keys=True, allow_nan=False)),
    )


def collect(symbol, instant):
    if not symbol.isalnum() or len(symbol) > 10:
        raise ValueError("Invalid research symbol")
    params = {
        "timeframe": "5Min",
        "start": (instant - timedelta(days=7)).isoformat(),
        # Delayed SIP access requires an end outside the recent-data window,
        # including outside market hours. Keep the same SIP feed and the
        # declared feature availability lag; never substitute IEX candles.
        "end": (instant - timedelta(minutes=15)).isoformat(),
        "feed": "sip",
        "adjustment": "split",
        "asof": str(instant.date()),
        "sort": "asc",
        "limit": 10000,
    }
    rows = []
    pages = []
    tokens = set()
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        for _ in range(25):
            response = client.get(
                f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
                params=params,
                headers={
                    "APCA-API-KEY-ID": settings.alpaca_key,
                    "APCA-API-SECRET-KEY": settings.alpaca_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()
            pages.append(identity(payload))
            for r in payload.get("bars") or []:
                t = datetime.fromisoformat(r["t"].replace("Z", "+00:00"))
                if t + timedelta(minutes=20) <= instant:
                    rows.append([t.isoformat(), *[r[k] for k in ("o", "h", "l", "c", "v")]])
            token = payload.get("next_page_token")
            if not token:
                quote = None
                try:
                    qresponse = client.get(
                        f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes/latest",
                        params={"feed": "iex"},
                        headers={
                            "APCA-API-KEY-ID": settings.alpaca_key,
                            "APCA-API-SECRET-KEY": settings.alpaca_secret,
                        },
                    )
                    qresponse.raise_for_status()
                    q = qresponse.json()["quote"]
                    quote = {
                        "symbol": symbol,
                        "bid": q["bp"],
                        "ask": q["ap"],
                        "bid_size": q.get("bs"),
                        "ask_size": q.get("as"),
                        "source_time": q["t"],
                        "acquired_at": datetime.now(timezone.utc).isoformat(),
                        "feed": "iex",
                    }
                except (httpx.HTTPError, KeyError, ValueError):
                    pass  # An absent quote never becomes a simulated fill.
                return {
                    "symbol": symbol,
                    "bars": rows,
                    "acquired_at": datetime.now(timezone.utc).isoformat(),
                    "feed": "sip",
                    "adjustment": "split",
                    "page_hashes": pages,
                    "quote": quote,
                }
            if token in tokens:
                raise ValueError("Repeated data page")
            tokens.add(token)
            params["page_token"] = token
    raise ValueError("Incomplete data pagination")


def drift(resolved, reference, min_dates=40):
    days = {r["date"] for r in resolved}
    if len(days) < min_dates:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "dates": len(days),
            "required_dates": min_dates,
            "promotion_authority": False,
        }
    # Compare date means, preventing a busy day from dominating the monitor.
    daily = []
    for day in sorted(days):
        group = [r for r in resolved if r["date"] == day]
        daily.append(
            [
                np.mean([abs(r["y"] - r["forecast"]["expected_return"]) for r in group]),
                np.mean([((r["y"] > 0) - r["forecast"]["probability_up"]) ** 2 for r in group]),
                np.mean(
                    [r["forecast"]["lower_return"] <= r["y"] <= r["forecast"]["upper_return"] for r in group]
                ),
            ]
        )
    mae, brier, coverage = np.mean(daily[-60:], axis=0)
    reasons = []
    if mae > max(reference["mae"] * 1.5, 0.0001):
        reasons.append("RETURN_ERROR_INCREASED")
    if brier > reference["brier"] + 0.02:
        reasons.append("DIRECTION_CALIBRATION_DEGRADED")
    if coverage < 0.7:
        reasons.append("INTERVAL_UNDERCOVERAGE")
    return {
        "status": "ALERT" if reasons else "WITHIN_MONITOR_LIMITS",
        "reasons": reasons,
        "mae": float(mae),
        "brier": float(brier),
        "coverage": float(coverage),
        "dates": len(days),
        "promotion_authority": False,
        "interpretation": "Monitoring thresholds; not a significance or profitability test",
    }


def entry_opportunity(prediction, quote, instant):
    """Research intent only; a later observed quote is needed to price entry."""
    from chart_intelligence.phase7_execution import quote_at

    reasons = []
    usable = (
        quote_at({(quote.get("symbol"), instant.isoformat()): quote}, quote.get("symbol"), instant)
        if quote
        else None
    )
    if usable is None:
        reasons.append("FRESH_EXECUTABLE_QUOTE_UNAVAILABLE")
    if prediction["probability_up"] < 0.55:
        reasons.append("PREDICTION_CONFIDENCE")
    cost = quote["ask"] / quote["bid"] - 1 + 0.0012 if usable else None
    if cost is not None and prediction["expected_return"] <= cost + 0.0005:
        reasons.append("EXPECTED_RETURN_AFTER_QUOTED_SPREAD_AND_ALLOWANCES")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "notional": 25,
        "estimated_round_trip_cost": cost,
        "status": "AWAIT_POST_ISSUANCE_QUOTE" if not reasons else "REJECTED",
        "evidence_type": "HYPOTHETICAL_RESEARCH_INTENT",
        "execution_authority": False,
    }


def resolve_opportunity(db, p, protocol, instant):
    intent = p.get("hypothetical_entry") or {}
    if not intent.get("eligible"):
        return {"status": "NOT_ELIGIBLE_OR_NOT_RECORDED", "reasons": intent.get("reasons", [])}
    issued = datetime.fromisoformat(p["issued_at"])
    end = datetime.fromisoformat(p["decision"]) + timedelta(minutes=protocol.minutes * protocol.horizon_bars)
    quotes = []
    for row in db.execute("SELECT payload FROM evidence WHERE kind='quote'"):
        q = json.loads(row[0])
        if q["symbol"] != p["symbol"]:
            continue
        stamp = datetime.fromisoformat(q["source_time"].replace("Z", "+00:00"))
        acquired = datetime.fromisoformat(q["acquired_at"])
        if (
            issued <= stamp <= instant
            and 0 < q["bid"] <= q["ask"]
            and 0 <= (acquired - stamp).total_seconds() <= 15
        ):
            quotes.append((stamp, q))
    quotes.sort(key=lambda t: t[0])
    entries = [q for t, q in quotes if t <= issued + timedelta(minutes=2)]
    if not entries:
        return {"status": "UNPRICED_ENTRY", "net_pnl": None}
    entry = entries[0]
    entrytime = datetime.fromisoformat(entry["source_time"].replace("Z", "+00:00"))
    price = entry["ask"] * 1.0005
    for t, q in quotes:
        if t <= entrytime:
            continue
        move = q["bid"] / price - 1
        if move <= -0.02 or move >= 0.03 or end <= t <= end + timedelta(minutes=2):
            if t > end + timedelta(minutes=2):
                break
            out = q["bid"] * (1 - 0.0005)
            qty = 25 / price
            fees = 0.0001 * (25 + out * qty)
            return {
                "status": "HYPOTHETICAL_QUOTE_PRICED",
                "notional": 25,
                "net_pnl": qty * out - 25 - fees,
                "fees": fees,
                "entry_quote_time": entrytime.isoformat(),
                "exit_quote_time": t.isoformat(),
                "exit_reason": "STOP" if move <= -0.02 else "TARGET" if move >= 0.03 else "TIME",
                "limitation": "Observed IEX quotes plus modeled allowances; no fill, depth, queue or rejection guarantee",
            }
    return {"status": "UNPRICED_EXIT", "net_pnl": None, "open_exposure_uncertain": True}


def tick(report_id, symbols, collector=collect, instant=None, root=None, clock=None):
    fixture_time = instant
    if clock is None:
        clock = (lambda: fixture_time) if fixture_time is not None else (lambda: datetime.now(timezone.utc))
    instant = instant or clock()
    if instant.tzinfo is None:
        raise ValueError("Aware controller time required")
    if not symbols or len(set(symbols)) != len(symbols) or len(symbols) > 6:
        raise ValueError("Explicit bounded research universe required")
    root = Path(root or store.STORE)
    report = store.read(report_id, root)["payload"]
    if report.get("not_before") and instant < datetime.fromisoformat(report["not_before"]):
        raise ValueError("Cannot issue or resolve before prospective enrollment")
    model = store.read(report["model_id"], root)["payload"]
    protocol = Protocol(**report["protocol"])
    db = connection(root)
    try:
        db.execute("BEGIN IMMEDIATE")
        errors = []
        issued = []
        for symbol in symbols:
            try:
                snapshot = collector(symbol, instant)
                if snapshot.get("feed") != "sip" or snapshot.get("adjustment") != "split":
                    raise ValueError("Wrong feed or adjustment")
                completed = clock()
                acquired = datetime.fromisoformat(snapshot["acquired_at"])
                if acquired > completed + timedelta(seconds=2) or completed - acquired > timedelta(minutes=2):
                    raise ValueError("Stale acquisition or clock mismatch")
                source_id = store.put("monitor_snapshot", snapshot, root)
                if snapshot.get("quote"):
                    quote = snapshot["quote"]
                    append(db, identity([symbol, quote]), "quote", {**quote, "source_id": source_id})
                bars = parse_bars(snapshot["bars"])
                pending = db.execute("SELECT key,payload FROM evidence WHERE kind='prediction'").fetchall()
                for item in pending:
                    p = json.loads(item["payload"])
                    if (
                        p["symbol"] != symbol
                        or db.execute(
                            "SELECT 1 FROM evidence WHERE key=?", ("outcome:" + item["key"],)
                        ).fetchone()
                    ):
                        continue
                    decision = datetime.fromisoformat(p["decision"])
                    if (
                        decision
                        + timedelta(
                            minutes=protocol.minutes * protocol.horizon_bars
                            + protocol.availability_delay_minutes
                        )
                        > instant
                    ):
                        continue
                    try:
                        outcome = label(bars, decision, protocol)
                    except ValueError:
                        continue
                    opportunity = resolve_opportunity(db, p, protocol, completed)
                    append(
                        db,
                        "outcome:" + item["key"],
                        "outcome",
                        {
                            **p,
                            **outcome,
                            "outcome_source_id": source_id,
                            "resolved_at": completed.isoformat(),
                            "hypothetical_opportunity": opportunity,
                        },
                    )
                local = completed.astimezone(EASTERN)
                decision = local.replace(minute=0, second=0, microsecond=0)
                key = identity([report_id, symbol, decision.isoformat()])
                if local.hour not in (12, 14) or local - decision >= timedelta(minutes=2):
                    continue
                if db.execute("SELECT 1 FROM evidence WHERE key=?", (key,)).fetchone():
                    continue
                features = sequence(bars, decision, protocol)
                prediction = infer(model, [features])[0]
                issued_at = clock()
                if issued_at - decision >= timedelta(minutes=2):
                    errors.append(
                        {
                            "symbol": symbol,
                            "status": "ISSUANCE_DEADLINE_MISSED",
                            "reason": "Collection or inference completed too late",
                        }
                    )
                    continue
                row = {
                    "symbol": symbol,
                    "date": str(local.date()),
                    "decision": decision.isoformat(),
                    "issued_at": issued_at.isoformat(),
                    "report_id": report_id,
                    "model_id": report["model_id"],
                    "source_id": source_id,
                    "forecast": prediction,
                    "latest_feature_available_at": features["latest_feature_available_at"],
                    "features": features,
                    "action": "RESEARCH_OBSERVATION",
                    "evidence_type": "PROSPECTIVE_RESEARCH",
                    "execution_authority": False,
                    "hypothetical_entry": entry_opportunity(prediction, snapshot.get("quote"), issued_at),
                }
                append(db, key, "prediction", row)
                issued.append(row)
            except (ValueError, KeyError, httpx.HTTPError) as exc:
                # No raw provider responses or credential-bearing exception text.
                errors.append(
                    {"symbol": symbol, "reason": type(exc).__name__, "status": "DATA_OR_MODEL_UNAVAILABLE"}
                )
        outcomes = [
            json.loads(r[0])
            for r in db.execute("SELECT payload FROM evidence WHERE kind='outcome'")
            if json.loads(r[0])["report_id"] == report_id
        ]
        monitoring = drift(outcomes, report["models"]["sequence_mlp"])
        run = {
            "observed_at": instant.isoformat(),
            "report_id": report_id,
            "monitored": symbols,
            "issued": len(issued),
            "outcomes": len(outcomes),
            "errors": errors,
            "monitoring": monitoring,
            "execution_authority": False,
            "promotion_authority": False,
            "issuance_status": "ISSUED" if issued else "NO_NEW_FORECAST_OR_OUTSIDE_WINDOW",
        }
        append(db, identity(run), "controller_run", run)
        if monitoring["status"] == "ALERT":
            append(
                db,
                identity([report_id, monitoring]),
                "offline_proposal",
                {
                    "status": "PENDING_OFFLINE_REVIEW",
                    "reference_report": report_id,
                    "monitoring": monitoring,
                    "action": "Evaluate frozen baselines on newly accumulated evidence; require a new declaration before fitting improvements",
                    "promotion_authority": False,
                },
            )
            # A bounded refit runs only after enough mature new dates exist.
            # It receives its own untouched temporal suffix, never changes the
            # controller's pinned report/model and never publishes a model.
            dates = len({o["date"] for o in outcomes})
            slot = f"offline:{report_id}:{dates // 40}"
            if dates >= 220 and not db.execute("SELECT 1 FROM evidence WHERE key=?", (slot,)).fetchone():
                rows = [
                    {**o, **o["features"]}
                    for o in sorted(outcomes, key=lambda o: (o["decision"], o["symbol"]))
                ]
                candidate, comparison = evaluate(rows, protocol)
                candidate_id = store.put("offline_candidate", candidate, root)
                comparison["model_id"] = candidate_id
                comparison["reference_report"] = report_id
                comparison_id = store.put("offline_evaluation", comparison, root)
                append(
                    db,
                    slot,
                    "offline_evaluation",
                    {
                        "report_id": comparison_id,
                        "model_id": candidate_id,
                        "promotion_authority": False,
                        "execution_authority": False,
                    },
                )
        db.commit()
        return run
    finally:
        db.close()


def status(root=None):
    root = Path(root or store.STORE)
    if not (root / "controller.sqlite").exists():
        return {
            "status": "NOT_RUNNING",
            "predictions": [],
            "runs": [],
            "outcomes": [],
            "execution_authority": False,
        }
    db = connection(root)
    try:
        data = {
            kind: [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM evidence WHERE kind=? ORDER BY rowid DESC LIMIT 100", (kind,)
                )
            ]
            for kind in ("prediction", "outcome", "controller_run", "offline_proposal")
        }
        return {"status": "RECORDED", **data, "execution_authority": False}
    finally:
        db.close()
