"""Run the bounded 32-rule laboratory and freeze a paired AI comparison."""

import gzip
import hashlib
import importlib.util
import json
import sys
import time
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from intelligence.util import canonical, digest, now
from paperpilot import lab_registry as registry
from paperpilot.simulation_rules import RULE_VERSION
from paperpilot.strategy_lab import (
    OVERLAYS,
    SIZES,
    configurations,
    event_category,
    replay,
)

spec = importlib.util.spec_from_file_location(
    "prior_research", ROOT / "scripts/evaluate-mainkraft-walk-forward.py"
)
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)


def interval(values):
    v = np.asarray(values, dtype=float)
    if len(v) < 126:
        return None
    rng = np.random.default_rng(20261117)
    ix = (
        rng.integers(0, len(v) - 19, (2000, (len(v) + 19) // 20))[:, :, None]
        + np.arange(20)
    ).reshape(2000, -1)[:, : len(v)]
    return np.quantile(v[ix].mean(axis=1), [0.00625, 0.99375]).tolist()


def main():
    started = time.monotonic()
    cfgs = configurations()
    assert len(cfgs) == 32
    inputs = {}
    for filename in sorted(prior.ROOT.glob("*.json")):
        if filename.name.startswith(("daily-", "intraday-", "news-2026-")):
            inputs[filename.name] = digest(json.loads(filename.read_text()))
    code_files = [
        "apps/api/paperpilot/strategy_lab.py",
        "apps/api/paperpilot/simulation_rules.py",
        "scripts/run-mainkraft-strategy-lab.py",
        "scripts/evaluate-mainkraft-walk-forward.py",
        "apps/api/paperpilot/scan_models.py",
    ]
    protocol = {
        "version": "strategy-lab-v1",
        "rules": RULE_VERSION,
        "configurations": [asdict(c) for c in cfgs],
        "news_overlays": OVERLAYS,
        "sizes": SIZES,
        "input_manifests": inputs,
        "oos_prediction_sha256": hashlib.sha256((prior.ROOT / "oos-predictions.json.gz").read_bytes()).hexdigest(),
        "configuration_count": 32,
        "overlay_scenarios": 96,
        "capital_scenarios_per_overlay": 3,
        "cost_scenarios": [{"round_trip_floor": .0025, "exit_delay_minutes": 5},
                           {"round_trip_floor": .005, "exit_delay_minutes": 10}],
        "code_hashes": {p: digest((ROOT / p).read_text()) for p in code_files},
        "ceiling": 5000,
        "daily_loss": 50,
        "drawdown_review": 500,
        "selection": "For each calendar month, reset each development portfolio, permit entries on prior 60 sessions, then mature all labels over five embargo sessions. Select net P/L divided by 60 at 25bp costs. Hold choice for month; preserve portfolio and drawdown pauses across test months.",
        "limits": [
            "Previously exposed dates are exploratory",
            "Fixed current-survivor universe",
            "Missing historical news neutral and explicitly counted",
            "Bar fills use next observation. Quote-aware parity requires LEAN audit.",
            "No capital promotion from exploratory results",
        ],
    }
    protocol_id = registry.put("protocol", protocol)
    daily = prior.daily_table()
    bars, audit = prior.intraday_table()
    news, news_audit = prior.news_table()
    calendar = prior.cal
    all_dates = list(calendar)
    events = {}
    raw = {}
    for p in prior.ROOT.glob("news-2026-*.json"):
        obj, _ = prior.snapshot(p.stem)
        for key, a in obj["articles"].items():
            raw[key] = a
    from datetime import datetime
    from datetime import time as daytime

    from intelligence.calendar import EASTERN

    for day in prior.eval_days:
        cutoff = datetime.combine(day, daytime(9, 15), EASTERN)
        known = {}
        for key, a in raw.items():
            made = datetime.fromisoformat(a["created_at"])
            updated = datetime.fromisoformat(a["updated_at"])
            if (
                not cutoff - timedelta(hours=72)
                <= made
                <= cutoff - timedelta(minutes=20)
                or updated > cutoff
            ):
                continue
            for name in set(a.get("symbols") or []) & set(prior.cohort):
                category = event_category(
                    (a["headline"] or "") + " " + (a.get("summary") or "")
                )
                for kind, applies in category.items():
                    if applies:
                        known.setdefault(name, {}).setdefault(kind, []).append(
                            {
                                "source_id": key,
                                "text": a["headline"],
                                "created_at": a["created_at"],
                                "updated_at": a["updated_at"],
                                "url": a.get("url"),
                            }
                        )
        events[day] = known
    panel = {}
    first = date.fromisoformat(prior.plan["training_data_start"])
    for day in all_dates:
        if day < first or day > prior.stop:
            continue
        rows = {}
        for name in prior.cohort:
            row = prior.feature_inputs(daily, day, name)
            if row is None:
                continue
            old = all_dates.index(day)
            recent = all_dates[old - 20 : old]
            if len(recent) != 20 or any(d not in daily[name] for d in recent):
                continue
            observations = [daily[name][d] for d in recent]
            row.update(
                above_mean=row["last_close"]
                > np.mean([v["close"] for v in observations]),
                prior_high=max(float(v["high"]) for v in observations),
                volume_ratio=float(observations[-1]["volume"])
                / np.mean([v["volume"] for v in observations]),
            )
            rows[name] = row
        panel[day] = rows
    with gzip.open(prior.ROOT / "oos-predictions.json.gz", "rt") as file:
        old_predictions = json.load(file)
    for stamp, rows in old_predictions.items():
        day = date.fromisoformat(stamp)
        for r in rows:
            if r["symbol"] in panel.get(day, {}):
                panel[day][r["symbol"]]["prediction"] = r["prediction"]

    # Run all declared portfolios once. Monthly choices use only earlier timeline slices.
    periods = sorted(panel)
    variants = []
    for cfg in cfgs:
        for overlay in OVERLAYS:
            for size in SIZES:
                trial_start = time.monotonic()
                try:
                    result = replay(
                        cfg, overlay, size, periods, calendar, panel, bars, news, events
                    )
                    key = registry.put(
                        "experiment",
                        {
                            "protocol": protocol_id,
                            "configuration": asdict(cfg),
                            "overlay": overlay,
                            "size": size,
                            "elapsed_seconds": time.monotonic() - trial_start,
                            "result": result,
                        },
                    )
                    variants.append((cfg, overlay, size, result, key))
                except Exception as exc:
                    registry.put(
                        "failed_experiment",
                        {
                            "protocol": protocol_id,
                            "configuration": asdict(cfg),
                            "overlay": overlay,
                            "size": size,
                            "error": str(exc),
                        },
                    )
                    raise
        print("Completed", cfg.key, flush=True)
    # Choices score only the development interval. Historical marks are known at each month's boundary.
    folds = []
    for month in sorted({str(d)[:7] for d in prior.eval_days}):
        issuing = [d for d in prior.eval_days if str(d).startswith(month)]
        before = [d for d in periods if d < issuing[0]]
        development = before[-65:-5]
        candidates = []
        for cfg, overlay, size, result, key in variants:
            # No unsupported event selection before event coverage begins.
            if cfg.family == "event" and any(d not in events for d in development):
                continue
            if overlay != "technical" and any(d not in news for d in development):
                continue
            developed = replay(cfg, overlay, size, before[-65:], calendar, panel, bars,
                               news, events, entry_dates=set(development))
            value = developed["summary"]["net_pnl"]
            registry.put("development_window", {"protocol": protocol_id, "month": month,
                         "configuration": asdict(cfg), "overlay": overlay, "size": size,
                         "entries_through": development[-1], "matured_through": before[-1],
                         "result": developed})
            candidates.append(
                (value / len(development), cfg.key, overlay, size, key, cfg)
            )
        pick = min(candidates, key=lambda r: (-r[0], r[1], r[2], r[3]))
        frozen = {
            "month": month,
            "development_first": development[0],
            "development_last": development[-1],
            "first_issue": issuing[0],
            "configuration": asdict(pick[-1]),
            "overlay": pick[2],
            "size": pick[3],
            "development_daily": pick[0],
            "experiment_id": pick[4],
        }
        registry.put("monthly_freeze", frozen)
        folds.append(frozen)
    monthly_choices = {row["month"]: row for row in folds}
    walk_forward = replay(cfgs[0], "technical", 800, prior.eval_days,
                          calendar, panel, bars, news, events, monthly=monthly_choices)
    # Freeze the first test-month candidate before reading its AI comparison.
    nominated = folds[0]
    selected = next(c for c in cfgs if asdict(c) == nominated["configuration"])
    rule = replay(
        selected,
        nominated["overlay"],
        nominated["size"],
        prior.eval_days,
        calendar,
        panel,
        bars,
        news,
        events,
    )
    ai = replay(
        selected,
        nominated["overlay"],
        nominated["size"],
        prior.eval_days,
        calendar,
        panel,
        bars,
        news,
        events,
        ai=True,
    )
    stress = replay(
        selected,
        nominated["overlay"],
        nominated["size"],
        prior.eval_days,
        calendar,
        panel,
        bars,
        news,
        events,
        cost=0.005,
        delay=2,
    )
    ai_stress = replay(selected, nominated["overlay"], nominated["size"], prior.eval_days,
                       calendar, panel, bars, news, events, cost=.005, delay=2, ai=True)
    difference = np.diff(np.r_[0, [r["pnl"] for r in ai["timeline"]]]) - np.diff(
        np.r_[0, [r["pnl"] for r in rule["timeline"]]]
    )
    evaluation_results = {}
    for config, overlay, size, result, record in variants:
        evaluated = replay(config, overlay, size, prior.eval_days, calendar, panel, bars, news, events)
        evaluated["summary"]["daily_pnl_interval_98_75"] = interval(np.diff(np.r_[0, [r["pnl"] for r in evaluated["timeline"]]]))
        stressed = replay(config, overlay, size, prior.eval_days, calendar, panel, bars,
                          news, events, cost=.005, delay=2)
        evaluated["summary"]["stress_and_delay"] = stressed["summary"]
        registry.put("evaluation_stress_scenario", {
            "protocol": protocol_id, "configuration": asdict(config), "overlay": overlay,
            "size": size, "cost": .005, "delay": 2, "result": stressed,
        })
        evaluation_results[record] = evaluated["summary"]
        registry.put("evaluation_scenario", {
            "protocol": protocol_id, "configuration": asdict(config), "overlay": overlay,
            "size": size, "parent_development_record": record, "result": evaluated,
        })
    all_results = sorted(
        [
            {
                "configuration": asdict(c),
                "overlay": o,
                "size": s,
                "record_id": key,
                **evaluation_results[key],
            }
            for c, o, s, r, key in variants
        ],
        key=lambda x: -x["average_daily_pnl"],
    )
    audit_path = registry.LAB / "simulator-audit.json"
    reconciliation = (
        json.loads(audit_path.read_text())
        if audit_path.exists()
        else {"status": "pending"}
    )
    prospective = {
        "status": "not_nominated",
        "reason": "Requires simulator parity, positive cost-stressed development, and $20 average in development",
        "required_new_dates": 126,
        "automatic_promotion": False,
    }
    report = {
        "status": "complete_exploratory",
        "created_at": now(),
        "protocol_id": protocol_id,
        "configuration_count": 32,
        "experiment_count": len(variants),
        "evaluation_cost_scenario_count": len(variants) * 2,
        "compute_seconds": time.monotonic() - started,
        "coverage": audit,
        "news": news_audit,
        "folds": folds,
        "results": all_results,
        "monthly_walk_forward": walk_forward,
        "frozen_comparison": {
            "chosen_before_first_test": nominated,
            "rule": rule,
            "ai": ai,
            "stress_and_delay": stress,
            "ai_stress_and_delay": ai_stress,
            "paired_ai_daily_interval": interval(difference),
        },
        "simulator_audit": reconciliation,
        "prospective": prospective,
        "feasibility": {
            "supported": False,
            "reason": "Previously examined history is development evidence; review stress, drawdown stops and prospective data before making an income claim",
            "approved_capital": 5000,
            "target_average": 20,
        },
        "limitations": protocol["limits"],
    }
    identity = registry.publish(report)
    print("Published", identity, flush=True)
    print(
        "Frozen",
        canonical(report["frozen_comparison"]["chosen_before_first_test"]),
        flush=True,
    )
    print("Rule", canonical(rule["summary"]), flush=True)
    print("AI", canonical(ai["summary"]), flush=True)


if __name__ == "__main__":
    main()
