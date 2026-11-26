from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from test_paperpilot import paperdb  # noqa: F401

from intelligence.calendar import schedule
from intelligence.util import now
from paperpilot import engine
from paperpilot.daily_report import session_report
from paperpilot.db import connect, jb, migrate
from paperpilot.intraday import BAR, simulate
from paperpilot.strategy import features
from paperpilot.strategy_execution import can_retry_exit, planned_exit


def test_simulation_early_close_and_adverse_overnight_gap():
    calendar = list(schedule(date(2026, 11, 25), date(2026, 11, 27)).values())
    assert len(calendar) == 2 and calendar[-1]["close"] - calendar[-1]["open"] == timedelta(hours=3.5)
    bars = {
        session["open"] + index * BAR: (100, 101, 99, 100, 50)
        for session in calendar
        for index in range((session["close"] - session["open"]) // BAR)
    }
    day = simulate(bars, calendar[:1], 0.02, 0.03)
    assert day["exit_bar"] == calendar[0]["close"] - 3 * BAR
    bars[calendar[-1]["open"]] = (92, 94, 91, 93, 40)
    swing = simulate(bars, calendar, 0.02, 0.03)
    assert swing["reason"] == "stop_gap"
    assert swing["gross_return"] == pytest.approx(-0.08)
    first_entry = calendar[0]["open"] + BAR
    bars[first_entry] = (100, 105, 95, 101, 50)
    assert simulate(bars, calendar, 0.02, 0.03)["reason"] == "stop_loss"
    del bars[first_entry + BAR]
    assert simulate(bars, calendar, 0.02, 0.03) is None


def test_market_features_never_use_session_or_future_inputs():
    today = date(2026, 11, 17)
    rows = [
        {"session_date": d, "close": i + 100, "volume": i + 10000}
        for i, d in enumerate(schedule(today - timedelta(days=45), today))
    ]
    old = features(today, rows, rows, rows)
    rows[-1]["close"] = 90000
    rows[-1]["volume"] = 1
    assert features(today, rows, rows, rows) == old
    del rows[-2]
    with pytest.raises(ValueError, match="Missing prior"):
        features(today, rows, rows, rows)


def test_swing_deadline_uses_sessions_and_day_retries_exit():
    start, day = date(2026, 11, 23), date(2026, 11, 30)
    close = schedule(day, day)[day]["close"]
    assert planned_exit({"strategy": "swing", "session_date": start}, day, close - 3 * BAR, close)
    assert not planned_exit({"strategy": "swing", "session_date": start}, day, close - 4 * BAR, close)
    assert planned_exit({"strategy": "day", "session_date": start}, day, close - 15 * BAR, close)


def test_partial_exit_restarts_with_a_distinct_intent(paperdb, monkeypatch):  # noqa: F811
    session, timestamp = date(2026, 11, 16), now()
    monkeypatch.setattr(engine.settings, "paper_execution_permitted", True)
    monkeypatch.setattr(engine, "client_id", lambda *args: "kti4-2026-11-16-AMD-sell")
    with connect() as db:
        db.execute("UPDATE pilot_settings SET enabled=true")
        db.execute(
            "INSERT INTO pilot_strategy_sessions VALUES(%s,%s,%s,'bundle','{}')",
            (session, timestamp, timestamp),
        )
        db.execute(
            "INSERT INTO pilot_strategy_signals VALUES('owned',%s,'AMD','day','paper',%s,'BUY','{}')",
            (session, timestamp),
        )
        db.execute(
            """INSERT INTO pilot_intents(client_id,symbol,side,session_date,created_at,state,payload,strategy_signal_id)
            VALUES('kti4-2026-11-16-AMD-sell-1','AMD','sell',%s,%s,'canceled',%s,'owned')""",
            (session, timestamp - timedelta(seconds=90), jb({"filled_qty": "0.03"})),
        )
    broker = Mock()
    broker.submit.side_effect = engine.BrokerUnavailable("failed to acknowledge")
    initial = engine.submit_intent(
        broker, "AMD", "sell", session, quantity=Decimal("0.07"), strategy_signal_id="owned"
    )
    assert initial["status"] == "unknown"
    assert initial["client_id"].endswith("-2")
    assert broker.submit.call_args.args[0]["qty"] == "0.07"
    second = engine.submit_intent(
        broker, "AMD", "sell", session, quantity=Decimal("0.07"), strategy_signal_id="owned"
    )
    assert second["status"] == "exit_pending_or_retry_limit"
    assert broker.submit.call_count == 1
    with connect() as db:
        migration_rows = db.execute("SELECT * FROM pilot_intents").fetchall()
        assert not can_retry_exit(migration_rows, "AMD", session, now())
    migrate()


def test_report_is_provisional_until_close_reconciliation(paperdb):  # noqa: F811
    session = date(2026, 11, 27)
    close = schedule(session, session)[session]["close"]
    with connect() as db:
        db.execute(
            "INSERT INTO pilot_snapshots(observed_at,payload) VALUES(%s,%s)",
            (
                close + timedelta(seconds=10),
                jb({"cash_consistent": True, "account_pnl_available": True, "daily_pnl": 0, "positions": []}),
            ),
        )
    assert session_report(session, close - timedelta(minutes=1))["status"] == "provisional"
    assert session_report(session, close)["status"] == "closed_pending"
    result = session_report(session, close + timedelta(minutes=1))
    assert result["status"] == "final"
    assert result["broker_paper_pnl"] == 0
    assert result["estimated_net_pnl"] == 0


def test_daily_report_requires_the_private_token(monkeypatch):
    from fastapi.testclient import TestClient

    from paperpilot.service import app, settings

    monkeypatch.setattr(settings, "paper_internal_token", "test-secret")
    response = TestClient(app).get("/daily-report")
    assert response.status_code == 403


@pytest.mark.parametrize("exit_price,expected_pnl", [(104, 1), (97, -0.75)])
def test_freeze_to_paper_intent_and_virtual_outcome(paperdb, monkeypatch, exit_price, expected_pnl):  # noqa: F811
    from intelligence.calendar import EASTERN
    from paperpilot import risk, strategy_runtime

    session = date(2026, 11, 17)
    current = schedule(session, session)[session]
    cutoff = current["open"] - 3 * BAR
    monkeypatch.setattr(strategy_runtime, "now", lambda: cutoff + timedelta(seconds=10))
    monkeypatch.setattr(engine.settings, "paper_execution_permitted", True)
    monkeypatch.setattr(engine.settings, "paper_discovery_enabled", True)
    monkeypatch.setattr(engine, "client_id", lambda day, stock, side: f"kti4-{day}-{stock}-{side}")
    strategy_bundle = {
        "bundle_id": "frozen-test-bundle",
        "strategies": {
            kind: {"model": {}, "challenger": {}, "stop": 0.02, "take": 0.03} for kind in ("day", "swing")
        },
    }
    score = {
        "expected_net_return": 0.005,
        "probability_positive": 0.62,
        "lower_return": -0.01,
        "upper_return": 0.02,
    }
    monkeypatch.setattr(strategy_runtime, "bundle", lambda: strategy_bundle)
    monkeypatch.setattr(strategy_runtime, "forecast", lambda model, row: score)
    client = Mock()
    client.post.return_value.json.return_value = {
        "bundle_id": strategy_bundle["bundle_id"],
        "scores": {kind: {"AMD": score} for kind in ("day", "swing")},
    }
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(strategy_runtime.httpx, "Client", lambda **_: context)
    before = cutoff - timedelta(minutes=1)
    days = [d for d in schedule(session - timedelta(days=45), session) if d < session]
    bars = [{"session_date": d, "close": 100 + i, "volume": 1000 + i} for i, d in enumerate(days)]
    with connect() as db:
        db.execute("UPDATE pilot_settings SET enabled=true,updated_at=%s", (before,))
        db.execute(
            "INSERT INTO pilot_readiness(observed_at,payload) VALUES(%s,%s)",
            (before, jb({"status": "verified", "cash_consistent": True})),
        )
        db.execute(
            "INSERT INTO pilot_sources VALUES('news','*','discovery_news',%s,%s)",
            (before, jb({"articles": {}, "truncated": False})),
        )
        db.execute(
            "INSERT INTO pilot_research_runs VALUES('research',%s,'scheduled',%s,%s)",
            (
                session,
                before,
                jb(
                    {
                        "news_source_id": "news",
                        "picks": [{"symbol": "AMD", "stance": "bullish", "source_ids": []}],
                        "hf_sentiment": {
                            "acquired_at": before,
                            "per_symbol": {"AMD": {"assessment": "review"}},
                        },
                    }
                ),
            ),
        )
        db.execute("INSERT INTO pilot_selections VALUES(%s,'research',%s,%s)", (session, before, jb(["AMD"])))
        for name in ("AMD", "SPY", "XLK"):
            db.execute(
                "INSERT INTO pilot_sources VALUES(%s,%s,'daily_prices',%s,%s)",
                (name, name, before, jb({"bars": bars, "feed": "sip", "adjustment": "split"})),
            )
    strategy_runtime.issue_signals(cutoff + timedelta(seconds=1))
    strategy_runtime.issue_signals(cutoff + timedelta(seconds=2))
    entry = current["open"] + BAR
    strategy_runtime.freeze_shadow(
        session, {"AMD": {"bid": 100, "ask": 100.1, "source_time": entry, "feed": "iex"}}, entry
    )
    from psycopg import sql

    with connect() as db:
        # PostgreSQL has its own clock. Stamp simulated intents inside the replay session.
        db.execute(
            sql.SQL("ALTER TABLE pilot_intents ALTER COLUMN created_at SET DEFAULT {}").format(
                sql.Literal(entry)
            )
        )
        signals = db.execute("SELECT * FROM pilot_strategy_signals").fetchall()
        assert len(signals) == 4 and all(row["action"] == "BUY" for row in signals)
        day = next(row for row in signals if row["role"] == "paper" and row["strategy"] == "day")
        shadow = next(row for row in signals if row["role"] == "challenger")
        assert len(db.execute("SELECT picks FROM pilot_shadow_sessions").fetchone()["picks"]) == 2

    broker = Mock()
    clock = {"instant": entry}
    wallet = {"cash": 15000, "shares": 0, "quote": 100}
    for name, value in {
        "paper_mainkraft_enabled": True,
        "paper_key": "isolated-simulation-key",
        "paper_secret": "isolated-simulation-secret",
        "paper_account_id": "simulated-account",
        "paper_forbidden_account_id": "excluded-account",
    }.items():
        monkeypatch.setattr(engine.settings, name, value)
    monkeypatch.setattr(engine, "BUY_NOTIONAL", Decimal("25"))
    monkeypatch.setattr(risk, "BUY_NOTIONAL", Decimal("25"))
    monkeypatch.setattr(engine, "now", lambda: clock["instant"])
    # Membership lookup owns a separate clock. Freeze it to the same replay
    # session; the host's current date may have no frozen selection at all.
    from paperpilot import universe
    monkeypatch.setattr(universe, "now", lambda: clock["instant"])
    monkeypatch.setattr(engine, "refresh_prices", lambda: None)
    monkeypatch.setattr(engine, "refresh_company_information", lambda: None)
    monkeypatch.setattr(engine, "poll_quotes", lambda: None)

    def account():
        cash = wallet["cash"]
        return {
            "id": "simulated-account",
            "cash": cash,
            "buying_power": cash,
            "status": "ACTIVE",
            "equity": cash + wallet["shares"] * wallet["quote"],
        }

    def positions():
        quantity = wallet["shares"]
        market_value = wallet["quote"] * quantity
        return (
            [
                {
                    "symbol": "AMD",
                    "side": "long",
                    "qty": quantity,
                    "qty_available": quantity,
                    "cost_basis": 25,
                    "market_value": market_value,
                    "unrealized_pl": market_value - 25,
                }
            ]
            if quantity
            else []
        )

    def request(method, path, **kwargs):
        assert method == "GET"
        if path == "/v2/calendar":
            return [
                {
                    "date": session.isoformat(),
                    **{k: current[k].astimezone(EASTERN).strftime("%H:%M") for k in ("open", "close")},
                }
            ]
        assert path == "/v2/assets/AMD"
        return {
            "symbol": "AMD",
            "class": "us_equity",
            "tradable": True,
            "fractionable": True,
            "status": "active",
        }

    def fill(payload):
        price = wallet["quote"]
        if payload["side"] == "buy":
            quantity = float(payload["notional"]) / price
            wallet["shares"] += quantity
            wallet["cash"] -= price * quantity
        else:
            quantity = float(payload["qty"])
            assert quantity == wallet["shares"]
            wallet["shares"] -= quantity
            wallet["cash"] += price * quantity
        return payload | {
            "id": f"simulated-{payload['side']}",
            "status": "filled",
            "filled_qty": str(quantity),
            "filled_avg_price": str(price),
            "submitted_at": clock["instant"].isoformat(),
        }

    broker.account.side_effect = account
    broker.positions.side_effect = positions
    broker.clock.side_effect = lambda: {
        "timestamp": clock["instant"].isoformat(),
        "is_open": clock["instant"] < current["close"],
    }
    broker.request.side_effect = request
    broker.orders.return_value = []
    broker.submit.side_effect = fill

    def set_quote(stamp):
        with connect() as db:
            db.execute(
                """INSERT INTO pilot_quotes VALUES('AMD',%s,%s,100,99.95,100.05,'iex')
                ON CONFLICT(symbol) DO UPDATE SET source_time=excluded.source_time,acquired_at=excluded.acquired_at""",
                (stamp, stamp),
            )

    with pytest.raises(engine.BrokerUnavailable, match="matching paper"):
        engine.submit_intent(broker, "AMD", "buy", session, strategy_signal_id=shadow["id"])
    pilot = engine.Engine(broker)
    set_quote(entry - timedelta(seconds=30))
    pilot.tick()
    broker.submit.assert_not_called()
    set_quote(entry)
    pilot.tick()
    with connect() as db:
        gates = db.execute("SELECT payload FROM pilot_events WHERE kind='strategy_entry_gate'").fetchall()
    assert wallet["shares"] == 0.25, gates
    assert broker.submit.call_count == 1
    pilot.tick()
    assert broker.submit.call_count == 1
    wallet["quote"] = exit_price
    clock["instant"] = entry + timedelta(minutes=10)
    pilot.tick()
    assert wallet["shares"] == 0
    assert [call.args[0]["side"] for call in broker.submit.call_args_list] == ["buy", "sell"]
    with connect() as db:
        book = engine.ledger(engine.latest_orders(db))
        owned = db.execute("SELECT strategy_signal_id FROM pilot_intents").fetchall()
        assert {row["strategy_signal_id"] for row in owned} == {day["id"]}
    assert book["realized"] == pytest.approx(expected_pnl)
    clock["instant"] = current["close"] + timedelta(seconds=10)
    pilot.tick()

    quotes = [
        [current["open"] + index * BAR, 100, 101, 99, 100, 50]
        for index in range((current["close"] - current["open"]) // BAR)
    ]
    monkeypatch.setattr(strategy_runtime, "fetch_intraday", lambda *args: {"request": {}, "bars": quotes})
    after = current["close"] + timedelta(minutes=25)
    monkeypatch.setattr(strategy_runtime, "now", lambda: after)
    strategy_runtime.resolve_strategies(after)
    result = session_report(session, after)
    assert result["status"] == "final"
    assert result["broker_paper_pnl"] == pytest.approx(expected_pnl)
    assert result["estimated_net_pnl"] == pytest.approx(expected_pnl - 0.0005 * (25 + exit_price * 0.25))
    print(
        f"Simulation: buy 0.25 AMD at $100, sell at ${exit_price}, gross P/L ${expected_pnl:.2f}, "
        f"estimated net P/L ${result['estimated_net_pnl']:.4f}"
    )
    assert sum(row["simulated_net_return"] is not None for row in result["decisions"]) == 2
    assert sum(row["virtual_net_return"] is not None for row in result["decisions"]) == 2


def test_expectancy_trial_keeps_quote_and_return_checks(monkeypatch):
    from paperpilot import risk
    from paperpilot.strategy import SWING_EXPECTANCY_TRIAL, swing_trial_active

    day = date(2026, 11, 18)
    start = schedule(day, day)[day]["open"] + timedelta(minutes=5)
    first_20 = list(schedule(day, day + timedelta(days=44)))
    assert swing_trial_active(first_20[19], day)
    assert not swing_trial_active(first_20[20], day)
    assert not swing_trial_active(day - timedelta(days=1), day)
    monkeypatch.setattr(risk, "current_symbols", lambda session: ("NVDA",))
    monkeypatch.setattr(risk.settings, "paper_swing_trial_start", day)
    asset = {
        "symbol": "NVDA",
        "class": "us_equity",
        "tradable": True,
        "fractionable": True,
        "status": "active",
    }
    quote = {"source_time": start, "feed": "iex", "bid": 155.60, "ask": 155.64}
    signal = {
        "probability_positive": 0.4136,
        "expected_net_return": 0.0026,
        "volatility": 0.5,
        "news_stance": "bullish",
        "sentiment": "review",
    }
    account = {"cash": 15000, "buying_power": 15000}

    def check(value, q=quote, strategy="swing"):
        return risk.entry_permission(
            "NVDA", value, asset, q, start, [], [], account, True, 0, strategy=strategy
        )

    assert not check(signal)[0]
    trial = signal | {"entry_policy": SWING_EXPECTANCY_TRIAL}
    assert check(trial)[0]
    assert not check(trial, strategy="day")[0]
    assert not check(trial | {"expected_net_return": 0.0003})[0]
    assert not check(trial | {"news_stance": "mixed"})[0]
    assert not check(trial | {"sentiment": "avoid"})[0]
    assert not check(trial, quote | {"ask": 156.30})[0]
    assert not check(trial, quote | {"source_time": start - timedelta(seconds=20)})[0]


def test_trial_durable_cap_blocks_second_attempt(paperdb, monkeypatch):  # noqa: F811
    from paperpilot.strategy import SWING_EXPECTANCY_TRIAL

    day = date(2026, 11, 18)
    fixed = schedule(day, day)[day]["open"]
    monkeypatch.setattr(engine.settings, "paper_execution_permitted", True)
    monkeypatch.setattr(engine.settings, "paper_swing_trial_start", day)
    monkeypatch.setattr(engine, "client_id", lambda session, stock, side: f"kti4-{session}-{stock}-{side}")
    with connect() as c:
        c.execute("UPDATE pilot_settings SET enabled=true")
        c.execute("INSERT INTO pilot_strategy_sessions VALUES(%s,%s,%s,'bundle','{}')", (day, fixed, fixed))
        for symbol in ("NVDA", "AMD"):
            c.execute(
                "INSERT INTO pilot_strategy_signals VALUES(%s,%s,%s,'swing','paper',%s,'BUY',%s)",
                (symbol, day, symbol, fixed, jb({"entry_policy": SWING_EXPECTANCY_TRIAL})),
            )
    broker = Mock()
    broker.submit.side_effect = engine.BrokerUnavailable("Uncertain acknowledgement")
    assert engine.submit_intent(broker, "NVDA", "buy", day, strategy_signal_id="NVDA")["status"] == "unknown"
    assert (
        engine.submit_intent(broker, "AMD", "buy", day, strategy_signal_id="AMD")["status"] == "trial_limit"
    )
    assert broker.submit.call_count == 1


def test_scaled_broker_order_requires_exact_frozen_intent_and_flat_inventory(paperdb, monkeypatch):  # noqa: F811
    import json

    import httpx
    from test_paperpilot import configured

    from paperpilot import risk
    from paperpilot.broker import PaperBroker
    from paperpilot.strategy import SWING_EXPECTANCY_TRIAL

    session = date(2026, 11, 18)
    entry = schedule(session, session)[session]["open"] + BAR
    configured(monkeypatch)
    for key, value in {
        "paper_mainkraft_enabled": True,
        "paper_execution_permitted": True,
        "paper_swing_trial_start": session,
        "paper_swing_trial_notional": 800,
    }.items():
        monkeypatch.setattr(risk.settings, key, value)
    monkeypatch.setattr(risk, "now", lambda: entry)
    cid = f"kti4-{session}-AMD-buy"
    body = dict(
        symbol="AMD", side="buy", type="market", time_in_force="day", client_order_id=cid, notional="800"
    )
    account = {"id": "new-account", "status": "ACTIVE", "cash": "15000", "buying_power": "15000"}
    positions, posts = [], []

    def handler(request):
        assert request.url.host == "paper-api.alpaca.markets"
        if request.method == "POST":
            posts.append(json.loads(request.content))
            return httpx.Response(200, json=body | {"status": "accepted"})
        responses = {"/v2/account": account, "/v2/orders": [], "/v2/positions": positions}
        return httpx.Response(200, json=responses[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        broker = PaperBroker(client)
        with pytest.raises(engine.BrokerUnavailable, match="authorized Swing intent"):
            broker.submit(body)
        with connect() as conn:
            conn.execute("UPDATE pilot_settings SET enabled=true")
            conn.execute(
                "INSERT INTO pilot_strategy_sessions VALUES(%s,%s,%s,'bundle','{}')",
                (session, entry, entry),
            )
            conn.execute(
                "INSERT INTO pilot_strategy_signals VALUES('scale',%s,'AMD','swing','paper',%s,'BUY',%s)",
                (session, entry, jb({"entry_policy": SWING_EXPECTANCY_TRIAL, "entry_notional": 800})),
            )
            conn.execute(
                """INSERT INTO pilot_intents(client_id,symbol,side,session_date,state,notional,payload,strategy_signal_id)
                VALUES(%s,'AMD','buy',%s,'submitting',800,%s,'scale')""",
                (cid, session, jb(body)),
            )
        with pytest.raises(engine.BrokerUnavailable, match="authorized Swing intent"):
            broker.submit(body | {"notional": "799"})
        positions.append({"symbol": "MSFT", "side": "long", "qty": "0.1", "market_value": "25"})
        with pytest.raises(engine.BrokerUnavailable, match="flat broker inventory"):
            broker.submit(body)
        positions.clear()
        assert broker.submit(body)["status"] == "accepted"
        assert posts == [body]
        assert risk.scaled_position_cap(
            {"strategy_payload": {"entry_policy": SWING_EXPECTANCY_TRIAL}, "notional": Decimal("800")}
        ) == Decimal("850")
        monkeypatch.setattr(risk, "now", lambda: entry + timedelta(minutes=1))
        with pytest.raises(engine.BrokerUnavailable, match="authorized Swing intent"):
            broker.submit(body)


def test_scaled_notional_parses_environment_and_rejects_other_sizes(monkeypatch):
    from pydantic import ValidationError

    from paperpilot.config import PaperSettings

    monkeypatch.setenv("INTELLIGENCE_PAPER_SWING_TRIAL_NOTIONAL", "800")
    assert PaperSettings(_env_file=None).paper_swing_trial_notional == 800
    monkeypatch.setenv("INTELLIGENCE_PAPER_SWING_TRIAL_NOTIONAL", "799")
    with pytest.raises(ValidationError, match="paper_swing_trial_notional"):
        PaperSettings(_env_file=None)
