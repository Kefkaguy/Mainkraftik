import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from chart_intelligence.phase8 import METHODS, feature, partition, predict, train
from chart_intelligence.phase8_evidence import put_compressed, read_compressed, snapshot, verify
from chart_intelligence.phase8_execution import Costs, replay
from intelligence.calendar import schedule


def synthetic_rows(horizon="one_day"):
    rng = np.random.default_rng(8)
    rows = []
    calendar = schedule(date(2022, 11, 1), date(2025, 4, 2))
    days = list(calendar)
    for i, day in enumerate(days[:-6]):
        x = rng.normal(size=15)
        numeric = rng.normal(0, 0.01, 11)
        numeric[4] = 0.01
        y = float(0.004 * x[0] + rng.normal(0, 0.02))
        rows.append(
            {
                "date": str(day),
                "decision": datetime.combine(day, datetime.min.time(), timezone.utc)
                .replace(hour=20)
                .isoformat(),
                "label_end": calendar[days[i + 5]]["close"].isoformat(),
                "horizon": horizon,
                "symbol": "AMD",
                "x": x.tolist(),
                "numeric": numeric.tolist(),
                "y": y,
                "downside": -abs(y),
                "target_first": int(y > 0.03),
            }
        )
    return rows


def test_partitions_purge_labels_and_refuse_exposed_audit():
    rows = synthetic_rows()
    tr, cal, selection = partition(rows)
    assert max(r["label_end"] for r in tr) < min(r["decision"] for r in cal)
    assert max(r["label_end"] for r in cal) < min(r["decision"] for r in selection)
    with pytest.raises(ValueError, match="Exposed"):
        partition(rows + [{**rows[-1], "label_end": "2025-04-03T20:00:00+00:00"}])


@pytest.mark.parametrize("method", METHODS)
def test_bounded_new_horizon_models_ignore_outcomes_at_inference(method):
    tr, cal, selection = partition(synthetic_rows())
    m = train(method, "one_day", tr, cal)
    forecasts = predict(m, selection[:2])
    assert (
        predict(m, [r | {"y": 1000, "downside": -1000, "target_first": 0} for r in selection[:2]])
        == forecasts
    )
    with pytest.raises(ValueError, match="horizon"):
        predict(m, [selection[0] | {"horizon": "five_sessions"}])
    assert not m["execution_authority"]


def test_daily_features_exclude_uncompleted_origin_and_future():
    cal = schedule(date(2024, 1, 1), date(2024, 3, 1))
    days = list(cal)
    daily = {
        s: {
            str(d): {"open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 100000}
            for i, d in enumerate(days)
        }
        for s in ("AMD", "SPY", "XLK")
    }
    origin = str(days[-2])
    before = feature(daily, "AMD", origin, cal, False)
    for series in daily.values():
        for day in series:
            if day >= origin:
                series[day]["close"] = 999
    assert feature(daily, "AMD", origin, cal, False) == before
    assert before["daily_available_at"][:10] < origin


def opportunity():
    t = datetime(2024, 11, 1, 14, 35, tzinfo=timezone.utc)
    times = [t + timedelta(minutes=5 * i) for i in range(20)]
    market = {"AMD": {stamp: (100, 101, 99, 100, 100000) for stamp in times}}
    r = {
        "date": "2024-10-31",
        "decision": "2024-10-31T20:30:00-04:00",
        "symbol": "AMD",
        "entry_time": times[0].isoformat(),
        "exit_time": times[12].isoformat(),
        "liquidity": 1e8,
        "regime": "LOW_VOL_MARKET_UP",
    }
    return r, market


def test_multihorizon_replay_reconciles_marks_and_no_quote_fills():
    r, market = opportunity()
    p = {"expected_return": 0.02, "probability_up": 0.7}
    strict = replay([r], [p], market, strict_quotes=True)
    assert strict["filled_entries"] == 0 and strict["quote_coverage"] == "UNAVAILABLE"
    zero = Costs(reject=0, partial=0)
    s = replay([r], [p], market, zero)
    assert s["filled_entries"] == s["closed_trades"] == 1 and s["open_positions"] == 0
    assert s["net_pnl"] == pytest.approx(s["realized_pnl"] + s["unrealized_pnl"])
    assert s["net_pnl"] == pytest.approx(sum(s["daily_net_pnl"].values()))
    assert s["net_pnl"] < 0  # flat price, genuine cost drag
    assert s["conservative_ohlc_drawdown"] >= s["marked_max_drawdown"]


def test_missing_bars_never_fill_and_capital_is_shared():
    r, market = opportunity()
    p = {"expected_return": 0.02, "probability_up": 0.7}
    assert replay([r], [p], {}, Costs(reject=0, partial=0))["filled_entries"] == 0
    rows = [r | {"symbol": s} for s in ("AMD", "MSFT", "META")]
    markets = {s: market["AMD"] for s in ("AMD", "MSFT", "META")}
    s = replay(rows, [p] * 3, markets, Costs(reject=0, partial=0))
    assert s["filled_entries"] == 2 and s["rejections"]["CAPITAL_OR_POSITION_LIMIT"] == 1


def test_compressed_evidence_and_phase7_boundary(tmp_path):
    p = tmp_path / "reports/phase7"
    p.mkdir(parents=True)
    (p / "report.json").write_text("{}")
    q = tmp_path / ".local/phase7"
    q.mkdir(parents=True)
    (q / "enrollment.json").write_text(json.dumps({"report_id": "a" * 64, "model_id": "b" * 64}))
    target = tmp_path / ".local/chart-intelligence/records"
    target.mkdir(parents=True)
    for key in ("a", "b"):
        (target / (key * 64 + ".json")).write_text("{}")
    boundary = snapshot(tmp_path)
    assert verify(boundary, tmp_path)
    (p / "report.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="Phase 7"):
        verify(boundary, tmp_path)
    key = put_compressed("test", {"x": [1, 2, 3]}, tmp_path)
    assert read_compressed(key, tmp_path) == {"x": [1, 2, 3]}


def test_partial_fill_pnl_separates_realized_and_open_inventory():
    from chart_intelligence.phase8_accounting import split_pnl

    orders = [
        {"state": "FILLED", "side": "buy", "symbol": "AMD", "quantity": 1.0, "price": 100.0, "fee": 0.1},
        {
            "state": "PARTIAL_CANCELED",
            "side": "sell",
            "symbol": "AMD",
            "quantity": 0.4,
            "price": 110.0,
            "fee": 0.04,
        },
    ]
    result = split_pnl(orders, {"AMD": 105.0})
    assert result["realized_pnl"] == pytest.approx(3.92)
    assert result["unrealized_pnl"] == pytest.approx(2.94)
    assert result["net_pnl"] == pytest.approx(6.86)
