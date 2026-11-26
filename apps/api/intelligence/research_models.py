"""Bounded tabular candidates. All fitting precedes the scored test block."""

import numpy as np
from threadpoolctl import threadpool_limits

from intelligence.research import fit_heads, predict_heads, scores

SEED = 20261112
FEATURES = [
    "return_1",
    "return_5",
    "return_20",
    "return_60",
    "volatility_20",
    "atr_ratio_14",
    "relative_volume_20",
    "spy_return_5",
    "spy_volatility_20",
    "excess_return_20",
    "overnight_gap",
    "gap_mean_20",
    "volatility_ratio",
    "spy_return_60",
]
GROUPS = {
    "price": [0, 1, 2, 3, 6, 10, 11],
    "volatility": [0, 1, 2, 3, 6, 10, 11, 4, 5, 12],
    "market": list(range(14)),
    "original": list(range(10)),
}
CANDIDATES = {
    "mean-v2": {"family": "mean", "group": "price", "calibrate": False},
    "volatility-v2": {"family": "volatility", "group": "volatility", "calibrate": False, "shrinkage": 60},
    "original-linear-v2": {"family": "linear", "group": "original", "calibrate": False, "alpha": 1},
    "linear-price-v2": {"family": "linear", "group": "price", "calibrate": True, "alpha": 1},
    "linear-volatility-v2": {"family": "linear", "group": "volatility", "calibrate": True, "alpha": 1},
    "linear-market-v2": {"family": "linear", "group": "market", "calibrate": True, "alpha": 1},
    "histboost-market-v2": {
        "family": "histboost",
        "group": "market",
        "calibrate": True,
        "iterations": 64,
        "leaves": 7,
        "minimum_leaf": 30,
        "learning_rate": 0.05,
    },
    "forest-market-v2": {
        "family": "forest",
        "group": "market",
        "calibrate": True,
        "trees": 64,
        "depth": 3,
        "minimum_leaf": 30,
    },
    "ensemble-market-v2": {
        "family": "ensemble",
        "group": "market",
        "calibrate": True,
        "members": ["linear-market-v2", "histboost-market-v2"],
        "weights": [0.5, 0.5],
    },
}


def metrics(y, expected, probability, lower=None, upper=None):
    y, expected, probability = map(lambda a: np.asarray(a, dtype=float), (y, expected, probability))
    if not len(y) or any(a.shape != y.shape for a in (expected, probability)):
        raise ValueError("Metrics require equal nonempty arrays")
    if not all(np.isfinite(a).all() for a in (y, expected, probability)):
        raise ValueError("Nonfinite forecast or label")
    if np.any((probability < 0) | (probability > 1)):
        raise ValueError("Probability outside [0,1]")
    result = scores(y, expected, probability)
    result.update(
        {
            "rmse": float(np.sqrt(np.mean((expected - y) ** 2))),
            "bias": float(np.mean(expected - y)),
            "directional_accuracy": float(np.mean((probability >= 0.5) == (y > 0))),
            "positive_rate": float(np.mean(y > 0)),
            "probability_std": float(probability.std()),
            "probability_min": float(probability.min()),
            "probability_max": float(probability.max()),
            "expected_return_std": float(expected.std()),
            "ece": float(
                sum(b["count"] * abs(b["observed"] - b["predicted"]) for b in result["calibration"]) / len(y)
            ),
        }
    )
    if lower is not None:
        lower, upper = np.asarray(lower), np.asarray(upper)
        if lower.shape != y.shape or upper.shape != y.shape or np.any(lower > upper):
            raise ValueError("Invalid interval")
        result.update(
            {
                "interval_coverage": float(np.mean((y >= lower) & (y <= upper))),
                "interval_width": float(np.mean(upper - lower)),
                "interval_score_80": float(
                    np.mean(upper - lower + 10 * np.maximum(lower - y, 0) + 10 * np.maximum(y - upper, 0))
                ),
            }
        )
    return result


def _arrays(rows):
    return np.asarray([r["x"] for r in rows], dtype=float), np.asarray([r["y"] for r in rows], dtype=float)


def fit_predict(config, train, calibration, test, horizon):
    """Return test outputs plus an inspectable artifact, never serialized pickle."""
    from sklearn import __version__ as sklearn_version
    from sklearn.ensemble import (
        HistGradientBoostingClassifier,
        HistGradientBoostingRegressor,
        RandomForestClassifier,
        RandomForestRegressor,
    )
    from sklearn.linear_model import LogisticRegression

    tx, ty = _arrays(train)
    cx, cy = _arrays(calibration)
    vx, _ = _arrays(test)
    if min(len(train), len(calibration), len(test)) < 20:
        raise ValueError("At least twenty observations per partition are required")
    if not all(np.isfinite(a).all() for a in (tx, ty, cx, cy, vx)):
        raise ValueError("Nonfinite model input")
    if len(np.unique(ty > 0)) < 2:
        raise ValueError("Training direction requires both classes")
    columns = GROUPS[config["group"]]
    x, cal, val = tx[:, columns], cx[:, columns], vx[:, columns]
    reference = {"expected_return": float(ty.mean()), "probability_positive": float(np.mean(ty > 0))}
    artifact = {
        "config": config,
        "features": [FEATURES[i] for i in columns],
        "seed": SEED,
        "sklearn": sklearn_version,
        "reference": reference,
        "warnings": [],
    }
    family = config["family"]
    with threadpool_limits(limits=1):
        if family == "mean":
            mean = np.full(len(test), ty.mean())
            p = np.full(len(test), np.mean(ty > 0))
            cal_mean = np.full(len(cal), ty.mean())
            cal_p = np.full(len(cal), np.mean(ty > 0))
            train_mean, train_p = np.full(len(train), ty.mean()), np.full(len(train), np.mean(ty > 0))
        elif family == "volatility":
            edges = np.quantile(tx[:, 4], [1 / 3, 2 / 3])
            buckets = np.digitize(tx[:, 4], edges)
            parameters = []
            for bucket in range(3):
                values = ty[buckets == bucket]
                weight = len(values) / (len(values) + config["shrinkage"])
                parameters.append(
                    [
                        float(weight * values.mean() + (1 - weight) * ty.mean())
                        if len(values)
                        else float(ty.mean()),
                        float(weight * np.mean(values > 0) + (1 - weight) * np.mean(ty > 0))
                        if len(values)
                        else float(np.mean(ty > 0)),
                    ]
                )

            def bucket_predict(raw):
                result = np.asarray(parameters)[np.digitize(raw[:, 4], edges)]
                return result[:, 0], result[:, 1]

            mean, p = bucket_predict(vx)
            cal_mean, cal_p = bucket_predict(cx)
            train_mean, train_p = bucket_predict(tx)
            artifact.update({"edges": edges.tolist(), "buckets": parameters})
        elif family == "linear":
            model = fit_heads(x, ty)
            mean, p = predict_heads(model, val)
            cal_mean, cal_p = predict_heads(model, cal)
            train_mean, train_p = predict_heads(model, x)
            artifact["parameters"] = {k: v.tolist() for k, v in model.items()}
        elif family in {"histboost", "forest"}:
            if family == "histboost":
                options = dict(
                    max_iter=config["iterations"],
                    max_leaf_nodes=config["leaves"],
                    min_samples_leaf=config["minimum_leaf"],
                    learning_rate=config["learning_rate"],
                    l2_regularization=10,
                    early_stopping=False,
                    random_state=SEED,
                )
                reg, cls = HistGradientBoostingRegressor(**options), HistGradientBoostingClassifier(**options)
            else:
                options = dict(
                    n_estimators=config["trees"],
                    max_depth=config["depth"],
                    min_samples_leaf=config["minimum_leaf"],
                    n_jobs=1,
                    random_state=SEED,
                )
                reg, cls = RandomForestRegressor(**options), RandomForestClassifier(**options)
            reg.fit(x, ty)
            cls.fit(x, ty > 0)
            mean, p = reg.predict(val), cls.predict_proba(val)[:, 1]
            cal_mean, cal_p = reg.predict(cal), cls.predict_proba(cal)[:, 1]
            train_mean, train_p = reg.predict(x), cls.predict_proba(x)[:, 1]
            artifact["reproduction"] = (
                "Refit deterministic estimator from frozen dataset/config/code; no unsafe pickle stored"
            )
        elif family == "ensemble":
            # Members are raw fixed heads; ensemble probability is calibrated once below.
            results = [
                fit_predict({**CANDIDATES[name], "calibrate": False}, train, calibration, test, horizon)
                for name in config["members"]
            ]
            mean, p, cal_mean, cal_p, train_mean, train_p = [
                np.average([r[key] for r in results], axis=0, weights=config["weights"])
                for key in [
                    "mean",
                    "raw_probability",
                    "cal_mean",
                    "cal_probability",
                    "train_mean",
                    "train_probability",
                ]
            ]
            artifact["members"] = [r["artifact"] for r in results]
        else:
            raise ValueError("Unknown fixed candidate")
        raw = p.copy()
        if config["calibrate"] and len(np.unique(cy > 0)) == 2:

            def logit(prob):
                prob = np.clip(prob, 1e-6, 1 - 1e-6)
                return np.log(prob / (1 - prob)).reshape(-1, 1)

            calibrator = LogisticRegression(C=1, max_iter=200, random_state=SEED)
            calibrator.fit(logit(cal_p), cy > 0)
            p = calibrator.predict_proba(logit(p))[:, 1]
            artifact["sigmoid"] = {
                "coefficient": float(calibrator.coef_[0, 0]),
                "intercept": float(calibrator.intercept_[0]),
            }
        elif config["calibrate"]:
            p = np.full(len(test), np.mean(ty > 0))
            artifact["warnings"].append("Single-class calibration; abstain to training base rate")
    floor = max(float(np.quantile(tx[:, 4], 0.1)), 1e-6)
    cal_scale = np.maximum(cx[:, 4], floor) * np.sqrt(horizon)
    scale = np.maximum(vx[:, 4], floor) * np.sqrt(horizon)
    offsets = np.quantile((cy - cal_mean) / cal_scale, [0.1, 0.9])
    lower, upper = mean + offsets[0] * scale, mean + offsets[1] * scale
    artifact["interval"] = {
        "offsets": offsets.tolist(),
        "volatility_floor": floor,
        "method": "Calibration 10th/90th volatility-scaled residual quantiles; no coverage guarantee",
    }
    return {
        "mean": mean,
        "probability": p,
        "lower": lower,
        "upper": upper,
        "raw_probability": raw,
        "cal_mean": cal_mean,
        "cal_probability": cal_p,
        "train_mean": train_mean,
        "train_probability": train_p,
        "artifact": artifact,
    }
