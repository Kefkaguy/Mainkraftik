"""Deterministic, recoverable pilot. Unknown state blocks entries; no LLM authority."""

import asyncio
import shutil
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import numpy as np

from intelligence.calendar import EASTERN, schedule
from intelligence.experiments import enriched_panel
from intelligence.portable_models import fit, predict
from intelligence.util import digest, now
from paperpilot.broker import BrokerUnavailable, PaperBroker, verify_account
from paperpilot.config import current_symbols, managed_symbols, settings
from paperpilot.data import latest_sources, poll_quotes, refresh_company_information, refresh_prices
from paperpilot.db import connect, event, jb
from paperpilot.outcomes import resolve
from paperpilot.risk import (
    BUY_NOTIONAL,
    DAILY_LOSS,
    TERMINAL,
    client_id,
    entry_permission,
    scaled_position_cap,
    swing_trial_notional,
)
from paperpilot.simulation_rules import daily_change, position_exit


def hydrate(rows):
    out = []
    for row in rows:
        r = dict(row)
        for k in ("session_date", "adjustment_asof"):
            r[k] = date.fromisoformat(r[k]) if isinstance(r[k], str) else r[k]
        out.append(r)
    return out


def verified_session_close(session, actual):
    planned = schedule(session, session).get(session)
    if not planned or len(actual) != 1 or actual[0].get("date") != str(session):
        raise BrokerUnavailable("Actual broker session calendar unavailable or inconsistent")
    start = datetime.combine(session, time.fromisoformat(actual[0]["open"]), EASTERN).astimezone(UTC)
    close = datetime.combine(session, time.fromisoformat(actual[0]["close"]), EASTERN).astimezone(UTC)
    if start != planned["open"] or close != planned["close"]:
        raise BrokerUnavailable("Broker/exchange session schedules disagree; execution blocked")
    return close


def create_signals():
    instant = now()
    local = instant.astimezone(EASTERN)
    session = local.date()
    cutoff = datetime.combine(session, time(9, 15), EASTERN).astimezone(UTC)
    if session not in schedule(session, session) or not time(9, 15) <= local.time().replace(
        tzinfo=None
    ) < time(9, 25):
        return {"status": "skipped", "reason": "Outside premarket issuance window; no backdating"}
    sources = (
        latest_sources("daily_prices", list((*current_symbols(session), "SPY")), acquired_by=cutoff)
        if settings.paper_discovery_enabled
        else latest_sources("daily_prices", acquired_by=cutoff)
    )
    with connect() as conn:
        # The mutable current status may already contain a post-cutoff update.
        # Select actual completed pre-cutoff observations, including failures;
        # never let a later status erase evidence or resurrect an older success.
        readiness = conn.execute(
            "SELECT * FROM pilot_readiness WHERE observed_at<=%s AND observed_at>%s ORDER BY observed_at DESC,id DESC LIMIT 1",
            (cutoff, cutoff - timedelta(minutes=2)),
        ).fetchone()
        controls = conn.execute("SELECT * FROM pilot_settings WHERE id=1").fetchone()
        ready = bool(
            settings.paper_execution_permitted
            and controls["enabled"]
            and controls["updated_at"] <= cutoff
            and readiness
            and readiness["observed_at"] <= cutoff
            and cutoff - readiness["observed_at"] < timedelta(minutes=2)
            and readiness["payload"].get("status") == "verified"
            and readiness["payload"].get("cash_consistent")
        )
        selected = None
        context_sources = {}
        if settings.paper_market_shadow_enabled:
            context_sources = latest_sources("daily_prices", ["QQQ", "IWM", "TLT"], acquired_by=cutoff)
        if settings.paper_discovery_enabled:
            from paperpilot.universe import selection

            selected = selection(session)
            ready = ready and bool(selected and selected["cutoff"] <= cutoff)
        for symbol in current_symbols(session):
            identity = digest(
                [
                    "paper-pilot-v4-context-cost"
                    if settings.paper_market_shadow_enabled or settings.paper_cost_gate_enabled
                    else "paper-pilot-v3-hf"
                    if settings.paper_sentiment_enabled
                    else "paper-pilot-v2"
                    if settings.paper_discovery_enabled
                    else "paper-pilot-v1",
                    session,
                    symbol,
                ]
            )
            if conn.execute("SELECT 1 FROM pilot_decisions WHERE id=%s", (identity,)).fetchone():
                continue
            result = {
                "model": "experimental-hf-news-screened-ridge-logistic-v4"
                if settings.paper_market_shadow_enabled or settings.paper_cost_gate_enabled
                else "experimental-hf-news-screened-ridge-logistic-v3"
                if settings.paper_sentiment_enabled
                else "experimental-news-screened-ridge-logistic-v2"
                if settings.paper_discovery_enabled
                else "experimental-ridge-logistic-pilot-v1",
                "validated": False,
                "reason": "Data unavailable",
                "horizons": {},
                "account_ready_before_cutoff": ready,
                "readiness_observation_id": readiness["id"] if readiness else None,
                "readiness_observed_at": readiness["observed_at"] if readiness else None,
                "risk_settings": {
                    "daily_loss_limit": float(DAILY_LOSS),
                    "order_timeout_seconds": settings.paper_order_timeout_seconds,
                },
            }
            action = "SKIP"
            try:
                own, market = sources.get(symbol), sources.get("SPY")
                if not own or not market or max(own["acquired_at"], market["acquired_at"]) > cutoff:
                    raise ValueError("Prices not actually acquired before 09:15 cutoff")
                bars, spy = hydrate(own["payload"]["bars"]), hydrate(market["payload"]["bars"])
                sessions = list(schedule(session - timedelta(days=15), session))
                prior = [d for d in sessions if d < session][-1]
                if bars[-1]["session_date"] != prior or spy[-1]["session_date"] != prior:
                    raise ValueError("Prior completed session is missing")
                panel = enriched_panel(bars, spy)
                if not panel or panel[-1]["date"] != prior:
                    raise ValueError("Latest complete session has no usable feature observation")
                for h in (1, 5):
                    rows = [
                        {
                            **r,
                            "end": bars[r["index"] + h]["session_date"],
                            "y": float(bars[r["index"] + h]["close"]) / float(bars[r["index"] + 1]["open"])
                            - 1,
                        }
                        for r in panel
                        if r["index"] + h < len(bars)
                    ]
                    if len(rows) < 504:
                        raise ValueError("Insufficient mature per-stock outcomes")
                    a = int(len(rows) * 0.8)
                    train = [r for r in rows[: a - 5] if r["end"] < rows[a]["date"]]
                    artifact = fit(train, rows[a:], list(range(14)), "linear", {"alpha": 100, "C": 0.1}, h)
                    mean, p, lo, hi = predict(artifact, [panel[-1]])
                    result["horizons"][str(h)] = {
                        "expected_return": float(mean[0]),
                        "probability_positive": float(p[0]),
                        "lower_return": float(lo[0]),
                        "upper_return": float(hi[0]),
                        "artifact": artifact,
                    }
                signal = result["horizons"]["1"]
                if settings.paper_market_shadow_enabled:
                    from paperpilot.market_shadow import issue

                    try:
                        result["market_context_shadow"] = issue(bars, spy, context_sources, cutoff)
                    except ValueError as exc:
                        result["market_context_shadow"] = {
                            "status": "unavailable",
                            "reason": str(exc),
                            "execution_authority": "none",
                        }
                stance = None
                if selected:
                    run = conn.execute(
                        "SELECT payload FROM pilot_research_runs WHERE id=%s", (selected["research_id"],)
                    ).fetchone()
                    pick = next(p for p in run["payload"]["picks"] if p["symbol"] == symbol)
                    stance = pick["stance"]
                    result.update(
                        research_id=selected["research_id"],
                        news_stance=stance,
                        news_rationale=pick["rationale"],
                        news_source_ids=pick["source_ids"],
                    )
                    if settings.paper_sentiment_enabled:
                        evidence = run["payload"].get("hf_sentiment")
                        if not evidence or datetime.fromisoformat(evidence["acquired_at"]) > cutoff:
                            raise ValueError("Local financial sentiment unavailable before cutoff")
                        result["hf_news_assessment"] = evidence["per_symbol"][symbol]["assessment"]
                        result["hf_sentiment_source_id"] = evidence["source_id"]
                        result["hf_sentiment_model"] = evidence["model"]
                result.update(
                    {
                        "source_ids": [own["id"], market["id"]],
                        "feed": own["payload"]["feed"],
                        "volatility": float(panel[-1]["x"][4] * np.sqrt(252)),
                        "reason": "Predeclared technical model; not a validated edge",
                    }
                )
                action = (
                    "BUY"
                    if signal["expected_return"] > 0.0015
                    and signal["probability_positive"] >= (0.70 if settings.paper_discovery_enabled else 0.55)
                    and result["volatility"] <= 0.8
                    and (not settings.paper_discovery_enabled or stance == "bullish")
                    and (not settings.paper_sentiment_enabled or result.get("hf_news_assessment") == "review")
                    else "HOLD"
                )
            except ValueError as exc:
                result["reason"] = str(exc)
            issued_at = now()
            if not ready:
                action = "SKIP"
                result["reason"] = "Account/deployment readiness was not verified before 09:15 cutoff"
            if issued_at.astimezone(EASTERN).time().replace(tzinfo=None) >= time(9, 25):
                action = "SKIP"
                result["reason"] = "Model completed after premarket issuance deadline"
            from paperpilot.decision_trace import legacy_signal_trace
            result["decision_trace"] = legacy_signal_trace(result, action, settings.paper_discovery_enabled, settings.paper_sentiment_enabled)
            conn.execute(
                "INSERT INTO pilot_decisions VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (identity, session, symbol, cutoff, issued_at, action, jb(result)),
            )
    return {"status": "recorded", "session": session}


def latest_orders(conn):
    return [
        r["payload"]
        for r in conn.execute(
            "SELECT DISTINCT ON(client_id) client_id,payload FROM pilot_order_versions ORDER BY client_id,observed_at DESC,id DESC"
        ).fetchall()
    ]


def ledger(orders):
    lots = {}
    realized = 0.0
    buys = sells = 0.0
    wins = losses = 0
    trades = []
    for order in sorted(orders, key=lambda o: o.get("submitted_at") or o.get("created_at") or ""):
        qty = float(order.get("filled_qty") or 0)
        price = float(order.get("filled_avg_price") or 0)
        if qty <= 0 or price <= 0:
            continue
        symbol = order["symbol"]
        lot = lots.setdefault(symbol, {"qty": 0.0, "cost": 0.0, "realized": 0.0})
        value = qty * price
        pnl = None
        if order["side"] == "buy":
            lot["qty"] += qty
            lot["cost"] += value
            buys += value
        else:
            if qty > lot["qty"] + 1e-5:
                raise BrokerUnavailable("Sell fill exceeds owned inventory; reconciliation required")
            basis = lot["cost"] / lot["qty"] if lot["qty"] else 0
            pnl = qty * (price - basis)
            realized += pnl
            lot["realized"] += pnl
            lot["cost"] -= qty * basis
            lot["qty"] -= qty
            sells += value
            if lot["qty"] < 1e-5:
                wins += lot["realized"] > 0
                losses += lot["realized"] < 0
                lot["realized"] = 0
        trades.append(
            {
                "symbol": symbol,
                "side": order["side"],
                "qty": qty,
                "price": price,
                "value": value,
                "realized_pnl": pnl,
                "order_id": order.get("id"),
                "client_order_id": order.get("client_order_id"),
                "status": order["status"],
                "time": order.get("filled_at") or order.get("updated_at"),
            }
        )
    return {
        "realized": realized,
        "buys": buys,
        "sells": sells,
        "lots": lots,
        "wins": int(wins),
        "losses": int(losses),
        "trades": trades,
    }


def record_order(order):
    client = order.get("client_order_id", "")
    if not client.startswith("kti4-"):
        return
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM pilot_intents WHERE client_id=%s", (client,)).fetchone():
            raise BrokerUnavailable("Unexpected pilot order with no durable intent")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (client,))
        previous = conn.execute(
            "SELECT payload FROM pilot_order_versions WHERE client_id=%s ORDER BY observed_at DESC,id DESC LIMIT 1",
            (client,),
        ).fetchone()
        if previous:
            old = previous["payload"]
            if float(order.get("filled_qty") or 0) < float(old.get("filled_qty") or 0):
                return  # Delayed stream/REST updates cannot erase cumulative fills.
            if old.get("updated_at") and order.get("updated_at") and order["updated_at"] < old["updated_at"]:
                return
        identity = digest(order)
        conn.execute(
            "INSERT INTO pilot_order_versions VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (identity, client, now(), jb(order)),
        )
        conn.execute(
            "UPDATE pilot_intents SET state=%s,broker_id=%s,payload=%s WHERE client_id=%s",
            (order["status"], order["id"], jb(order), client),
        )


def submit_intent(broker, symbol, side, session, decision_id=None, quantity=None, strategy_signal_id=None):
    # Serialize final submission with emergency disable across bounded HTTP.
    with connect() as lease:
        lease.execute("SELECT pg_advisory_lock(753194102)")
        lease.commit()
        try:
            return _submit_intent(broker, symbol, side, session, decision_id, quantity, strategy_signal_id)
        finally:
            lease.execute("SELECT pg_advisory_unlock(753194102)")


def _submit_intent(broker, symbol, side, session, decision_id=None, quantity=None, strategy_signal_id=None):
    cid = client_id(session, symbol, side)
    notional = BUY_NOTIONAL
    with connect() as conn:
        enabled = conn.execute("SELECT enabled FROM pilot_settings WHERE id=1").fetchone()["enabled"]
        if not settings.paper_execution_permitted or not enabled:
            return {"status": "disabled"}
        if strategy_signal_id:
            signal = conn.execute(
                "SELECT * FROM pilot_strategy_signals WHERE id=%s", (strategy_signal_id,)
            ).fetchone()
            if not signal or signal["role"] != "paper" or signal["symbol"] != symbol:
                raise BrokerUnavailable("No matching paper strategy signal")
            if side == "buy":
                from paperpilot.strategy import STRATEGIES, SWING_EXPECTANCY_TRIAL, swing_trial_active

                if signal["action"] != "BUY" or signal["session_date"] != session:
                    raise BrokerUnavailable("Signal does not authorize a current entry")
                remaining = conn.execute(
                    """SELECT count(*) AS n FROM pilot_intents i JOIN pilot_strategy_signals s
                    ON i.strategy_signal_id=s.id WHERE i.side='buy' AND i.session_date=%s AND s.strategy=%s""",
                    (session, signal["strategy"]),
                ).fetchone()["n"]
                if remaining >= STRATEGIES[signal["strategy"]]["limit"]:
                    return {"status": "strategy_limit"}
                if signal["payload"].get("entry_policy") == SWING_EXPECTANCY_TRIAL:
                    if signal["strategy"] != "swing" or not swing_trial_active(
                        session, settings.paper_swing_trial_start
                    ):
                        return {"status": "trial_outside_approved_sessions"}
                    used = conn.execute(
                        """SELECT 1 FROM pilot_intents i JOIN pilot_strategy_signals s ON s.id=i.strategy_signal_id
                        WHERE i.side='buy' AND i.session_date=%s AND s.payload->>'entry_policy'=%s LIMIT 1""",
                        (session, SWING_EXPECTANCY_TRIAL),
                    ).fetchone()
                    if used:
                        return {"status": "trial_limit"}
                    notional = swing_trial_notional()
                    if notional > 100 and signal["payload"].get("entry_notional") != int(notional):
                        return {"status": "trial_size_changed_after_freeze"}
            else:
                from paperpilot.strategy_execution import can_retry_exit

                sells = conn.execute(
                    "SELECT * FROM pilot_intents WHERE symbol=%s AND side='sell'", (symbol,)
                ).fetchall()
                if not can_retry_exit(sells, symbol, session, now()):
                    return {"status": "exit_pending_or_retry_limit"}
                sequence = sum(row["session_date"] == session for row in sells) + 1
                cid += f"-{sequence}"
        body = {
            "symbol": symbol,
            "side": side,
            "type": "market",
            "time_in_force": "day",
            "client_order_id": cid,
        }
        body.update({"notional": str(notional)} if side == "buy" else {"qty": str(quantity)})
        inserted = conn.execute(
            """INSERT INTO pilot_intents(client_id,decision_id,symbol,side,session_date,state,notional,quantity,payload,
            strategy_signal_id) VALUES(%s,%s,%s,%s,%s,'prepared',%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING client_id""",
            (
                cid,
                decision_id,
                symbol,
                side,
                session,
                notional if side == "buy" else None,
                quantity,
                jb(body),
                strategy_signal_id,
            ),
        ).fetchone()
        if not inserted:
            return {"status": "already_attempted", "client_id": cid}
    # Any failure after this commit is reconciled by deterministic client ID, never resubmitted.
    with connect() as conn:
        if not conn.execute("SELECT enabled FROM pilot_settings WHERE id=1").fetchone()["enabled"]:
            conn.execute("UPDATE pilot_intents SET state='canceled' WHERE client_id=%s", (cid,))
            return {"status": "disabled"}
        conn.execute("UPDATE pilot_intents SET state='submitting' WHERE client_id=%s", (cid,))
        quote = conn.execute("SELECT * FROM pilot_quotes WHERE symbol=%s", (symbol,)).fetchone()
    event(
        "order_submission_evidence",
        {
            "client_id": cid,
            "decision_id": decision_id,
            "strategy_signal_id": strategy_signal_id,
            "request": body,
            "quote": quote,
            "submitted_at": now(),
        },
    )
    try:
        order = broker.submit(body)
        if not order:
            raise BrokerUnavailable("Acknowledgement unknown")
        record_order(order)
        event("order_submitted", {"client_id": cid, "broker_id": order["id"], "symbol": symbol, "side": side})
        return {"status": order["status"], "client_id": cid}
    except BrokerUnavailable:
        with connect() as conn:
            conn.execute("UPDATE pilot_intents SET state='unknown' WHERE client_id=%s", (cid,))
        event("order_acknowledgement_unknown", {"client_id": cid, "resubmitted": False})
        return {"status": "unknown", "client_id": cid}


def disable(broker=None):
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194102)")
        conn.execute(
            "UPDATE pilot_settings SET enabled=false,reason='Emergency disable: automatic submissions stopped; positions remain open',updated_at=now() WHERE id=1"
        )
        active = conn.execute(
            "SELECT * FROM pilot_intents WHERE state NOT IN ('filled','canceled','expired','rejected')"
        ).fetchall()
    canceled, unknown = [], []
    broker = broker or PaperBroker()
    for intent in active:
        try:
            order = broker.by_client(intent["client_id"])
            if order and order["status"] not in TERMINAL:
                broker.cancel(order)
                canceled.append(intent["client_id"])
            elif order:
                record_order(order)
            else:
                unknown.append(intent["client_id"])
        except BrokerUnavailable:
            unknown.append(intent["client_id"])
    result = {
        "enabled": False,
        "cancel_requested": canceled,
        "cancellation_unconfirmed": unknown,
        "positions": "Remain open; no liquidation assumed",
    }
    event("emergency_disabled", result)
    return result


class Engine:
    def __init__(self, broker=None, evidence_type="ALPACA_PAPER"):
        if evidence_type not in {"ALPACA_PAPER", "DRY_RUN"}:
            raise ValueError("Invalid execution evidence type")
        self.evidence_type = evidence_type
        self.broker = broker or PaperBroker()
        self.last_prices = None
        self.last_info = None
        self.last_quotes = None
        self.last_research = None
        self.last_strategy_attempt = None
        self.last_gate_reasons = {}
        self.last_snapshot = None

    def tick(self):
        instant = now()
        with connect() as lease:
            if not lease.execute("SELECT pg_try_advisory_lock(753194101) acquired").fetchone()["acquired"]:
                return
            lease.commit()
            try:
                self._tick(instant)
            finally:
                lease.execute("SELECT pg_advisory_unlock(753194101)")

    def _tick(self, instant):
        with connect() as conn:
            size = conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"]
        free = shutil.disk_usage("/").free
        resource_ready = size < 246 * 1024**2 and free >= 10 * 1024**3
        if not resource_ready and not settings.paper_mainkraft_enabled:
            disable(self.broker)
            raise BrokerUnavailable(
                "Resource reserve reached; pilot disabled, existing positions require review"
            )
        local = instant.astimezone(EASTERN)
        session = local.date()
        if (
            resource_ready
            and settings.paper_discovery_enabled
            and session in schedule(session, session)
            and time(7, 30) <= local.time().replace(tzinfo=None) < time(9)
        ):
            from paperpilot.discovery import research
            from paperpilot.universe import selection

            if not selection(session) and (
                self.last_research is None or instant - self.last_research > timedelta(minutes=20)
            ):
                self.last_research = instant
                try:
                    result = research(session)
                    if result["status"] == "frozen":
                        self.last_info = None
                        self.last_prices = None
                    self.connection(
                        "research", {"status": result["status"], "research_id": result.get("research_id")}
                    )
                except Exception as exc:
                    event("daily_research_failed", {"session": session, "reason": str(exc)[:200]})
                    self.connection("research", {"status": "unavailable", "reason": str(exc)[:200]})
        if resource_ready and (
            self.last_prices is None
            or instant - self.last_prices > timedelta(hours=1)
            or (
                time(8, 30) <= local.time().replace(tzinfo=None) < time(9, 15)
                and instant - self.last_prices > timedelta(minutes=5)
            )
        ):
            refresh_prices()
            if not settings.paper_mainkraft_enabled:
                resolve()
            self.last_prices = instant
        if resource_ready and (self.last_info is None or instant - self.last_info > timedelta(hours=12)):
            refresh_company_information()
            self.last_info = instant
        try:
            poll_quotes()
        except Exception as exc:
            self.connection("market_poll", {"status": "unavailable", "reason": type(exc).__name__})
        if settings.paper_mainkraft_enabled:
            from paperpilot.strategy_runtime import freeze_shadow, issue_signals

            decision_time = now()
            local_time = decision_time.astimezone(EASTERN)
            if (
                resource_ready
                and time(9, 15) <= local_time.time() < time(9, 25)
                and self.last_strategy_attempt != local_time.date()
            ):
                try:
                    issue_signals(decision_time)
                    self.last_strategy_attempt = local_time.date()
                except (ValueError, OSError) as exc:
                    if self.last_strategy_attempt != local_time.date():
                        event(
                            "strategy_issuance_failed",
                            {"session": local_time.date(), "reason": type(exc).__name__},
                        )
                    self.last_strategy_attempt = local_time.date()
            shadow_time = now()
            shadow_session = shadow_time.astimezone(EASTERN)
            if time(9, 35) <= shadow_session.time() < time(9, 36):
                with connect() as conn:
                    shadow_quotes = {row["symbol"]: row for row in conn.execute("SELECT * FROM pilot_quotes")}
                freeze_shadow(shadow_session.date(), shadow_quotes, shadow_time)
        else:
            create_signals()
        instant = now()
        local = instant.astimezone(EASTERN)
        session = local.date()
        if not settings.paper_key or not settings.paper_secret:
            self.connection("paper", {"status": "credentials_missing", "execution_enabled": False})
            return
        account = self.broker.account()
        positions = self.broker.positions()
        orders = self.broker.orders()
        verify_account(account, positions, orders)
        with connect() as conn:
            first = conn.execute("SELECT * FROM pilot_snapshots ORDER BY id LIMIT 1").fetchone()
            intents = self.strategy_intents(conn)
        if not first and (positions or orders):
            raise BrokerUnavailable(
                "Dedicated account must start empty; no existing positions/orders imported"
            )
        with connect() as conn:
            assets = {
                r["symbol"]: r
                for r in conn.execute(
                    "SELECT DISTINCT ON(symbol) * FROM pilot_asset_versions ORDER BY symbol,observed_at DESC"
                ).fetchall()
            }
        for symbol in managed_symbols():
            if symbol not in assets or instant - assets[symbol]["observed_at"] > timedelta(hours=12):
                asset = self.broker.request("GET", "/v2/assets/" + symbol)
                with connect() as conn:
                    conn.execute(
                        "INSERT INTO pilot_asset_versions VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (digest([asset, instant.date()]), symbol, instant, jb(asset)),
                    )
                assets[symbol] = {"payload": asset}
        for intent in intents:
            if intent["state"] not in TERMINAL:
                order = self.broker.by_client(intent["client_id"])
                if order:
                    record_order(order)
                    if order["status"] not in TERMINAL and instant - intent["created_at"] > timedelta(
                        seconds=settings.paper_order_timeout_seconds
                    ):
                        self.broker.cancel(order)
        with connect() as conn:
            book = ledger(latest_orders(conn))
            enabled = (
                conn.execute("SELECT enabled FROM pilot_settings WHERE id=1").fetchone()["enabled"]
                and settings.paper_execution_permitted
            )
            quotes = {r["symbol"]: r for r in conn.execute("SELECT * FROM pilot_quotes").fetchall()}
            current_intents = conn.execute(
                "SELECT * FROM pilot_intents WHERE session_date=%s OR state NOT IN ('filled','canceled','expired','rejected')",
                (session,),
            ).fetchall()
            decisions = {
                r["symbol"]: r
                for r in conn.execute(
                    "SELECT * FROM pilot_decisions WHERE session_date=%s", (session,)
                ).fetchall()
            }
        for position in positions:
            owned = book["lots"].get(position["symbol"], {}).get("qty", 0)
            if abs(owned - float(position["qty"])) > 1e-5:
                raise BrokerUnavailable(
                    "Broker inventory differs from reconciled strategy ledger; reset/unrelated trade suspected"
                )
        for symbol, lot in book["lots"].items():
            actual_qty = next((float(p["qty"]) for p in positions if p["symbol"] == symbol), 0)
            if abs(lot["qty"] - actual_qty) > 1e-5:
                raise BrokerUnavailable(
                    "Owned inventory disappeared or changed outside ledger; reset suspected"
                )
        expected_cash = (
            (float(first["payload"]["account"]["cash"]) if first else float(account["cash"]))
            - book["buys"]
            + book["sells"]
        )
        cash_consistent = abs(float(account["cash"]) - expected_cash) < 0.05
        unrealized = sum(float(p.get("unrealized_pl", 0)) for p in positions)
        strategy_pnl = book["realized"] + unrealized
        equity = 600 + strategy_pnl
        with connect() as conn:
            start = conn.execute(
                """SELECT payload FROM pilot_snapshots WHERE observed_at>=%s
                AND payload->>'cash_consistent'='true' AND payload->>'account_pnl_available'='true'
                ORDER BY id LIMIT 1""",
                (datetime.combine(session, time.min, EASTERN),),
            ).fetchone()
        daily_pnl = daily_change(equity, float(start["payload"]["strategy_equity"]) if start else None)
        clock = self.broker.clock()
        broker_time = datetime.fromisoformat(clock["timestamp"].replace("Z", "+00:00"))
        if abs((now() - broker_time).total_seconds()) > 30:
            raise BrokerUnavailable("Broker/server clock mismatch")
        actual = self.broker.request(
            "GET", "/v2/calendar", params={"start": str(session), "end": str(session)}
        )
        session_close = verified_session_close(session, actual) if clock["is_open"] or actual else None
        payload = {
            "account": {
                k: account.get(k) for k in ("id", "status", "cash", "equity", "last_equity", "buying_power")
            },
            "positions": positions,
            "orders": orders,
            "quotes": quotes,
            "realized_pnl": book["realized"],
            "unrealized_pnl": unrealized,
            "strategy_equity": equity,
            "strategy_pnl": strategy_pnl,
            "daily_pnl": daily_pnl,
            "wins": book["wins"],
            "losses": book["losses"],
            "cash_consistent": cash_consistent,
            "account_pnl_available": cash_consistent,
            "trades": book["trades"][-100:],
            "starting_paper_equity": float(first["payload"]["account"]["equity"])
            if first
            else float(account["equity"]),
            "enabled": enabled,
            "clock": clock,
            "budget": 600,
            "pnl_basis": "Gross reconciled fills plus broker unrealized marks; external cash flows excluded",
        }
        save_snapshot = (
            not settings.paper_mainkraft_enabled
            or self.last_snapshot is None
            or (instant - self.last_snapshot >= timedelta(seconds=60 if clock["is_open"] else 180))
            or (session_close and self.last_snapshot < session_close <= instant)
            or self.last_snapshot.astimezone(EASTERN).date() != session
        )
        with connect() as conn:
            if save_snapshot:
                conn.execute(
                    "INSERT INTO pilot_snapshots(observed_at,account_id,payload) VALUES(%s,%s,%s)",
                    (instant, account["id"], jb(payload)),
                )
                self.last_snapshot = instant
            inception = conn.execute("SELECT payload FROM pilot_benchmarks ORDER BY id LIMIT 1").fetchone()
            benchmark_symbols = (
                [s for s in inception["payload"]["inception_marks"] if s != "SPY"]
                if inception
                else list(current_symbols())
            )
            if (
                enabled
                and clock["is_open"]
                and cash_consistent
                and benchmark_symbols
                and all(
                    s in quotes and instant - quotes[s]["source_time"] < timedelta(seconds=15)
                    for s in (*benchmark_symbols, "SPY")
                )
            ):
                marks = {s: float(quotes[s]["price"]) for s in (*benchmark_symbols, "SPY")}
                base = inception["payload"]["inception_marks"] if inception else marks
                benchmark = {
                    "inception_marks": base,
                    "spy_equity": 600 * marks["SPY"] / base["SPY"],
                    "passive_six_equity": sum(
                        600 / len(benchmark_symbols) * marks[s] / base[s] for s in benchmark_symbols
                    ),
                    "selection_symbols": benchmark_symbols,
                    "basis": "Inception-fixed equal allocation across the first selected stock set, actual IEX mid marks; hypothetical, dividends/costs excluded",
                }
                conn.execute(
                    "INSERT INTO pilot_benchmarks(observed_at,payload) VALUES(%s,%s)",
                    (instant, jb(benchmark)),
                )
        self.connection(
            "paper",
            {
                "status": "verified" if cash_consistent else "blocked_cash_flow",
                "execution_enabled": enabled,
                "cash_consistent": cash_consistent,
                "account_id_suffix": account["id"][-6:],
            },
        )
        if not clock["is_open"] or not enabled:
            return
        if daily_pnl <= -float(DAILY_LOSS):
            for order in orders:
                if order["side"] == "buy":
                    self.broker.cancel(order)
        # Reconcile every decision including HOLD/SKIP; no forced buys.
        from paperpilot.strategy_execution import owned_signals, planned_exit

        with connect() as conn:
            owners = owned_signals(self.strategy_intents(conn))
        for position in positions:
            symbol = position["symbol"]
            cost = float(position["cost_basis"])
            pnl = float(position.get("unrealized_pl", 0))
            owner = owners.get(symbol) or {}
            spec = owner.get("strategy_payload") or {}
            stop, take = spec.get("stop", 0.02), spec.get("take", 0.03)
            timed = (
                planned_exit(owner, session, instant, session_close)
                if owner.get("strategy")
                else bool(session_close and instant >= session_close - timedelta(minutes=15))
            )
            liquidation_started = bool(owner.get("strategy_signal_id")) and any(
                item["side"] == "sell" and item.get("strategy_signal_id") == owner["strategy_signal_id"]
                for item in intents
            )
            risk_exit = (
                liquidation_started
                or (cost > 0 and position_exit(pnl / cost, stop, take))
                or Decimal(str(position["market_value"])) > scaled_position_cap(owner)
                or daily_pnl <= -float(DAILY_LOSS)
                or timed
                or (
                    not owner.get("strategy")
                    and settings.paper_discovery_enabled
                    and decisions.get(symbol, {})
                    .get("payload", {})
                    .get("horizons", {})
                    .get("1", {})
                    .get("probability_positive", 1)
                    <= 0.30
                )
            )
            if risk_exit and not any(o["symbol"] == symbol and o["side"] == "sell" for o in orders):
                pending_buys = [o for o in orders if o["symbol"] == symbol and o["side"] == "buy"]
                if pending_buys:
                    for order in pending_buys:
                        self.broker.cancel(order)
                    continue  # Reconcile cancellation/fills before sizing an exit.
                quantity = position.get("qty_available", position["qty"])
                if quantity is None or Decimal(str(quantity)) <= 0:
                    continue
                submit_intent(
                    self.broker,
                    symbol,
                    "sell",
                    session,
                    quantity=quantity,
                    strategy_signal_id=owner.get("strategy_signal_id"),
                )
        if settings.paper_mainkraft_enabled:
            if cash_consistent and resource_ready:
                self.strategy_entries(session, positions, account, quotes, assets, enabled, daily_pnl)
            return
        if not time(9, 35) <= local.time().replace(tzinfo=None) < time(10) or not cash_consistent:
            return
        with connect() as conn:
            current_intents = conn.execute(
                "SELECT * FROM pilot_intents WHERE session_date=%s OR state NOT IN ('filled','canceled','expired','rejected')",
                (session,),
            ).fetchall()
        for symbol, decision in decisions.items():
            signal = {
                **decision["payload"].get("horizons", {}).get("1", {}),
                "volatility": decision["payload"].get("volatility", float("inf")),
                "news_stance": decision["payload"].get("news_stance"),
                "hf_news_assessment": decision["payload"].get("hf_news_assessment"),
            }
            if decision["action"] != "BUY":
                continue
            asset = assets.get(symbol, {}).get("payload")
            allowed, reason = entry_permission(
                symbol,
                signal,
                asset,
                quotes.get(symbol),
                now(),
                positions,
                current_intents,
                account,
                enabled,
                daily_pnl,
            )
            cost_evidence = None
            if settings.paper_cost_gate_enabled and quotes.get(symbol):
                from paperpilot.risk import execution_cost_evidence

                try:
                    cost_evidence = execution_cost_evidence(signal, quotes[symbol])
                except (ArithmeticError, KeyError, TypeError, ValueError):
                    cost_evidence = {"available": False, "reason": "Invalid quote/forecast for cost planning"}
            event(
                "entry_gate",
                {
                    "decision_id": decision["id"],
                    "symbol": symbol,
                    "allowed": allowed,
                    "reason": reason,
                    **({"cost_evidence": cost_evidence} if cost_evidence is not None else {}),
                },
            )
            if allowed:
                submit_intent(self.broker, symbol, "buy", session, decision["id"])
                with connect() as conn:
                    current_intents = conn.execute(
                        "SELECT * FROM pilot_intents WHERE session_date=%s OR state NOT IN ('filled','canceled','expired','rejected')",
                        (session,),
                    ).fetchall()

    @staticmethod
    def strategy_intents(conn):
        return conn.execute(
            """SELECT i.*,s.strategy,s.payload AS strategy_payload FROM pilot_intents i
            LEFT JOIN pilot_strategy_signals s ON i.strategy_signal_id=s.id"""
        ).fetchall()

    def strategy_entries(self, session, positions, account, quotes, assets, enabled, daily_pnl):
        from paperpilot.strategy_execution import ranked_entries, strategy_entry

        with connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pilot_strategy_signals WHERE session_date=%s AND role='paper'", (session,)
            ).fetchall()
            intents = self.strategy_intents(conn)
        active = schedule(session, session)[session]
        opens = active["open"] + timedelta(minutes=5)
        ends = opens + timedelta(minutes=1)
        for row in ranked_entries(rows, quotes):
            instant = now()
            from paperpilot.decision_trace import DecisionTrace
            trace = DecisionTrace({"stage": "entry", "signal_id": row["id"], "symbol": row["symbol"],
                                   "observed_at": instant, "evidence_type": self.evidence_type})
            trace.expect(["execution_window", "entry_risk", "order_intent", "broker"])
            trace.blocked("execution_window", not opens <= instant < ends, "09:35–09:36 exchange session window")
            if not opens <= instant < ends:
                window_key = ("window", row["id"])
                if window_key not in self.last_gate_reasons:
                    event("decision_trace", trace.payload())
                    self.last_gate_reasons[window_key] = True
                continue
            symbol = row["symbol"]
            allowed, reason = strategy_entry(
                row,
                quotes.get(symbol),
                assets.get(symbol, {}).get("payload"),
                instant,
                positions,
                intents,
                account,
                daily_pnl,
                enabled,
                trace=trace,
            )
            trace.blocked("entry_risk", not allowed, reason)
            previous = self.last_gate_reasons.get(symbol)
            verdict = (row["id"], allowed, reason)
            if previous != verdict:
                event(
                    "strategy_entry_gate",
                    {
                        "signal_id": row["id"],
                        "strategy": row["strategy"],
                        "symbol": symbol,
                        "allowed": allowed,
                        "reason": reason,
                        "decision_trace": trace.payload(),
                    },
                )
                self.last_gate_reasons[symbol] = verdict
            if allowed:
                submitted = submit_intent(self.broker, symbol, "buy", session, strategy_signal_id=row["id"])
                attempted = submitted.get("status") in {"filled", "partially_filled", "accepted", "new", "pending_new", "rejected", "canceled", "expired", "unknown"}
                trace.blocked("order_intent", not attempted, submitted["status"])
                if attempted:
                    trace.blocked("broker", submitted["status"] in {"unknown", "rejected"}, submitted["status"])
                event("decision_trace", trace.payload())
                with connect() as conn:
                    intents = self.strategy_intents(conn)

    def connection(self, name, payload):
        observed_at = now()  # Completion time, never the tick's earlier start.
        with connect() as conn:
            conn.execute(
                "INSERT INTO pilot_connection VALUES(%s,%s,%s) ON CONFLICT(id) DO UPDATE SET observed_at=EXCLUDED.observed_at,payload=EXCLUDED.payload",
                (name, observed_at, jb(payload)),
            )
            local = observed_at.astimezone(EASTERN)
            if name == "paper" and time(9, 13) < local.time().replace(tzinfo=None) <= time(9, 15):
                # A bounded daily audit window is sufficient for the two-minute
                # readiness rule; healthy off-hours polling need not grow it.
                conn.execute(
                    "INSERT INTO pilot_readiness(observed_at,payload) VALUES(%s,%s)",
                    (observed_at, jb(payload)),
                )

    async def run(self):
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception as exc:
                self.connection(
                    "paper", {"status": "blocked", "reason": str(exc)[:180], "execution_enabled": False}
                )
            await asyncio.sleep(15)
