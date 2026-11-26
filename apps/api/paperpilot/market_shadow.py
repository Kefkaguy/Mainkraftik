"""Genuine prospective context challenger; its outputs never authorize orders."""

import numpy as np

from paperpilot.market_context import (
    CONTEXT_SYMBOLS,
    FEATURE_VERSION,
    MODEL_VERSION,
    fit_candidate,
    labeled_rows,
    panel,
    predict_candidate,
)


def issue(bars, spy, contexts, cutoff):
    from datetime import date

    def hydrate(rows):
        return [
            {
                **r,
                "session_date": date.fromisoformat(r["session_date"])
                if isinstance(r["session_date"], str)
                else r["session_date"],
                "adjustment_asof": date.fromisoformat(r["adjustment_asof"])
                if isinstance(r["adjustment_asof"], str)
                else r["adjustment_asof"],
            }
            for r in rows
        ]

    if set(contexts) != set(CONTEXT_SYMBOLS):
        raise ValueError("Incomplete context history")
    if any(s["acquired_at"] > cutoff for s in contexts.values()):
        raise ValueError("Context acquired after cutoff")
    context_bars = {k: hydrate(v["payload"]["bars"]) for k, v in contexts.items()}
    if any(v[-1]["session_date"] != bars[-1]["session_date"] for v in context_bars.values()):
        raise ValueError("Context lacks latest completed session")
    features = panel(bars, spy, context_bars)
    result = {
        "model": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "validated": False,
        "execution_authority": "none",
        "status": "issued",
        "source_ids": [s["id"] for s in contexts.values()],
        "horizons": {},
    }
    for horizon in (1, 5):
        rows = labeled_rows(features, bars, horizon)
        if len(rows) < 504:
            raise ValueError("Insufficient mature context history")
        a = int(len(rows) * 0.8)
        train = [r for r in rows[: a - 5] if r["end"] < rows[a]["date"]]
        artifact = fit_candidate(train, rows[a:], horizon)
        mean, p, lo, hi = predict_candidate(artifact, [features[-1]])
        if not all(np.isfinite(v[0]) for v in (mean, p, lo, hi)):
            raise ValueError("Non-finite shadow output")
        result["horizons"][str(horizon)] = {
            "expected_return": float(mean[0]),
            "probability_positive": float(p[0]),
            "lower_return": float(lo[0]),
            "upper_return": float(hi[0]),
            "artifact": artifact,
        }
    return result
