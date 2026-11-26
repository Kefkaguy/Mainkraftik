"""One declared Phase 7 search; never reads labels from the exposed evaluation."""

import gzip
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.core import dataset, identity, parse_bars
from chart_intelligence.phase7 import (
    AUDIT_END,
    CANDIDATES,
    DEVELOPMENT_END,
    VERSION,
    forecast_metrics,
    predict,
    split_development,
    train,
)
from chart_intelligence.phase7_execution import Execution, bootstrap_daily, replay
from intelligence.util import digest

FOLDER = ROOT / ".local/phase7"


def main():
    completed = list(FOLDER.glob("*.complete.json"))
    if completed:
        # Source edits or additional reporting code must not silently create
        # another search against the now-exposed Phase 7 audit period.
        if len(completed) != 1:
            raise ValueError("Ambiguous prior searches; manual evidence audit required")
        print(completed[0].read_text())
        return
    entries = [
        json.loads(s)
        for s in (ROOT / ".local/mainkraft/history/coverage.jsonl")
        .read_text()
        .splitlines()
    ]
    files = list((ROOT / "apps/api/chart_intelligence").glob("phase7*.py"))
    declaration = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "candidates": CANDIDATES,
        "development_end": DEVELOPMENT_END,
        "diagnostic_audit_end": AUDIT_END,
        "old_evaluation": "FORBIDDEN",
        "selection": "Highest 98.75% lower bound on daily assumed net P/L at 30-minute holding; minimum 30 trades; ties use calibration MAE then fixed candidate order",
        "confirmation": "Only future forecasts recorded after this declaration can supply independent confirmation",
        "holding_variants": [3, 6],
        "execution": Execution().__dict__,
        "stress_roundtrip_costs": [0.0022, 0.0042, 0.0082],
        "data": entries,
        "source_archive": store.put(
            "sources", {str(p.relative_to(ROOT)): p.read_text() for p in files}, FOLDER
        ),
    }
    # Fixed search identity excludes wall time, so restart cannot silently launch
    # another search with the same declaration and access its audit twice.
    search_id = identity({k: v for k, v in declaration.items() if k != "created_at"})
    marker = FOLDER / (search_id + ".complete.json")
    if marker.exists():
        print(marker.read_text())
        return
    declaration_id = store.put("declaration", declaration, FOLDER)
    print("Declared", declaration_id, flush=True)
    snapshots = {}
    indexed = {}
    for entry in entries:
        with gzip.open(
            ROOT / ".local/mainkraft/history" / (entry["hash"] + ".json.gz"), "rt"
        ) as f:
            snapshot = json.load(f)
        if digest(snapshot) != entry["hash"]:
            raise ValueError("Data hash mismatch")
        # Do not create labels or features for the previously exposed evaluation.
        snapshot["bars"] = [r for r in snapshot["bars"] if str(r[0])[:10] <= AUDIT_END]
        snapshots[entry["symbol"]] = snapshot
        indexed[entry["symbol"]] = parse_bars(snapshot["bars"])
    rows, rejected = dataset(snapshots)
    for row in rows:
        decision = datetime.fromisoformat(row["decision"])
        bars = indexed[row["symbol"]]
        row["path"] = [
            [t.isoformat(), *bars[t].tolist()]
            for t in [decision + timedelta(minutes=5 * i) for i in range(6)]
        ]
        known = decision - timedelta(minutes=20)
        row["known_dollar_volume"] = float(bars[known][3] * bars[known][4])
        row["regime"] = ("HIGH_VOL" if row["numeric"][4] > 0.003 else "LOW_VOL") + (
            "_UP" if row["numeric"][7] > 0 else "_DOWN"
        )
    train_rows, cal, selection, audit = split_development(rows)
    dataset_id = store.put(
        "dataset",
        {"rows": rows, "rejected": rejected, "declaration_id": declaration_id},
        FOLDER,
    )
    # Every model and forecast is frozen before the one-time audit is evaluated.
    fitted = {}
    development = {}
    artifacts = {}
    selections = {}
    for name in CANDIDATES:
        model = train(name, train_rows, cal)
        artifacts[name] = store.put("candidate", model, FOLDER)
        fitted[name] = model
        forecasts = predict(model, selection)
        simulated = replay(selection, forecasts, mode="ASSUMPTION_ONLY")
        development[name] = {
            "prediction": forecast_metrics(selection, forecasts),
            "optimization": model["optimization"],
            "strategy": {
                k: v
                for k, v in simulated.items()
                if k not in {"orders", "trades", "daily_net_pnl"}
            },
        }
        ci = simulated["daily_mean_net_pnl_ci_98_75"]
        selections[name] = (
            ci[0] if ci and simulated["trade_count"] >= 30 else float("-inf"),
            -development[name]["prediction"]["mae"],
        )
        print(
            "Development",
            name,
            simulated["trade_count"],
            simulated["net_pnl"],
            flush=True,
        )
    eligible = [n for n in CANDIDATES if selections[n][0] != float("-inf")]
    winner = (
        max(eligible, key=lambda n: selections[n])
        if eligible
        else min(CANDIDATES, key=lambda n: development[n]["prediction"]["mae"])
    )
    frozen_id = store.put(
        "frozen_selection",
        {
            "winner": winner,
            "selection_basis": "net_lower_bound"
            if eligible
            else "forecast_diagnostic_only_no_eligible_strategy",
            "artifacts": artifacts,
            "development": development,
            "declaration_id": declaration_id,
        },
        FOLDER,
    )
    audit_result = {}
    predictions = {}
    for name in CANDIDATES:
        forecasts = predict(fitted[name], audit)
        predictions[name] = forecasts
        scenarios = {}
        for label, execution in (
            ("base", Execution()),
            ("stress", Execution(spread=0.002, slippage_per_leg=0.001)),
            ("severe", Execution(spread=0.004, slippage_per_leg=0.002)),
        ):
            for hold in (3, 6):
                scenarios[f"{label}_{hold * 5}min"] = replay(
                    audit,
                    forecasts,
                    execution,
                    mode="ASSUMPTION_ONLY",
                    holding_bars=hold,
                )
        audit_result[name] = {
            "prediction": forecast_metrics(audit, forecasts),
            "scenarios": scenarios,
            "strict_quote_audit": replay(audit, forecasts),
            "model_id": artifacts[name],
        }
    always = replay(
        audit,
        [{"probability_up": 1.0, "expected_return": 1.0}] * len(audit),
        mode="ASSUMPTION_ONLY",
        benchmark=True,
    )
    selected = audit_result[winner]["scenarios"]["base_30min"]
    differences = [
        selected["daily_net_pnl"][d] - always["daily_net_pnl"][d]
        for d in selected["daily_net_pnl"]
    ]
    # No independent unseen confirmation, no full executable quote history:
    # any positive diagnostic remains a failure of deployment acceptance.
    report = {
        "version": VERSION,
        "status": "NO_INDEPENDENTLY_VALIDATED_STRATEGY",
        "execution_authority": False,
        "promotion_authority": False,
        "created_at": datetime.now(UTC).isoformat(),
        "declaration_id": declaration_id,
        "dataset_id": dataset_id,
        "frozen_selection_id": frozen_id,
        "winner": winner,
        "winner_model_id": artifacts[winner],
        "eligible_development_strategies": eligible,
        "split": [
            {"first": g[0]["date"], "last": g[-1]["date"], "observations": len(g)}
            for g in (train_rows, cal, selection, audit)
        ],
        "audit_classification": "One-time out-of-fit audit on an old calibration period; not independent final confirmation",
        "development": development,
        "audit": audit_result,
        "benchmarks": {"cash": {"net_pnl": 0, "trade_count": 0}, "always_long": always},
        "winner_minus_always_long_daily_pnl_ci_98_75": bootstrap_daily(differences),
        "acceptance": {
            "independent_confirmation": False,
            "measured_cost_history": False,
            "complete_executable_quotes": False,
            "deployable": False,
        },
        "trial_accounting": {
            "models": len(CANDIDATES),
            "holding_variants": 2,
            "cost_scenarios": 3,
            "no_post_audit_refitting": True,
        },
        "prospective": {
            "status": "PENDING_FRESH_SESSIONS",
            "observations": 0,
            "minimum_dates": 126,
        },
        "limitations": [
            "Fixed survivor cohort; split-only revised prices",
            "Synthetic quote/fill/liquidity scenarios cannot establish attainable net alpha",
            "Base stop and target remain 2%/3%; software exits use next-bar prices",
            "Cost/fill rejection rates are assumptions, not broker estimates",
            "Daily mark drawdown and adverse intratrade marks are separate; no real-money or new paper policy",
        ],
    }
    key = store.put("phase7_report", report, FOLDER)
    forecasts_id = store.put(
        "audit_forecasts", {"rows": audit, "predictions": predictions}, FOLDER
    )
    result = {
        "report_id": key,
        "winner": winner,
        "model_id": artifacts[winner],
        "forecasts_id": forecasts_id,
        "search_id": search_id,
    }
    marker.write_text(json.dumps(result, indent=2), encoding="utf-8")
    out = ROOT / "reports/phase7"
    out.mkdir(parents=True, exist_ok=True)
    # Full ledgers live in immutable artifacts; a compact review copy is public locally.
    for candidate in report["audit"].values():
        for scenario in [
            *candidate["scenarios"].values(),
            candidate["strict_quote_audit"],
        ]:
            scenario.pop("orders", None)
            scenario.pop("trades", None)
            scenario.pop("daily_net_pnl", None)
    report["benchmarks"]["always_long"] = {
        k: v
        for k, v in always.items()
        if k not in {"orders", "trades", "daily_net_pnl"}
    }
    (out / "research-results.json").write_text(
        json.dumps({**result, **report}, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
