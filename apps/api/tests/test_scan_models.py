"""Research model and policy invariants for the walk-forward comparison."""

import numpy as np
import pytest

from paperpilot.scan_models import CLASS_FIELDS, RETURN_FIELDS, choose_rank, fit_fold, score_fold


def test_news_can_change_rank_without_changing_technical_eligibility():
    forecast = {"net": 0.004}
    negative = {"positive": 0.05, "negative": 0.9, "veto": True}
    assert choose_rank(forecast, negative, "technical_only") == 0.004
    assert choose_rank(forecast, negative, "news_filtered") is None
    assert 0 < choose_rank(forecast, negative, "news_ranked") < 0.004
    assert choose_rank(forecast, None, "news_ranked") == 0.004
    assert choose_rank({"net": 0.0004}, {"positive": 0.99, "negative": 0}, "news_ranked") is None


def test_monthly_artifact_scores_the_six_requested_outcomes():
    rng = np.random.default_rng(3)

    def rows(number):
        for _ in range(number):
            features = rng.normal(size=11).tolist()
            y = 0.002 * features[0]
            yield {
                "x": features,
                "label": {
                    **{key: y for key in RETURN_FIELDS},
                    "target_hit": features[0] > 0.4,
                    "adverse_move": features[1] < -0.4,
                },
            }

    train, calibration, later = list(rows(260)), list(rows(70)), list(rows(15))
    fold = fit_fold(train, calibration)
    scores = score_fold(fold, [{"x": row["x"]} for row in later])
    assert len(scores) == 15
    assert all(set(row) == set(RETURN_FIELDS + CLASS_FIELDS) for row in scores)
    assert all(0 <= row[name] <= 1 for row in scores for name in CLASS_FIELDS)
    assert scores[0]["net"] == pytest.approx(scores[0]["move_5"])
    assert score_fold(fold, []) == []
