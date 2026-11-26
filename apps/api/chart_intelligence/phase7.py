"""Predeclared bounded development research. The old evaluation set is forbidden.

No execution imports, pickle artifacts, model promotion, or policy changes.
"""

import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.neural_network import MLPRegressor
from threadpoolctl import threadpool_limits

from chart_intelligence.core import forward, network
from intelligence.portable_models import fit as numeric_fit
from intelligence.portable_models import predict as numeric_predict

VERSION = "phase7-bounded-v1"
DEVELOPMENT_END = "2025-04-02"
AUDIT_END = "2026-01-23"
FORBIDDEN_START = "2026-01-26"
CANDIDATES = (
    "historical_mean",
    "zero_return",
    "mainkraft_linear",
    "mainkraft_histboost",
    "sequence_ridge",
    "sequence_original150",
    "sequence_adam400",
    "sequence_lbfgs250",
)


def split_development(rows):
    if any(r["date"] > AUDIT_END for r in rows):
        raise ValueError("Exposed evaluation or later data refused by Phase 7 development")
    days = sorted({r["date"] for r in rows if r["date"] <= DEVELOPMENT_END})
    if len(days) < 200:
        raise ValueError("At least 200 development dates required")
    a, b = int(len(days) * 0.6), int(len(days) * 0.8)
    groups = [[r for r in rows if r["date"] in set(d)] for d in (days[: a - 1], days[a : b - 1], days[b:])]
    audit = [r for r in rows if DEVELOPMENT_END < r["date"] <= AUDIT_END]
    return *groups, audit


def linear_head(x, labels):
    if len(set(labels)) < 2:
        p = float(np.clip(np.mean(labels), 1e-6, 1 - 1e-6))
        return {"coef": [0.0] * x.shape[1], "intercept": float(np.log(p / (1 - p)))}
    cls = LogisticRegression(C=0.1, max_iter=400, random_state=20261118).fit(x, labels)
    return {"coef": cls.coef_[0].tolist(), "intercept": float(cls.intercept_[0])}


def sigmoid(value):
    return 1 / (1 + np.exp(-np.clip(value, -35, 35)))


def train(name, rows, calibration):
    if name not in CANDIDATES:
        raise ValueError("Undeclared model")
    if max(r["date"] for r in rows) >= min(r["date"] for r in calibration):
        raise ValueError("Training/calibration chronology overlaps")
    if any(r["date"] > DEVELOPMENT_END for r in [*rows, *calibration]):
        raise ValueError("Fit outside declared development")
    x = np.asarray([r["x"] for r in rows])
    y = np.asarray([[r["y"], r["downside"]] for r in rows])
    mean = x.mean(0)
    scale = x.std(0)
    scale[scale < 1e-8] = 1
    z = np.clip((x - mean) / scale, -8, 8)
    artifact = {
        "version": VERSION,
        "name": name,
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "base_return": float(y[:, 0].mean()),
        "base_downside": float(y[:, 1].mean()),
        "base_up": float(np.mean(y[:, 0] > 0)),
        "base_target": float(np.mean([r["target_first"] for r in rows])),
        "radius": 0.0,
        "calibration": [],
        "optimization": {},
    }
    with threadpool_limits(limits=1), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        if name.startswith("mainkraft_"):
            groups = [[r | {"x": r["numeric"]} for r in group] for group in (rows, calibration)]
            family = "linear" if name.endswith("linear") else "histboost"
            params = (
                {"alpha": 100, "C": 0.1}
                if family == "linear"
                else {"iterations": 100, "leaves": 7, "learning_rate": 0.05, "l2": 10, "minimum_leaf": 100}
            )
            artifact["numeric"] = numeric_fit(groups[0], groups[1], list(range(11)), family, params, 1)
        elif name == "sequence_ridge":
            reg = Ridge(alpha=1000).fit(z, y)
            artifact["ridge"] = {"coef": reg.coef_.tolist(), "intercept": reg.intercept_.tolist()}
        elif name.startswith("sequence_"):
            opts = {
                "hidden_layer_sizes": (24, 12),
                "max_iter": 150,
                "solver": "adam",
                "alpha": 0.1,
                "learning_rate_init": 0.001,
            }
            if name == "sequence_adam400":
                opts.update(
                    hidden_layer_sizes=(16,),
                    max_iter=400,
                    alpha=10,
                    learning_rate_init=0.0003,
                    batch_size=128,
                )
            elif name == "sequence_lbfgs250":
                opts.update(hidden_layer_sizes=(8,), max_iter=250, solver="lbfgs", alpha=10, max_fun=15000)
            ym = y.mean(0)
            ys = np.maximum(y.std(0), 0.001)
            if name == "sequence_original150":
                ym = np.zeros(2)
                ys = np.full(2, max(float(y.std()), 0.001))
            reg = MLPRegressor(random_state=20261118, early_stopping=False, **opts).fit(z, (y - ym) / ys)
            artifact.update(regressor=network(reg), target_mean=ym.tolist(), target_scale=ys.tolist())
            curve = list(getattr(reg, "loss_curve_", []))
            artifact["optimization"] = {
                "iterations": int(reg.n_iter_),
                "budget": opts["max_iter"],
                "budget_exhausted": bool(reg.n_iter_ >= opts["max_iter"]),
                "loss_tail": curve[-10:],
                "training_loss": float(reg.loss_),
            }
        artifact["optimization"]["warnings"] = [str(w.message) for w in caught]
        heads = []
        for labels in (y[:, 0] > 0, np.asarray([r["target_first"] for r in rows])):
            heads.append(linear_head(z, labels))
        artifact["heads"] = heads
        raw = raw_predict(artifact, calibration)
        for i, labels in enumerate(
            ([r["y"] > 0 for r in calibration], [r["target_first"] for r in calibration])
        ):
            logits = np.log(
                np.clip(raw[:, 2 + i], 1e-6, 1 - 1e-6) / np.clip(1 - raw[:, 2 + i], 1e-6, 1 - 1e-6)
            )
            artifact["calibration"].append(linear_head(logits.reshape(-1, 1), labels))
        residual = np.asarray([r["y"] for r in calibration]) - raw[:, 0]
        artifact["radius"] = float(np.quantile(abs(residual), 0.9, method="higher"))
        fitted = raw_predict(artifact, rows)
        artifact["optimization"]["train_mae"] = float(np.mean(abs(fitted[:, 0] - y[:, 0])))
        artifact["optimization"]["calibration_mae"] = float(np.mean(abs(residual)))
    return artifact


def raw_predict(artifact, rows):
    x = np.asarray([r["x"] for r in rows], dtype=float)
    if x.ndim != 2 or x.shape[1] != len(artifact["mean"]) or not np.isfinite(x).all():
        raise ValueError("Invalid sequence inputs")
    z = np.clip((x - artifact["mean"]) / artifact["scale"], -8, 8)
    n = len(rows)
    mean = np.full(n, artifact["base_return"])
    downside = np.full(n, artifact["base_downside"])
    prob = np.full(n, artifact["base_up"])
    target = np.full(n, artifact["base_target"])
    if artifact["name"] == "zero_return":
        mean[:] = 0
        prob[:] = 0.5
    if "numeric" in artifact:
        mean, prob, _, _ = numeric_predict(artifact["numeric"], [r | {"x": r["numeric"]} for r in rows])
    if "ridge" in artifact:
        values = z @ np.asarray(artifact["ridge"]["coef"]).T + artifact["ridge"]["intercept"]
        mean, downside = values.T
    if "regressor" in artifact:
        values = forward(artifact["regressor"], z) * artifact["target_scale"] + artifact["target_mean"]
        mean, downside = values.T
    if artifact["name"].startswith("sequence_"):
        prob, target = [sigmoid(z @ np.asarray(h["coef"]) + h["intercept"]) for h in artifact["heads"]]
    return np.column_stack([mean, np.minimum(0, downside), prob, target])


def predict(artifact, rows):
    raw = raw_predict(artifact, rows)
    for i, c in enumerate(artifact["calibration"]):
        p = np.clip(raw[:, 2 + i], 1e-6, 1 - 1e-6)
        raw[:, 2 + i] = sigmoid(np.log(p / (1 - p)) * c["coef"][0] + c["intercept"])
    return [
        {
            "expected_return": float(m),
            "expected_downside": float(d),
            "probability_up": float(p),
            "probability_down": float(1 - p),
            "probability_target_before_stop": float(t),
            "lower_return": float(m - artifact["radius"]),
            "upper_return": float(m + artifact["radius"]),
        }
        for m, d, p, t in raw
    ]


def forecast_metrics(rows, predictions):
    from chart_intelligence.core import metrics

    result = metrics(rows, predictions)
    probs = np.asarray([p["probability_up"] for p in predictions])
    actual = np.asarray([r["y"] > 0 for r in rows])
    result["direction_accuracy"] = float(np.mean((probs >= 0.5) == actual))
    result["calibration_bins"] = [
        {
            "count": int(mask.sum()),
            "forecast": float(probs[mask].mean()) if mask.any() else None,
            "actual": float(actual[mask].mean()) if mask.any() else None,
        }
        for lo in np.arange(0, 1, 0.2)
        for mask in [(probs >= lo) & (probs < lo + 0.2)]
    ]
    return result
