"""Compare predeclared Mainkraft policies on the same dates and stock universe."""

import gzip
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx
import numpy as np

sys.path[:0] = ["apps/api"]
from intelligence.calendar import EASTERN, schedule
from intelligence.config import settings
from intelligence.providers import get_json
from intelligence.util import canonical, digest, now
from paperpilot import sentiment
from paperpilot.intraday import BAR
from paperpilot.scan_models import (
    CLASS_FIELDS,
    RETURN_FIELDS,
    choose_rank,
    fit_fold,
    score_fold,
)

ROOT = Path(".local/mainkraft/walk-forward-v1")
plan = json.loads((ROOT / "protocol.json").read_text())
cohort = plan["cohort"]
start, begin, stop, end = (
    date.fromisoformat(plan[x])
    for x in (
        "training_data_start",
        "first_test_date",
        "last_test_date",
        "last_outcome_date",
    )
)
cal = schedule(start - timedelta(days=45), end)
cal_days = list(cal)
eval_days = [d for d in cal_days if begin <= d <= stop]
idx = {d: i for i, d in enumerate(cal_days)}
NOTIONAL = 800
COST = 0.0025
RISK = 12 / NOTIONAL
ARMS = ("technical_only", "news_filtered", "news_ranked", "technical_entry_guards")
RECONSTRUCTION_LIMIT = "All inputs retrieved after these sessions. Historically revised articles are omitted, original Groq judgments unavailable."
FIT_SPEC = "Each fold uses only targets whose fifth session closed before the fold; fit on up to 190 dates and calibrate on the 55 most recent mature dates with a five-date embargo."
GUARD_ADDENDUM = {
    "rank": "Technical net estimate minus 25 bp times the predicted probability of a 2% adverse price move, require resulting score above 5 bp",
    "interpretation": "Compare the full guard package. In a separate quote-aware replay use the last IEX quote at 9:35 from the prior 15 seconds. Reject spread over 0.50% or cost-adjusted margin below 5 bp.",
}
note = ROOT / "guard-addendum.json"
if not note.exists():
    with note.open("x", encoding="utf-8") as file:
        file.write(canonical(GUARD_ADDENDUM) + "\n")
else:
    assert json.loads(note.read_text()) == GUARD_ADDENDUM


def snapshot(which):
    ref = json.loads((ROOT / (which + ".json")).read_text())
    with gzip.open(ROOT / (ref["snapshot"] + ".json.gz"), "rt") as fh:
        obj = json.load(fh)
    if digest(obj) != ref["snapshot"]:
        raise ValueError(f"Broken snapshot integrity: {which}")
    return obj, ref["snapshot"]


def daily_table():
    all_rows = {}
    for s in [*cohort, "SPY", "XLK"]:
        obj, _ = snapshot("daily-" + s)
        all_rows[s] = {
            date.fromisoformat(row["session_date"]): row for row in obj["bars"]
        }
    return all_rows


def intraday_table():
    result = {}
    audits = {}
    for symbol in cohort:
        obj, hash_id = snapshot("intraday-" + symbol)
        by_day = defaultdict(dict)
        for t, o, h, l, c, v in obj["bars"]:
            stamp = datetime.fromisoformat(t)
            session = stamp.astimezone(EASTERN).date()
            n = (stamp - cal[session]["open"]) // BAR
            by_day[session][n] = (o, h, l, c)
        result[symbol] = by_day
        audits[symbol] = {
            "snapshot": hash_id,
            "bars": len(obj["bars"]),
            "missing_bars": len(obj["missing"]),
        }
    return result, audits


def feature_inputs(daily, day, symbol):
    prior = cal_days[idx[day] - 21 : idx[day]]
    if len(prior) != 21:
        return None
    try:
        series = {
            s: np.array([daily[s][t]["close"] for t in prior])
            for s in (symbol, "SPY", "XLK")
        }
        volume = np.array([daily[symbol][t]["volume"] for t in prior])
    except KeyError:
        return None
    price, spy, tech = (series[s] for s in (symbol, "SPY", "XLK"))
    if min(price.min(), spy.min(), tech.min()) <= 0 or volume[:-1].mean() <= 0:
        return None
    ret = price[1:] / price[:-1] - 1
    r1, r5, r10, r20 = (float(price[-1] / price[-1 - n] - 1) for n in (1, 5, 10, 20))
    rr5, rr20 = (float(spy[-1] / spy[-1 - n] - 1) for n in (5, 20))
    v10, v20 = float(ret[-10:].std()), float(ret.std())
    x = [
        r1,
        r5,
        r10,
        r20,
        v10,
        v20,
        float(volume[-1] / volume[:-1].mean()),
        rr5,
        rr20,
        float(tech[-1] / tech[-6] - 1 - rr5),
        r5 - rr5,
    ]
    return {
        "x": x,
        "volatility": v20 * np.sqrt(252),
        "annualizable_volatility": v20 * np.sqrt(252),
        "last_close": float(price[-1]),
        "liquidity": float(np.mean(price[-20:] * volume[-20:])),
    }


def complete_path(bars, day, offset, ending):
    interval = cal[day]
    length = (interval["close"] - interval["open"]) // BAR
    end_index = length - 3 if ending else length - 1
    day_bars = bars.get(day, {})
    if any(i not in day_bars for i in range(offset, end_index + 1)):
        return None
    return [day_bars[i] for i in range(offset, end_index + 1)]


def simulate_session(bars, day, lot):
    origin, anchor = lot["origin"], lot["entry"]
    entering_today = day == origin
    origin_index = idx[origin]
    timed = idx[day] == origin_index + 4
    rows = complete_path(bars, day, 1 if entering_today else 0, timed)
    if rows is None:
        return {"missing_exit_path": True}
    peak_stop = anchor * (1 - lot["stop"])
    daily_stop = lot["mark"] - anchor * RISK
    floor = max(peak_stop, daily_stop)
    stop_reason = "position_stop" if peak_stop >= daily_stop else "daily_loss_trigger"
    target = anchor * (1 + lot["take"])
    for i, (o, h, l, c) in enumerate(rows):
        time_index = i + (1 if entering_today else 0)
        if o <= floor:
            return {
                "exit": o,
                "reason": stop_reason,
                "time_index": time_index,
                "gap": o < floor,
            }
        if o >= target:
            return {
                "exit": target,
                "reason": "target",
                "time_index": time_index,
                "gap": False,
            }
        if timed and i == len(rows) - 1:
            return {"exit": o, "reason": "time", "time_index": time_index, "gap": False}
        if l <= floor:
            return {
                "exit": floor,
                "reason": stop_reason,
                "time_index": time_index,
                "gap": False,
            }
        if h >= target:
            return {
                "exit": target,
                "reason": "target",
                "time_index": time_index,
                "gap": False,
            }
    return {"mark": rows[-1][-1]}


def label(bars, day):
    span = cal_days[idx[day] : idx[day] + 5]
    if len(span) < 5 or span[-1] > end:
        return None
    price = bars.get(day, {}).get(1)
    if price is None:
        return None
    anchor = price[0]
    moves = {}
    for n, key in [(1, "move_1"), (3, "move_3"), (5, "move_5")]:
        sess = span[n - 1]
        exit_index = (cal[sess]["close"] - cal[sess]["open"]) // BAR - 3
        target = bars.get(sess, {}).get(exit_index)
        if not target:
            return None
        moves[key] = target[0] / anchor - 1
    mins = []
    for i, sess in enumerate(span):
        segment = complete_path(bars, sess, 1 if i == 0 else 0, i == 4)
        if segment is None:
            return None
        if i == 4:
            # The timed exit uses the open of this last bar rather than later lows.
            segment = segment[:-1] + [(segment[-1][0],) * 4]
        mins.append(min(bar[2] for bar in segment))
    simulated = {
        "origin": day,
        "entry": anchor,
        "mark": anchor,
        "stop": 0.02,
        "take": 0.03,
    }
    for sess in span:
        outcome = simulate_session(bars, sess, simulated)
        if "exit" in outcome:
            return {
                "net": outcome["exit"] / anchor - 1 - COST,
                "target_hit": outcome["reason"] == "target",
                "adverse_move": min(mins) / anchor - 1 <= -0.02,
                **moves,
            }
        simulated["mark"] = outcome["mark"]
    raise AssertionError("Missing forced exit")


def build_panel(daily, bars):
    panel = {}
    for day in cal_days:
        if not start <= day <= stop:
            continue
        pane = []
        for symbol in cohort:
            row = feature_inputs(daily, day, symbol)
            if row is None or row["last_close"] < 5 or row["liquidity"] < 20_000_000:
                continue
            target = label(bars[symbol], day)
            pane.append(row | {"symbol": symbol, "label": target})
        panel[day] = pane
    return panel


def news_table():
    cache = ROOT / "news-sentiment-by-session.json"
    if cache.exists():
        saved = json.loads(cache.read_text())
        return (
            {date.fromisoformat(d): r for d, r in saved["scores"].items()},
            saved["audit"],
        )
    documents = []
    raw = {}
    for ref in sorted(ROOT.glob("news-2026-*.json")):
        if not ref.stem.removeprefix("news-").count("-") == 2:
            continue
        payload, identity = snapshot(ref.stem)
        documents.append(identity)
        for key, article in payload["articles"].items():
            previous = raw.get(key)
            if not previous or article["updated_at"] >= previous["updated_at"]:
                raw[key] = article
    if not documents:
        raise ValueError("No news evidence")
    by_name = defaultdict(list)
    malformed = 0
    for key, article in raw.items():
        try:
            created = datetime.fromisoformat(article["created_at"])
            known = max(created, datetime.fromisoformat(article["updated_at"]))
        except (ValueError, TypeError):
            malformed += 1
            continue
        for symbol in set(article.get("symbols") or []) & set(cohort):
            by_name[symbol].append((known, created, key))
    refs = {}
    for day in eval_days:
        cutoff = datetime.combine(day, time(7, 30), EASTERN)
        refs[day] = {
            symbol: [
                key
                for known, created, key in sorted(by_name[symbol], reverse=True)
                if cutoff - timedelta(hours=72)
                <= created
                <= cutoff - timedelta(minutes=20)
                and known <= cutoff
            ][:3]
            for symbol in cohort
        }
    ids = sorted(
        {
            ref
            for selections in refs.values()
            for seq in selections.values()
            for ref in seq
        }
    )
    items = [
        {
            "id": ref,
            "text": (
                (raw[ref]["headline"] or "") + ". " + (raw[ref].get("summary") or "")
            )[:1500],
        }
        for ref in ids
    ]
    packages = [{"items": items[i : i + 120]} for i in range(0, len(items), 120)]
    models = ROOT.parent.parent / "models" / "distilroberta-financial-q8"
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--mount",
        f"type=bind,source={models.resolve()},target=/models,readonly",
        "keftrade-intelligence-sentiment:sentiment-v1",
        "python",
        "-c",
        "import sys,json; from app import Batch,score; print(json.dumps([score(Batch.model_validate(r)) for r in json.load(sys.stdin)]))",
    ]
    proc = subprocess.run(
        command, input=json.dumps(packages).encode(), capture_output=True, check=True
    )
    results = json.loads(proc.stdout)
    import hashlib

    probabilities = {}
    for response in results:
        model = response["model"]
        if (
            model["revision"] != sentiment.REVISION
            or model["sha256"]["onnx/model_quantized.onnx"] != sentiment.WEIGHT_HASH
        ):
            raise ValueError("Unapproved offline sentiment model")
        for row in response["scores"]:
            probabilities[row["id"]] = row
    if len(probabilities) != len(items):
        raise ValueError("Missing sentiment outputs")
    for r in items:
        assert (
            probabilities[r["id"]]["text_hash"]
            == hashlib.sha256(r["text"].encode()).hexdigest()
        )
    output = {}
    for day, group in refs.items():
        output[day] = {}
        for symbol, keys in group.items():
            if keys:
                values = {
                    k: float(np.mean([probabilities[i]["scores"][k] for i in keys]))
                    for k in ("positive", "negative", "neutral")
                }
                output[day][symbol] = values | {
                    "veto": values["negative"] >= 0.60
                    and values["negative"] - values["positive"] >= 0.20,
                    "article_ids": keys,
                }
    stats = {
        "distinct_articles_received": len(raw),
        "scored_articles": len(items),
        "bad_timestamps": malformed,
        "candidate_day_cells_with_news": sum(map(len, output.values())),
        "total_cells": len(eval_days) * len(cohort),
        "source_snapshots": documents,
    }
    with cache.open("x", encoding="utf-8") as fh:
        fh.write(
            canonical(
                {"scores": {str(d): s for d, s in output.items()}, "audit": stats}
            )
        )
    print("News data", stats, flush=True)
    return output, stats


def predictions(panel):
    result = {}
    model_directory = ROOT / "folds"
    model_directory.mkdir(exist_ok=True)
    folds = {}
    for day in eval_days:
        key = str(day)[:7]
        if key not in folds:
            mature = [d for d in panel if idx[d] + 4 < idx[day]][-250:]
            training = mature[:-60]
            calibration = mature[-55:]
            assert (
                idx[training[-1]] + 4 < idx[calibration[0]]
                and idx[calibration[-1]] + 4 < idx[day]
            )
            train = [r for d in training for r in panel[d] if r["label"] is not None]
            tune = [r for d in calibration for r in panel[d] if r["label"] is not None]
            artifact = fit_fold(train, tune) | {
                "first_issue": day,
                "train_begin": training[0],
                "train_end": training[-1],
                "calibration_begin": calibration[0],
                "calibration_end": calibration[-1],
                "max_mature_outcome_date": cal_days[idx[calibration[-1]] + 4],
                "training_rows": len(train),
                "calibration_rows": len(tune),
            }
            name = digest(artifact) + ".json"
            if not (model_directory / name).exists():
                with (model_directory / name).open("x", encoding="utf-8") as output:
                    output.write(canonical(artifact))
            folds[key] = artifact | {"artifact_hash": name.removesuffix(".json")}
            print(
                "Fold", key, "trained", len(train), "calibrated", len(tune), flush=True
            )
        rows = panel[day]
        artifact = folds[key]
        result[day] = [
            r | {"prediction": p}
            for r, p in zip(rows, score_fold(artifact, rows), strict=True)
        ]
    return result, folds


def forecast_quality(by_day, folds):
    scores = {"by_head": {}}
    matched = [
        (d, r) for d, rows in by_day.items() for r in rows if r["label"] is not None
    ]
    scores["labeled_forecasts"] = len(matched)
    for key in (*RETURN_FIELDS, *CLASS_FIELDS):
        rows = [
            (
                float(r["prediction"][key]),
                float(r["label"][key]),
                folds[str(d)[:7]][
                    "regression" if key in RETURN_FIELDS else "classification"
                ][key]["baseline"],
            )
            for d, r in matched
        ]
        pred, truth, baseline = np.asarray(rows).T
        if key in RETURN_FIELDS:
            metrics = {
                "mae": float(np.mean(abs(pred - truth))),
                "baseline_mae": float(np.mean(abs(baseline - truth))),
            }
        else:
            metrics = {
                "brier": float(np.mean((pred - truth) ** 2)),
                "baseline_brier": float(np.mean((baseline - truth) ** 2)),
                "mean_prediction": float(np.mean(pred)),
                "observed_rate": float(np.mean(truth)),
            }
            metrics["calibration_bins"] = []
            for low, high in zip(
                np.arange(0, 1, 0.1), np.arange(0.1, 1.1, 0.1), strict=True
            ):
                mask = (pred >= low) & (pred < high)
                if sum(mask):
                    metrics["calibration_bins"].append(
                        {
                            "count": int(sum(mask)),
                            "p": float(pred[mask].mean()),
                            "rate": float(truth[mask].mean()),
                        }
                    )
        scores["by_head"][key] = metrics
    return scores


class OpeningQuotes:
    def __init__(self):
        self.client = httpx.Client(timeout=25, follow_redirects=False)
        self.headers = {
            "APCA-API-KEY-ID": settings.alpaca_key,
            "APCA-API-SECRET-KEY": settings.alpaca_secret,
        }
        self.path = ROOT / "opening-quotes.json"
        self.cache = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.fetch_errors = {}
        self.read_count = 0

    def spread(self, symbol, day):
        key = f"{day}/{symbol}"
        if key not in self.cache:
            instant = cal[day]["open"] + BAR
            args = {
                "feed": "iex",
                "asof": str(day),
                "sort": "desc",
                "limit": 1,
                "start": (instant - timedelta(seconds=15)).isoformat(),
                "end": instant.isoformat(),
            }
            try:
                q = get_json(
                    self.client,
                    f"https://data.alpaca.markets/v2/stocks/{symbol}/quotes",
                    params=args,
                    headers=self.headers,
                )["quotes"]
            except (ValueError, httpx.HTTPError) as exc:
                self.fetch_errors[key] = str(exc)
                return None
            self.read_count += 1
            clean = q[0] if q else None
            self.cache[key] = clean
            if self.read_count % 25 == 0:
                self.path.write_text(canonical(self.cache), encoding="utf-8")
                print("Opening quotes fetched:", self.read_count, flush=True)
        value = self.cache[key]
        if not value or not 0 < value["bp"] <= value["ap"]:
            return None
        instant = cal[day]["open"] + BAR
        stamp = datetime.fromisoformat(value["t"])
        if not timedelta(0) <= instant - stamp <= timedelta(seconds=15):
            return None
        return value["ap"] / value["bp"] - 1

    def finish(self):
        self.path.write_text(canonical(self.cache), encoding="utf-8")
        self.client.close()
        return {
            "date_symbol_requests": len(self.cache),
            "with_quote": sum(v is not None for v in self.cache.values()),
            "failures": self.fetch_errors,
        }


def portfolio(selected, by_day, headlines, bars, cost, opening_quotes=None):
    lot = None
    realized = 0.0
    trades, timeline = [], []
    no_entry_reasons = Counter()
    for day in eval_days:
        started_flat = lot is None
        if started_flat:
            candidates = []
            for r in by_day[day]:
                if r["volatility"] > 0.8:
                    continue
                score = choose_rank(
                    r["prediction"], headlines.get(day, {}).get(r["symbol"]), selected
                )
                if score is not None and selected == "technical_entry_guards":
                    score -= 0.0025 * r["prediction"]["adverse_move"]
                    if score <= 0.0005:
                        score = None
                if score is not None:
                    candidates.append((-score, r["symbol"], r))
            for _, name, r in sorted(candidates, key=lambda q: q[:2]):
                entry = bars[name].get(day, {}).get(1)
                if not entry:
                    no_entry_reasons["opening bar missing"] += 1
                    continue
                trade_cost = cost
                if opening_quotes is not None:
                    spread = opening_quotes.spread(name, day)
                    if spread is None:
                        no_entry_reasons["fresh IEX quote unavailable"] += 1
                        continue
                    trade_cost = max(cost, spread + 0.001)
                    if (
                        spread > 0.005
                        or r["prediction"]["net"] - (trade_cost - COST) <= 0.0005
                    ):
                        no_entry_reasons["IEX spread or net cost gate"] += 1
                        continue
                stop, take = 0.02, 0.03
                if selected == "technical_entry_guards":
                    if abs(entry[0] / r["last_close"] - 1) > 0.01 or r["x"][1] <= 0:
                        no_entry_reasons["opening spike or nonpositive trend"] += 1
                        continue
                    stop = float(np.clip(r["x"][5], 0.01, 0.02))
                    take = float(np.clip(1.5 * r["x"][5], 0.02, 0.04))
                lot = {
                    "origin": day,
                    "symbol": name,
                    "entry": entry[0],
                    "mark": entry[0],
                    "risk": r["prediction"]["adverse_move"],
                    "expected_net": r["prediction"]["net"],
                    "target_probability": r["prediction"]["target_hit"],
                    "stop": stop,
                    "take": take,
                    "cost": trade_cost,
                }
                trades.append(lot)
                break
            if not candidates:
                no_entry_reasons["no eligible signal"] += 1
        if lot:
            outcome = simulate_session(bars[lot["symbol"]], day, lot)
            if "missing_exit_path" in outcome:
                return {
                    "status": "inconclusive",
                    "missing_path": day,
                    "symbol": lot["symbol"],
                }
            if "exit" in outcome:
                pnl = NOTIONAL * (outcome["exit"] / lot["entry"] - 1 - lot["cost"])
                lot.update(outcome | {"exit_day": day, "net_pnl": pnl})
                realized += pnl
                lot = None
            else:
                lot["mark"] = outcome["mark"]
        marked = (
            0
            if lot is None
            else NOTIONAL * (lot["mark"] / lot["entry"] - 1 - lot["cost"])
        )
        timeline.append(
            {
                "date": day,
                "equity_delta": realized + marked,
                "realized": realized,
                "holding": lot is not None,
            }
        )
    closed = [row["net_pnl"] for row in trades if "net_pnl" in row]
    days = np.asarray([row["equity_delta"] for row in timeline])
    change = np.diff(np.r_[0, days])
    wins = sum(v for v in closed if v > 0)
    losses = -sum(v for v in closed if v < 0)
    months = {
        str(day)[:7]: float(
            sum(v for d, v in zip(eval_days, change) if str(d)[:7] == str(day)[:7])
        )
        for day in eval_days
    }
    gross = np.r_[0, days]
    return {
        "status": "complete",
        "positions_opened": len(trades),
        "closed_trades": len(closed),
        "ending_marked_pnl": float(days[-1]),
        "realized_pnl": float(realized),
        "max_drawdown_dollars": float(np.max(np.maximum.accumulate(gross) - gross)),
        "profit_factor": wins / losses if losses else None,
        "winning_trades": sum(v > 0 for v in closed),
        "positive_dates": int(sum(change > 1e-9)),
        "negative_dates": int(sum(change < -1e-9)),
        "zero_dates": int(sum(abs(change) <= 1e-9)),
        "dates_with_20_plus": int(sum(change >= 20)),
        "positive_months": sum(v > 0 for v in months.values()),
        "monthly_net_changes": months,
        "gap_exits": sum(r.get("gap", False) for r in trades),
        "no_entry_reasons": no_entry_reasons,
        "trades": trades,
        "timeline": timeline,
    }


def compare(periods, stressed):
    n = len(eval_days)
    rng = np.random.default_rng(20261117)
    draw = (
        rng.integers(0, n - 19, (2000, (n + 19) // 20))[:, :, None] + np.arange(20)
    ).reshape(2000, -1)[:, :n]

    def interval(x):
        return np.quantile(np.mean(x[draw], axis=1), [0.00625, 0.99375]).tolist()

    def change(v):
        return np.diff(np.r_[0, [r["equity_delta"] for r in v["timeline"]]])

    measures = {}
    base = periods["technical_only"]
    for name, value in periods.items():
        if (
            value["status"] != "complete"
            or base["status"] != "complete"
            or stressed[name]["status"] != "complete"
        ):
            measures[name] = {"verdict": "inconclusive coverage"}
            continue
        cash_ci = interval(change(value))
        compared = interval(change(value) - change(base))
        adequate = n >= 126 and stressed[name]["ending_marked_pnl"] > 0
        result = (
            "exploratory positive evidence"
            if adequate and cash_ci[0] > 0
            else (
                "under cash in this replay"
                if value["ending_marked_pnl"] < 0
                else "inconclusive"
            )
        )
        measures[name] = {
            "net_vs_cash_daily_interval": cash_ci,
            "net_vs_technical_daily_interval": compared,
            "stress_net_pnl": stressed[name]["ending_marked_pnl"],
            "verdict": result,
        }
    return measures


def write_summary(full):
    output = ROOT / "walk-forward-report.json"
    stored = json.loads(canonical(full))
    with output.open("x", encoding="utf-8") as fh:
        json.dump(stored, fh, indent=2)
    for name, v in full["arms"].items():
        print(
            name,
            {k: r for k, r in v.items() if k not in {"trades", "timeline"}},
            full["comparison"][name],
            flush=True,
        )
    print("Forecast quality", full["forecast_quality"], flush=True)


if __name__ == "__main__":
    print(len(eval_days), "evaluation sessions", begin, stop, flush=True)
    # Complete the independent research jobs before looking at performance.
    headlines, news_evidence = news_table()
    daily = daily_table()
    bars, bar_evidence = intraday_table()
    panel = build_panel(daily, bars)
    forecasts, folds = predictions(panel)
    evidence = {str(d): rows for d, rows in forecasts.items()}
    record_path = ROOT / "oos-predictions.json.gz"
    if not record_path.exists():
        with gzip.open(record_path, "xb") as file:
            file.write(canonical(evidence).encode())
    standard = {
        rule: portfolio(rule, forecasts, headlines, bars, COST) for rule in ARMS
    }
    stressed = {
        rule: portfolio(rule, forecasts, headlines, bars, 0.005) for rule in ARMS
    }
    quote_source = OpeningQuotes()
    quote_aware = {
        rule: portfolio(
            rule, forecasts, headlines, bars, COST, opening_quotes=quote_source
        )
        for rule in ARMS
    }
    quote_audit = quote_source.finish()
    write_summary(
        {
            "protocol": plan,
            "guard_addendum": GUARD_ADDENDUM,
            "created_at": now(),
            "fit_spec": FIT_SPEC,
            "session_dates": eval_days,
            "news_provenance_limit": RECONSTRUCTION_LIMIT,
            "news": news_evidence,
            "price_coverage": bar_evidence,
            "folds": {
                month: {
                    k: v
                    for k, v in art.items()
                    if k not in {"mean", "scale", "classification", "regression"}
                }
                for month, art in folds.items()
            },
            "arms": standard,
            "stress": stressed,
            "comparison": compare(standard, stressed),
            "quote_aware": quote_aware,
            "quote_audit": quote_audit,
            "prediction_evidence_hash": digest(evidence),
            "forecast_quality": forecast_quality(forecasts, folds),
        }
    )
