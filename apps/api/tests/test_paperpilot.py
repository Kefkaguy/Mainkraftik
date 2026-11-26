import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg import sql

from paperpilot import engine
from paperpilot.broker import BrokerUnavailable, PaperBroker, verify_account
from paperpilot.config import settings
from paperpilot.db import connect, migrate
from paperpilot.risk import client_id, entry_permission


@pytest.fixture
def paperdb(monkeypatch):
    uri = urlsplit(settings.paper_database_url)
    assert uri.hostname in {"127.0.0.1", "localhost"} and uri.port == 5543
    name = "intelligence_paper_test_" + secrets.token_hex(5)
    admin = urlunsplit(uri._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    monkeypatch.setattr(settings, "paper_database_url", urlunsplit(uri._replace(path="/" + name)))
    migrate()
    yield
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def configured(monkeypatch):
    monkeypatch.setattr(settings, "paper_key", "dedicated-test-key")
    monkeypatch.setattr(settings, "paper_secret", "test-only")
    monkeypatch.setattr(settings, "paper_account_id", "new-account")
    monkeypatch.setattr(settings, "paper_forbidden_account_id", "old-account")
    monkeypatch.setattr(settings, "paper_base_url", "https://paper-api.alpaca.markets")


def test_live_endpoint_and_bulk_cancel_are_rejected_before_network(monkeypatch):
    configured(monkeypatch)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        broker = PaperBroker(c)
        monkeypatch.setattr(settings, "paper_base_url", "https://api.alpaca.markets")
        with pytest.raises(BrokerUnavailable):
            broker.submit({})
        monkeypatch.setattr(settings, "paper_base_url", "https://paper-api.alpaca.markets")
        with pytest.raises(BrokerUnavailable):
            broker.request("DELETE", "/v2/orders")
        with pytest.raises(BrokerUnavailable):
            broker.cancel({"id": str(uuid4()), "client_order_id": "old-trader"})
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "BTCUSD"},
        {"side": "short"},
        {"notional": "101"},
        {"notional": "NaN"},
        {"extended_hours": True},
        {"qty": "2"},
    ],
)
def test_order_policy_rejects_unsafe_shapes(monkeypatch, change):
    configured(monkeypatch)
    body = {
        "symbol": "AMD",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "kti4-test",
        "notional": "90",
        **change,
    }
    with pytest.raises(BrokerUnavailable):
        PaperBroker().submit(body)


def test_account_ids_and_cash_isolation(monkeypatch):
    configured(monkeypatch)
    account = {"id": "new-account", "status": "ACTIVE", "cash": "1000"}
    assert verify_account(account, [], [])
    with pytest.raises(BrokerUnavailable):
        verify_account({**account, "id": "old-account"}, [], [])
    with pytest.raises(BrokerUnavailable):
        verify_account(account, [], [{"client_order_id": "other-trader"}])
    with pytest.raises(BrokerUnavailable):
        verify_account(account, [{"symbol": "AMD", "side": "short"}], [])


def test_every_broker_mutation_rechecks_actual_account_identity(monkeypatch):
    configured(monkeypatch)
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        return httpx.Response(200, json={"id": "old-account"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        broker = PaperBroker(client)
        with pytest.raises(BrokerUnavailable):
            broker.submit(
                {
                    "symbol": "AMD",
                    "side": "buy",
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": "kti4-test",
                    "notional": "90",
                }
            )
        with pytest.raises(BrokerUnavailable):
            broker.cancel({"id": str(uuid4()), "client_order_id": "kti4-test"})
    assert calls == [("GET", "/v2/account"), ("GET", "/v2/account")]


def test_final_cash_read_prevents_borrowing_when_an_earlier_buy_fills(monkeypatch):
    configured(monkeypatch)
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        if req.url.path == "/v2/account":
            cash = "100" if len(calls) == 1 else "10"
            return httpx.Response(
                200, json={"id": "new-account", "status": "ACTIVE", "cash": cash, "buying_power": "1000"}
            )
        if req.url.path == "/v2/orders":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200, json=[{"symbol": "MSFT", "side": "long", "qty": "1", "market_value": "90"}]
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BrokerUnavailable):
            PaperBroker(client).submit(
                {
                    "symbol": "AMD",
                    "side": "buy",
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": "kti4-test",
                    "notional": "90",
                }
            )
    assert calls == [
        ("GET", "/v2/account"),
        ("GET", "/v2/orders"),
        ("GET", "/v2/positions"),
        ("GET", "/v2/account"),
    ]


def test_final_sell_gate_cannot_exceed_available_long_inventory(monkeypatch):
    configured(monkeypatch)
    calls = []

    def handler(req):
        calls.append(req.method)
        if req.url.path == "/v2/account":
            return httpx.Response(
                200, json={"id": "new-account", "status": "ACTIVE", "cash": "600", "buying_power": "600"}
            )
        if req.url.path == "/v2/orders":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {"symbol": "AMD", "side": "long", "qty": "1", "qty_available": "0.5", "market_value": "90"}
            ],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BrokerUnavailable):
            PaperBroker(client).submit(
                {
                    "symbol": "AMD",
                    "side": "sell",
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": "kti4-test",
                    "qty": "1",
                }
            )
    assert all(method == "GET" for method in calls)


def risk_inputs():
    instant = datetime(2026, 11, 13, 14, 35, tzinfo=UTC)
    return dict(
        symbol="AMD",
        signal={"expected_return": 0.002, "probability_positive": 0.6, "volatility": 0.2},
        asset={
            "symbol": "AMD",
            "class": "us_equity",
            "status": "active",
            "tradable": True,
            "fractionable": True,
        },
        quote={"source_time": instant, "bid": 100, "ask": 100.1, "feed": "iex"},
        instant=instant,
        positions=[],
        intents=[],
        account={"cash": "600", "buying_power": "2400"},
        enabled=True,
        daily_pnl=0,
    )


def test_risk_cash_reservations_staleness_and_no_averaging():
    args = risk_inputs()
    assert entry_permission(**args)[0]
    assert not entry_permission(**{**args, "daily_pnl": -12})[0]
    assert not entry_permission(**{**args, "enabled": False})[0]
    assert not entry_permission(
        **{**args, "quote": {**args["quote"], "source_time": args["instant"] - timedelta(seconds=16)}}
    )[0]
    assert not entry_permission(
        **{**args, "positions": [{"symbol": "AMD", "qty": "0.1", "market_value": "10"}]}
    )[0]
    assert not entry_permission(**{**args, "account": {"cash": "50", "buying_power": "10000"}})[0]
    reserve = [{"symbol": "MSFT", "side": "buy", "state": "new", "notional": Decimal("550")}]
    assert not entry_permission(**{**args, "intents": reserve})[0]
    assert not entry_permission(
        **{**args, "intents": [{"symbol": "AMD", "side": "sell", "state": "filled"}]}
    )[0]


def test_broker_calendar_missing_or_mismatched_cannot_authorize_execution():
    day = datetime(2026, 11, 13, tzinfo=UTC).date()
    with pytest.raises(BrokerUnavailable):
        engine.verified_session_close(day, [])
    with pytest.raises(BrokerUnavailable):
        engine.verified_session_close(day, [{"date": str(day), "open": "09:30", "close": "13:00"}])
    shortened = datetime(2026, 11, 27, tzinfo=UTC).date()
    close = engine.verified_session_close(
        shortened, [{"date": str(shortened), "open": "09:30", "close": "13:00"}]
    )
    assert close == datetime(2026, 11, 27, 18, tzinfo=UTC)


def test_lost_acknowledgement_does_not_resubmit_after_restart(paperdb, monkeypatch):
    monkeypatch.setattr(settings, "paper_execution_permitted", True)
    with connect() as c:
        c.execute("UPDATE pilot_settings SET enabled=true WHERE id=1")

    class Broker:
        calls = 0

        def submit(self, body):
            self.calls += 1
            raise BrokerUnavailable("Connection lost")

    broker = Broker()
    day = datetime(2026, 11, 13, tzinfo=UTC).date()
    first = engine.submit_intent(broker, "AMD", "buy", day)
    second = engine.submit_intent(broker, "AMD", "buy", day)
    assert first["status"] == "unknown" and second["status"] == "already_attempted"
    assert broker.calls == 1
    with connect() as c:
        assert c.execute("SELECT state FROM pilot_intents").fetchone()["state"] == "unknown"


def test_partial_fills_rejections_and_fill_ledger(paperdb):
    day = datetime(2026, 11, 13, tzinfo=UTC).date()
    cid = client_id(day, "AMD", "buy")
    with connect() as c:
        c.execute(
            "INSERT INTO pilot_intents(client_id,symbol,side,session_date,state,payload) VALUES(%s,'AMD','buy',%s,'unknown','{}')",
            (cid, day),
        )
    order = {
        "id": str(uuid4()),
        "client_order_id": cid,
        "symbol": "AMD",
        "side": "buy",
        "filled_qty": "1",
        "filled_avg_price": "10",
        "status": "partially_filled",
        "submitted_at": "2026-11-13T14:35:00Z",
    }
    engine.record_order(order)
    engine.record_order({**order, "filled_qty": "2", "status": "filled"})
    with connect() as c:
        book = engine.ledger(engine.latest_orders(c))
    assert book["buys"] == 20 and book["lots"]["AMD"]["qty"] == 2
    sell = {
        "symbol": "AMD",
        "side": "sell",
        "filled_qty": "2",
        "filled_avg_price": "9",
        "status": "filled",
        "submitted_at": "2026-11-13T15:00:00Z",
    }
    book = engine.ledger([{**order, "filled_qty": "2"}, sell])
    assert book["realized"] == -2 and book["losses"] == 1 and book["lots"]["AMD"]["qty"] == 0
    engine.record_order({**order, "filled_qty": "0", "filled_avg_price": None, "status": "rejected"})
    with connect() as c:
        assert c.execute("SELECT state FROM pilot_intents").fetchone()["state"] == "filled"


def test_rejected_intent_and_immutable_evidence(paperdb):
    day = datetime(2026, 11, 13, tzinfo=UTC).date()
    cid = client_id(day, "MSFT", "buy")
    with connect() as c:
        c.execute(
            "INSERT INTO pilot_intents(client_id,symbol,side,session_date,state,payload) VALUES(%s,'MSFT','buy',%s,'submitting','{}')",
            (cid, day),
        )
    engine.record_order(
        {
            "id": str(uuid4()),
            "client_order_id": cid,
            "symbol": "MSFT",
            "side": "buy",
            "status": "rejected",
            "filled_qty": "0",
        }
    )
    with connect() as c:
        assert c.execute("SELECT state FROM pilot_intents").fetchone()["state"] == "rejected"
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("DELETE FROM pilot_order_versions")


def test_emergency_stop_cancels_only_owned_orders_and_leaves_positions(paperdb):
    day = datetime(2026, 11, 13, tzinfo=UTC).date()
    cid = client_id(day, "AMD", "buy")
    with connect() as c:
        c.execute(
            "INSERT INTO pilot_intents(client_id,symbol,side,session_date,state,payload) VALUES(%s,'AMD','buy',%s,'new','{}')",
            (cid, day),
        )

    class Broker:
        canceled = []

        def by_client(self, client):
            return {"id": str(uuid4()), "client_order_id": client, "status": "new"}

        def cancel(self, order):
            self.canceled.append(order["client_order_id"])

    broker = Broker()
    result = engine.disable(broker)
    assert not result["enabled"] and broker.canceled == [cid]
    assert "Remain open" in result["positions"]


def test_pilot_service_requires_private_bridge_token(paperdb, monkeypatch):
    from fastapi.testclient import TestClient

    from paperpilot.service import app

    monkeypatch.setattr(settings, "paper_internal_token", "test-token")
    client = TestClient(app)
    assert client.get("/status").status_code == 403
    assert client.post("/control", json={"enabled": True}).status_code == 403
    response = client.get("/status", headers={"x-pilot-token": "test-token"})
    assert response.status_code == 200 and response.json()["snapshot"] is None
    assert [s["symbol"] for s in response.json()["stocks"]] == ["AMD", "MSFT", "GOOG", "AMZN", "TSM", "META"]


def test_missing_precutoff_readiness_records_six_skips_without_backdating(paperdb, monkeypatch):
    instant = datetime(2026, 11, 13, 14, 16, tzinfo=UTC)
    monkeypatch.setattr(engine, "now", lambda: instant)
    monkeypatch.setattr(engine, "latest_sources", lambda provider, **kwargs: {})
    result = engine.create_signals()
    assert result["status"] == "recorded"
    engine.create_signals()  # Worker restart/repeated tick does not duplicate decisions.
    with connect() as c:
        rows = c.execute("SELECT * FROM pilot_decisions").fetchall()
        assert len(rows) == 6 and all(r["action"] == "SKIP" for r in rows)
        assert all(
            r["issued_at"] == instant and not r["payload"]["account_ready_before_cutoff"] for r in rows
        )
        assert c.execute("SELECT count(*) n FROM pilot_intents").fetchone()["n"] == 0
