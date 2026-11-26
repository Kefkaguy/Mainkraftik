import copy
from datetime import datetime, timedelta, timezone

import pytest
from test_chart_intelligence import synthetic_rows

from chart_intelligence.core import identity
from chart_intelligence.phase7 import CANDIDATES, predict, split_development, train
from chart_intelligence.phase7_execution import Execution, quote_at, replay


def test_exposed_evaluation_is_rejected_before_fitting():
    rows = synthetic_rows()
    rows.append({**rows[0], "date": "2026-01-27"})
    with pytest.raises(ValueError, match="Exposed evaluation"):
        split_development(rows)


@pytest.mark.parametrize("name", CANDIDATES)
def test_bounded_model_exports_finite_probabilities_without_label_inputs(name):
    rows = synthetic_rows()
    tr, cal, test, _ = split_development(rows)
    artifact = train(name, tr, cal)
    original = identity(artifact)
    predictions = predict(artifact, test[:3])
    poisoned = [{**r, "y": 999, "downside": -999, "target_first": 999} for r in test[:3]]
    assert predict(artifact, poisoned) == predictions
    assert identity(artifact) == original
    for p in predictions:
        assert 0 <= p["probability_up"] <= 1
        assert p["probability_up"] + p["probability_down"] == pytest.approx(1)
        assert p["lower_return"] <= p["expected_return"] <= p["upper_return"]


def opportunities():
    start = datetime(2025, 5, 1, 16, tzinfo=timezone.utc)
    rows = []
    for day in range(60):
        instant = start + timedelta(days=day)
        rows.append(
            {
                "symbol": "AMD",
                "date": str(instant.date()),
                "decision": instant.isoformat(),
                "known_dollar_volume": 1e8,
                "regime": "LOW_VOL_UP",
                "path": [
                    [(instant + timedelta(minutes=5 * i)).isoformat(), 100, 101, 99, 100.5, 1000]
                    for i in range(6)
                ],
            }
        )
    return rows, [{"probability_up": 0.8, "expected_return": 0.01} for _ in rows]


def test_missing_quotes_never_fabricate_fills():
    rows, p = opportunities()
    result = replay(rows, p)
    assert result["trade_count"] == 0 and result["net_pnl"] == 0
    assert result["rejections"]["EXECUTABLE_ENTRY_QUOTE_UNAVAILABLE"] == 60
    assert not result["quote_coverage_complete"]


def test_synthetic_costs_reduce_returns_and_keep_rejections_partials_and_risk():
    rows, p = opportunities()
    base = replay(rows, p, mode="ASSUMPTION_ONLY")
    stress = replay(rows, p, Execution(spread=0.003), mode="ASSUMPTION_ONLY")
    assert stress["net_pnl"] < base["net_pnl"]
    assert base["daily_mean_net_pnl_ci_98_75"] is not None
    assert any(o["state"] == "PARTIAL_THEN_REMAINDER_CANCELED" for o in base["orders"])
    assert any(o["state"] == "REJECTED" for o in base["orders"])
    assert replay(rows, p, mode="ASSUMPTION_ONLY") == base
    with pytest.raises(ValueError, match="risk"):
        replay(rows, p, Execution(notional=800))
    illiquid = copy.deepcopy(rows)
    illiquid[0]["known_dollar_volume"] = 1
    assert replay(illiquid, p, mode="ASSUMPTION_ONLY")["rejections"]["LIQUIDITY"] == 1


def test_no_entry_uses_outcome_or_unavailable_quote_to_pass_signal_gate():
    rows, p = opportunities()
    p = [{**v, "probability_up": 0.4} for v in p]
    result = replay(rows, p, mode="ASSUMPTION_ONLY")
    assert result["trade_count"] == 0 and result["rejections"] == {"PREDICTION_CONFIDENCE": 60}
    strict = replay(rows, p)
    assert strict["quote_coverage_status"] == "NOT_EVALUATED_NO_ELIGIBLE_OPPORTUNITIES"
    assert not strict["quote_coverage_complete"]
    now = datetime.fromisoformat(rows[0]["decision"])
    quotes = {
        ("AMD", now.isoformat()): {
            "bid": 100,
            "ask": 101,
            "source_time": (now - timedelta(seconds=16)).isoformat(),
            "acquired_at": now.isoformat(),
        }
    }
    assert quote_at(quotes, "AMD", now) is None


def test_portfolio_caps_and_daily_loss_prevent_more_entries():
    rows, p = opportunities()
    base = rows[0]
    rows = []
    for symbol in ("A", "B", "C"):
        path = copy.deepcopy(base["path"])
        path[0][3] = 70
        for bar in path[1:]:
            bar[1:5] = [70, 71, 69, 70]
        rows.append({**base, "symbol": symbol, "path": path})
    late = copy.deepcopy(rows[0])
    late["symbol"] = "D"
    late["decision"] = (datetime.fromisoformat(base["decision"]) + timedelta(hours=2)).isoformat()
    rows.append(late)
    result = replay(
        rows, [p[0]] * 4, Execution(reject_fraction=0, partial_fraction=0), mode="ASSUMPTION_ONLY"
    )
    assert result["trade_count"] == 2
    assert result["rejections"]["CAPITAL_OR_POSITION_LIMIT"] == 1
    assert result["rejections"]["DAILY_LOSS"] == 1


def test_hypothetical_entry_needs_post_prediction_and_exit_quotes(tmp_path):
    from chart_intelligence.controller import append, connection, entry_opportunity, resolve_opportunity
    from chart_intelligence.core import Protocol

    now = datetime(2026, 11, 18, 17, tzinfo=timezone.utc)

    def q(t, bid=100, ask=100.05):
        return {
            "symbol": "AMD",
            "source_time": t.isoformat(),
            "acquired_at": (t + timedelta(seconds=1)).isoformat(),
            "bid": bid,
            "ask": ask,
        }

    before = q(now - timedelta(seconds=2))
    plan = entry_opportunity({"probability_up": 0.8, "expected_return": 0.01}, before, now)
    assert plan["eligible"] and plan["status"] == "AWAIT_POST_ISSUANCE_QUOTE"
    p = {
        "symbol": "AMD",
        "issued_at": now.isoformat(),
        "decision": now.isoformat(),
        "hypothetical_entry": plan,
    }
    db = connection(tmp_path)
    append(db, "before", "quote", before)
    append(db, "exit", "quote", q(now + timedelta(minutes=30), 101, 101.05))
    assert resolve_opportunity(db, p, Protocol(), now + timedelta(hours=1))["status"] == "UNPRICED_ENTRY"
    append(db, "entry", "quote", q(now + timedelta(seconds=30)))
    result = resolve_opportunity(db, p, Protocol(), now + timedelta(hours=1))
    assert result["status"] == "HYPOTHETICAL_QUOTE_PRICED"
    assert result["entry_quote_time"] > p["issued_at"]
    assert result["net_pnl"] > 0
    db.close()


def test_controller_records_completion_time_and_blocks_backdating(tmp_path):
    from test_chart_intelligence import candles

    from chart_intelligence import store
    from chart_intelligence.controller import status, tick
    from chart_intelligence.core import Protocol

    tr, cal, test, _ = split_development(synthetic_rows())
    model = train("historical_mean", tr, cal)
    bars, instant = candles()
    instant -= timedelta(minutes=30)
    start = instant + timedelta(seconds=1)
    finished = instant + timedelta(seconds=10)
    report = {
        "model_id": store.put("model", model, tmp_path),
        "protocol": Protocol().__dict__,
        "not_before": instant.isoformat(),
        "models": {"sequence_mlp": {"mae": 0.01, "brier": 0.25}},
    }
    key = store.put("enrollment", report, tmp_path)

    def collect(symbol, _):
        return {
            "bars": [[t.isoformat(), *v.tolist()] for t, v in bars.items()],
            "feed": "sip",
            "adjustment": "split",
            "acquired_at": finished.isoformat(),
        }

    result = tick(key, ["AMD"], collect, start, tmp_path, clock=lambda: finished)
    assert result["issued"] == 1
    assert status(tmp_path)["prediction"][0]["issued_at"] == finished.isoformat()
    with pytest.raises(ValueError, match="enrollment"):
        tick(key, ["AMD"], collect, instant - timedelta(seconds=1), tmp_path)
    # A second issue after restart cannot rewrite the timestamp or prediction.
    tick(key, ["AMD"], collect, start, tmp_path, clock=lambda: finished + timedelta(seconds=5))
    assert len(status(tmp_path)["prediction"]) == 1


def test_delayed_sip_request_stays_outside_subscription_window(monkeypatch):
    import httpx

    from chart_intelligence import controller

    instant = datetime(2026, 11, 18, 17, tzinfo=timezone.utc)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/bars"):
            assert datetime.fromisoformat(request.url.params["end"]) == instant - timedelta(minutes=15)
            assert request.url.params["feed"] == "sip"
            return httpx.Response(200, json={"bars": []})
        assert request.url.path.endswith("/quotes/latest")
        return httpx.Response(403, json={})

    factory = controller.httpx.Client
    monkeypatch.setattr(
        controller.httpx, "Client", lambda **kwargs: factory(transport=httpx.MockTransport(handler), **kwargs)
    )
    result = controller.collect("AMD", instant)
    assert len(requests) == 2 and result["quote"] is None and result["feed"] == "sip"
    assert all(r.url.host == "data.alpaca.markets" and r.method == "GET" for r in requests)


def test_portfolio_envelope_reconciles_cash_and_separates_mark_drawdown():
    from chart_intelligence.phase7_accounting import portfolio_envelope

    rows, p = opportunities()
    result = replay(rows, p, mode="ASSUMPTION_ONLY")
    marked = portfolio_envelope(rows, result["trades"])
    assert marked["final_cash"] == pytest.approx(600 + result["net_pnl"])
    assert marked["max_drawdown_dollars"] >= result["realized_exit_max_drawdown"]
