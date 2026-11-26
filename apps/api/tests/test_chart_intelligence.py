from datetime import date, timedelta

import numpy as np
import pytest

from chart_intelligence import store
from chart_intelligence.core import Protocol, evaluate, identity, infer, label, partitions, sequence
from intelligence.calendar import schedule


def candles():
    day = date(2026, 11, 16)
    start = schedule(day, day)[day]["open"]
    return {
        start + timedelta(minutes=5 * i): np.array([100.0, 101.0, 99.0, 100.0, 1000.0]) for i in range(78)
    }, start + timedelta(hours=3)


def test_features_are_causal_across_all_timeframes():
    bars, decision = candles()
    before = sequence(bars, decision)
    assert len(before["x"]) == 110
    for t in bars:
        if t + timedelta(minutes=20) > decision:
            bars[t] = np.array([200, 300, 1, 250, 999999])
    assert before == sequence(bars, decision)
    assert before["latest_feature_available_at"] <= decision.isoformat()
    del bars[min(bars)]
    with pytest.raises(ValueError, match="Missing"):
        sequence(bars, decision)


def test_path_labels_handle_ambiguity_and_unresolved_horizon():
    bars, decision = candles()
    bars[decision] = np.array([100, 104, 97, 101, 1000])
    outcome = label(bars, decision)
    assert outcome["ambiguous"] and outcome["target_first"] == 0
    assert outcome["downside"] == pytest.approx(-0.03)
    del bars[decision + timedelta(minutes=5)]
    with pytest.raises(ValueError, match="incomplete"):
        label(bars, decision)


def synthetic_rows():
    rng = np.random.default_rng(42)
    rows = []
    for d in range(220):
        for s in ("A", "B"):
            x = rng.normal(size=110)
            numeric = rng.normal(0, 0.01, 11)
            numeric[4] = 0.01
            y = 0.003 * x[0] + rng.normal(0, 0.01)
            day = str(date(2020, 1, 1) + timedelta(days=d))
            rows.append(
                {
                    "date": day,
                    "decision": day + "T12:00:00+00:00",
                    "label_end": day + "T12:30:00+00:00",
                    "symbol": s,
                    "x": x.tolist(),
                    "numeric": numeric.tolist(),
                    "y": y,
                    "downside": -abs(y) - 0.01,
                    "target_first": int(x[1] > 0),
                }
            )
    return rows


def test_chronology_export_and_unseen_evaluation():
    rows = synthetic_rows()
    train, cal, test = partitions(rows, Protocol())
    assert (
        max(r["date"] for r in train)
        < min(r["date"] for r in cal)
        < max(r["date"] for r in cal)
        < min(r["date"] for r in test)
    )
    model, report = evaluate(rows)
    assert report["execution_authority"] is False
    assert len(report["models"]) == 4
    predicted = infer(model, [test[0]])[0]
    assert predicted["probability_down"] + predicted["probability_up"] == pytest.approx(1)
    assert predicted["lower_return"] <= predicted["expected_return"] <= predicted["upper_return"]
    changed = [dict(r) for r in rows]
    for r in changed:
        if r["date"] >= test[0]["date"]:
            r["y"] += 1
    model2, _ = evaluate(changed)
    assert identity(model2) == identity(model), "Evaluation labels leaked into fit/calibration"


def test_store_is_immutable_and_verifies_hashes(tmp_path):
    key = store.put("test", {"x": 1}, tmp_path)
    assert store.put("test", {"x": 1}, tmp_path) == key
    store.publish(key, tmp_path)
    assert store.latest(tmp_path)["x"] == 1
    (tmp_path / "records" / (key + ".json")).write_text("{}")
    with pytest.raises(ValueError, match="integrity"):
        store.read(key, tmp_path)
