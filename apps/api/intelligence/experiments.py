"""Immutable, bounded development research. Exposed holdouts never become fresh data."""

import hashlib
import time
from datetime import date
from pathlib import Path

import numpy as np

from intelligence.db import connection
from intelligence.research import build_panel, paired_block_interval, predict_heads
from intelligence.research_models import CANDIDATES, FEATURES, fit_predict, metrics
from intelligence.storage import bars_at, jsonb, save_manifest
from intelligence.util import digest

PLAN_ID = "bounded-development-v2"
PROTOCOL = {
    "version": PLAN_ID,
    "splits": [0.6, 0.2, 0.2],
    "purge_sessions": 5,
    "minimum_evaluation_dates": 126,
    "bootstrap_samples": 2000,
    "block_sessions": 20,
    "confidence": 0.9875,
    "primary_comparisons": 4,
    "maximum_experiments": 36,
    "symbols": ["MSI", "AXON"],
    "horizons": [1, 5],
    "evidence": "development_only_previously_seen_history",
    "promotion": "blocked_until_new_predeclared_prospective_confirmation",
}


def code_hash():
    folder = Path(__file__).parent
    return hashlib.sha256(
        b"".join(
            (folder / name).read_bytes()
            for name in [
                "experiments.py",
                "research_models.py",
                "research.py",
                "forecasting.py",
                "calendar.py",
                "storage.py",
            ]
        )
    ).hexdigest()


def enriched_panel(bars, spy):
    if len({(b["feed"], b["adjustment"], b["adjustment_asof"]) for b in [*bars, *spy]}) != 1:
        raise ValueError("Stock and benchmark must share feed and adjustment snapshot")
    panel = build_panel(bars, spy)
    spy_at = {r["session_date"]: i for i, r in enumerate(spy)}
    for row in panel:
        i, j = row["index"], spy_at[row["date"]]
        gaps = [float(bars[k]["open"]) / float(bars[k - 1]["close"]) - 1 for k in range(i - 19, i + 1)]
        log_returns = np.diff(np.log([float(r["close"]) for r in bars[i - 60 : i + 1]]))
        slow_vol = max(float(np.std(log_returns)), 1e-9)
        row["x"] = [
            *row["x"],
            gaps[-1],
            float(np.mean(gaps)),
            row["x"][4] / slow_vol,
            float(spy[j]["close"]) / float(spy[j - 60]["close"]) - 1,
        ]
    return panel


def partition_dates(panels):
    shared = sorted(set.intersection(*({r["date"] for r in panel} for panel in panels)))
    if len(shared) < 800:
        raise ValueError("At least 800 shared development origin dates are required")
    a, b = int(len(shared) * 0.6), int(len(shared) * 0.8)
    return {
        "train_end_exclusive": shared[a - 5],
        "calibration_start": shared[a],
        "calibration_end_exclusive": shared[b - 5],
        "evaluation_start": shared[b],
        "shared_dates": len(shared),
    }


def labeled_partitions(panel, bars, horizon, boundaries):
    groups = {"train": [], "calibration": [], "test": []}
    for row in panel:
        i = row["index"]
        if i + horizon >= len(bars):
            continue
        label = {
            **row,
            "end": bars[i + horizon]["session_date"],
            "y": float(bars[i + horizon]["close"]) / float(bars[i + 1]["open"]) - 1,
        }
        if row["date"] < boundaries["train_end_exclusive"] and label["end"] < boundaries["calibration_start"]:
            groups["train"].append(label)
        elif (
            boundaries["calibration_start"] <= row["date"] < boundaries["calibration_end_exclusive"]
            and label["end"] < boundaries["evaluation_start"]
        ):
            groups["calibration"].append(label)
        elif row["date"] >= boundaries["evaluation_start"]:
            groups["test"].append(label)
    return groups


def _protected_boundary(conn):
    reports = conn.execute("SELECT report FROM evaluation_reports WHERE model_id='technical-v1'").fetchall()
    starts = [
        date.fromisoformat(h["partitions"]["evaluation_start"])
        for r in reports
        for h in r["report"].get("horizons", [])
        if h.get("partitions")
    ]
    if not starts:
        raise ValueError("Original evaluation boundary must be audited before declaring development")
    return min(starts)


def declare_plan():
    """Commit the data/config/code budget before any new fitting."""
    identity = code_hash()
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194030)")
        existing = conn.execute("SELECT * FROM research_plans WHERE id=%s", (PLAN_ID,)).fetchone()
        if existing:
            if existing["code_hash"] != identity:
                raise ValueError(
                    "Frozen research code changed; existing results remain readable. A new reviewed declaration is required, not a silent rerun"
                )
            return existing
        boundary = _protected_boundary(conn)
        streams = {
            s: [b for b in bars_at(conn, s) if b["session_date"] < boundary]
            for s in [*PROTOCOL["symbols"], "SPY"]
        }
        if any(not b for b in streams.values()):
            raise ValueError("All explicitly configured development streams are required")
        panels = [enriched_panel(streams[s], streams["SPY"]) for s in PROTOCOL["symbols"]]
        partitions = partition_dates(panels)
        specification = {
            "protocol": PROTOCOL,
            "candidates": CANDIDATES,
            "features": FEATURES,
            "partitions": partitions,
            "membership": PROTOCOL["symbols"],
            "watchlist_independent": True,
            "untouched_confirmation_available": False,
        }
        import sklearn

        specification["environment"] = {"numpy": np.__version__, "sklearn": sklearn.__version__, "threads": 1}
        dataset_id = save_manifest(
            conn,
            "development_frozen",
            sum(streams.values(), []),
            specification=specification,
            code_hash=identity,
        )
        conn.execute(
            "INSERT INTO research_plans(id,dataset_id,protected_start,specification,code_hash) VALUES(%s,%s,%s,%s,%s)",
            (PLAN_ID, dataset_id, boundary, jsonb(specification), identity),
        )
        for candidate, config in CANDIDATES.items():
            conn.execute(
                "INSERT INTO research_candidates(id,specification) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (candidate, jsonb(config)),
            )
        return conn.execute("SELECT * FROM research_plans WHERE id=%s", (PLAN_ID,)).fetchone()


def frozen_streams(conn, plan):
    manifest = conn.execute(
        "SELECT payload FROM dataset_manifests WHERE id=%s", (plan["dataset_id"],)
    ).fetchone()["payload"]
    rows = conn.execute(
        "SELECT * FROM daily_bars WHERE id=ANY(%s) ORDER BY symbol,session_date", (manifest["bar_ids"],)
    ).fetchall()
    if len(rows) != len(manifest["bar_ids"]) or {r["content_hash"] for r in rows} != set(manifest["hashes"]):
        raise ValueError("Frozen dataset identity mismatch")
    if any(r["session_date"] >= plan["protected_start"] for r in rows):
        raise ValueError("Exposed evaluation period entered development dataset")
    return {s: [r for r in rows if r["symbol"] == s] for s in [*PROTOCOL["symbols"], "SPY"]}


def evaluate_candidate(config, groups, symbol, horizon):
    start = time.perf_counter()
    train, calibration, test = (groups[k] for k in ("train", "calibration", "test"))
    output = fit_predict(config, train, calibration, test, horizon)
    truth = np.asarray([r["y"] for r in test])
    ty = np.asarray([r["y"] for r in train])
    base_return, base_p = np.full(len(test), ty.mean()), np.full(len(test), np.mean(ty > 0))
    mean, p, lower, upper = (output[k] for k in ["mean", "probability", "lower", "upper"])
    comparisons = {}
    for metric, differences in {
        "mae": np.abs(truth - base_return) - np.abs(truth - mean),
        "brier": (base_p - (truth > 0)) ** 2 - (p - (truth > 0)) ** 2,
    }.items():
        interval = paired_block_interval([r["date"] for r in test], differences)
        diagnostic = "inconclusive" if len(test) < 126 else "passed" if interval["lower"] > 0 else "failed"
        comparisons[metric] = {
            "diagnostic_status": diagnostic,
            "interval": interval,
            "validation_status": "inconclusive",
        }
    threshold = float(np.median([r["x"][8] for r in train]))
    regimes = [
        ("up" if r["x"][13] >= 0 else "down") + ("/high-vol" if r["x"][8] > threshold else "/low-vol")
        for r in test
    ]
    slices = {}
    for kind, values in {"year": [str(r["date"].year) for r in test], "regime": regimes}.items():
        slices[kind] = {}
        for value in sorted(set(values)):
            mask = np.asarray([v == value for v in values])
            slices[kind][value] = {
                "dates": int(mask.sum()),
                "candidate": metrics(truth[mask], mean[mask], p[mask], lower[mask], upper[mask]),
                "baseline": metrics(truth[mask], base_return[mask], base_p[mask]),
            }
    records = [
        {
            "origin": r["date"],
            "target": r["end"],
            "expected": float(mean[i]),
            "probability": float(p[i]),
            "lower": float(lower[i]),
            "upper": float(upper[i]),
            "truth": r["y"],
            "baseline_return": float(base_return[i]),
            "baseline_probability": float(base_p[i]),
            "regime": regimes[i],
        }
        for i, r in enumerate(test)
    ]
    return {
        "status": "inconclusive",
        "evidence": PROTOCOL["evidence"],
        "symbol": symbol,
        "horizon": horizon,
        "reason": "Development data was previously available for model design; fresh prospective confirmation required",
        "training_dates": len(train),
        "calibration_dates": len(calibration),
        "evaluation_dates": len(test),
        "non_overlapping_horizon_count": len(test[::horizon]),
        "approximate_20_session_blocks": len(test) // 20,
        "effective_independent_observations": None,
        "dependence_note": "Neither non-overlapping windows nor block counts prove independence",
        "candidate": metrics(truth, mean, p, lower, upper),
        "baseline": metrics(truth, base_return, base_p),
        "uncalibrated": metrics(truth, mean, output["raw_probability"]),
        "training_in_sample": metrics(ty, output["train_mean"], output["train_probability"]),
        "median_reference_mae": float(np.mean(np.abs(truth - np.median(ty)))),
        "comparisons": comparisons,
        "slices": slices,
        "artifact": output["artifact"],
        "interval_method": output["artifact"]["interval"]["method"],
        "seconds": time.perf_counter() - start,
        "regime_threshold_training_spy_volatility": threshold,
    }, records


def run_suite():
    plan = declare_plan()
    with connection() as lease:
        if not lease.execute("SELECT pg_try_advisory_lock(753194031) acquired").fetchone()["acquired"]:
            raise ValueError("A bounded research suite is already running")
        lease.commit()
        try:
            with connection() as conn:
                streams = frozen_streams(conn, plan)
            boundaries = {
                k: date.fromisoformat(v) if isinstance(v, str) and len(v) == 10 else v
                for k, v in plan["specification"]["partitions"].items()
            }
            for symbol in PROTOCOL["symbols"]:
                panel = enriched_panel(streams[symbol], streams["SPY"])
                for horizon in PROTOCOL["horizons"]:
                    groups = labeled_partitions(panel, streams[symbol], horizon, boundaries)
                    for candidate, config in CANDIDATES.items():
                        experiment_id = digest([PLAN_ID, candidate, symbol, horizon])
                        with connection() as conn:
                            if conn.execute(
                                "SELECT 1 FROM research_experiments WHERE id=%s", (experiment_id,)
                            ).fetchone():
                                continue
                        try:
                            report, predictions = evaluate_candidate(config, groups, symbol, horizon)
                        except Exception as exc:
                            # Failed runtime consumes its fixed slot; never silently retry until favorable.
                            report, predictions = (
                                {
                                    "status": "failed",
                                    "runtime_failure": True,
                                    "reason": f"{type(exc).__name__}: bounded candidate failed; see local logs",
                                },
                                [],
                            )
                            import logging

                            logging.getLogger("intelligence.research").exception("Candidate failed")
                        report.update(
                            {
                                "dataset_id": plan["dataset_id"],
                                "code_hash": plan["code_hash"],
                                "partitions": plan["specification"]["partitions"],
                            }
                        )
                        with connection() as conn:
                            conn.execute(
                                "INSERT INTO research_experiments(id,plan_id,candidate_id,symbol,horizon,status,report) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                                (
                                    experiment_id,
                                    PLAN_ID,
                                    candidate,
                                    symbol,
                                    horizon,
                                    report["status"],
                                    jsonb(report),
                                ),
                            )
                            with conn.cursor() as cur:
                                cur.executemany(
                                    """INSERT INTO research_predictions VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    [
                                        (
                                            experiment_id,
                                            p["origin"],
                                            p["target"],
                                            p["expected"],
                                            p["probability"],
                                            p["lower"],
                                            p["upper"],
                                            p["truth"],
                                            p["baseline_return"],
                                            p["baseline_probability"],
                                            p["regime"],
                                        )
                                        for p in predictions
                                    ],
                                )
            with connection() as conn:
                return {
                    "plan_id": PLAN_ID,
                    "protected_start": plan["protected_start"],
                    "experiments": conn.execute(
                        "SELECT candidate_id,symbol,horizon,status FROM research_experiments WHERE plan_id=%s ORDER BY symbol,horizon,candidate_id",
                        (PLAN_ID,),
                    ).fetchall(),
                }
        finally:
            lease.execute("SELECT pg_advisory_unlock(753194031)")


def audit_original():
    """Reconstruct predictions from immutable fitted parameters; no new tuning."""
    summaries = []
    with connection() as conn:
        originals = conn.execute(
            "SELECT DISTINCT ON(symbol) * FROM evaluation_reports WHERE model_id='technical-v1' ORDER BY symbol,created_at DESC"
        ).fetchall()
        total = conn.execute(
            "SELECT count(*) n FROM evaluation_reports WHERE model_id='technical-v1'"
        ).fetchone()["n"]
        for original in originals:
            manifest = conn.execute(
                "SELECT payload FROM dataset_manifests WHERE id=%s", (original["dataset_id"],)
            ).fetchone()["payload"]
            bars = conn.execute(
                "SELECT * FROM daily_bars WHERE id=ANY(%s) ORDER BY symbol,session_date",
                (manifest["bar_ids"],),
            ).fetchall()
            own = [b for b in bars if b["symbol"] == original["symbol"]]
            spy = [b for b in bars if b["symbol"] == "SPY"]
            panel = build_panel(own, spy)
            for h in original["report"].get("horizons", []):
                if not h.get("fitted_parameters"):
                    continue
                partitions = {k: date.fromisoformat(v) for k, v in h["partitions"].items()}
                train, test = [], []
                for row in panel:
                    i = row["index"]
                    if i + h["horizon"] >= len(own):
                        continue
                    item = {**row, "y": float(own[i + h["horizon"]]["close"]) / float(own[i + 1]["open"]) - 1}
                    if row["date"] <= partitions["train_end"]:
                        train.append(item)
                    if row["date"] >= partitions["evaluation_start"]:
                        test.append(item)
                model = {k: np.asarray(v) for k, v in h["fitted_parameters"].items()}
                pred, prob = predict_heads(model, np.asarray([r["x"] for r in test]))
                train_pred, train_prob = predict_heads(model, np.asarray([r["x"] for r in train]))
                truth = np.asarray([r["y"] for r in test])
                actual = metrics(truth, pred, prob)
                if not np.isclose(actual["mae"], h["candidate"]["mae"], rtol=1e-10):
                    raise ValueError("Original prediction reconstruction does not reconcile")
                summaries.append(
                    {
                        "report_id": str(original["id"]),
                        "symbol": original["symbol"],
                        "horizon": h["horizon"],
                        "original_status": h["status"],
                        "test": actual,
                        "train": metrics(np.asarray([r["y"] for r in train]), train_pred, train_prob),
                        "baseline": h["baseline"],
                        "comparisons": h["comparisons"],
                        "interval_coverage": h["interval_coverage"],
                        "evaluation_dates": h["evaluation_dates"],
                        "non_overlapping_horizons": h["non_overlapping_horizon_count"],
                        "blocks": h["approximate_20_session_blocks"],
                        "partitions": h["partitions"],
                        "diagnosis": "Point estimates worsen on MSI; AXON improvements are small relative to dependence-aware uncertainty. No corrected interval establishes improvement.",
                    }
                )
        report = {
            "original_reports_preserved": total,
            "reconstructed_latest_heads": summaries,
            "future_input_leakage": "No future open in inputs; training-only scaling and contained purged labels verified in code/tests",
            "methodological_limits": [
                "Inspected holdout is exposed, even if code hashes create new dataset identities",
                "Original logistic probabilities were not calibrated on calibration data",
                "Fixed residual intervals undercovered in observed evaluation",
                "Mean baseline is the declared reference; median is an additional MAE diagnostic",
                "Four-comparison intervals do not correct lifetime selection across stocks or candidates",
                "Historical bars reconstruct current split-adjusted history, not a point-in-time archive",
            ],
            "do_not_repeat": "No hyperparameter tuning or new candidate fitting on the original evaluation period",
        }
        identity = digest(report)
        conn.execute(
            "INSERT INTO research_audits(id,report) VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (identity, jsonb(report)),
        )
    return {"id": identity, **report}
