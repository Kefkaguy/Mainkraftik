from datetime import timedelta

import pytest
from test_chart_intelligence import candles, synthetic_rows

from chart_intelligence import store
from chart_intelligence.controller import connection, drift, status, tick
from chart_intelligence.core import evaluate


def test_controller_issues_once_resolves_and_survives_restart(tmp_path):
    model, report = evaluate(synthetic_rows())
    model_id = store.put("model", model, tmp_path)
    report["model_id"] = model_id
    key = store.put("evaluation", report, tmp_path)
    bars, decision = candles()
    decision = decision - timedelta(minutes=30)  # 12:00 ET

    def collector(symbol, instant):
        return {
            "symbol": symbol,
            "bars": [
                [t.isoformat(), *v.tolist()] for t, v in bars.items() if t + timedelta(minutes=20) <= instant
            ],
            "acquired_at": instant.isoformat(),
            "feed": "sip",
            "adjustment": "split",
        }

    issued = tick(key, ["AMD"], collector, decision + timedelta(seconds=1), tmp_path)
    assert issued["issued"] == 1 and not issued["execution_authority"]
    assert tick(key, ["AMD"], collector, decision + timedelta(seconds=2), tmp_path)["issued"] == 0
    resolved = tick(key, ["AMD"], collector, decision + timedelta(hours=1), tmp_path)
    assert resolved["outcomes"] == 1 and resolved["monitoring"]["status"] == "INSUFFICIENT_EVIDENCE"
    assert tick(key, ["AMD"], collector, decision + timedelta(hours=1), tmp_path)["outcomes"] == 1
    db = connection(tmp_path)
    assert db.execute("SELECT count(*) FROM evidence WHERE kind='prediction'").fetchone()[0] == 1
    with pytest.raises(Exception, match="append-only"):
        db.execute("DELETE FROM evidence")
    db.close()
    assert len(status(tmp_path)["prediction"]) == 1


def test_drift_requires_dates_and_records_declining_accuracy():
    forecast = {"expected_return": 0.5, "probability_up": 0.99, "lower_return": 0.4, "upper_return": 0.6}
    rows = [{"date": str(i), "y": -0.01, "forecast": forecast} for i in range(40)]
    reference = {"mae": 0.01, "brier": 0.25}
    assert drift(rows[:20], reference)["status"] == "INSUFFICIENT_EVIDENCE"
    result = drift(rows, reference)
    assert result["status"] == "ALERT" and not result["promotion_authority"]
    assert len(result["reasons"]) == 3


def test_missing_data_records_failure_without_fake_prediction(tmp_path):
    model, report = evaluate(synthetic_rows())
    report["model_id"] = store.put("model", model, tmp_path)
    key = store.put("evaluation", report, tmp_path)
    _, decision = candles()

    def unavailable(*args):
        raise ValueError("Fixture missing source")

    result = tick(key, ["AMD"], unavailable, decision, tmp_path)
    assert result["issued"] == 0 and result["errors"][0]["status"] == "DATA_OR_MODEL_UNAVAILABLE"


def test_drift_runs_bounded_offline_refit_without_promoting(tmp_path):
    rows = synthetic_rows()
    model, report = evaluate(rows)
    report["model_id"] = store.put("model", model, tmp_path)
    key = store.put("evaluation", report, tmp_path)
    store.publish(key, tmp_path)
    db = connection(tmp_path)
    from chart_intelligence.controller import append

    for i, r in enumerate(rows):
        append(
            db,
            str(i),
            "outcome",
            {
                **r,
                "report_id": key,
                "features": {"x": r["x"], "numeric": r["numeric"]},
                "forecast": {
                    "expected_return": 0.5,
                    "probability_up": 0.99,
                    "lower_return": 0.4,
                    "upper_return": 0.6,
                },
            },
        )
    db.commit()
    db.close()
    _, decision = candles()

    def unavailable(*args):
        raise ValueError("No fresh source")

    tick(key, ["AMD"], unavailable, decision, tmp_path)
    tick(key, ["AMD"], unavailable, decision, tmp_path)
    db = connection(tmp_path)
    assert db.execute("SELECT count(*) FROM evidence WHERE kind='offline_evaluation'").fetchone()[0] == 1
    db.close()
    assert store.latest(tmp_path)["record_id"] == key
