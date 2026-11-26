"""Portable walk-forward models for Mainkraft's 36-stock research scan."""

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from threadpoolctl import threadpool_limits

MODEL_VERSION = "mainkraft-scan-linear-v1"
RETURN_FIELDS = ("net", "move_1", "move_3", "move_5")
CLASS_FIELDS = ("target_hit", "adverse_move")


def logistic(value):
    return 1 / (1 + np.exp(-np.clip(value, -40, 40)))


def _head(logits, y):
    """Platt calibration on a later, disjoint sample with smoothed fallback."""
    if len(np.unique(y)) < 2:
        prior = float((sum(y) + 1) / (len(y) + 2))
        return {"weight": 0.0, "offset": float(np.log(prior / (1 - prior)))}
    model = LogisticRegression(C=1, random_state=20261117).fit(logits[:, None], y)
    return {"weight": float(model.coef_[0][0]), "offset": float(model.intercept_[0])}


def fit_fold(train, calibration):
    """Fit only already-mature samples. The runner enforces the chronological embargo."""
    if len(train) < 200 or len(calibration) < 50:
        raise ValueError("Too few labeled rows for the shared training and calibration folds")
    raw = np.asarray([r["x"] for r in train], dtype=float)
    cal = np.asarray([r["x"] for r in calibration], dtype=float)
    if not np.isfinite(raw).all() or not np.isfinite(cal).all():
        raise ValueError("Scan features must be finite")
    mean, scale = raw.mean(axis=0), raw.std(axis=0)
    scale[scale < 1e-8] = 1
    x, cx = (raw - mean) / scale, (cal - mean) / scale
    artifact = {
        "version": MODEL_VERSION,
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "regression": {},
        "classification": {},
    }
    with threadpool_limits(limits=1):
        for key in RETURN_FIELDS:
            y = np.array([r["label"][key] for r in train])
            model = Ridge(alpha=100).fit(x, y)
            artifact["regression"][key] = {
                "weights": model.coef_.tolist(),
                "offset": float(model.intercept_),
                "baseline": float(y.mean()),
            }
        for key in CLASS_FIELDS:
            y = np.array([r["label"][key] for r in train], dtype=int)
            target = np.array([r["label"][key] for r in calibration], dtype=int)
            if len(np.unique(y)) < 2:
                logits = np.zeros(len(calibration))
                weights, offset = np.zeros(x.shape[1]).tolist(), 0.0
            else:
                model = LogisticRegression(C=0.1, max_iter=300, random_state=20261117).fit(x, y)
                logits, weights, offset = (
                    model.decision_function(cx),
                    model.coef_[0].tolist(),
                    float(model.intercept_[0]),
                )
            artifact["classification"][key] = {
                "weights": weights,
                "offset": offset,
                "calibration": _head(logits, target),
                "baseline": float((sum(y) + 1) / (len(y) + 2)),
            }
    return artifact


def score_fold(artifact, rows):
    if not rows:
        return []
    x = (np.asarray([r["x"] for r in rows], dtype=float) - artifact["mean"]) / artifact["scale"]
    predicted = {}
    for key, head in artifact["regression"].items():
        predicted[key] = x @ np.asarray(head["weights"]) + head["offset"]
    for key, head in artifact["classification"].items():
        logits = x @ np.asarray(head["weights"]) + head["offset"]
        cal = head["calibration"]
        predicted[key] = logistic(logits * cal["weight"] + cal["offset"])
    if not all(np.isfinite(values).all() for values in predicted.values()):
        raise ValueError("The frozen scan produced non-finite estimates")
    return [{key: float(values[i]) for key, values in predicted.items()} for i in range(len(rows))]


def choose_rank(forecast, news, policy):
    """Use the same candidate forecast in each arm. Missing news is neutral in ranking."""
    if forecast["net"] <= 0.0005:
        return None
    if policy == "news_filtered" and not (news and news["positive"] >= 0.50 and not news["veto"]):
        return None
    bump = 0.0015 * (news["positive"] - news["negative"]) if news and policy == "news_ranked" else 0
    return forecast["net"] + bump
