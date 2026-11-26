"""Full Engine integration against an in-memory exchange and isolated local DB.

No credentials or network transports are used. Exchange state survives Engine
replacement, as broker state does when a worker restarts.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from test_paperpilot import paperdb  # noqa: F401

from intelligence.calendar import EASTERN, schedule
from paperpilot import engine, risk, universe
from paperpilot.db import connect, jb
from paperpilot.decision_trace import DecisionTrace, signal_trace


@pytest.fixture
def exchange(paperdb, monkeypatch):  # noqa: F811
    session = date(2026, 11, 27)  # early close, followed by weekend
    cal = schedule(session, session)[session]
    stamp = cal["open"] + timedelta(minutes=5)
    state = SimpleNamespace(
        time=stamp,
        price=100.0,
        cash=1000.0,
        qty=0.0,
        orders={},
        calls=0,
        partial=False,
        unknown=False,
        partial_exit=False,
    )
    for name, value in {
        "paper_mainkraft_enabled": True,
        "paper_discovery_enabled": True,
        "paper_execution_permitted": True,
        "paper_key": "fixture",
        "paper_secret": "fixture",
        "paper_account_id": "fixture-account",
        "paper_forbidden_account_id": "excluded",
    }.items():
        monkeypatch.setattr(engine.settings, name, value)
    for module in (engine, universe, risk):
        monkeypatch.setattr(module, "now", lambda: state.time)
    monkeypatch.setattr(engine, "BUY_NOTIONAL", Decimal("25"))
    monkeypatch.setattr(risk, "BUY_NOTIONAL", Decimal("25"))
    monkeypatch.setattr(engine.shutil, "disk_usage", lambda _: SimpleNamespace(free=20 * 1024**3))
    for name in ("refresh_prices", "refresh_company_information", "poll_quotes"):
        monkeypatch.setattr(engine, name, lambda: None)
    # Fail the test if any real HTTP transport is attempted.
    import httpx

    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Network in dry run"))
    signal = {
        "expected_net_return": 0.005,
        "probability_positive": 0.62,
        "volatility": 0.2,
        "news_stance": "bullish",
        "sentiment": "review",
        "stop": 0.02,
        "take": 0.03,
    }
    with connect() as db:
        db.execute("UPDATE pilot_settings SET enabled=true")
        db.execute("INSERT INTO pilot_research_runs VALUES('r',%s,'scheduled',%s,'{}')", (session, stamp))
        db.execute("INSERT INTO pilot_selections VALUES(%s,'r',%s,'[\"AMD\"]')", (session, stamp))
        db.execute(
            "INSERT INTO pilot_strategy_sessions VALUES(%s,%s,%s,'test','{}')", (session, stamp, stamp)
        )
        db.execute(
            "INSERT INTO pilot_strategy_signals VALUES('s',%s,'AMD','day','paper',%s,'BUY',%s)",
            (session, stamp, jb(signal)),
        )
        from psycopg import sql

        db.execute(
            sql.SQL("ALTER TABLE pilot_intents ALTER COLUMN created_at SET DEFAULT {}").format(
                sql.Literal(stamp)
            )
        )

    class Exchange:
        def account(self):
            return {
                "id": "fixture-account",
                "status": "ACTIVE",
                "cash": state.cash,
                "buying_power": state.cash,
                "equity": state.cash + state.qty * state.price,
            }

        def positions(self):
            return (
                [
                    {
                        "symbol": "AMD",
                        "side": "long",
                        "qty": state.qty,
                        "qty_available": state.qty,
                        "cost_basis": state.qty * 100,
                        "market_value": state.qty * state.price,
                        "unrealized_pl": state.qty * (state.price - 100),
                    }
                ]
                if state.qty
                else []
            )

        def orders(self):
            return [o for o in state.orders.values() if o["status"] not in risk.TERMINAL]

        def clock(self):
            return {"timestamp": state.time.isoformat(), "is_open": state.time < cal["close"]}

        def request(self, method, path, **kwargs):
            assert method == "GET"
            if path == "/v2/calendar":
                return [
                    {
                        "date": str(session),
                        **{k: cal[k].astimezone(EASTERN).strftime("%H:%M") for k in ("open", "close")},
                    }
                ]
            assert path == "/v2/assets/AMD"
            return {
                "symbol": "AMD",
                "class": "us_equity",
                "status": "active",
                "tradable": True,
                "fractionable": True,
            }

        def by_client(self, cid):
            return state.orders.get(cid)

        def cancel(self, order):
            state.orders[order["client_order_id"]] = order | {"status": "canceled"}

        def submit(self, body):
            cid = body["client_order_id"]
            assert cid not in state.orders, "Duplicate exchange submission"
            state.calls += 1
            buy = body["side"] == "buy"
            qty = float(body["notional"]) / state.price if buy else float(body["qty"])
            partial = state.partial if buy else state.partial_exit
            qty = qty / 2 if partial else qty
            state.qty += qty if buy else -qty
            state.cash += -qty * state.price if buy else qty * state.price
            order = body | {
                "id": cid,
                "status": "partially_filled" if partial else "filled",
                "filled_qty": str(qty),
                "filled_avg_price": str(state.price),
                "submitted_at": state.time.isoformat(),
                "updated_at": state.time.isoformat(),
            }
            state.orders[cid] = order
            if state.unknown:
                state.unknown = False
                raise engine.BrokerUnavailable("Fixture lost acknowledgement after acceptance")
            return order

    broker = Exchange()

    def tick(worker):
        with connect() as db:
            db.execute(
                """INSERT INTO pilot_quotes VALUES('AMD',%s,%s,%s,%s,%s,'iex')
                ON CONFLICT(symbol) DO UPDATE SET source_time=excluded.source_time, acquired_at=excluded.acquired_at,
                price=excluded.price,bid=excluded.bid,ask=excluded.ask""",
                (state.time, state.time, state.price, state.price - 0.01, state.price + 0.01),
            )
        worker.tick()

    return state, broker, tick, cal


@pytest.mark.parametrize(
    "exit_kind,price,pnl", [("stop", 97.0, -0.75), ("target", 104.0, 1.0), ("time", 101.0, 0.25)]
)
def test_engine_restarts_and_reconciles_replay(exchange, exit_kind, price, pnl):
    state, broker, tick, cal = exchange
    worker = engine.Engine(broker, evidence_type="DRY_RUN")
    tick(worker)
    assert state.qty == 0.25 and state.calls == 1
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    assert state.calls == 1
    state.price = price
    state.time = (
        cal["close"] - timedelta(minutes=15) if exit_kind == "time" else state.time + timedelta(minutes=5)
    )
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    assert state.qty == 0 and state.calls == 2
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    with connect() as db:
        book = engine.ledger(engine.latest_orders(db))
    assert book["realized"] == pytest.approx(pnl)
    assert state.cash == pytest.approx(1000 + pnl)
    assert book["lots"]["AMD"]["qty"] == 0
    # Independent replay of the actual executable fills, not OHLC assumptions.
    from paperpilot.fill_replay import reconcile

    result = reconcile(list(state.orders.values()), 1000)
    assert result["cash"] == pytest.approx(state.cash)
    assert result["realized"] == pytest.approx(book["realized"])


def test_partial_unknown_restart_and_partial_exit(exchange):
    state, broker, tick, cal = exchange
    state.partial = state.unknown = True
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    assert state.qty == 0.125 and state.calls == 1
    state.time += timedelta(seconds=20)
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    assert state.calls == 1
    # Exchange completes the remaining buy; cumulative notification is idempotent.
    cid, order = next(iter(state.orders.items()))
    state.qty += 0.125
    state.cash -= 12.5
    state.orders[cid] = order | {"filled_qty": ".25", "status": "filled"}
    engine.record_order(state.orders[cid])
    engine.record_order(state.orders[cid])
    engine.record_order(order)  # late smaller cumulative fill is ignored
    state.partial_exit = True
    state.price = 97
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    assert state.qty == 0.125
    sell = next(o for o in state.orders.values() if o["side"] == "sell")
    broker.cancel(sell)
    state.time += timedelta(seconds=20)
    state.partial_exit = False
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    assert state.qty == 0 and state.calls == 3
    with connect() as db:
        book = engine.ledger(engine.latest_orders(db))
    assert book["realized"] == pytest.approx(-0.75)
    from paperpilot.fill_replay import reconcile

    assert reconcile(list(state.orders.values()), 1000)["cash"] == pytest.approx(state.cash)


def test_structured_gate_status_and_policy_equivalence():
    t = DecisionTrace().expect(["first", "second", "third"])
    assert not t.blocked("first", False, "available")
    assert t.blocked("second", True, "stale")
    assert [g["status"] for g in t.gates.values()] == ["PASSED", "FAILED", "NOT_EVALUATED"]
    result = {
        "expected_net_return": 0.0001,
        "probability_positive": 0.4,
        "volatility": 0.9,
        "news_stance": "mixed",
        "sentiment": "avoid",
    }
    payload = signal_trace("day", result, True, "paper", [], "HOLD")
    assert all(
        payload["gates"][k]["status"] == "FAILED"
        for k in ("probability", "expected_net_return", "volatility", "news", "sentiment")
    )
    assert payload["gates"]["broker"]["status"] == "NOT_EVALUATED"


@pytest.mark.parametrize("restriction", ["cash", "window", "disabled", "daily_loss", "resource"])
def test_engine_blocks_risk_and_never_submits(exchange, monkeypatch, restriction):
    state, broker, tick, cal = exchange
    if restriction == "cash":
        state.cash = 10
    elif restriction == "window":
        state.time += timedelta(minutes=1)
    elif restriction == "disabled":
        monkeypatch.setattr(engine.settings, "paper_execution_permitted", False)
    elif restriction == "resource":
        monkeypatch.setattr(engine.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    else:
        with connect() as db:
            db.execute(
                "INSERT INTO pilot_snapshots(observed_at,account_id,payload) VALUES(%s,'fixture-account',%s)",
                (
                    state.time - timedelta(minutes=1),
                    jb(
                        {
                            "account": broker.account(),
                            "cash_consistent": True,
                            "account_pnl_available": True,
                            "strategy_equity": 612,
                        }
                    ),
                ),
            )
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    assert state.calls == 0 and state.qty == 0


def test_signal_trace_is_immutable_and_risk_short_circuit_is_visible(exchange):
    state, broker, tick, _ = exchange
    state.cash = 10
    tick(engine.Engine(broker, evidence_type="DRY_RUN"))
    with connect() as db:
        trace = db.execute(
            "SELECT payload->'decision_trace' AS trace FROM pilot_events WHERE kind='strategy_entry_gate'"
        ).fetchone()["trace"]
    assert trace["version"] == "decision-trace-v1"
    assert trace["gates"]["actual_cash_buying_power_insufficient_borrowing_prohibited"]["status"] == "FAILED"
    assert trace["gates"]["broker"]["status"] == "NOT_EVALUATED"
    with pytest.raises(Exception, match="append-only"):
        with connect() as db:
            db.execute("UPDATE pilot_events SET payload='{}'")
