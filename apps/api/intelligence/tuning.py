"""Finite Optuna studies on inner temporal development blocks; no holdout search."""

import hashlib
import logging
from datetime import date
from pathlib import Path

import numpy as np
import optuna
import sklearn
from optuna.distributions import distribution_to_json

from intelligence.db import connection
from intelligence.point_features import GROUPS, NAMES, VERSION, build_features, load_context, persist_panel
from intelligence.portable_models import SEED, fit, predict
from intelligence.research import paired_block_interval
from intelligence.research_models import metrics
from intelligence.storage import jsonb
from intelligence.util import digest

PLAN = "information-optuna-v3"
BUDGET = 4
FAMILIES = ("linear", "histboost")


def implementation_hash():
    folder = Path(__file__).parent
    return hashlib.sha256(
        b"".join(
            (folder / name).read_bytes()
            for name in [
                "tuning.py",
                "point_features.py",
                "portable_models.py",
                "prospective.py",
                "information.py",
            ]
        )
    ).hexdigest()


def boundaries(dates):
    dates = sorted(set(dates))
    if len(dates) < 250:
        raise ValueError("At least 250 development dates required")
    a, b = int(len(dates) * 0.6), int(len(dates) * 0.8)
    return {"train_end": dates[a - 5], "cal_start": dates[a], "cal_end": dates[b - 5], "test_start": dates[b]}


def split(rows, bounds):
    b = {k: date.fromisoformat(v) if isinstance(v, str) else v for k, v in bounds.items()}
    return {
        "train": [r for r in rows if r["date"] < b["train_end"] and r["end"] < b["cal_start"]],
        "cal": [r for r in rows if b["cal_start"] <= r["date"] < b["cal_end"] and r["end"] < b["test_start"]],
        "test": [r for r in rows if r["date"] >= b["test_start"]],
    }


def declare():
    identity = implementation_hash()
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194040)")
        existing = conn.execute("SELECT * FROM tuning_plans WHERE id=%s", (PLAN,)).fetchone()
        if existing:
            environment = existing["specification"]["environment"]
            if (
                environment["optuna"] != optuna.__version__
                or environment["numpy"] != np.__version__
                or environment["sklearn"] != sklearn.__version__
            ):
                raise ValueError("Frozen tuning runtime differs; no silent study continuation")
            if existing["code_hash"] != identity:
                raise ValueError(
                    "Frozen phase-3 implementation differs; read existing reports, do not silently tune again"
                )
            return existing
        old = conn.execute(
            "SELECT protected_start FROM research_plans ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not old:
            raise ValueError("Original exposed evaluation boundary must be preserved")
        streams, info, coverage, facts, events = load_context(conn)
        streams = {
            s: [r for r in bars if r["session_date"] < old["protected_start"]] for s, bars in streams.items()
        }
        panels = {
            s: build_features(
                streams,
                s,
                info,
                coverage,
                [f for f in facts if f["symbol"] == s],
                [e for e in events if e["symbol"] == s],
            )
            for s in ("MSI", "AXON")
        }
        shared = set.intersection(*({r["date"] for r in p} for p in panels.values()))
        outer = boundaries(shared)
        dataset = {}
        feature_ids = {}
        for symbol, panel in panels.items():
            ids = persist_panel(conn, symbol, panel, "historical")
            feature_ids[symbol] = ids
            dataset[symbol] = {}
            bars = streams[symbol]
            for h in (1, 5):
                dataset[symbol][str(h)] = [
                    {
                        "date": r["date"],
                        "end": bars[r["index"] + h]["session_date"],
                        "x": r["x"],
                        "feature_id": ids[i],
                        "y": float(bars[r["index"] + h]["close"]) / float(bars[r["index"] + 1]["open"]) - 1,
                    }
                    for i, r in enumerate(panel)
                    if r["date"] in shared and r["index"] + h < len(bars)
                ]
        specification = {
            "trial_budget_per_study": BUDGET,
            "maximum_trials": 96,
            "families": FAMILIES,
            "groups": GROUPS,
            "features": NAMES,
            "feature_version": VERSION,
            "outer_boundaries": outer,
            "protected_start": old["protected_start"],
            "membership": ["MSI", "AXON"],
            "horizons": [1, 5],
            "code_hash": identity,
            "seed": SEED,
            "feature_ids": feature_ids,
            "confirmation": "None: previously exposed historical development",
            "selection": "Inner-only normalized MAE/Brier across separate stock heads; no outer-test selection",
            "search_space": {
                "linear": {"alpha": [1, 10, 100, 1000], "C": [0.01, 0.1, 1, 10]},
                "histboost": {
                    "iterations": [32, 64],
                    "leaves": [3, 7],
                    "learning_rate": [0.02, 0.05, 0.1],
                    "l2": [1, 10, 100],
                    "minimum_leaf": [20, 40, 80],
                },
            },
            "environment": {
                "numpy": np.__version__,
                "optuna": optuna.__version__,
                "sklearn": sklearn.__version__,
                "threads": 1,
            },
        }
        conn.execute(
            "INSERT INTO tuning_plans(id,code_hash,specification,dataset) VALUES(%s,%s,%s,%s)",
            (PLAN, identity, jsonb(specification), jsonb(dataset)),
        )
        return conn.execute("SELECT * FROM tuning_plans WHERE id=%s", (PLAN,)).fetchone()


def decode_rows(rows):
    return [
        {
            **r,
            "date": date.fromisoformat(r["date"]) if isinstance(r["date"], str) else r["date"],
            "end": date.fromisoformat(r["end"]) if isinstance(r["end"], str) else r["end"],
        }
        for r in rows
    ]


def study_parameters(trial, family, spec):
    return {
        name: trial.suggest_categorical(name, values) for name, values in spec["search_space"][family].items()
    }


def loss(artifact, train, test):
    y = np.asarray([r["y"] for r in test])
    ty = np.asarray([r["y"] for r in train])
    pred = predict(artifact, test)
    base_mae = max(float(np.mean(np.abs(y - ty.mean()))), 1e-9)
    base_brier = max(float(np.mean((np.mean(ty > 0) - (y > 0)) ** 2)), 1e-9)
    return float(
        0.5 * np.mean(np.abs(y - pred[0])) / base_mae + 0.5 * np.mean((pred[1] - (y > 0)) ** 2) / base_brier
    )


def report_result(artifact, parts, symbol, horizon):
    train, test = parts["train"], parts["test"]
    y = np.asarray([r["y"] for r in test])
    ty = np.asarray([r["y"] for r in train])
    mean, p, lower, upper = predict(artifact, test)
    base_mean, base_p = np.full(len(test), ty.mean()), np.full(len(test), np.mean(ty > 0))
    comparisons = {}
    for name, delta in {
        "mae": np.abs(y - base_mean) - np.abs(y - mean),
        "brier": (base_p - (y > 0)) ** 2 - (p - (y > 0)) ** 2,
    }.items():
        interval = paired_block_interval([r["date"] for r in test], delta)
        comparisons[name] = {
            "interval": interval,
            "diagnostic_gate": "passed" if len(test) >= 126 and interval["lower"] > 0 else "failed",
            "validation_status": "inconclusive",
        }
    by_year = {}
    for year in sorted({r["date"].year for r in test}):
        mask = np.asarray([r["date"].year == year for r in test])
        by_year[str(year)] = {
            "dates": int(mask.sum()),
            "candidate": metrics(y[mask], mean[mask], p[mask], lower[mask], upper[mask]),
            "baseline": metrics(y[mask], base_mean[mask], base_p[mask]),
        }
    records = [
        {
            "date": r["date"],
            "target": r["end"],
            "expected_return": float(mean[i]),
            "probability_positive": float(p[i]),
            "lower_return": float(lower[i]),
            "upper_return": float(upper[i]),
            "truth": r["y"],
            "feature_id": r["feature_id"],
            "baseline_return": float(base_mean[i]),
            "baseline_probability": float(base_p[i]),
        }
        for i, r in enumerate(test)
    ]
    return {
        "status": "inconclusive",
        "evidence": "Historical development; exposed history, not independent confirmation",
        "symbol": symbol,
        "horizon": horizon,
        "candidate": metrics(y, mean, p, lower, upper),
        "baseline": metrics(y, base_mean, base_p),
        "comparisons": comparisons,
        "by_year": by_year,
        "training_dates": len(train),
        "calibration_dates": len(parts["cal"]),
        "evaluation_dates": len(test),
        "non_overlapping_horizons": len(test[::horizon]),
        "approximate_20_session_blocks": len(test) // 20,
        "effective_independent_observations": None,
        "artifact": artifact,
    }, records


def run_tuning():
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    plan = declare()
    spec = plan["specification"]
    with connection() as lease:
        if not lease.execute("SELECT pg_try_advisory_lock(753194041) acquired").fetchone()["acquired"]:
            raise ValueError("Phase-3 tuning already running")
        lease.commit()
        try:
            for group, columns in GROUPS.items():
                for family in FAMILIES:
                    for h in (1, 5):
                        outer = {
                            s: split(decode_rows(plan["dataset"][s][str(h)]), spec["outer_boundaries"])
                            for s in ("MSI", "AXON")
                        }
                        # Eligibility is decided using outer training only, never by favorable test results.
                        extra = [c for c in columns if c >= 14]
                        eligible = all(
                            not extra
                            or sum(any(r["x"][c] is not None for c in extra) for r in p["train"]) >= 100
                            for p in outer.values()
                        )
                        if not eligible:
                            with connection() as conn:
                                for symbol in ("MSI", "AXON"):
                                    key = digest([PLAN, group, family, symbol, h])
                                    conn.execute(
                                        "INSERT INTO candidate_results VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                        (
                                            key,
                                            PLAN,
                                            family,
                                            group,
                                            symbol,
                                            h,
                                            "inconclusive",
                                            jsonb(
                                                {
                                                    "reason": "Feature group blocked: insufficient historical availability in training"
                                                }
                                            ),
                                            jsonb([]),
                                        ),
                                    )
                            continue
                        inner_dates = set.intersection(
                            *({r["date"] for r in p["train"]} for p in outer.values())
                        )
                        inner_bounds = boundaries(inner_dates)
                        inner = {s: split(p["train"], inner_bounds) for s, p in outer.items()}
                        prefix = f"{group}:{family}:{h}"
                        study = optuna.create_study(
                            direction="minimize",
                            sampler=optuna.samplers.TPESampler(seed=SEED, n_startup_trials=2),
                        )
                        with connection() as conn:
                            saved = conn.execute(
                                "SELECT * FROM tuning_trials WHERE plan_id=%s AND family=%s AND feature_group=%s AND horizon=%s ORDER BY number",
                                (PLAN, family, group, h),
                            ).fetchall()
                        for result in saved:
                            replay = study.ask()
                            replay_params = study_parameters(replay, family, spec)
                            if replay_params != result["parameters"]:
                                raise ValueError(
                                    "Optuna sampler replay changed; frozen trials cannot be silently replaced"
                                )
                            if result["status"] == "complete":
                                study.tell(replay, result["objective"])
                            else:
                                study.tell(replay, state=optuna.trial.TrialState.FAIL)
                        for number in range(len(saved), BUDGET):
                            trial = study.ask()
                            parameters = study_parameters(trial, family, spec)
                            distributions = {
                                k: distribution_to_json(v) for k, v in trial.distributions.items()
                            }
                            key = f"{prefix}:{number}"
                            with connection() as conn:
                                started = conn.execute(
                                    "SELECT 1 FROM tuning_events WHERE plan_id=%s AND trial_key=%s AND event='started'",
                                    (PLAN, key),
                                ).fetchone()
                                if not started:
                                    conn.execute(
                                        "INSERT INTO tuning_events(plan_id,trial_key,event,detail) VALUES(%s,%s,'started',%s)",
                                        (
                                            PLAN,
                                            key,
                                            jsonb({"parameters": parameters, "distributions": distributions}),
                                        ),
                                    )
                            objective, error, status = None, None, "complete"
                            try:
                                if started:
                                    raise ValueError("Interrupted slot consumed; no refit")
                                losses = []
                                for symbol, parts in inner.items():
                                    artifact = fit(
                                        parts["train"], parts["cal"], columns, family, parameters, h
                                    )
                                    losses.append(loss(artifact, parts["train"], parts["test"]))
                                objective = float(np.mean(losses))
                                if not np.isfinite(objective):
                                    raise ValueError("Nonfinite tuning objective")
                                study.tell(trial, objective)
                            except Exception as exc:
                                logging.getLogger("intelligence.tuning").exception("Bounded trial failed")
                                status, error = "failed", f"{type(exc).__name__}: trial failed or interrupted"
                                study.tell(trial, state=optuna.trial.TrialState.FAIL)
                            with connection() as conn:
                                conn.execute(
                                    "INSERT INTO tuning_trials VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                    (
                                        PLAN,
                                        key,
                                        family,
                                        group,
                                        h,
                                        number,
                                        status,
                                        jsonb(parameters),
                                        objective,
                                        jsonb(
                                            {
                                                "distributions": distributions,
                                                "inner_boundaries": inner_bounds,
                                                "error": error,
                                            }
                                        ),
                                    ),
                                )
                                conn.execute(
                                    "INSERT INTO tuning_events(plan_id,trial_key,event,detail) VALUES(%s,%s,'finished',%s)",
                                    (PLAN, key, jsonb({"status": status, "objective": objective})),
                                )
                        completed = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
                        best = min(completed, key=lambda t: (t.value, t.number)) if completed else None
                        for symbol, parts in outer.items():
                            identity = digest([PLAN, group, family, symbol, h])
                            with connection() as conn:
                                if conn.execute(
                                    "SELECT 1 FROM candidate_results WHERE id=%s", (identity,)
                                ).fetchone():
                                    continue
                            try:
                                if not best:
                                    raise ValueError("No completed tuning trial")
                                artifact = fit(parts["train"], parts["cal"], columns, family, best.params, h)
                                report, predictions = report_result(artifact, parts, symbol, h)
                                report.update(
                                    {
                                        "selected_inner_objective": best.value,
                                        "selected_trial": best.number,
                                        "feature_names": [NAMES[c] for c in columns],
                                    }
                                )
                            except Exception as exc:
                                logging.getLogger("intelligence.tuning").exception("Outer evaluation failed")
                                report, predictions = (
                                    {
                                        "status": "failed",
                                        "reason": f"{type(exc).__name__}: outer candidate failed",
                                    },
                                    [],
                                )
                            with connection() as conn:
                                conn.execute(
                                    "INSERT INTO candidate_results VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                    (
                                        identity,
                                        PLAN,
                                        family,
                                        group,
                                        symbol,
                                        h,
                                        report["status"],
                                        jsonb(report),
                                        jsonb(predictions),
                                    ),
                                )
            with connection() as conn:
                return {
                    "plan_id": PLAN,
                    "trials": conn.execute(
                        "SELECT count(*) n FROM tuning_trials WHERE plan_id=%s", (PLAN,)
                    ).fetchone()["n"],
                    "results": conn.execute(
                        "SELECT family,feature_group,symbol,horizon,status FROM candidate_results WHERE plan_id=%s ORDER BY feature_group,family,symbol,horizon",
                        (PLAN,),
                    ).fetchall(),
                }
        finally:
            lease.execute("SELECT pg_advisory_unlock(753194041)")
