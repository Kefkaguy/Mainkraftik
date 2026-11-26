"""Immutable decisions and virtual outcomes for the versioned Mainkraft policies."""

from datetime import datetime, timedelta

import httpx

from intelligence.calendar import EASTERN, schedule
from intelligence.util import digest, now
from paperpilot.config import current_symbols, settings
from paperpilot.data import latest_sources, store
from paperpilot.db import connect, jb
from paperpilot.intraday import COST, fetch_intraday, index_bars, simulate
from paperpilot.strategy import (
    POLICY,
    PROBABILITY_BLOCK,
    STRATEGIES,
    SWING_EXPECTANCY_TRIAL,
    bundle,
    decision_cutoff,
    entry_reasons,
    features,
    forecast,
    swing_trial_active,
)
from paperpilot.universe import selection


def issue_signals(instant):
    session = instant.astimezone(EASTERN).date()
    close = schedule(session, session).get(session)
    cutoff = decision_cutoff(session)
    if not close or not cutoff <= instant < cutoff + timedelta(minutes=10):
        return
    with connect() as conn:
        if conn.execute("SELECT 1 FROM pilot_strategy_sessions WHERE session_date=%s", (session,)).fetchone():
            return
    trial = swing_trial_active(session, settings.paper_swing_trial_start)
    selected = selection(session)
    if not selected or selected["cutoff"] > cutoff:
        return
    models = bundle()
    symbols = current_symbols(session)
    prices = latest_sources("daily_prices", [*symbols, "SPY", "XLK"], acquired_by=cutoff)
    with connect() as conn:
        research = conn.execute(
            "SELECT payload FROM pilot_research_runs WHERE id=%s", (selected["research_id"],)
        ).fetchone()["payload"]
        source_news = conn.execute(
            "SELECT acquired_at,payload FROM pilot_sources WHERE id=%s AND acquired_at<=%s",
            (research["news_source_id"], cutoff),
        ).fetchone()
        readiness = conn.execute(
            """SELECT * FROM pilot_readiness WHERE observed_at<=%s AND observed_at>%s
            ORDER BY observed_at DESC,id DESC LIMIT 1""",
            (cutoff, cutoff - timedelta(minutes=2)),
        ).fetchone()
        controls = conn.execute("SELECT * FROM pilot_settings WHERE id=1").fetchone()
    ready = bool(
        readiness
        and readiness["payload"].get("status") == "verified"
        and readiness["payload"].get("cash_consistent")
        and controls["enabled"]
        and controls["updated_at"] <= cutoff
        and settings.paper_execution_permitted
    )
    picks = {p["symbol"]: p for p in research["picks"]}
    sentiment = research.get("hf_sentiment") or {}
    tone = {}
    if sentiment and datetime.fromisoformat(sentiment["acquired_at"]) <= cutoff:
        tone = sentiment.get("per_symbol", {})
    articles = source_news["payload"]["articles"] if source_news else {}
    news_counts = {symbol: [0, 0] for symbol in symbols}
    for article in articles.values():
        created = datetime.fromisoformat(article["created_at"])
        updated = datetime.fromisoformat(article["updated_at"])
        if max(created, updated) > cutoff:
            continue
        for symbol in set(article.get("symbols") or []) & news_counts.keys():
            count = news_counts[symbol]
            count[1] += 1
            if created >= cutoff - timedelta(hours=24):
                count[0] += 1
    inputs, errors = {}, {}
    for symbol in symbols:
        try:
            names = (symbol, "SPY", "XLK")
            if any(
                prices.get(s, {}).get("payload", {}).get("feed") != "sip"
                or prices[s]["payload"].get("adjustment") != "split"
                for s in names
            ):
                raise ValueError("Cutoff-safe split-adjusted SIP inputs unavailable")
            row = features(session, *(prices[s]["payload"]["bars"] for s in names))
            inputs[symbol] = row | {
                "source_ids": [prices[s]["id"] for s in names],
                "news_stance": picks[symbol]["stance"],
                "news_features": {
                    "recent_24h": news_counts[symbol][0],
                    "observed_window_total": news_counts[symbol][1],
                    "truncated": source_news["payload"]["truncated"],
                    "acquired_at": source_news["acquired_at"],
                    "source_id": research["news_source_id"],
                }
                if source_news
                else None,
                "news_source_ids": picks[symbol]["source_ids"],
                "sentiment": tone.get(symbol, {}).get("assessment"),
            }
        except ValueError as exc:
            errors[symbol] = str(exc)

    shadow, shadow_error = {}, None
    if inputs:
        try:
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                response = client.post(
                    "http://challenger:8400/score",
                    json={
                        "bundle_id": models["bundle_id"],
                        "inputs": {s: row["x"] for s, row in inputs.items()},
                    },
                )
                response.raise_for_status()
                data = response.json()
            if data["bundle_id"] != models["bundle_id"]:
                raise ValueError("Challenger model differs from approved bundle")
            shadow = data["scores"]
        except (httpx.HTTPError, ValueError, KeyError):
            shadow_error = "Challenger scoring unavailable"
    issued = now()
    if issued >= cutoff + timedelta(minutes=10):
        return  # Never enter against a late run.
    with connect() as conn:
        inserted = conn.execute(
            """INSERT INTO pilot_strategy_sessions VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING session_date""",
            (
                session,
                cutoff,
                issued,
                models["bundle_id"],
                jb(
                    {
                        "policy": POLICY,
                        "research_id": selected["research_id"],
                        "selected": list(symbols),
                        "readiness_id": readiness["id"] if readiness else None,
                        "paper_ready": ready,
                        "shadow_error": shadow_error,
                    }
                ),
            ),
        ).fetchone()
        if not inserted:
            return
        for symbol in symbols:
            for strategy in STRATEGIES:
                version = models["strategies"][strategy]
                for role in ("paper", "challenger"):
                    result = {
                        "bundle_id": models["bundle_id"],
                        "model_family": "linear" if role == "paper" else "histboost",
                        "stop": version["stop"],
                        "take": version["take"],
                        "protocol": POLICY,
                        "validated": False,
                    }
                    row = inputs.get(symbol)
                    if row is None:
                        action, reasons = "SKIP", [errors[symbol]]
                    elif role == "challenger" and symbol not in shadow.get(strategy, {}):
                        action, reasons = "SKIP", [shadow_error or "Challenger score missing"]
                    else:
                        score = (
                            forecast(version["model"], row) if role == "paper" else shadow[strategy][symbol]
                        )
                        result.update(row | score)
                        reasons = entry_reasons(strategy, result, row["news_stance"], row["sentiment"])
                        action = "HOLD" if reasons else "BUY"
                        if (
                            role == "paper"
                            and ready
                            and trial
                            and strategy == "swing"
                            and reasons == [PROBABILITY_BLOCK]
                        ):
                            result["entry_policy"] = SWING_EXPECTANCY_TRIAL
                            result["entry_notional"] = settings.paper_swing_trial_notional
                            reasons = [
                                f"${settings.paper_swing_trial_notional} Swing expectancy trial · signal gates passed"
                            ]
                            action = "BUY"
                        if role == "paper" and not ready:
                            action = "SKIP"
                            reasons.append("Paper account not verified before decision cutoff")
                    identity = digest([session, symbol, strategy, role, models["bundle_id"]])
                    from paperpilot.decision_trace import signal_trace
                    result["decision_trace"] = signal_trace(strategy, result, ready, role, reasons, action)
                    conn.execute(
                        "INSERT INTO pilot_strategy_signals VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            identity,
                            session,
                            symbol,
                            strategy,
                            role,
                            issued,
                            action,
                            jb(result | {"reasons": reasons}),
                        ),
                    )


def resolve_strategies(instant):
    """Resolve comparable model and virtual-trade targets, never Alpaca executions."""
    today = instant.astimezone(EASTERN).date()
    with connect() as conn:
        pending = conn.execute(
            """SELECT s.*,o.signal_id AS resolved,v.signal_id AS virtual_resolved,entry.pick AS virtual_pick
            FROM pilot_strategy_signals s
            LEFT JOIN pilot_strategy_outcomes o ON o.signal_id=s.id
            LEFT JOIN pilot_virtual_outcomes v ON v.signal_id=s.id
            LEFT JOIN pilot_shadow_sessions sh ON sh.session_date=s.session_date
            LEFT JOIN LATERAL (SELECT pick FROM jsonb_array_elements(sh.picks) pick
                WHERE pick->>'signal_id'=s.id) entry ON true
            WHERE (o.signal_id IS NULL OR (entry.pick IS NOT NULL AND v.signal_id IS NULL))
            AND s.payload ? 'expected_net_return' 
            AND s.session_date<=%s ORDER BY
             s.session_date + CASE WHEN s.strategy='swing' THEN 4 ELSE 0 END,s.symbol LIMIT 240""",
            (today,),
        ).fetchall()
    cache = {}
    for row in pending:
        session, kind, symbol = row["session_date"], row["strategy"], row["symbol"]
        cal = list(schedule(session, session + timedelta(days=18)).values())[: STRATEGIES[kind]["horizon"]]
        if len(cal) != STRATEGIES[kind]["horizon"] or instant < cal[-1]["close"] + timedelta(minutes=20):
            continue
        key = (symbol, session, len(cal))
        if key not in cache:
            try:
                data = fetch_intraday(symbol, cal[0]["open"], cal[-1]["close"])
                source = store(
                    symbol, "mainkraft_outcome_sip", data, {"request": data["request"], "bars": data["bars"]}
                )
                cache[key] = (index_bars(data), source)
            except (ValueError, httpx.HTTPError):
                cache[key] = None
        if cache[key] is None:
            continue
        bars, source = cache[key]
        simulated = simulate(bars, cal, row["payload"]["stop"], row["payload"]["take"])
        selected = row["virtual_pick"]
        virtual = None
        if selected and not row["virtual_resolved"]:
            virtual = simulate(
                bars,
                cal,
                row["payload"]["stop"],
                row["payload"]["take"],
                cost=selected["cost"],
                virtual_entry=(cal[0]["open"] + timedelta(minutes=10), selected["midpoint"]),
            )
        with connect() as conn:
            if simulated and not row["resolved"]:
                conn.execute(
                    "INSERT INTO pilot_strategy_outcomes VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (row["id"], now(), jb(simulated | {"source_id": source, "basis": "simulated_sip"})),
                )
            if virtual:
                conn.execute(
                    "INSERT INTO pilot_virtual_outcomes VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        row["id"],
                        now(),
                        jb(virtual | {"source_id": source, "basis": "virtual_iex_mid_sip_path"}),
                    ),
                )


def freeze_shadow(session, quotes, instant):
    """Nominate virtual slots prospectively using the same planning costs."""
    from paperpilot.strategy import planning_return

    active = schedule(session, session).get(session)
    if not active or not active["open"] + timedelta(minutes=5) <= instant < active["open"] + timedelta(
        minutes=6
    ):
        return
    with connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM pilot_strategy_sessions WHERE session_date=%s", (session,)
        ).fetchone():
            return
        if conn.execute("SELECT 1 FROM pilot_shadow_sessions WHERE session_date=%s", (session,)).fetchone():
            return
        rows = conn.execute(
            """SELECT * FROM pilot_strategy_signals WHERE session_date=%s AND (action='BUY' OR (
            role='paper' AND action='SKIP' AND payload->'reasons' = '["Paper account not verified before decision cutoff"]'
            )) AND COALESCE(payload->>'entry_policy','')<>%s ORDER BY symbol""",
            (session, SWING_EXPECTANCY_TRIAL),
        ).fetchall()
        eligible = []
        for row in rows:
            quote = quotes.get(row["symbol"])
            if (
                not quote
                or quote["feed"] != "iex"
                or not -2 <= (instant - quote["source_time"]).total_seconds() <= 15
            ):
                continue
            bid, ask = float(quote["bid"]), float(quote["ask"])
            try:
                net = planning_return(row["payload"], bid, ask)
            except ValueError:
                continue
            if ask / bid - 1 <= 0.005 and net > 0.0005:
                eligible.append((net, row))
        recent = conn.execute(
            "SELECT picks FROM pilot_shadow_sessions WHERE session_date<%s AND session_date>=%s",
            (session, session - timedelta(days=14)),
        ).fetchall()
        carry = [p for r in recent for p in r["picks"] if p.get("swing_expiry", "") >= str(session)]
        expiry = list(schedule(session, session + timedelta(days=18)))[4]
        picks = []
        for role in ("paper", "challenger"):
            existing = [p for p in carry if p["role"] == role]
            used = {p["symbol"] for p in existing}
            for kind, spec in STRATEGIES.items():
                limit = spec["limit"] if kind == "day" else max(0, spec["limit"] - len(existing))
                ordered = sorted(
                    (x for x in eligible if x[1]["strategy"] == kind and x[1]["role"] == role),
                    key=lambda x: (-x[0], x[1]["symbol"]),
                )
                for net, row in [x for x in ordered if x[1]["symbol"] not in used][:limit]:
                    name = row["symbol"]
                    used.add(name)
                    q = quotes[name]
                    bid, ask = float(q["bid"]), float(q["ask"])
                    picks.append(
                        {
                            "signal_id": row["id"],
                            "symbol": name,
                            "strategy": kind,
                            "role": role,
                            "planning_net_return": net,
                            "midpoint": (bid + ask) / 2,
                            "quote_time": q["source_time"],
                            "cost": max(COST, ask / bid - 1 + 0.001),
                            "swing_expiry": str(expiry) if kind == "swing" else "",
                        }
                    )
        conn.execute(
            "INSERT INTO pilot_shadow_sessions VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            (session, instant, jb(picks)),
        )
