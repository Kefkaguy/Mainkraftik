"""Preregister once, run 25 fixed candidates, freeze all outcomes including failures."""

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.core import identity, parse_bars
from chart_intelligence.phase7 import forecast_metrics
from chart_intelligence.phase8 import (
    END,
    HORIZONS,
    METHODS,
    VERSION,
    build,
    partition,
    predict,
    train,
)
from chart_intelligence.phase8_evidence import put_compressed, verify
from chart_intelligence.phase8_execution import Costs, interval, replay
from intelligence.util import digest

LOCAL = ROOT / ".local/phase8"
OUT = ROOT / "reports/phase8"


def main():
    boundary = json.loads((OUT / "phase7-freeze.json").read_text())
    verify(boundary)
    if (LOCAL / "complete.json").exists():
        print((LOCAL / "complete.json").read_text())
        return
    sources = [
        *list((ROOT / "apps/api/chart_intelligence").glob("phase8*.py")),
        ROOT / "apps/api/chart_intelligence/phase7.py",
        ROOT / "apps/api/chart_intelligence/core.py",
        ROOT / "apps/api/intelligence/portable_models.py",
        ROOT / "apps/api/requirements.lock.txt",
        Path(__file__),
    ]
    archived = store.put(
        "source_archive",
        {str(p.relative_to(ROOT)): p.read_text() for p in sources},
        LOCAL,
    )
    prereg = {
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "source_archive": archived,
        "phase7_boundary_sha256": identity(boundary),
        "horizons": HORIZONS,
        "methods": METHODS,
        "candidate_count": 25,
        "development_end": END,
        "forbidden_selection_period": "2025-04-03 onward, including all Phase 7 audit and prior evaluation",
        "partitions": {
            "train_last_origin": "2024-04-11",
            "calibration_first": "2024-04-22",
            "calibration_last_origin": "2024-10-03",
            "selection_first": "2024-10-14",
        },
        "purging": "All label ends precede the next partition decision; five-session boundary gaps; no labels beyond development_end",
        "screen": {
            "closed_trades": 30,
            "lower_family_ci_net_pnl_vs_cash": 0,
            "lower_family_ci_vs_risk_matched_passive": 0,
            "open_positions": 0,
        },
        "independent_confirmation": {
            "first_eligible_prediction": "after this declaration and a separate frozen candidate enrollment",
            "minimum_dates": 126,
            "minimum_closed_trades": 100,
            "complete_measured_quotes": True,
            "no_repeated_peeking": True,
            "positive_family_adjusted_net_and_benchmark_difference": True,
            "stress_cost_survival": True,
        },
        "costs": {
            "base": Costs().__dict__,
            "stress": Costs(spread=0.002, slippage=0.001).__dict__,
            "severe": Costs(spread=0.004, slippage=0.002).__dict__,
        },
        "risk": {
            "capital": 600,
            "entry": 25,
            "max_positions": 2,
            "total_cap": 100,
            "daily_loss": 12,
            "stop": 0.02,
            "target": 0.03,
        },
        "selection": "Per horizon, highest family-adjusted daily P/L lower bound among screen survivors; otherwise no strategy nominated",
        "uncertainty": "5000 circular 20-session block resamples; alpha=.05/25, including all no-trade days; descriptive screening only",
        "historical_news": "Excluded: no contemporaneously acquired historical version coverage across this development window",
        "cohort": "Existing six-stock survivor cohort; SPY/XLK daily context. Prior development exposure acknowledged.",
        "execution_authority": False,
        "promotion_authority": False,
    }
    declaration = LOCAL / "preregistration.json"
    if declaration.exists():
        old = json.loads(declaration.read_text())
        if {k: v for k, v in old.items() if k != "created_at"} != {
            k: v for k, v in prereg.items() if k != "created_at"
        }:
            raise ValueError(
                "A different search is already declared; do not reuse its exposed selection"
            )
        prereg = old
    else:
        LOCAL.mkdir(parents=True, exist_ok=True)
        with declaration.open("x") as f:
            json.dump(prereg, f, indent=2)
    declared = store.put("preregistration", prereg, LOCAL)
    (OUT / "preregistration.json").write_text(
        json.dumps(prereg | {"record_id": declared}, indent=2)
    )
    print("Preregistered", declared, flush=True)
    history = ROOT / ".local/mainkraft/history"
    manifests = {
        name: [json.loads(s) for s in (history / name).read_text().splitlines()]
        for name in ("daily.jsonl", "coverage.jsonl")
    }
    daily = {}
    markets = {}
    for name, entries in manifests.items():
        for entry in entries:
            with gzip.open(history / (entry["hash"] + ".json.gz"), "rt") as f:
                raw = json.load(f)
            if digest(raw) != entry["hash"]:
                raise ValueError("Input snapshot hash mismatch")
            if name == "daily.jsonl":
                daily[entry["symbol"]] = {
                    r["session_date"]: r
                    for r in raw["bars"]
                    if r["session_date"] <= END
                }
            else:
                markets[entry["symbol"]] = parse_bars(
                    [r for r in raw["bars"] if str(r[0])[:10] <= END]
                )
    data, rejected = build(daily, markets)
    data_id = put_compressed(
        "datasets",
        {
            "horizons": data,
            "rejected": rejected,
            "sources": manifests,
            "declaration_id": declared,
        },
        LOCAL,
    )
    print(
        "Rows",
        {k: len(v) for k, v in data.items()},
        "rejected",
        len(rejected),
        flush=True,
    )
    results = {}
    model_ids = {}
    forecast_ids = {}
    nominated = {}
    for horizon, rows in data.items():
        tr, cal, selection = partition(rows)
        risk_passive = replay(
            selection,
            [{"expected_return": 1.0, "probability_up": 1.0}] * len(selection),
            markets,
            benchmark=True,
        )
        results[horizon] = {
            "partitions": [
                {"first": g[0]["date"], "last": g[-1]["date"], "rows": len(g)}
                for g in (tr, cal, selection)
            ],
            "risk_matched_passive": risk_passive,
            "cash": {"net_pnl": 0, "net_return": 0, "max_drawdown": 0, "trades": 0},
            "candidates": {},
        }
        survivors = []
        for method in METHODS:
            model = train(method, horizon, tr, cal)
            key = store.put("candidate", model, LOCAL)
            model_ids[horizon + ":" + method] = key
            forecasts = predict(model, selection)
            forecast_ids[horizon + ":" + method] = put_compressed(
                "forecasts",
                {"rows": selection, "forecasts": forecasts, "model_id": key},
                LOCAL,
            )
            scenarios = {
                name: replay(selection, forecasts, markets, Costs(**cost))
                for name, cost in prereg["costs"].items()
            }
            base = scenarios["base"]
            passive = risk_passive["daily_net_pnl"]
            paired = [
                pnl - passive.get(d, 0.0)
                for d, pnl in sorted(base["daily_net_pnl"].items())
            ]
            ci = interval(paired)
            netci = base["daily_mean_net_pnl_family_ci"]
            passed = (
                base["closed_trades"] >= 30
                and base["open_positions"] == 0
                and netci is not None
                and netci[0] > 0
                and ci is not None
                and ci[0] > 0
            )
            if passed:
                survivors.append((netci[0], method))
            results[horizon]["candidates"][method] = {
                "model_id": key,
                "prediction": forecast_metrics(selection, forecasts),
                "optimization": model["model"]["optimization"],
                "scenarios": scenarios,
                "versus_passive_family_ci": ci,
                "strict_quote_audit": replay(
                    selection, forecasts, markets, strict_quotes=True
                ),
                "screen_passed": passed,
                "independent_confirmation": "PENDING_NOT_AVAILABLE",
                "validated_edge": False,
            }
            print(
                horizon,
                method,
                "trades",
                base["closed_trades"],
                "pnl",
                round(base["net_pnl"], 3),
                "screen",
                passed,
                flush=True,
            )
        nominated[horizon] = max(survivors)[1] if survivors else None
    report = {
        "version": VERSION,
        "status": "NO_PROFITABLE_TRADING_ADVANTAGE_VALIDATED",
        "declaration_id": declared,
        "dataset_id": data_id,
        "results": results,
        "nominations": nominated,
        "models": model_ids,
        "forecast_records": forecast_ids,
        "independent_confirmation": {
            "status": "PENDING_FUTURE_DATA",
            "observations": 0,
        },
        "acceptance": {
            "validated_horizons": [],
            "deployment_authorized": False,
            "measured_cost_history": False,
        },
        "limitations": [
            "Development selection data is historically exposed; no independent confirmation available",
            "No measured historical executable quotes, actual borrow or depth. IEX observations are not NBBO or fills",
            "Synthetic rejection/partial/slippage rates; fixed survivor cohort and retrospectively adjusted SIP prices",
            "Macro news is excluded for lack of contemporaneous historical coverage; model complexity is not a validation result",
        ],
        "execution_authority": False,
        "promotion_authority": False,
    }
    report_id = put_compressed("full_report", report, LOCAL)
    for h in results.values():
        for s in [
            h["risk_matched_passive"],
            *[
                s
                for c in h["candidates"].values()
                for s in [*c["scenarios"].values(), c["strict_quote_audit"]]
            ],
        ]:
            for key in ("orders", "trades", "daily_net_pnl"):
                s.pop(key, None)
    report["report_id"] = report_id
    (OUT / "research-results.json").write_text(json.dumps(report, indent=2))
    complete = {
        "report_id": report_id,
        "declaration_id": declared,
        "dataset_id": data_id,
        "nominations": nominated,
    }
    with (LOCAL / "complete.json").open("x") as f:
        json.dump(complete, f, indent=2)
    verify(boundary)
    print(json.dumps(complete, indent=2))


if __name__ == "__main__":
    main()
