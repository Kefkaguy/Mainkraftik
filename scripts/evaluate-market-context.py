"""One frozen exploratory comparison. Writes an immutable experiment, never orders."""

import gzip
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from intelligence.research import paired_block_interval, scores
from intelligence.util import canonical, digest
from paperpilot.engine import hydrate
from paperpilot.market_context import (
    fit_candidate,
    labeled_rows,
    panel,
    predict_candidate,
    shared_boundaries,
    split_rows,
)


def summary(rows):
    if not rows:
        return {"observations": 0}
    r = scores(
        np.asarray([v["y"] for v in rows]),
        np.asarray([v["mean"] for v in rows]),
        np.asarray([v["p"] for v in rows]),
    )
    return {
        **r,
        "observations": len(rows),
        "distinct_dates": len({v["date"] for v in rows}),
        "interval_coverage": sum(v["lo"] <= v["y"] <= v["hi"] for v in rows)
        / len(rows),
    }


def main():
    folder = ROOT / ".local/context-research"
    plan = json.loads((ROOT / "docs/market-context-shadow-plan.json").read_text())
    raw = (folder / "stock-snapshot.json.gz").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == plan["snapshot_sha256"]
    snapshot = json.loads(gzip.decompress(raw))
    context_raw = (folder / "context-snapshot.json.gz").read_bytes()
    context_sources = json.loads(gzip.decompress(context_raw))
    contexts = {k: hydrate(v["payload"]["bars"]) for k, v in context_sources.items()}
    spy = hydrate(snapshot["sources"]["SPY"]["payload"]["bars"])
    common_start = max(v[0]["session_date"] for v in [spy, *contexts.values()])
    shared = [v["session_date"] for v in spy if v["session_date"] >= common_start][60:]
    boundaries = shared_boundaries(shared)
    report = {
        "plan": plan,
        "started_at": datetime.now(UTC),
        "boundaries": boundaries,
        "context_snapshot_sha256": hashlib.sha256(context_raw).hexdigest(),
        "exclusions": [],
        "fits": 0,
        "records": [],
    }
    # Persist the input specification before fitting, not after viewing losses.
    manifest = {
        "plan_hash": digest(plan),
        "boundaries": boundaries,
        "context_hash": report["context_snapshot_sha256"],
        "implementation_hash": digest(
            [
                Path(__file__).read_text(),
                (ROOT / "apps/api/paperpilot/market_context.py").read_text(),
            ]
        ),
    }
    manifest_id = digest(manifest)
    frozen = folder / (manifest_id + ".manifest.json")
    if not frozen.exists():
        frozen.write_text(canonical(manifest), encoding="utf-8")
    destination = folder / (manifest_id + ".experiment.json.gz")
    if destination.exists():
        raise ValueError("Immutable experiment already exists; read saved results")
    for symbol in plan["universe"]:
        if symbol not in snapshot["sources"]:
            report["exclusions"].append(
                {"symbol": symbol, "reason": "No mature price history"}
            )
            continue
        bars = hydrate(snapshot["sources"][symbol]["payload"]["bars"])
        try:
            features = panel(bars, spy, contexts)
            for h in (1, 5):
                groups = split_rows(labeled_rows(features, bars, h), boundaries)
                train, cal, test = [
                    groups[n] for n in ("train", "calibration", "evaluation")
                ]
                if len(test) < 126:
                    raise ValueError("Fewer than126 evaluation dates")
                available = train + cal
                for family in (
                    "technical14",
                    "market20",
                    "mean_available",
                    "mean_train",
                ):
                    if family.startswith("mean_"):
                        historical = available if family == "mean_available" else train
                        values = [v["y"] for v in historical]
                        mean = np.full(len(test), np.mean(values))
                        prob = np.full(len(test), np.mean(np.asarray(values) > 0))
                        lo, hi = [
                            np.full(len(test), q)
                            for q in np.quantile(values, [0.1, 0.9])
                        ]
                    else:
                        report["fits"] += 1
                        assert report["fits"] <= plan["maximum_fits"]
                        artifact = fit_candidate(
                            train, cal, h, with_context=family == "market20"
                        )
                        mean, prob, lo, hi = predict_candidate(artifact, test)
                    for i, row in enumerate(test):
                        report["records"].append(
                            {
                                "symbol": symbol,
                                "horizon": h,
                                "model": family,
                                "date": row["date"],
                                "end": row["end"],
                                "y": row["y"],
                                "mean": float(mean[i]),
                                "p": float(prob[i]),
                                "lo": float(lo[i]),
                                "hi": float(hi[i]),
                                "volatility": float(row["x"][4] * np.sqrt(252)),
                            }
                        )
            print(symbol + ": evaluated", flush=True)
        except ValueError as exc:
            report["exclusions"].append({"symbol": symbol, "reason": str(exc)})
    metrics, comparisons, sensitivity = [], [], []
    rows = report["records"]
    for h in (1, 5):
        for model in ("mean_train", "mean_available", "technical14", "market20"):
            group = [r for r in rows if r["horizon"] == h and r["model"] == model]
            metrics.append(
                {
                    "horizon": h,
                    "model": model,
                    **summary(group),
                    "by_stock": {
                        s: summary([r for r in group if r["symbol"] == s])
                        for s in sorted({r["symbol"] for r in group})
                    },
                    "by_year": {
                        str(y): summary([r for r in group if r["date"].year == y])
                        for y in sorted({r["date"].year for r in group})
                    },
                }
            )
        candidate = [r for r in rows if r["horizon"] == h and r["model"] == "market20"]
        for base in ("mean_available", "technical14"):
            lookup = {
                (r["symbol"], r["date"]): r
                for r in rows
                if r["horizon"] == h and r["model"] == base
            }
            for loss in ("mae", "brier"):

                def error(r, loss=loss):
                    return (
                        abs(r["y"] - r["mean"])
                        if loss == "mae"
                        else (r["p"] - int(r["y"] > 0)) ** 2
                    )

                dates = [r["date"] for r in candidate]
                differences = [
                    error(lookup[(r["symbol"], r["date"])]) - error(r)
                    for r in candidate
                ]
                interval = paired_block_interval(dates, differences)
                verdict = (
                    "inconclusive"
                    if len(set(dates)) < 126 or not interval
                    else "passed"
                    if interval["lower"] > 0
                    else "failed"
                    if interval["upper"] <= 0
                    else "inconclusive"
                )
                comparisons.append(
                    {
                        "horizon": h,
                        "baseline": base,
                        "loss": loss,
                        "primary": base == "mean_available",
                        "interval": interval,
                        "exploratory_comparison_result": verdict,
                        "validated": False,
                    }
                )
        for model in ("technical14", "market20"):
            selected = [
                r
                for r in rows
                if r["horizon"] == h
                and r["model"] == model
                and r["p"] >= 0.70
                and r["mean"] > 0.0015
                and r["volatility"] <= 0.8
            ]
            for bps in plan["fee_sensitivity_roundtrip_bps"]:
                sensitivity.append(
                    {
                        "horizon": h,
                        "model": model,
                        "planning_cost_bps": bps,
                        "qualifying_rows": len(selected),
                        "mean_target_return_minus_cost": float(
                            np.mean([r["y"] - bps / 10000 for r in selected])
                        )
                        if selected
                        else None,
                        "limitation": "Price-only descriptive target returns; no historical news replay, fills, stop/take-profit, costs or execution backtest",
                    }
                )
    report.update(
        metrics=metrics,
        comparisons=comparisons,
        fee_sensitivity=sensitivity,
        completed_at=datetime.now(UTC),
        validated=False,
        execution_authority="none",
        uncertainty_limitation="Shared-date20-session blocks,selected current survivors,overlapping5d labels; not independent confirmation",
    )
    with destination.open("xb") as out:
        out.write(gzip.compress(canonical(report).encode()))
    public = {k: v for k, v in report.items() if k != "records"}
    public["experiment_hash"] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (ROOT / "docs/market-context-shadow-results.json").write_text(
        canonical(public), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "fits": report["fits"],
                "comparisons": comparisons,
                "metrics": [
                    {
                        k: v
                        for k, v in r.items()
                        if k not in ["by_stock", "by_year", "calibration"]
                    }
                    for r in metrics
                ],
            },
            default=str,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
