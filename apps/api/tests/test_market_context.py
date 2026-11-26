from copy import deepcopy

import numpy as np
import pytest
from test_calendar_and_predictions import make_bars

from intelligence.calendar import schedule
from intelligence.experiments import enriched_panel
from paperpilot import market_context as mc


@pytest.fixture
def streams():
    bars = make_bars()[:700]
    contexts = {}
    for j, name in enumerate(mc.CONTEXT_SYMBOLS):
        stream = deepcopy(bars)
        for i, r in enumerate(stream):
            r["symbol"] = name
            r["close"] *= 1 + (j + 1) * 0.0001 * i
        contexts[name] = stream
    return bars, deepcopy(bars), contexts


def test_context_changes_cannot_change_earlier_features(streams):
    bars, spy, contexts = streams
    original = mc.panel(bars[:300], spy[:300], {k: v[:300] for k, v in contexts.items()})
    future = deepcopy(contexts)
    for stream in future.values():
        for row in stream[300:]:
            row["close"] *= 100
    extended = mc.panel(bars, spy, future)
    assert original == extended[: len(original)]
    base = enriched_panel(bars, spy)
    assert all(a["x"][:14] == b["x"] for a, b in zip(extended, base))
    assert len(extended[0]["x"]) == 20


def test_features_match_same_session_returns(streams):
    bars, spy, contexts = streams
    row = mc.panel(bars, spy, contexts)[100]
    i = row["index"]
    qqq = contexts["QQQ"]
    expected = qqq[i]["close"] / qqq[i - 5]["close"] - spy[i]["close"] / spy[i - 5]["close"]
    assert row["x"][14] == pytest.approx(expected)
    tlt = contexts["TLT"]
    assert row["x"][19] == pytest.approx(tlt[i]["close"] / tlt[i - 20]["close"] - 1)


@pytest.mark.parametrize("corruption", ["feed", "asof", "missing", "truncated_tail"])
def test_policy_mixing_or_missing_context_is_not_filled(streams, corruption):
    bars, spy, contexts = streams
    if corruption == "feed":
        for row in contexts["QQQ"]:
            row["feed"] = "iex"
    elif corruption == "asof":
        contexts["IWM"][0]["adjustment_asof"] = bars[-1]["session_date"]
    elif corruption == "missing":
        contexts["TLT"].pop(100)
    else:
        contexts["QQQ"] = contexts["QQQ"][:-1]
    with pytest.raises(ValueError):
        mc.panel(bars, spy, contexts)


def test_common_start_trim_preserves_original_and_aligned_label_indexes(streams):
    bars, spy, contexts = streams
    contexts["QQQ"] = contexts["QQQ"][1:]
    original = mc.panel(bars, spy, contexts)
    trimmed_bars, trimmed_spy, trimmed_contexts = mc.align_inputs(bars, spy, contexts)
    trimmed = mc.panel(trimmed_bars, trimmed_spy, trimmed_contexts)
    assert original[0]["index"] == 61 and trimmed[0]["index"] == 60
    assert original[0]["date"] == trimmed[0]["date"]
    assert original[0]["x"] == trimmed[0]["x"]
    original_labels = mc.labeled_rows(original, bars, 5)
    trimmed_labels = mc.labeled_rows(trimmed, trimmed_bars, 5)
    assert [r["y"] for r in original_labels] == [r["y"] for r in trimmed_labels]
    assert [r["end"] for r in original_labels] == [r["end"] for r in trimmed_labels]


def test_shared_partitions_purge_exchange_sessions_and_contain_labels(streams):
    bars, spy, contexts = streams
    panel = mc.panel(bars, spy, contexts)
    boundaries = mc.shared_boundaries([r["date"] for r in panel])
    days = list(schedule(panel[0]["date"], panel[-1]["date"]))
    assert days.index(boundaries["calibration_start"]) - days.index(boundaries["train_end_exclusive"]) == 5
    assert (
        days.index(boundaries["evaluation_start"]) - days.index(boundaries["calibration_end_exclusive"]) == 5
    )
    for h in (1, 5):
        groups = mc.split_rows(mc.labeled_rows(panel, bars, h), boundaries)
        assert max(r["end"] for r in groups["train"]) < boundaries["calibration_start"]
        assert max(r["end"] for r in groups["calibration"]) < boundaries["evaluation_start"]
        assert min(r["date"] for r in groups["evaluation"]) >= boundaries["evaluation_start"]
        assert max(r["end"] for r in groups["evaluation"]) <= boundaries["evaluation_end_inclusive"]
        assert not any(
            boundaries["train_end_exclusive"] <= r["date"] < boundaries["calibration_start"]
            for r in groups["train"]
        )


def test_candidate_fit_cannot_read_evaluation_labels(streams):
    bars, spy, contexts = streams
    panel = mc.panel(bars, spy, contexts)
    rows = mc.labeled_rows(panel, bars, 1)
    groups = mc.split_rows(rows, mc.shared_boundaries([r["date"] for r in panel]))
    model = mc.fit_candidate(groups["train"], groups["calibration"], 1)
    assert model["execution_authority"] == "none" and model["validated"] is False
    first = mc.predict_candidate(model, groups["evaluation"])
    changed = deepcopy(groups["evaluation"])
    for row in changed:
        row["y"] = 999
    second = mc.predict_candidate(model, changed)
    for left, right in zip(first, second):
        np.testing.assert_array_equal(left, right)
    reference = mc.fit_candidate(groups["train"], groups["calibration"], 1, with_context=False)
    assert reference["columns"] == list(range(14))


def test_labels_do_not_use_future_open_as_features(streams):
    bars, spy, contexts = streams
    first = mc.panel(bars[:300], spy[:300], {k: v[:300] for k, v in contexts.items()})
    changed = deepcopy(bars)
    changed[300]["open"] *= 2
    second = mc.panel(changed, spy, contexts)
    assert first == second[: len(first)]
    labels = mc.labeled_rows(second, changed, 1)
    assert labels[0]["y"] == pytest.approx(changed[61]["close"] / changed[61]["open"] - 1)
