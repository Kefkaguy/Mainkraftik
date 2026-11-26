
"""Frozen exploratory evaluation. Run from the project root after gathering evidence."""

import gzip
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import optuna
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from intelligence.calendar import schedule
from intelligence.portable_models import fit, predict
from intelligence.util import canonical, digest, now
from paperpilot.config import SYMBOLS
from paperpilot.intraday import COST, DEFAULT_EXIT, EXIT_PAIRS, index_bars, simulate
from paperpilot.strategy import GROUPS, POLICY, STRATEGIES, features

ROOT = Path(".local/mainkraft")
optuna.logging.set_verbosity(optuna.logging.WARNING)


def load_evidence(manifest):
    entries = {row["symbol"]: row for row in map(json.loads, (ROOT / "history" / manifest).read_text().splitlines())}
    data = {}
    for symbol, row in entries.items():
        with gzip.open(ROOT / "history" / (row["hash"] + ".json.gz"), "rt") as file:
            snapshot = json.load(file)
        if digest(snapshot) != row["hash"]:
            raise ValueError(f"Snapshot hash mismatch: {symbol}")
        data[symbol] = snapshot
    return data, entries


def save(name, value):
    target = ROOT / "experiments" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as file:
        file.write(canonical(value) + "\n")


def interval(values, level=0.9875):
    values = np.asarray(values)
    if len(values) < 126:
        return None
    rng = np.random.default_rng(20261117)
    starts = rng.integers(0, len(values) - 19, (2000, (len(values) + 19) // 20))
    indices = (starts[:, :, None] + np.arange(20)).reshape(2000, -1)[:, :len(values)]
    tail = (1 - level) / 2
    return np.quantile(values[indices].mean(axis=1), [tail, 1 - tail]).tolist()


def summarize(y, mean, probability, lower, upper):
    p = np.clip(probability, 1e-6, 1 - 1e-6)
    up = y > 0
    return {
        "observations": len(y),
        "mae": float(np.mean(abs(y - mean))),
        "brier": float(np.mean((up - p) ** 2)),
        "log_loss": float(np.mean(-np.where(up, np.log(p), np.log(1 - p)))),
        "coverage": float(np.mean((y >= lower) & (y <= upper))),
        "calibration": [{"count": int(mask.sum()), "forecast": float(p[mask].mean()),
                         "actual": float(up[mask].mean())}
                        for i in range(10) if (mask := (p >= i / 10) & (p < (i + 1) / 10)).any()],
    }


def by_date(rows, values):
    groups = {}
    for row, v in zip(rows, values, strict=True):
        groups.setdefault(row["date"], []).append(v)
    return [float(np.mean(groups[d])) for d in sorted(groups)]


def params(trial, family):
    if family == "linear":
        return {"alpha": trial.suggest_categorical("alpha", [10, 50, 100, 300, 1000]),
                "C": trial.suggest_categorical("C", [0.01, 0.05, 0.1, 0.5, 1])}
    return {"iterations": trial.suggest_categorical("iterations", [25, 50, 75]),
            "leaves": trial.suggest_categorical("leaves", [4, 8, 12]),
            "learning_rate": trial.suggest_categorical("learning_rate", [0.03, 0.07]),
            "l2": trial.suggest_categorical("l2", [1, 5]),
            "minimum_leaf": trial.suggest_categorical("minimum_leaf", [40, 90])}


def research(strategy, panel, calendar, bars, run):
    horizon = STRATEGIES[strategy]["horizon"]
    days = list(calendar)
    day_index = {d: i for i, d in enumerate(days)}
    # The common calendar is frozen before any model or exit selection.
    dates = sorted({r["date"] for r in panel})
    first, second = int(len(dates) * .6), int(len(dates) * .8)
    bounds = {d: i for i, d in enumerate(dates)}
    train_cutoff, evaluation_cutoff = dates[first], dates[second]

    def label(rows, exits):
        result = []
        for row in rows:
            index = day_index[row["date"]]
            span = days[index:index + horizon]
            if len(span) != horizon:
                continue
            outcome = simulate(bars[row["symbol"]], [calendar[d] for d in span], *exits)
            if outcome:
                result.append(row | {"y": outcome["net_return"], "end": span[-1]})
        return result

    origins = [r for r in panel if bounds[r["date"]] < first - 5
               and days[min(day_index[r["date"]] + horizon - 1, len(days) - 1)] < train_cutoff]
    default_returns = {(r["date"], r["symbol"]): r["y"] for r in label(origins, DEFAULT_EXIT)}
    exit_results = []
    for exits in EXIT_PAIRS:
        paired = label(origins, exits)
        improvement = by_date(paired, [r["y"] - default_returns[r["date"], r["symbol"]] for r in paired])
        ci = interval(improvement, .99375)
        exit_results.append({"pair": exits, "net_improvement": float(np.mean(improvement)),
                             "interval": ci, "days": len(improvement)})
    supported = [row for row in exit_results if row["interval"] and row["interval"][0] > 0]
    exits = max(supported, key=lambda r: r["net_improvement"])["pair"] if supported else DEFAULT_EXIT
    save(f"{run}-{strategy}-exits.json", {"tests": exit_results, "selected": exits, "scope": "training only"})

    train = label(origins, exits)
    calibration = label([r for r in panel if first <= bounds[r["date"]] < second - 5
                         and days[min(day_index[r["date"]] + horizon - 1, len(days) - 1)] < evaluation_cutoff], exits)
    cal_days = sorted({r["date"] for r in calibration})
    mid = len(cal_days) // 2
    tune_cal = [r for r in calibration if r["date"] < cal_days[mid - 5] and r["end"] < cal_days[mid]]
    tune_test = [r for r in calibration if r["date"] >= cal_days[mid]]
    tune_y = np.asarray([r["y"] for r in tune_test])
    columns = list(range(11))
    artifacts = {}
    for family in ("linear", "histboost"):
        def objective(trial):
            parameters = params(trial, family)
            try:
                model = fit(train, tune_cal, columns, family, parameters, horizon)
                mean, prob, lo, hi = predict(model, tune_test)
                metrics = summarize(tune_y, mean, prob, lo, hi)
                score = metrics["mae"] + metrics["brier"]
                save(f"{run}-{strategy}-{family}-{trial.number}.json",
                     {"parameters": parameters, "development_metrics": metrics, "score": score})
                return score
            except Exception as exc:
                save(f"{run}-{strategy}-{family}-{trial.number}.failed.json",
                     {"parameters": parameters, "error": str(exc)})
                raise

        study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=20261117))
        study.optimize(objective, n_trials=5, catch=(ValueError,))
        artifacts[family] = fit(train, calibration, columns, family, study.best_params, horizon)
        for group, excluded in GROUPS.items():
            reduced = [c for c in columns if c not in excluded]
            try:
                model = fit(train, tune_cal, reduced, family, study.best_params, horizon)
                metrics = summarize(tune_y, *predict(model, tune_test))
                save(f"{run}-{strategy}-{family}-without-{group}.json", metrics)
            except ValueError as exc:
                save(f"{run}-{strategy}-{family}-without-{group}.failed.json", {"error": str(exc)})
    # 10 tuning fits, 8 ablations and 2 final fits. Archive before opening evaluation.
    save(f"{run}-{strategy}-frozen.json",
         {"models": artifacts, "exits": exits, "train_end_exclusive": train_cutoff,
          "evaluation_start": evaluation_cutoff, "execution_candidate": "linear", "challenger": "histboost"})
    test = label([r for r in panel if bounds[r["date"]] >= second], exits)
    y = np.asarray([r["y"] for r in test])
    train_y = np.asarray([r["y"] for r in train])
    base_mean = np.repeat(train_y.mean(), len(y))
    base_prob = np.repeat((train_y > 0).mean(), len(y))
    lower, upper = (np.repeat(v, len(y)) for v in np.quantile(train_y, [.1, .9]))
    metrics = {"baseline": summarize(y, base_mean, base_prob, lower, upper)}
    for family, artifact in artifacts.items():
        mean, prob, lo, hi = predict(artifact, test)
        result = summarize(y, mean, prob, lo, hi)
        result["by_stock"] = {}
        result["by_year"] = {}
        for field, keys in (("by_stock", [r["symbol"] for r in test]),
                            ("by_year", [str(r["date"].year) for r in test])):
            for key in sorted(set(keys)):
                mask = np.asarray([k == key for k in keys])
                result[field][key] = summarize(y[mask], mean[mask], prob[mask], lo[mask], hi[mask])
        result["mae_improvement_ci"] = interval(by_date(test, abs(y - base_mean) - abs(y - mean)))
        result["brier_improvement_ci"] = interval(by_date(test, (y > 0) * 0.0 + ((y > 0) - base_prob) ** 2
                                                              - ((y > 0) - prob) ** 2))
        daily = {}
        for row, actual, m, p in zip(test, y, mean, prob, strict=True):
            daily.setdefault(row["date"], [])
            if m > .0005 and p >= STRATEGIES[strategy]["probability"] and row["volatility"] <= .8:
                daily[row["date"]].append((m, row["symbol"], actual))
        returns = [25 * sum(item[2] for item in sorted(items, key=lambda x: (-x[0], x[1]))
                            [:STRATEGIES[strategy]["limit"]]) for items in daily.values()]
        equity = np.r_[0, np.cumsum(returns)]
        result.update(exploratory_selected_net_dollars=float(sum(returns)),
                      exploratory_no_entry_dates=sum(not rows for rows in daily.values()),
                      exploratory_max_drawdown_dollars=float(np.max(np.maximum.accumulate(equity) - equity)),
                      exploratory_daily_net_ci=interval(returns),
                      qualification_scope="Price-only policy diagnostic; no historical news replay or broker fills")
        metrics[family] = result
    evaluation_dates = len({r["date"] for r in test})
    return {"horizon": horizon, "stop": exits[0], "take": exits[1], "model": artifacts["linear"],
            "challenger": artifacts["histboost"], "evaluation": metrics, "evaluation_dates": evaluation_dates,
            "evaluation_blocks_approx": evaluation_dates / 20,
            "status": "inconclusive", "feature_limits": "Retrospective SIP revisions and fixed current-survivor cohort. News not trained historically."}


def main():
    raw, intraday = load_evidence("coverage.jsonl")
    daily, inputs = load_evidence("daily.jsonl")
    calendar = schedule(date(2022, 11, 1), date(2026, 11, 13))
    panel = []
    for d in calendar:
        for symbol in SYMBOLS:
            try:
                row = features(d, daily[symbol]["bars"], daily["SPY"]["bars"], daily["XLK"]["bars"])
            except ValueError:
                continue
            panel.append(row | {"date": d, "symbol": symbol})
    bars = {symbol: index_bars(raw[symbol]) for symbol in SYMBOLS}
    run = now().strftime("%Y%m%dT%H%M%S%fZ")
    config = {"protocol": POLICY, "dataset": {"intraday": intraday, "daily": inputs}, "trained_at": now(),
              "historical_independence": "exploratory", "training_universe": SYMBOLS}
    save(f"{run}-protocol.json", config)
    with threadpool_limits(limits=1):
        config["strategies"] = {kind: research(kind, panel, calendar, bars, run) for kind in STRATEGIES}
    config["bundle_id"] = digest(config)
    save(f"{run}-bundle.json", config)
    print(config["bundle_id"], flush=True)
    for name, strategy in config["strategies"].items():
        print(name, strategy["evaluation_dates"], strategy["stop"], strategy["take"],
              {kind: {k: round(v[k], 6) for k in ("mae", "brier")} for kind, v in strategy["evaluation"].items()},
              flush=True)


if __name__ == "__main__":
    main()
