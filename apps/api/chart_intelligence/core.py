"""Causal multiscale OHLCV sequences, fixed neural heads, chronological evaluation.

All timestamps are bar starts. Features require completed bars plus the declared
feed delay. Labels start at the decision bar open, after feature availability.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier, MLPRegressor
from threadpoolctl import threadpool_limits

from intelligence.calendar import EASTERN, schedule
from intelligence.portable_models import fit as numeric_fit
from intelligence.portable_models import predict as numeric_predict

VERSION = "candle-sequence-v1"


@dataclass(frozen=True)
class Protocol:
    seed: int = 20261118
    minutes: int = 5
    availability_delay_minutes: int = 15
    horizon_bars: int = 6
    stop: float = 0.02
    target: float = 0.03
    cost: float = 0.002
    train_days: int = 60
    calibration_days: int = 20
    evaluation_days: int = 40


def identity(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def parse_bars(rows):
    bars = {}
    for stamp, *values in rows:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        values = np.asarray(values, dtype=float)
        if t.tzinfo is None or t in bars or len(values) != 5 or not np.isfinite(values).all():
            raise ValueError("Duplicate, non-finite or unzoned candle")
        o, h, low, c, v = values
        if not 0 < low <= min(o, c) <= max(o, c) <= h or v < 0:
            raise ValueError("Invalid OHLCV")
        bars[t] = values
    return bars


def sequence(bars, decision, protocol=Protocol(), calendar=None):
    day = decision.astimezone(EASTERN).date()
    cal = (calendar if calendar is not None else schedule(day, day)).get(day)
    if not cal or decision > cal["close"]:
        raise ValueError("Outside exchange session")
    step = timedelta(minutes=protocol.minutes)
    cutoff = decision - timedelta(minutes=protocol.availability_delay_minutes)
    slots = int((cutoff - cal["open"]) // step)
    # Each coarser candle is exchange-open anchored and wholly completed.
    frames, source_times = [], []
    for factor, count in ((1, 12), (3, 8), (12, 2)):
        end = (slots // factor) * factor
        if end < factor * count:
            raise ValueError("Insufficient completed multiscale context")
        frame = []
        for start in range(end - factor * count, end, factor):
            times = [cal["open"] + (start + j) * step for j in range(factor)]
            if any(t not in bars for t in times):
                raise ValueError("Missing candle; no interpolation")
            a = np.asarray([bars[t] for t in times])
            frame.append([a[0, 0], a[:, 1].max(), a[:, 2].min(), a[-1, 3], a[:, 4].sum()])
            source_times.extend(times)
        frames.append(np.asarray(frame))
    ref = frames[0][-1, 3]
    flattened = []
    for a in frames:
        normalized = a.copy()
        normalized[:, :4] = np.log(a[:, :4] / ref)
        normalized[:, 4] = np.log1p(a[:, 4] / max(a[:, 4].mean(), 1))
        flattened.extend(normalized.ravel().tolist())
    close = frames[0][:, 3]
    ret = np.diff(np.log(close))
    numeric = [float(close[-1] / close[-1 - n] - 1) for n in (1, 3, 6, 11)]
    numeric += [
        float(ret.std()),
        float(ret[-5:].std()),
        float(frames[0][-1, 4] / max(frames[0][:-1, 4].mean(), 1)),
        float(ret.mean()),
        float(ret.min()),
        float(ret.max()),
        float((close[-1] - close.mean()) / ref),
    ]
    return {
        "x": flattened,
        "numeric": numeric,
        "latest_feature_available_at": (
            max(source_times) + step + timedelta(minutes=protocol.availability_delay_minutes)
        ).isoformat(),
    }


def label(bars, decision, protocol=Protocol(), calendar=None):
    times = [decision + timedelta(minutes=protocol.minutes * i) for i in range(protocol.horizon_bars)]
    day = decision.astimezone(EASTERN).date()
    cal = (calendar if calendar is not None else schedule(day, day)).get(day)
    if (
        not cal
        or times[-1] + timedelta(minutes=protocol.minutes) > cal["close"]
        or any(t not in bars for t in times)
    ):
        raise ValueError("Unresolved or incomplete horizon")
    future = np.asarray([bars[t] for t in times])
    entry = future[0, 0]
    event, ambiguous = "NEITHER", False
    for o, h, low, c, v in future:
        stop, target = low <= entry * (1 - protocol.stop), h >= entry * (1 + protocol.target)
        if o <= entry * (1 - protocol.stop):
            event = "STOP"
        elif o >= entry * (1 + protocol.target):
            event = "TARGET"
        elif stop:
            event, ambiguous = "STOP", bool(target)
        elif target:
            event = "TARGET"
        if event != "NEITHER":
            break
    return {
        "y": float(future[-1, 3] / entry - 1),
        "downside": float(min(0, future[:, 2].min() / entry - 1)),
        "target_first": int(event == "TARGET"),
        "path_event": event,
        "ambiguous": ambiguous,
        "label_end": (times[-1] + timedelta(minutes=protocol.minutes)).isoformat(),
    }


def dataset(snapshots, protocol=Protocol()):
    rows, rejected = [], []
    for symbol, snapshot in sorted(snapshots.items()):
        bars = parse_bars(snapshot["bars"])
        days = sorted({t.astimezone(EASTERN).date() for t in bars})
        if not days:
            continue
        calendar = schedule(days[0], days[-1])
        for day in days:
            for hour in (12, 14):
                decision = datetime.combine(day, datetime.min.time(), EASTERN).replace(hour=hour)
                try:
                    features = sequence(bars, decision, protocol, calendar)
                    outcome = label(bars, decision, protocol, calendar)
                    rows.append(
                        {
                            "symbol": symbol,
                            "decision": decision.isoformat(),
                            "date": str(day),
                            **features,
                            **outcome,
                        }
                    )
                except ValueError as exc:
                    rejected.append({"symbol": symbol, "decision": decision.isoformat(), "reason": str(exc)})
    return sorted(rows, key=lambda r: (r["decision"], r["symbol"])), rejected


def partitions(rows, protocol):
    days = sorted({r["date"] for r in rows})
    if len(days) < protocol.train_days + protocol.calibration_days + protocol.evaluation_days + 2:
        raise ValueError("Chronological evidence floors not met")
    a, b = int(len(days) * 0.6), int(len(days) * 0.8)
    groups = [[r for r in rows if r["date"] in set(d)] for d in (days[: a - 1], days[a : b - 1], days[b:])]
    if any(
        len({r["date"] for r in g}) < floor
        for g, floor in zip(
            groups, (protocol.train_days, protocol.calibration_days, protocol.evaluation_days)
        )
    ):
        raise ValueError("Split-specific date floors not met")
    return groups


def network(model):
    return {"weights": [a.tolist() for a in model.coefs_], "biases": [a.tolist() for a in model.intercepts_]}


def forward(model, x, sigmoid=False):
    out = np.asarray(x)
    for i, (w, b) in enumerate(zip(model["weights"], model["biases"])):
        out = out @ np.asarray(w) + np.asarray(b)
        if i < len(model["weights"]) - 1:
            out = np.maximum(out, 0)
    return 1 / (1 + np.exp(-np.clip(out, -35, 35))) if sigmoid else out


def infer(artifact, features):
    if artifact.get("version") == "phase7-bounded-v1":
        from chart_intelligence.phase7 import predict

        return predict(artifact, features)
    x = np.asarray([r["x"] for r in features], dtype=float)
    if x.ndim != 2 or x.shape[1] != len(artifact["mean"]) or not np.isfinite(x).all():
        raise ValueError("Invalid sequence feature shape")
    x = (x - np.asarray(artifact["mean"])) / np.asarray(artifact["scale"])
    reg = forward(artifact["regressor"], x) * artifact["target_scale"]
    cls = forward(artifact["classifier"], x, True)
    probabilities = []
    for i, c in enumerate(artifact["calibration"]):
        p = np.clip(cls[:, i], 1e-6, 1 - 1e-6)
        logits = np.log(p / (1 - p))
        probabilities.append(1 / (1 + np.exp(-np.clip(c["intercept"] + c["coefficient"] * logits, -35, 35))))
    return [
        {
            "expected_return": float(reg[i, 0]),
            "probability_up": float(probabilities[0][i]),
            "probability_down": float(1 - probabilities[0][i]),
            "probability_target_before_stop": float(probabilities[1][i]),
            "expected_downside": float(min(0, reg[i, 1])),
            "lower_return": float(reg[i, 0] - artifact["radius"]),
            "upper_return": float(reg[i, 0] + artifact["radius"]),
        }
        for i in range(len(features))
    ]


def metrics(rows, predictions):
    y = np.asarray([r["y"] for r in rows])
    p = np.asarray([r["probability_up"] for r in predictions])
    pred = np.asarray([r["expected_return"] for r in predictions])
    return {
        "observations": len(rows),
        "dates": len({r["date"] for r in rows}),
        "positive_outcomes": int((y > 0).sum()),
        "target_first_outcomes": sum(r["target_first"] for r in rows),
        "mae": float(abs(y - pred).mean()),
        "brier": float(((y > 0) - p) ** 2 @ np.ones(len(y)) / len(y)),
        "target_brier": float(
            np.mean(
                [
                    (r["target_first"] - p["probability_target_before_stop"]) ** 2
                    for r, p in zip(rows, predictions)
                ]
            )
        ),
        "coverage": float(
            np.mean([p["lower_return"] <= r["y"] <= p["upper_return"] for r, p in zip(rows, predictions)])
        ),
        "downside_mae": float(
            np.mean([abs(r["downside"] - p["expected_downside"]) for r, p in zip(rows, predictions)])
        ),
    }


def evaluate(rows, protocol=Protocol()):
    train, cal, test = partitions(rows, protocol)
    x = np.asarray([r["x"] for r in train])
    mean, scale = x.mean(0), x.std(0)
    scale[scale < 1e-8] = 1
    z = (x - mean) / scale
    cz = (np.asarray([r["x"] for r in cal]) - mean) / scale
    y = np.asarray([[r["y"], r["downside"]] for r in train])
    target_scale = max(float(y.std()), 0.001)
    classes = np.asarray([[r["y"] > 0, r["target_first"]] for r in train], dtype=int)
    if any(len(np.unique(classes[:, i])) != 2 for i in (0, 1)):
        raise ValueError("Both classes required for each neural head")
    with threadpool_limits(limits=1):
        opts = dict(
            hidden_layer_sizes=(24, 12),
            max_iter=150,
            alpha=0.1,
            random_state=protocol.seed,
            early_stopping=False,
        )
        reg = MLPRegressor(**opts).fit(z, y / target_scale)
        cls = MLPClassifier(**opts).fit(z, classes)
        artifact = {
            "version": VERSION,
            "mean": mean.tolist(),
            "scale": scale.tolist(),
            "target_scale": target_scale,
            "regressor": network(reg),
            "classifier": network(cls),
            "calibration": [],
            "radius": 0.0,
        }
        raw = forward(artifact["classifier"], cz, True)
        for i, labels in enumerate(([r["y"] > 0 for r in cal], [r["target_first"] for r in cal])):
            if len(set(labels)) < 2:
                p = np.clip(classes[:, i].mean(), 1e-6, 1 - 1e-6)
                artifact["calibration"].append({"intercept": float(np.log(p / (1 - p))), "coefficient": 0.0})
            else:
                logits = np.log(np.clip(raw[:, i], 1e-6, 1 - 1e-6) / np.clip(1 - raw[:, i], 1e-6, 1 - 1e-6))
                c = LogisticRegression(C=1, random_state=protocol.seed).fit(logits.reshape(-1, 1), labels)
                artifact["calibration"].append(
                    {"intercept": float(c.intercept_[0]), "coefficient": float(c.coef_[0, 0])}
                )
        cal_pred = forward(artifact["regressor"], cz)[:, 0] * target_scale
        artifact["radius"] = float(
            np.quantile(abs(np.asarray([r["y"] for r in cal]) - cal_pred), 0.9, method="higher")
        )
        # Export parity gate: JSON inference must match sklearn before evaluation.
        if not np.allclose(
            forward(artifact["regressor"], cz), reg.predict(cz), atol=1e-10
        ) or not np.allclose(raw, cls.predict_proba(cz), atol=1e-10):
            raise ValueError("Neural numeric export mismatch")
        numeric = [[r | {"x": r["numeric"]} for r in group] for group in (train, cal, test)]
        old = numeric_fit(numeric[0], numeric[1], list(range(11)), "linear", {"alpha": 100, "C": 0.1}, 1)
        nm, np_, nl, nu = numeric_predict(old, numeric[2])
    base = {
        "expected_return": float(y[:, 0].mean()),
        "probability_up": float(classes[:, 0].mean()),
        "probability_target_before_stop": float(classes[:, 1].mean()),
        "expected_downside": float(y[:, 1].mean()),
        "lower_return": float(np.quantile(y[:, 0], 0.05)),
        "upper_return": float(np.quantile(y[:, 0], 0.95)),
    }
    neural = infer(artifact, test)
    old_preds = [
        base
        | {
            "expected_return": float(a),
            "probability_up": float(b),
            "lower_return": float(c),
            "upper_return": float(d),
        }
        for a, b, c, d in zip(nm, np_, nl, nu)
    ]
    # Preserve within-day dependence across symbols and overlapping observations.
    # These intervals characterize this historical comparison only.
    dates = sorted({r["date"] for r in test})
    paired = {}
    rng = np.random.default_rng(protocol.seed)
    starts = rng.integers(0, len(dates), (2000, (len(dates) + 19) // 20))
    indices = ((starts[:, :, None] + np.arange(20)) % len(dates)).reshape(2000, -1)[:, : len(dates)]
    for name, baseline in (("historical_mean", [base] * len(test)), ("mainkraft_linear_refit", old_preds)):
        differences = []
        for day in dates:
            eligible = [i for i, r in enumerate(test) if r["date"] == day]
            differences.append(
                np.mean(
                    [
                        abs(test[i]["y"] - baseline[i]["expected_return"])
                        - abs(test[i]["y"] - neural[i]["expected_return"])
                        for i in eligible
                    ]
                )
            )
        values = np.asarray(differences)
        paired[name] = {
            "daily_mae_improvement": float(values.mean()),
            "interval_98_75": np.quantile(values[indices].mean(1), [0.00625, 0.99375]).tolist(),
            "method": "2000 circular 20-session paired block resamples; positive favors sequence model",
        }
    report = {
        "version": VERSION,
        "protocol": asdict(protocol),
        "evidence_type": "HISTORICAL_SIMULATION",
        "status": "EXPLORATORY",
        "promotion_authority": False,
        "execution_authority": False,
        "optimization": {
            "regressor_iterations": int(reg.n_iter_),
            "classifier_iterations": int(cls.n_iter_),
            "regressor_converged": bool(reg.n_iter_ < 150),
            "classifier_converged": bool(cls.n_iter_ < 150),
        },
        "split": [{"first": g[0]["date"], "last": g[-1]["date"], "rows": len(g)} for g in (train, cal, test)],
        "models": {
            "sequence_mlp": metrics(test, neural),
            "historical_mean": metrics(test, [base] * len(test)),
            "zero_return": metrics(
                test, [base | {"expected_return": 0.0, "probability_up": 0.5}] * len(test)
            ),
            "mainkraft_linear_refit": metrics(test, old_preds),
        },
        "paired_comparisons": paired,
        "comparison_scope": "Existing Mainkraft ridge/logistic implementation refit to the same 30-minute target using causal summaries; not the deployed day/swing artifact.",
        "limitations": [
            "Historical cohort and periods previously used by other studies; independent confirmation still required",
            "Intervals are calibration residual ranges, not guaranteed coverage",
            "Ambiguous target/stop bars count as stop first",
            "No historical news or executable quotes; cost scenarios are hypothetical",
        ],
        "predictions": [
            {
                "symbol": r["symbol"],
                "decision": r["decision"],
                "label_end": r["label_end"],
                "actual_return": r["y"],
                "actual_target_first": r["target_first"],
                **p,
            }
            for r, p in zip(test, neural)
        ],
        "cost_scenarios": {
            str(c): {"all_observations_mean_net_return": float(np.mean([r["y"] - c for r in test]))}
            for c in (0.0, protocol.cost, 0.005)
        },
    }
    return artifact, report
