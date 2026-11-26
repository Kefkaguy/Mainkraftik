import json
from datetime import UTC, datetime

import httpx
import pytest
from test_paperpilot import configured, risk_inputs
from test_paperpilot import paperdb as _paperdb

from paperpilot.broker import BrokerUnavailable, PaperBroker
from paperpilot.config import current_symbols, settings
from paperpilot.db import connect, jb
from paperpilot.discovery import rank, reserve_call, validate_picks
from paperpilot.risk import entry_permission

paperdb = _paperdb


def freeze(symbols):
    day = datetime(2026, 11, 16, tzinfo=UTC).date()
    with connect() as conn:
        conn.execute(
            "INSERT INTO pilot_research_runs VALUES('test',%s,'scheduled',now(),%s)", (day, jb({"picks": []}))
        )
        conn.execute("INSERT INTO pilot_selections VALUES(%s,'test',now(),%s)", (day, jb(symbols)))
    return day


def test_news_output_cannot_invent_symbols_sources_or_probability():
    candidates = {"NVDA": {}}
    articles = {"n1": {"symbols": ["NVDA"]}}
    pick = {"symbol": "NVDA", "source_ids": ["n1"], "stance": "bullish", "rationale": "Sourced announcement"}
    assert validate_picks({"picks": [pick]}, candidates, articles)[0]["symbol"] == "NVDA"
    for changed in [{"symbol": "FAKE"}, {"source_ids": ["invented"]}, {"confidence": 0.99}]:
        with pytest.raises(ValueError):
            validate_picks({"picks": [{**pick, **changed}]}, candidates, articles)
    with pytest.raises(ValueError):
        validate_picks({"picks": [pick] * 21}, candidates, articles)


def test_selection_membership_is_frozen_and_limited(paperdb, monkeypatch):
    monkeypatch.setattr(settings, "paper_discovery_enabled", True)
    day = freeze(["NVDA"])
    assert current_symbols(day) == ("NVDA",)
    with connect() as conn:
        with pytest.raises(Exception):
            conn.execute("UPDATE pilot_selections SET symbols='[]'")


def test_daily_groq_budget_survives_a_restart(paperdb):
    for i in range(3):
        reserve_call(datetime.now(UTC).date(), str(i))
    with pytest.raises(ValueError, match="budget"):
        reserve_call(datetime.now(UTC).date(), "fourth")


def test_llm_response_is_research_only_and_has_no_order_tools(paperdb, monkeypatch):
    monkeypatch.setattr(settings, "groq_key", "test-only")
    candidates = {"NVDA": {"price": 100, "dollar_volume_20": 50_000_000}}
    articles = {"n1": {"symbols": ["NVDA"], "headline": "Company announcement", "summary": "Reported news"}}

    def handler(request):
        body = json.loads(request.content)
        assert "tools" not in body and body["response_format"]["json_schema"]["strict"]
        assert "test-only" not in json.dumps(body)
        return httpx.Response(
            200,
            json={
                "id": "r1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "picks": [
                                        {
                                            "symbol": "NVDA",
                                            "source_ids": ["n1"],
                                            "stance": "bullish",
                                            "rationale": "Announcement",
                                        }
                                    ]
                                }
                            )
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        picks, metadata = rank(candidates, articles, datetime.now(UTC).date(), client)
    assert picks[0]["symbol"] == "NVDA" and "probability" not in picks[0]


def test_discovery_requires_70_percent_and_bullish_news(paperdb, monkeypatch):

    monkeypatch.setattr(settings, "paper_discovery_enabled", True)
    freeze(["NVDA"])
    args = risk_inputs()
    args.update(symbol="NVDA", instant=datetime(2026, 11, 16, 14, 35, tzinfo=UTC))
    args["asset"] = {**args["asset"], "symbol": "NVDA"}
    args["quote"] = {**args["quote"], "source_time": args["instant"]}
    args["signal"] = {
        "expected_return": 0.003,
        "probability_positive": 0.69,
        "volatility": 0.2,
        "news_stance": "bullish",
    }
    assert not entry_permission(**args)[0]
    args["signal"]["probability_positive"] = 0.70
    assert entry_permission(**args)[0]
    args["signal"]["news_stance"] = "mixed"
    assert not entry_permission(**args)[0]


def test_broker_rejects_unselected_stock_before_network(paperdb, monkeypatch):
    from paperpilot import broker

    configured(monkeypatch)
    monkeypatch.setattr(settings, "paper_discovery_enabled", True)
    day = freeze(["NVDA"])
    monkeypatch.setattr(broker, "current_symbols", lambda: current_symbols(day))
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BrokerUnavailable):
            PaperBroker(client).submit(
                {
                    "symbol": "AMD",
                    "side": "buy",
                    "notional": "25",
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": "kti4-test",
                }
            )
    assert calls == []
