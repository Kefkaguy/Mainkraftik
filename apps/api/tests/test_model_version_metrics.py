from datetime import UTC, datetime, timedelta

import pytest
from test_paperpilot import paperdb as _paperdb

from paperpilot.config import settings
from paperpilot.db import connect, jb
from paperpilot.outcomes import AGGREGATE_SCOPE, metrics, metrics_by_model

paperdb = _paperdb
V1 = "experimental-ridge-logistic-pilot-v1"
V3 = "experimental-hf-news-screened-ridge-logistic-v3"
V4 = "experimental-hf-news-screened-ridge-logistic-v4"


def seed_versions():
    instant = datetime(2026, 11, 13, 14, 15, tzinfo=UTC)
    with connect() as conn:
        for i, (version, symbol, expected, probability) in enumerate(
            [(V1, "AMD", 0.02, 0.9), (V3, "NVDA", -0.04, 0.1), (V4, "GOOG", 0.01, 0.6)]
        ):
            forecast = {
                "expected_return": expected,
                "probability_positive": probability,
                "lower_return": -0.1,
                "upper_return": 0.1,
            }
            conn.execute(
                "INSERT INTO pilot_decisions VALUES(%s,%s,%s,%s,%s,'HOLD',%s)",
                (
                    str(i),
                    instant.date(),
                    symbol,
                    instant,
                    instant,
                    jb({"model": version, "horizons": {"1": forecast, "5": forecast}}),
                ),
            )
            if version == V4:
                continue  # Issued model remains visible before outcomes mature.
            for h in (1, 5):
                conn.execute(
                    "INSERT INTO pilot_forecast_outcomes VALUES(%s,%s,%s,%s,%s)",
                    (f"{i}-{h}", str(i), h, instant + timedelta(days=10), jb({"return": 0.02})),
                )
        # A later append-only correction belongs to one forecast/version only.
        conn.execute(
            "INSERT INTO pilot_forecast_outcomes VALUES('new-v3-1','1',1,%s,%s)",
            (instant + timedelta(days=11), jb({"return": 0.02})),
        )


def test_model_views_do_not_pool_shared_dates_and_outcomes(paperdb):
    seed_versions()
    with connect() as conn:
        aggregate = metrics(conn)
        views = metrics_by_model(conn)
    assert len(aggregate) == 2
    assert aggregate[0]["observations"] == 2  # Latest revisions do not add samples.
    assert aggregate[0]["aggregation"] == AGGREGATE_SCOPE
    assert aggregate[0]["mae"] == pytest.approx(0.03)
    assert aggregate[0]["brier"] == pytest.approx(0.41)
    for h in (0, 1):
        assert views[V1][h]["observations"] == 1
        assert views[V1][h]["mae"] == pytest.approx(0)
        assert views[V1][h]["brier"] == pytest.approx(0.01)
        assert views[V3][h]["observations"] == 1
        assert views[V3][h]["mae"] == pytest.approx(0.06)
        assert views[V3][h]["brier"] == pytest.approx(0.81)
        assert views[V3][h]["models_included"] == [V3]
        assert views[V3][h]["aggregation"] == "single_model_version"
        assert views[V3][h]["model_version"] == V3
        assert views[V3][h]["status"] == "inconclusive"
        assert views[V4][h]["observations"] == 0
        assert views[V4][h]["model_version"] == V4


def test_exact_model_filter_and_missing_version_are_explicit(paperdb):
    seed_versions()
    with connect() as conn:
        selected = metrics(conn, V1)
        missing = metrics(conn, "absent' OR true --")
    assert selected[0]["observations"] == 1
    assert selected[0]["models_included"] == [V1]
    assert all(r["observations"] == 0 for r in missing)
    assert all(r["status"] == "inconclusive" for r in missing)


def test_empty_legacy_array_remains_available(paperdb):
    with connect() as conn:
        aggregate = metrics(conn)
        views = metrics_by_model(conn)
    assert [r["horizon"] for r in aggregate] == [1, 5]
    assert all(r["observations"] == 0 and r["aggregation"] == AGGREGATE_SCOPE for r in aggregate)
    assert views == {}


def test_status_exposes_labeled_legacy_and_separate_model_views(paperdb, monkeypatch):
    from paperpilot.service import status

    seed_versions()
    monkeypatch.setattr(settings, "paper_discovery_enabled", False)
    monkeypatch.setattr(settings, "paper_cost_gate_enabled", True)
    monkeypatch.setattr(settings, "paper_market_shadow_enabled", False)
    result = status()
    assert isinstance(result["forecast_metrics"], list)
    assert result["forecast_metrics_aggregation"] == AGGREGATE_SCOPE
    assert result["forecast_metrics_by_model"][V1][0]["observations"] == 1
    assert result["forecast_metrics_by_model"][V3][0]["observations"] == 1
    assert result["forecast_metrics_by_model"][V4][0]["observations"] == 0
    assert result["market_shadow_enabled"] is False
    assert result["risk"]["execution_cost_gate"]["enabled"] is True
    assert result["risk"]["execution_cost_gate"]["slippage_allowance"] == 0.001
    assert result["risk"]["execution_cost_gate"]["required_net_edge"] == 0.0005
