"""Opt-in local horizon forecast collection; no trading or promotion authority.

Uses the existing append-only research ledger mechanism in a separate directory.
No mutation to the Phase 7 enrollment, models, or original forecast ledger.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from chart_intelligence import store
from chart_intelligence.controller import append, collect, connection
from chart_intelligence.core import identity, parse_bars, sequence
from chart_intelligence.phase8 import HORIZONS, feature, predict
from intelligence.calendar import EASTERN, schedule
from intelligence.config import ROOT, settings
from intelligence.providers import fetch_bars, get_json, normalize_bars
from intelligence.util import canonical


def fresh_inputs(symbols, instant):
    if settings.feed != "sip":
        raise ValueError("No implicit feed substitution")
    daily = {}
    markets = {}
    quotes = {}
    provenance = {}
    for symbol in [*symbols, "SPY", "XLK"]:
        raw, document, _ = fetch_bars(
            symbol, (instant - timedelta(days=90)).date(), instant - timedelta(minutes=15), instant.date()
        )
        acquired = datetime.now(timezone.utc)
        normalized = normalize_bars(symbol, raw, "sip", instant.date(), acquired)
        daily[symbol] = {str(r["session_date"]): r for r in normalized}
        provenance[symbol] = {
            "acquired_at": acquired.isoformat(),
            "response_hash": identity(json.loads(canonical(document))),
        }
        if symbol in symbols:
            snapshot = collect(symbol, datetime.now(timezone.utc))
            # A five-session target can span more than seven calendar days.
            # Preserve enough intraday history to resolve its exact endpoints.
            rows = []
            params = {
                "timeframe": "5Min",
                "start": (instant - timedelta(days=35)).isoformat(),
                "end": (instant - timedelta(minutes=15)).isoformat(),
                "feed": "sip",
                "adjustment": "split",
                "asof": str(instant.date()),
                "limit": 10000,
                "sort": "asc",
            }
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                seen = set()
                for _ in range(25):
                    payload = get_json(
                        client,
                        f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
                        params=params,
                        headers={
                            "APCA-API-KEY-ID": settings.alpaca_key,
                            "APCA-API-SECRET-KEY": settings.alpaca_secret,
                        },
                    )
                    for r in payload.get("bars") or []:
                        t = datetime.fromisoformat(r["t"].replace("Z", "+00:00"))
                        if t + timedelta(minutes=20) <= instant:
                            rows.append([t.isoformat(), *[r[k] for k in ("o", "h", "l", "c", "v")]])
                    token = payload.get("next_page_token")
                    if not token:
                        break
                    if token in seen:
                        raise ValueError("Repeated intraday pagination")
                    seen.add(token)
                    params["page_token"] = token
                else:
                    raise ValueError("Incomplete long-horizon history")
            markets[symbol] = rows
            quotes[symbol] = snapshot.get("quote")
    return json.loads(
        canonical(
            {
                "daily": daily,
                "markets": markets,
                "quotes": quotes,
                "acquired_at": datetime.now(timezone.utc),
                "provenance": provenance,
                "feed": "sip",
                "adjustment": "split",
            }
        )
    )


def targets(horizon, decision, calendar):
    day = decision.astimezone(EASTERN).date()
    if horizon == "one_hour":
        return decision, decision + timedelta(hours=1)
    days = [d for d in calendar if d > day]
    count = HORIZONS[horizon]
    if len(days) < count:
        raise ValueError("Missing future exchange calendar")
    return calendar[days[0]]["open"] + timedelta(minutes=5), calendar[days[count - 1]]["close"] - timedelta(
        minutes=15
    )


def tick(bundle, symbols, collector=fresh_inputs, clock=None, root=None, predictor=predict):
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = clock()
    if started.tzinfo is None or started < datetime.fromisoformat(bundle["not_before"]):
        raise ValueError("Cannot backdate enrollment")
    if not symbols or len(set(symbols)) != len(symbols) or len(symbols) > 6:
        raise ValueError("Bounded explicit universe required")
    root = Path(root or ROOT / ".local/phase8/forward")
    db = connection(root)
    issued = 0
    resolved = 0
    errors = []
    try:
        db.execute("BEGIN IMMEDIATE")
        raw = collector(symbols, started)
        completed = clock()
        acquired = datetime.fromisoformat(raw["acquired_at"])
        if (
            raw.get("feed") != "sip"
            or raw.get("adjustment") != "split"
            or not 0 <= (completed - acquired).total_seconds() <= 120
        ):
            raise ValueError("Stale source or wrong feed")
        source_id = store.put("fresh_inputs", raw, root)
        markets = {s: parse_bars(v) for s, v in raw["markets"].items()}
        oldest = min(datetime.fromisoformat(d).date() for series in raw["daily"].values() for d in series)
        day = completed.astimezone(EASTERN).date()
        calendar = schedule(oldest, day + timedelta(days=35))
        for symbol, q in raw["quotes"].items():
            if q:
                append(db, identity([symbol, q]), "quote", {**q, "symbol": symbol, "source_id": source_id})
        for record in db.execute("SELECT key,payload FROM evidence WHERE kind='prediction'").fetchall():
            key, payload = record
            p = json.loads(payload)
            if db.execute("SELECT 1 FROM evidence WHERE key=?", ("outcome:" + key,)).fetchone():
                continue
            end = datetime.fromisoformat(p["target_time"])
            entry = datetime.fromisoformat(p["entry_time"])
            if completed < end + timedelta(minutes=20):
                continue
            bars = markets.get(p["symbol"], {})
            expected = [
                cal["open"] + timedelta(minutes=5 * i)
                for d, cal in calendar.items()
                for i in range((cal["close"] - cal["open"]) // timedelta(minutes=5))
                if entry <= cal["open"] + timedelta(minutes=5 * i) <= end
            ]
            if entry not in bars or end not in bars or any(t not in bars for t in expected):
                append(
                    db,
                    "coverage:" + key,
                    "resolution_coverage",
                    {"prediction_id": key, "status": "INTRADAY_PATH_INCOMPLETE"},
                )
                continue
            actual = float(bars[end][0] / bars[entry][0] - 1)
            downside = min(
                0.0, min(float(bars[t][2]) for t in expected if t < end) / float(bars[entry][0]) - 1
            )
            basis = "Delayed SIP frozen entry/exit bar opens; not quote execution"
            append(
                db,
                "outcome:" + key,
                "outcome",
                {
                    "prediction_id": key,
                    **p,
                    "resolved_at": completed.isoformat(),
                    "actual_return": actual,
                    "actual_downside": downside,
                    "basis": basis,
                    "outcome_source_id": source_id,
                    "hypothetical_quote_outcome": quote_outcome(db, p, completed),
                },
            )
            resolved += 1
        local = started.astimezone(EASTERN)
        active = []
        if day in calendar:
            if local.hour in (12, 14) and local.minute < 2:
                active = ["one_hour"]
            elif local.hour == 20 and 30 <= local.minute < 32:
                active = [h for h in HORIZONS if h != "one_hour"]
        for horizon in active:
            decision = local.replace(minute=0 if horizon == "one_hour" else 30, second=0, microsecond=0)
            if completed >= decision + timedelta(minutes=2):
                errors.append(
                    {"horizon": horizon, "reason": "Issuance deadline missed during data collection"}
                )
                continue
            entry, end = targets(horizon, decision, calendar)
            for symbol in symbols:
                key = identity([bundle["id"], horizon, symbol, decision.isoformat()])
                if db.execute("SELECT 1 FROM evidence WHERE key=?", (key,)).fetchone():
                    continue
                try:
                    f = feature(
                        raw["daily"], symbol, str(day), calendar, include_origin=horizon != "one_hour"
                    )
                    if horizon == "one_hour":
                        f["x"] += sequence(markets[symbol], decision, calendar=calendar)["x"]
                    model = store.read(bundle["models"][horizon], bundle["model_root"])["payload"]
                    forecast = predictor(model, [f | {"horizon": horizon}])[0]
                    issued_at = clock()
                    if issued_at >= decision + timedelta(minutes=2):
                        raise ValueError("Issuance deadline missed")
                    append(
                        db,
                        key,
                        "prediction",
                        {
                            "bundle_id": bundle["id"],
                            "symbol": symbol,
                            "horizon": horizon,
                            "decision": decision.isoformat(),
                            "issued_at": issued_at.isoformat(),
                            "entry_time": entry.isoformat(),
                            "target_time": end.isoformat(),
                            "forecast": forecast,
                            "source_id": source_id,
                            "model_id": bundle["models"][horizon],
                            "evidence_type": "PROSPECTIVE_RESEARCH_FORECAST",
                            "role": bundle["roles"][horizon],
                            "execution_authority": False,
                            "promotion_authority": False,
                        },
                    )
                    issued += 1
                except (KeyError, ValueError) as exc:
                    errors.append({"symbol": symbol, "horizon": horizon, "reason": str(exc)})
        result = {
            "bundle_id": bundle["id"],
            "observed_at": completed.isoformat(),
            "issued": issued,
            "resolved": resolved,
            "errors": errors,
            "issuance_status": "ISSUED" if issued else "OUTSIDE_WINDOW_OR_ALREADY_RECORDED",
            "execution_authority": False,
            "promotion_authority": False,
        }
        append(db, identity(result), "controller_run", result)
        db.commit()
        return result
    finally:
        db.close()


def quote_outcome(db, p, instant):
    """Individual quote-priced opportunity, never an actual or allocated fill."""
    if p["forecast"]["probability_up"] < 0.55:
        return {"status": "CONFIDENCE_REJECTED", "net_pnl": None}
    start = max(datetime.fromisoformat(p["entry_time"]), datetime.fromisoformat(p["issued_at"]))
    end = datetime.fromisoformat(p["target_time"])
    quotes = []
    for record in db.execute("SELECT payload FROM evidence WHERE kind='quote'"):
        q = json.loads(record[0])
        t = datetime.fromisoformat(q["source_time"].replace("Z", "+00:00"))
        a = datetime.fromisoformat(q["acquired_at"])
        if (
            q["symbol"] == p["symbol"]
            and start <= t <= instant
            and 0 < q["bid"] <= q["ask"]
            and 0 <= (a - t).total_seconds() <= 15
        ):
            quotes.append((t, q))
    quotes.sort(key=lambda v: v[0])
    entries = [(t, q) for t, q in quotes if t <= start + timedelta(minutes=2)]
    if not entries:
        return {"status": "UNPRICED_ENTRY", "net_pnl": None}
    entrytime, q = entries[0]
    cost = q["ask"] / q["bid"] - 1 + 0.0012
    if p["forecast"]["expected_return"] <= cost + 0.0005:
        return {"status": "COST_REJECTED", "net_pnl": None}
    price = q["ask"] * 1.0005
    for t, q in quotes:
        if t <= entrytime:
            continue
        move = q["bid"] / price - 1
        if (move <= -0.02 or move >= 0.03 or t >= end) and t <= end + timedelta(minutes=2):
            out = q["bid"] * 0.9995
            qty = 25 / price
            fees = 0.0001 * (25 + out * qty)
            return {
                "status": "HYPOTHETICAL_IEX_QUOTE_PRICED",
                "net_pnl": out * qty - 25 - fees,
                "fees": fees,
                "entry_time": entrytime.isoformat(),
                "exit_time": t.isoformat(),
                "limitation": "Individual hypothetical $25 opportunity. No guaranteed fill, portfolio allocation, depth or measured slippage.",
            }
    return {"status": "UNPRICED_EXIT", "net_pnl": None, "exposure_uncertain": True}


def summary(root=None):
    """Read all rows, not a truncated dashboard page, for prospective counts."""
    root = Path(root or ROOT / ".local/phase8/forward")
    if not (root / "controller.sqlite").exists():
        return {"status": "NOT_RUN", "predictions": 0, "resolved": 0}
    db = connection(root)
    try:
        predictions = [
            json.loads(r[0]) for r in db.execute("SELECT payload FROM evidence WHERE kind='prediction'")
        ]
        outcomes = [json.loads(r[0]) for r in db.execute("SELECT payload FROM evidence WHERE kind='outcome'")]
        result = {}
        for h in HORIZONS:
            p = [r for r in predictions if r["horizon"] == h]
            o = [r for r in outcomes if r["horizon"] == h]
            dates = {r["decision"][:10] for r in o}
            result[h] = {
                "predictions": len(p),
                "resolved": len(o),
                "dates": len(dates),
                "mae": sum(abs(r["actual_return"] - r["forecast"]["expected_return"]) for r in o) / len(o)
                if o
                else None,
                "brier": sum(((r["actual_return"] > 0) - r["forecast"]["probability_up"]) ** 2 for r in o)
                / len(o)
                if o
                else None,
                "direction_accuracy": sum(
                    (r["forecast"]["probability_up"] >= 0.5) == (r["actual_return"] > 0) for r in o
                )
                / len(o)
                if o
                else None,
                "quote_priced_opportunities": sum(
                    r["hypothetical_quote_outcome"]["status"] == "HYPOTHETICAL_IEX_QUOTE_PRICED" for r in o
                ),
                "independently_validated": False,
                "status": "INSUFFICIENT_CONFIRMATION"
                if len(dates) < 126
                else "REQUIRES_INDEPENDENT_COST_AND_PORTFOLIO_REVIEW",
            }
        return {
            "status": "RECORDED",
            "predictions": len(predictions),
            "resolved": len(outcomes),
            "horizons": result,
            "execution_authority": False,
            "promotion_authority": False,
        }
    finally:
        db.close()
