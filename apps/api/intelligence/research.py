"""Small, frozen NumPy ridge/logistic research protocol; no pooled-cohort shortcut."""

import hashlib
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

import numpy as np

from intelligence.db import connection
from intelligence.forecasting import continuity
from intelligence.storage import bars_at, jsonb, save_manifest

PROTOCOL = {
    "version": "technical-v1",
    "splits": [0.6, 0.2, 0.2],
    "purge_sessions": 5,
    "bootstrap_samples": 2000,
    "block_sessions": 20,
    "confidence": 0.9875,
    "primary_comparisons": 4,
    "minimum_evaluation_dates": 126,
    "feature_version": "technical-daily-v1",
    "historical_not_prospective": True,
}
FEATURE_NAMES = [
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
]


def build_panel(bars, spy):
    for stream in (bars, spy):
        valid, reason = continuity(stream)
        if not valid:
            raise ValueError(reason)
    spy_at = {r["session_date"]: i for i, r in enumerate(spy)}
    closes = np.asarray([float(r["close"]) for r in bars])
    volumes = np.asarray([float(r["volume"]) for r in bars])
    spy_close = np.asarray([float(r["close"]) for r in spy])
    records = []
    for i in range(60, len(bars)):
        j = spy_at.get(bars[i]["session_date"], -1)
        if j < 60:
            continue

        def r(n):
            return closes[i] / closes[i - n] - 1

        def sr(n):
            return spy_close[j] / spy_close[j - n] - 1

        ret = np.diff(np.log(closes[i - 20 : i + 1]))
        sret = np.diff(np.log(spy_close[j - 20 : j + 1]))
        tr = [
            max(
                float(bars[k]["high"]) - float(bars[k]["low"]),
                abs(float(bars[k]["high"]) - closes[k - 1]),
                abs(float(bars[k]["low"]) - closes[k - 1]),
            )
            for k in range(i - 13, i + 1)
        ]
        volume_mean = float(volumes[i - 20 : i].mean())
        if volume_mean <= 0:
            continue
        values = [
            r(1),
            r(5),
            r(20),
            r(60),
            np.std(ret),
            np.mean(tr) / closes[i],
            volumes[i] / volume_mean,
            sr(5),
            np.std(sret),
            r(20) - sr(20),
        ]
        if np.isfinite(values).all():
            records.append({"date": bars[i]["session_date"], "index": i, "x": values})
    return records


def fit_heads(x, y):
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-12] = 1
    z = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    penalty = np.eye(z.shape[1])
    penalty[0, 0] = 0
    ridge = np.linalg.solve(z.T @ z + penalty, z.T @ y)
    labels = (y > 0).astype(float)
    if labels.min() == labels.max():
        raise ValueError("Directional training labels require both classes")
    coef = np.zeros(z.shape[1])
    for _ in range(100):
        prob = 1 / (1 + np.exp(-np.clip(z @ coef, -35, 35)))
        weights = np.maximum(prob * (1 - prob), 1e-8)
        grad = z.T @ (prob - labels) + penalty @ coef
        hessian = (z.T * weights) @ z + penalty + np.eye(z.shape[1]) * 1e-9
        step = np.linalg.solve(hessian, grad)
        coef -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return {"mean": mean, "scale": scale, "ridge": ridge, "logistic": coef}


def predict_heads(model, x):
    z = np.column_stack([np.ones(len(x)), (x - model["mean"]) / model["scale"]])
    return z @ model["ridge"], 1 / (1 + np.exp(-np.clip(z @ model["logistic"], -35, 35)))


def scores(y, expected, probabilities):
    labels = (np.asarray(y) > 0).astype(float)
    p = np.clip(probabilities, 1e-8, 1 - 1e-8)
    bins = []
    assignments = np.minimum((p * 10).astype(int), 9)
    for index in range(10):
        low = index / 10
        mask = assignments == index
        if mask.any():
            bins.append(
                {
                    "lower": float(low),
                    "count": int(mask.sum()),
                    "predicted": float(p[mask].mean()),
                    "observed": float(labels[mask].mean()),
                }
            )
    return {
        "mae": float(np.mean(np.abs(y - expected))),
        "brier": float(np.mean((p - labels) ** 2)),
        "log_loss": float(-np.mean(labels * np.log(p) + (1 - labels) * np.log(1 - p))),
        "calibration": bins,
    }


def paired_block_interval(dates, differences, *, samples=2000, block=20, confidence=0.9875):
    # First aggregate same-date stock observations; resample whole date blocks,
    # retaining market-wide dependence instead of counting each stock as independent.
    grouped = defaultdict(list)
    for d, value in zip(dates, differences, strict=True):
        grouped[d].append(float(value))
    series = np.asarray([np.mean(grouped[d]) for d in sorted(grouped)])
    if len(series) < 2:
        return None
    n = len(series)
    block = min(block, n)
    rng = np.random.default_rng(20261112)
    starts = rng.integers(0, n - block + 1, size=(samples, int(np.ceil(n / block))))
    indices = (starts[:, :, None] + np.arange(block)).reshape(samples, -1)[:, :n]
    means = series[indices].mean(axis=1)
    tail = (1 - confidence) / 2
    return {
        "mean_improvement": float(series.mean()),
        "lower": float(np.quantile(means, tail)),
        "upper": float(np.quantile(means, 1 - tail)),
        "confidence": confidence,
        "samples": samples,
        "block_sessions": block,
        "distinct_dates": n,
    }


def evaluate(bars, spy, symbol):
    panel = build_panel(bars, spy)
    if len(panel) < 400:
        return {
            "status": "inconclusive",
            "reason": "At least 400 feature-complete origin dates are required",
            "horizons": [],
        }
    dates = [r["date"] for r in panel]
    calibration_start, evaluation_start = dates[int(len(dates) * 0.6)], dates[int(len(dates) * 0.8)]
    # Exclude five full origin sessions at each boundary, independently of
    # each horizon, and additionally require labels to resolve within the block.
    train_end = dates[max(0, int(len(dates) * 0.6) - 5)]
    calibration_end = dates[max(0, int(len(dates) * 0.8) - 5)]
    reports = []
    for horizon in (1, 5):
        rows = []
        for r in panel:
            i = r["index"]
            if i + horizon >= len(bars):
                continue
            end = bars[i + horizon]["session_date"]
            y = float(bars[i + horizon]["close"]) / float(bars[i + 1]["open"]) - 1
            rows.append({**r, "end": end, "y": y})
        train = [r for r in rows if r["date"] < train_end and r["end"] < calibration_start]
        calibration = [
            r
            for r in rows
            if calibration_start <= r["date"] < calibration_end and r["end"] < evaluation_start
        ]
        test = [r for r in rows if r["date"] >= evaluation_start]
        if min(len(train), len(calibration), len(test)) < 20:
            reports.append(
                {"horizon": horizon, "status": "inconclusive", "reason": "Insufficient partition coverage"}
            )
            continue

        def x(block_):
            return np.asarray([r["x"] for r in block_], dtype=float)

        def y(block_):
            return np.asarray([r["y"] for r in block_], dtype=float)

        model = fit_heads(x(train), y(train))
        predicted, probability = predict_heads(model, x(test))
        cal_predicted, _ = predict_heads(model, x(calibration))
        residuals = y(calibration) - cal_predicted
        interval_offsets = np.quantile(residuals, [0.1, 0.9])
        truth = y(test)
        base_return, base_prob = float(y(train).mean()), float(np.mean(y(train) > 0))
        baseline_return, baseline_prob = np.full(len(test), base_return), np.full(len(test), base_prob)
        date_values = [r["date"] for r in test]
        by_period = {}
        for year in sorted({r["date"].year for r in test}):
            mask = np.asarray([r["date"].year == year for r in test])
            by_period[str(year)] = {
                "dates": int(mask.sum()),
                "candidate": scores(truth[mask], predicted[mask], probability[mask]),
                "baseline": scores(truth[mask], baseline_return[mask], baseline_prob[mask]),
            }
        comparisons = {}
        differences = {
            "mae": np.abs(truth - base_return) - np.abs(truth - predicted),
            "brier": (baseline_prob - (truth > 0)) ** 2 - (probability - (truth > 0)) ** 2,
        }
        for metric, delta in differences.items():
            interval = paired_block_interval(date_values, delta)
            state = (
                "inconclusive"
                if len(set(date_values)) < 126
                else "passed"
                if interval and interval["lower"] > 0
                else "failed"
            )
            comparisons[metric] = {"status": state, "interval": interval}
        horizon_status = (
            "passed"
            if all(c["status"] == "passed" for c in comparisons.values())
            else "inconclusive"
            if any(c["status"] == "inconclusive" for c in comparisons.values())
            else "failed"
        )
        reports.append(
            {
                "horizon": horizon,
                "status": horizon_status,
                "symbol": symbol,
                "training_dates": len(train),
                "calibration_dates": len(calibration),
                "evaluation_dates": len(test),
                "non_overlapping_horizon_count": len(test[::horizon]),
                "approximate_20_session_blocks": len(test) // 20,
                "candidate": scores(truth, predicted, probability),
                "baseline": scores(truth, baseline_return, baseline_prob),
                "comparisons": comparisons,
                "by_stock": {symbol: scores(truth, predicted, probability)},
                "by_year": by_period,
                "interval_coverage": float(
                    np.mean(
                        (truth >= predicted + interval_offsets[0])
                        & (truth <= predicted + interval_offsets[1])
                    )
                ),
                "interval_method": "Calibration residual 10th–90th percentile; no coverage guarantee under dependence",
                "partitions": {
                    "train_end": train[-1]["date"],
                    "calibration_start": calibration[0]["date"],
                    "calibration_end": calibration[-1]["date"],
                    "evaluation_start": test[0]["date"],
                },
                "fitted_parameters": {key: value.tolist() for key, value in model.items()},
                "reference_parameters": {"expected_return": base_return, "probability_positive": base_prob},
                "calibration_interval_offsets": interval_offsets.tolist(),
            }
        )
    status = (
        "passed"
        if reports and all(r["status"] == "passed" for r in reports)
        else "failed"
        if any(r["status"] == "failed" for r in reports)
        else "inconclusive"
    )
    return {
        "status": status,
        "symbol": symbol,
        "protocol": PROTOCOL,
        "features": FEATURE_NAMES,
        "horizons": reports,
        "limitations": [
            "Single-security exploratory study; watchlist selection is not an unbiased market universe.",
            "Distinct dates are not guaranteed independent; blocks address some serial dependence.",
            "Historical adjusted prices are a reconstructed archive, not prospective observations.",
            "Four-comparison correction applies within this report, not to repeated reports or a search across stocks.",
            "No automatic promotion, trading authority, or claim of profitability.",
        ],
    }


def run_evaluation(symbol):
    # Once bounded development is declared, this exposed holdout is sealed.
    # Code/hash changes must never be an excuse to evaluate new candidates on it.
    with connection() as conn:
        if conn.execute("SELECT 1 FROM research_plans LIMIT 1").fetchone():
            existing = conn.execute(
                "SELECT * FROM evaluation_reports WHERE model_id='technical-v1' AND symbol=%s ORDER BY created_at DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if not existing:
                raise ValueError("Original holdout is sealed; use the bounded development research ledger")
            conn.execute(
                "INSERT INTO evaluation_access_log(id,report_id,purpose) VALUES(%s,%s,'sealed_holdout_read_only')",
                (uuid4(), existing["id"]),
            )
            return {
                "id": str(existing["id"]),
                "reused_frozen_report": True,
                "holdout_sealed": True,
                **existing["report"],
            }
    implementation_hash = hashlib.sha256(
        b"".join(
            (Path(__file__).parent / name).read_bytes()
            for name in ["research.py", "forecasting.py", "calendar.py", "providers.py"]
        )
    ).hexdigest()
    with connection() as conn:
        bars, spy = bars_at(conn, symbol), bars_at(conn, "SPY")
        dataset_id = save_manifest(
            conn,
            "historical_evaluation",
            bars + spy,
            symbol=symbol,
            protocol=PROTOCOL,
            implementation_hash=implementation_hash,
        )
        existing = conn.execute(
            "SELECT * FROM evaluation_reports WHERE model_id='technical-v1' AND dataset_id=%s", (dataset_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "INSERT INTO evaluation_access_log(id,report_id,purpose) VALUES(%s,%s,'read_existing_frozen_report')",
                (uuid4(), existing["id"]),
            )
            return {"id": str(existing["id"]), "reused_frozen_report": True, **existing["report"]}
    report = evaluate(bars, spy, symbol)
    report["provenance"] = {
        "implementation_hash": implementation_hash,
        "dataset_id": dataset_id,
        "evaluation_kind": "historical_not_prospective",
    }
    report_id = uuid4()
    with connection() as conn:
        conn.execute(
            "INSERT INTO evaluation_reports(id,model_id,symbol,dataset_id,status,protocol,report) VALUES(%s,'technical-v1',%s,%s,%s,%s,%s) ON CONFLICT(model_id,dataset_id) DO NOTHING",
            (report_id, symbol, dataset_id, report["status"], jsonb(PROTOCOL), jsonb(report)),
        )
        saved = conn.execute(
            "SELECT * FROM evaluation_reports WHERE model_id='technical-v1' AND dataset_id=%s", (dataset_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO evaluation_access_log(id,report_id,purpose) VALUES(%s,%s,'fixed_protocol_evaluation')",
            (uuid4(), saved["id"]),
        )
    return {"id": str(saved["id"]), **saved["report"]}
