from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from intelligence.calendar import cutoff_for, future_sessions, issuance_for, issuance_window, schedule
from intelligence.forecasting import baseline, continuity, historical_samples
from intelligence.providers import ProviderError, normalize_bars
from intelligence.research import build_panel, fit_heads, paired_block_interval, predict_heads


def make_bars(start=date(2022, 1, 3), end=date(2026, 10, 30)):
    out = []
    for i, (day, session) in enumerate(schedule(start, end).items()):
        price = 100 + i * 0.04 + np.sin(i / 8) * 3
        out.append(
            {
                "id": i + 1,
                "symbol": "MSI",
                "session_date": day,
                "feed": "sip",
                "adjustment": "split",
                "adjustment_asof": end,
                "open": price,
                "high": price + 2,
                "low": price - 2,
                "close": price + np.cos(i) * 0.6,
                "volume": 10000 + i,
                "available_at": session["close"] + timedelta(minutes=15),
                "acquired_at": session["close"] + timedelta(minutes=20),
                "content_hash": str(i),
            }
        )
    return out


def test_holiday_and_early_close():
    assert future_sessions(date(2026, 11, 25), 1) == [date(2026, 11, 27)]
    assert schedule(date(2026, 11, 27), date(2026, 11, 27))[date(2026, 11, 27)]["close"].hour == 18


def test_dst_cutoff():
    assert cutoff_for(date(2026, 7, 1)).hour == 0
    assert cutoff_for(date(2026, 11, 12)).hour == 1


def test_no_early_or_retroactive_issuance():
    d = date(2026, 11, 12)
    assert issuance_window(issuance_for(d) - timedelta(seconds=1))[0] is None
    assert issuance_window(issuance_for(d))[0] == d
    next_day = future_sessions(d, 1)[0]
    assert issuance_window(schedule(next_day, next_day)[next_day]["open"])[0] is None


def test_exact_open_to_close_targets():
    rows = make_bars()[:6]
    assert historical_samples(rows, 1)[0] == pytest.approx(rows[0]["close"] / rows[0]["open"] - 1)
    assert historical_samples(rows, 5)[0] == pytest.approx(rows[4]["close"] / rows[0]["open"] - 1)


def test_baseline_reference_and_minimum():
    with pytest.raises(ValueError):
        baseline(np.ones(251))
    output = baseline(np.tile([-0.01, 0.02], 126))
    assert output["expected_return"] == pytest.approx(0.005)
    assert output["probability_positive"] == 0.5


@pytest.mark.parametrize("change", ["feed", "adjustment", "adjustment_asof"])
def test_mixed_sources_refused(change):
    rows = make_bars()[:10]
    rows[3][change] = "different"
    assert not continuity(rows)[0]


def test_missing_and_duplicate_dates_refused():
    rows = make_bars()[:10]
    assert not continuity(rows[:3] + rows[4:])[0]
    assert not continuity(rows + [rows[-1]])[0]


def test_future_changes_do_not_affect_features():
    rows = make_bars()
    original = build_panel(rows[:200], rows[:200])
    for row in rows[200:]:
        row["close"] *= 100
    full = build_panel(rows, rows)
    assert original == full[: len(original)]


def test_numpy_heads_fit_simple_relation():
    x = np.arange(500, dtype=float).reshape(-1, 1) / 500
    y = x[:, 0] - 0.5
    model = fit_heads(x, y)
    estimates, probabilities = predict_heads(model, x)
    assert np.mean(np.abs(estimates - y)) < 0.002
    assert probabilities[0] < 0.1 and probabilities[-1] > 0.9
    assert np.all((probabilities >= 0) & (probabilities <= 1))


def test_block_interval_groups_same_date_stocks():
    days = list(schedule(date(2025, 1, 1), date(2025, 12, 31)))[:150]
    one = paired_block_interval(days, [0.02] * len(days))
    two = paired_block_interval([d for d in days for _ in range(2)], [0.02] * (len(days) * 2))
    assert one == two
    assert one["distinct_dates"] == 150
    assert one["lower"] == pytest.approx(0.02)


def test_normalizer_rejects_bad_values_and_duplicates():
    raw = {"t": "2026-11-12T05:00:00Z", "o": 100, "h": 105, "l": 99, "c": 102, "v": 1000}
    acquired = datetime(2026, 11, 13, tzinfo=UTC)
    with pytest.raises(ProviderError):
        normalize_bars("MSI", [raw, raw], "sip", date(2026, 11, 12), acquired)
    with pytest.raises(ProviderError):
        normalize_bars("MSI", [{**raw, "c": 1000}], "sip", date(2026, 11, 12), acquired)
    assert len(normalize_bars("MSI", [raw], "sip", date(2026, 11, 12), acquired)) == 1
