from datetime import UTC, datetime, timedelta

import pytest

from paperpilot.reporting import closed_cycles, exposure_integral, quality


def test_exposure_excludes_missing_time_and_cash_flow_breaks():
    origin = datetime(2026, 11, 13, 15, tzinfo=UTC)
    rows = [
        {
            "observed_at": origin + timedelta(seconds=t),
            "payload": {
                "clock": {"is_open": True},
                "cash_consistent": True,
                "positions": [{"market_value": v}],
            },
        }
        for t, v in [(0, 100), (15, 200), (300, 300)]
    ]
    result = exposure_integral(rows)
    assert result["observed_seconds"] == 15
    assert result["average_deployed_capital"] == pytest.approx(100)
    rows[0]["payload"]["cash_consistent"] = False
    assert exposure_integral(rows)["average_deployed_capital"] is None


def test_round_trips_include_partial_exits_and_break_even():
    rows = [
        {"symbol": "AMD", "side": "buy", "qty": 2, "realized_pnl": None, "time": "t0"},
        {"symbol": "AMD", "side": "sell", "qty": 1, "realized_pnl": 3, "time": "t1"},
        {"symbol": "AMD", "side": "sell", "qty": 1, "realized_pnl": -1, "time": "t2"},
    ]
    assert closed_cycles(rows) == [{"symbol": "AMD", "closed_at": "t2", "realized_pnl": 2}]


def test_calibration_and_empty_evidence_are_explicit():
    assert quality([])[0]["metrics"] is None
    row = {
        "horizon": 1,
        "session_date": "2026-11-13",
        "decision": {
            "horizons": {
                "1": {
                    "expected_return": 0.02,
                    "probability_positive": 0.8,
                    "lower_return": -0.1,
                    "upper_return": 0.1,
                }
            }
        },
        "outcome": {"return": 0.03},
    }
    report = quality([row])[0]
    assert report["status"] == "inconclusive"
    assert report["metrics"]["brier"] == pytest.approx(0.04)
    assert report["metrics"]["interval_coverage"] == 1
    assert report["metrics"]["ece"] == pytest.approx(0.2)


def test_uncertainty_requires_126_dates_and_never_promotes_a_model():
    start = datetime(2026, 1, 1, tzinfo=UTC).date()
    rows = [
        {
            "symbol": "AMD",
            "horizon": 1,
            "session_date": start + timedelta(days=i),
            "decision": {
                "horizons": {
                    "1": {
                        "expected_return": 0.02,
                        "probability_positive": 0.8,
                        "lower_return": -0.1,
                        "upper_return": 0.1,
                    }
                }
            },
            "outcome": {"return": 0.03},
        }
        for i in range(126)
    ]
    assert quality(rows[:125])[0]["uncertainty"] is None
    report = quality(rows)[0]
    assert report["status"] == "inconclusive"
    assert report["uncertainty"]["samples"] == 2000 and report["uncertainty"]["confidence"] == 0.9875
    assert report["uncertainty"]["mae_interval"] == pytest.approx([0.01, 0.01])
