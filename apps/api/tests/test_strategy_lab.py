from datetime import date, timedelta

import pytest

from intelligence.calendar import schedule
from paperpilot import lab_registry as registry
from paperpilot.simulation_rules import bar_trigger, can_enter, daily_change
from paperpilot.strategy_lab import Configuration, configurations, event_category, replay


def test_registry_rejects_modified_records(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "LAB", tmp_path)
    identity = registry.put("test", {"x": 1})
    assert registry.put("test", {"x": 1}) == identity
    assert registry.read(identity) == {"x": 1}
    (tmp_path / "records" / (identity + ".json")).write_text("{}")
    with pytest.raises(ValueError):
        registry.read(identity)


def test_shared_risk_and_ambiguous_bar():
    assert daily_change(4980, 5000) == -20
    assert daily_change(5000, None) == 0
    assert not can_enter(1000, 4000, 1000, 800, 1, 0, 50, 0, 500)
    assert not can_enter(5000, 0, 0, 800, 0, -50, 50, 0, 500)
    assert not can_enter(5000, 0, 0, 800, 0, 0, 50, 500, 500)
    assert bar_trigger(100, 104, 97, 100, 0.02, 0.03) == (98, "stop")
    assert bar_trigger(90, 94, 89, 100, 0.02, 0.03)[0] == 90


def test_boundaries_and_source_events():
    assert len(configurations()) == 32
    assert len({c.key for c in configurations()}) == 32
    assert event_category("Company signs contract")["acquisition_contract"]
    assert event_category("Reports quarterly results")["earnings_guidance"]


def test_research_api_requires_authentication():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from intelligence.lab_api import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/v1/research-lab/results").status_code == 401


def test_missing_observations_never_create_a_fill():
    day = date(2026, 11, 27)
    row = {"x": [0.01] * 11, "volatility": .2, "last_close": 100,
           "liquidity": 25_000_000, "above_mean": True, "prior_high": 102, "volume_ratio": 1}
    result = replay(Configuration("momentum", 0, False, 1), "technical", 800, [day],
                    schedule(day, day), {day: {"AMD": row}}, {"AMD": {day: {}}}, {}, {})
    assert result["trades"] == [] and result["summary"]["net_pnl"] == 0


def test_two_positions_and_daily_loss_latch_survive_price_recovery():
    day = date(2026, 11, 27)
    row = {"x": [.01] * 11, "volatility": .2, "last_close": 100,
           "liquidity": 25_000_000, "above_mean": True, "prior_high": 99, "volume_ratio": 1}
    bars = {name: {day: {i: (100, 100, 100, 100) for i in range(42)}}
            for name in ["AMD", "MSFT", "NVDA"]}
    for name in bars:
        bars[name][day][1] = (100, 100, 96, 96)
    result = replay(Configuration("breakout", 0, False, 1), "technical", 800, [day],
                    schedule(day, day), {day: {name: row for name in bars}}, bars, {}, {})
    # Two simultaneous losses trigger exits. Recovery cannot admit a third stock.
    assert result["summary"]["entries"] == 2
    assert all(t["exit_index"] == 2 for t in result["trades"])
    assert result["open_positions"] == []


def test_embargo_matures_positions_without_new_entries():
    day = date(2026, 11, 27)
    row = {"x": [.01] * 11, "volatility": .2, "last_close": 100,
           "liquidity": 25_000_000, "above_mean": True, "prior_high": 99, "volume_ratio": 1}
    bars = {"AMD": {day: {i: (100, 100, 100, 100) for i in range(42)}}}
    result = replay(Configuration("momentum", 0, False, 1), "technical", 800, [day],
                    schedule(day, day), {day: {"AMD": row}}, bars, {}, {}, entry_dates=set())
    assert result["summary"]["entries"] == 0


def test_partial_fills_and_restart_ledger():
    from paperpilot.engine import ledger

    buy = {"symbol": "AMD", "side": "buy", "status": "filled", "filled_qty": "5", "filled_avg_price": "100"}
    partial = {"symbol": "AMD", "side": "sell", "status": "partially_filled", "filled_qty": "2", "filled_avg_price": "102"}
    book = ledger([buy, partial])
    assert book["realized"] == 4 and book["lots"]["AMD"]["qty"] == 3
    replayed = ledger([buy, partial])
    assert replayed == book


def test_early_close_delays_exit_to_next_bar_and_blocks_same_day_reentry():
    day = date(2026, 11, 27)
    cal = schedule(day, day)
    slots = (cal[day]["close"] - cal[day]["open"]) // timedelta(minutes=5)
    assert slots == 42
    row = {
        "x": [0.01, 0.02, 0.03, 0.04, 0.01, 0.01, 1, 0.01, 0.02, 0.01, 0.01],
        "volatility": 0.2,
        "last_close": 100,
        "liquidity": 25_000_000,
        "above_mean": True,
        "prior_high": 102,
        "volume_ratio": 1,
    }
    bars = {"AMD": {day: {i: (100, 100, 100, 100) for i in range(slots)}}}
    bars["AMD"][day][2] = (100, 105, 99, 104)
    bars["AMD"][day][3] = (101, 101, 101, 101)
    result = replay(
        Configuration("momentum", 0, False, 1),
        "technical",
        800,
        [day],
        cal,
        {day: {"AMD": row}},
        bars,
        {},
        {},
    )
    trade = result["trades"][0]
    assert trade["exit_index"] == 3 and trade["exit_value"] == 808
    assert result["summary"]["entries"] == 1
