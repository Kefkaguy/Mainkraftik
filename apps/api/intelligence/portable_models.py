"""Train bounded heads and export numeric JSON artifacts, not executable pickle."""

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from threadpoolctl import threadpool_limits

SEED = 20261112


def matrix(rows, columns, imputation=None):
    raw = np.asarray([[r["x"][c] for c in columns] for r in rows], dtype=float)
    missing = ~np.isfinite(raw)
    if imputation is None:
        imputation = [
            float(np.median(raw[~missing[:, i], i])) if (~missing[:, i]).any() else 0.0
            for i in range(raw.shape[1])
        ]
    filled = np.where(missing, np.asarray(imputation), raw)
    return np.column_stack([filled, missing.astype(float)]), imputation


def export_trees(model):
    trees = []
    for iteration in model._predictors:
        if len(iteration) != 1:
            raise ValueError("Only binary/regression histogram heads are supported")
        nodes = iteration[0].nodes
        if np.any(nodes["is_categorical"]):
            raise ValueError("Categorical tree export is unsupported")
        trees.append(
            [
                {
                    name: (float(node[name]) if name in {"value", "num_threshold"} else int(node[name]))
                    for name in [
                        "value",
                        "feature_idx",
                        "num_threshold",
                        "missing_go_to_left",
                        "left",
                        "right",
                        "is_leaf",
                    ]
                }
                for node in nodes
            ]
        )
    return {"base": float(np.asarray(model._baseline_prediction).reshape(-1)[0]), "trees": trees}


def tree_predict(head, x):
    out = np.full(len(x), head["base"])
    for tree in head["trees"]:
        for i, row in enumerate(x):
            index = 0
            while not tree[index]["is_leaf"]:
                node = tree[index]
                value = row[node["feature_idx"]]
                left = bool(node["missing_go_to_left"]) if np.isnan(value) else value <= node["num_threshold"]
                index = node["left"] if left else node["right"]
            out[i] += tree[index]["value"]
    return out


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -35, 35)))


def raw_predict(artifact, rows):
    x, _ = matrix(rows, artifact["columns"], artifact["imputation"])
    if artifact["family"] == "linear":
        z = (x - np.asarray(artifact["mean"])) / np.asarray(artifact["scale"])
        mean = z @ np.asarray(artifact["return_coef"]) + artifact["return_intercept"]
        prob = sigmoid(z @ np.asarray(artifact["direction_coef"]) + artifact["direction_intercept"])
    else:
        mean = tree_predict(artifact["return_head"], x)
        prob = sigmoid(tree_predict(artifact["direction_head"], x))
    return mean, prob


def predict(artifact, rows):
    mean, raw = raw_predict(artifact, rows)
    p = np.clip(raw, 1e-6, 1 - 1e-6)
    calibration = artifact["calibration"]
    probability = sigmoid(calibration["intercept"] + calibration["coefficient"] * np.log(p / (1 - p)))
    volatility = np.maximum([r["x"][4] for r in rows], artifact["volatility_floor"]) * np.sqrt(
        artifact["horizon"]
    )
    return (
        mean,
        probability,
        mean + artifact["interval_offsets"][0] * volatility,
        mean + artifact["interval_offsets"][1] * volatility,
    )


def fit(train, calibration, columns, family, parameters, horizon):
    import sklearn

    if len(train) < 100 or len(calibration) < 20:
        raise ValueError("Training/calibration sample floor not met")
    x, imputation = matrix(train, columns)
    y = np.asarray([r["y"] for r in train])
    cy = np.asarray([r["y"] for r in calibration])
    if len(np.unique(y > 0)) != 2:
        raise ValueError("Both directional classes required")
    artifact = {
        "version": "portable-numeric-v3",
        "family": family,
        "parameters": parameters,
        "columns": columns,
        "imputation": imputation,
        "horizon": horizon,
        "training_dates": len(train),
        "sklearn": sklearn.__version__,
        "numpy": np.__version__,
        "missing_policy": "Training medians plus explicit missing indicators; source values remain unavailable",
    }
    with threadpool_limits(limits=1):
        if family == "linear":
            mean, scale = x.mean(axis=0), x.std(axis=0)
            scale[scale < 1e-10] = 1
            z = (x - mean) / scale
            reg = Ridge(alpha=parameters["alpha"]).fit(z, y)
            cls = LogisticRegression(C=parameters["C"], max_iter=300, random_state=SEED).fit(z, y > 0)
            artifact.update(
                {
                    "mean": mean.tolist(),
                    "scale": scale.tolist(),
                    "return_coef": reg.coef_.tolist(),
                    "return_intercept": float(reg.intercept_),
                    "direction_coef": cls.coef_[0].tolist(),
                    "direction_intercept": float(cls.intercept_[0]),
                }
            )
        elif family == "histboost":
            options = dict(
                max_iter=parameters["iterations"],
                max_leaf_nodes=parameters["leaves"],
                learning_rate=parameters["learning_rate"],
                l2_regularization=parameters["l2"],
                min_samples_leaf=parameters["minimum_leaf"],
                early_stopping=False,
                random_state=SEED,
            )
            reg, cls = HistGradientBoostingRegressor(**options), HistGradientBoostingClassifier(**options)
            reg.fit(x, y)
            cls.fit(x, y > 0)
            artifact.update({"return_head": export_trees(reg), "direction_head": export_trees(cls)})
            # Internal sklearn structures are version-specific: fail closed unless numeric export reconciles.
            cx, _ = matrix(calibration, columns, imputation)
            prediction = raw_predict(artifact, calibration)
            if not (
                np.allclose(prediction[0], reg.predict(cx), atol=1e-10, rtol=1e-10)
                and np.allclose(prediction[1], cls.predict_proba(cx)[:, 1], atol=1e-10, rtol=1e-10)
            ):
                raise ValueError("Portable histogram artifact failed parity verification")
        else:
            raise ValueError("Unknown model family")
        cal_mean, cal_prob = raw_predict(artifact, calibration)
        clipped = np.clip(cal_prob, 1e-6, 1 - 1e-6)
        logits = np.log(clipped / (1 - clipped)).reshape(-1, 1)
        if len(np.unique(cy > 0)) == 2:
            calibrator = LogisticRegression(C=1, max_iter=200, random_state=SEED).fit(logits, cy > 0)
            artifact["calibration"] = {
                "intercept": float(calibrator.intercept_[0]),
                "coefficient": float(calibrator.coef_[0, 0]),
            }
        else:
            p = float(np.clip(np.mean(y > 0), 1e-6, 1 - 1e-6))
            artifact["calibration"] = {
                "intercept": float(np.log(p / (1 - p))),
                "coefficient": 0.0,
                "warning": "Single-class calibration; training base rate",
            }
    floor = max(float(np.quantile([r["x"][4] for r in train], 0.1)), 1e-6)
    scale = np.maximum([r["x"][4] for r in calibration], floor) * np.sqrt(horizon)
    artifact["volatility_floor"] = floor
    artifact["interval_offsets"] = np.quantile((cy - cal_mean) / scale, [0.1, 0.9]).tolist()
    artifact["interval_method"] = (
        "Disjoint calibration 10th/90th volatility-scaled residuals; no coverage guarantee"
    )
    return artifact
