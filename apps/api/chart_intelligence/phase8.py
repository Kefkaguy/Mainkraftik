"""Bounded multi-horizon research; Phase 7 audit labels cannot enter this module."""

from datetime import datetime, timedelta

import numpy as np

from chart_intelligence.core import sequence
from chart_intelligence.phase7 import predict as predict_heads
from chart_intelligence.phase7 import train as fit_heads
from intelligence.calendar import EASTERN, schedule

VERSION = "phase8-horizons-v1"
END = "2025-04-02"
HORIZONS = {"one_hour": 0, "one_day": 1, "two_sessions": 2, "three_sessions": 3, "five_sessions": 5}
METHODS = ("historical_mean", "zero_return", "mainkraft_linear", "mainkraft_histboost", "sequence_adam400")


def feature(daily, symbol, origin, calendar, include_origin):
    days = [d for d in calendar if str(d) < origin or include_origin and str(d) == origin][-21:]
    if len(days) != 21:
        raise ValueError("Incomplete daily context")
    inputs = []
    closes = []
    volumes = []
    for name in (symbol, "SPY", "XLK"):
        if any(str(d) not in daily[name] for d in days):
            raise ValueError("Missing daily stock/market session")
        rows = [daily[name][str(d)] for d in days]
        a = np.asarray([[float(r[k]) for k in ("open", "high", "low", "close", "volume")] for r in rows])
        v = a[:, 4].mean()
        if v <= 0 or not np.isfinite(a).all() or np.min(a[:, :4]) <= 0:
            raise ValueError("Invalid daily observations")
        norm = a.copy()
        norm[:, :4] = np.log(a[:, :4] / a[-1, 3])
        norm[:, 4] = np.log1p(a[:, 4] / v)
        inputs.extend(norm.ravel().tolist())
        closes.append(a[:, 3])
        volumes.append(a[:, 4])
    own, spy, tech = closes
    changes = np.diff(own) / own[:-1]

    def ret(a, n):
        return float(a[-1] / a[-1 - n] - 1)

    numeric = [ret(own, n) for n in (1, 5, 10, 20)] + [
        float(changes[-10:].std()),
        float(changes.std()),
        float(volumes[0][-1] / volumes[0][:-1].mean()),
        ret(spy, 5),
        ret(spy, 20),
        ret(tech, 5) - ret(spy, 5),
        ret(own, 5) - ret(spy, 5),
    ]
    known = calendar[days[-1]]["close"] + timedelta(minutes=15)
    return {
        "x": inputs,
        "numeric": numeric,
        "daily_available_at": known.isoformat(),
        "liquidity": float(np.mean(own * volumes[0])),
        "regime": ("HIGH_VOL" if numeric[5] * np.sqrt(252) > 0.4 else "LOW_VOL")
        + ("_MARKET_UP" if numeric[7] > 0 else "_MARKET_DOWN"),
    }


def build(daily, markets):
    if any(day > END for series in daily.values() for day in series):
        raise ValueError("Phase 7 audit/exposed data forbidden")
    if any(t.astimezone(EASTERN).date().isoformat() > END for series in markets.values() for t in series):
        raise ValueError("Exposed intraday data forbidden")
    all_days = sorted({datetime.fromisoformat(d).date() for series in daily.values() for d in series})
    calendar = schedule(all_days[0], datetime.fromisoformat(END).date())
    sessions = list(calendar)
    output = {key: [] for key in HORIZONS}
    rejections = []
    for symbol, market in sorted(markets.items()):
        for origin in sessions:
            text = str(origin)
            if text < "2022-11-01":
                continue
            for horizon, count in HORIZONS.items():
                try:
                    f = feature(daily, symbol, text, calendar, include_origin=count > 0)
                    if count == 0:
                        decisions = [
                            datetime.combine(origin, datetime.min.time(), EASTERN).replace(hour=h)
                            for h in (12, 14)
                        ]
                    else:
                        decisions = [
                            datetime.combine(origin, datetime.min.time(), EASTERN).replace(hour=20, minute=30)
                        ]
                    for decision in decisions:
                        try:
                            x = list(f["x"])
                            if count == 0:
                                intraday = sequence(market, decision, calendar=calendar)
                                x += intraday["x"]
                                entry = decision
                                exit_time = entry + timedelta(hours=1)
                                available = intraday["latest_feature_available_at"]
                            else:
                                index = sessions.index(origin)
                                if index + count >= len(sessions):
                                    raise ValueError("Label extends into forbidden audit")
                                entry = calendar[sessions[index + 1]]["open"] + timedelta(minutes=5)
                                exit_time = calendar[sessions[index + count]]["close"] - timedelta(minutes=15)
                                available = f["daily_available_at"]
                            if datetime.fromisoformat(available) > decision:
                                raise ValueError("Feature unavailable at issue")
                            times = [
                                t
                                for d in sessions
                                if entry.date() - timedelta(days=1) <= d <= exit_time.date()
                                for t in (
                                    calendar[d]["open"] + timedelta(minutes=5 * i)
                                    for i in range(
                                        (calendar[d]["close"] - calendar[d]["open"]) // timedelta(minutes=5)
                                    )
                                )
                                if entry <= t <= exit_time
                            ]
                            if not times or any(t not in market for t in times):
                                raise ValueError("Missing executable path candle")
                            entry_price = float(market[entry][0])
                            last = float(market[exit_time][0])
                            lows = [float(market[t][2]) for t in times if t < exit_time]
                            event = 0
                            for t in times:
                                if t >= exit_time:
                                    break
                                o, hi, lo, c, v = market[t]
                                if lo <= entry_price * 0.98:
                                    break
                                if hi >= entry_price * 1.03:
                                    event = 1
                                    break
                            output[horizon].append(
                                {
                                    **f,
                                    "x": x,
                                    "symbol": symbol,
                                    "date": text,
                                    "decision": decision.isoformat(),
                                    "entry_time": entry.isoformat(),
                                    "exit_time": exit_time.isoformat(),
                                    "label_end": exit_time.isoformat(),
                                    "latest_feature_available_at": available,
                                    "horizon": horizon,
                                    "y": last / entry_price - 1,
                                    "downside": min(0, min(lows) / entry_price - 1),
                                    "target_first": event,
                                }
                            )
                        except ValueError as exc:
                            rejections.append(
                                {"symbol": symbol, "date": text, "horizon": horizon, "reason": str(exc)}
                            )
                except (KeyError, ValueError) as exc:
                    rejections.append(
                        {"symbol": symbol, "date": text, "horizon": horizon, "reason": str(exc)}
                    )
    return {k: sorted(v, key=lambda r: (r["decision"], r["symbol"])) for k, v in output.items()}, rejections


def partition(rows):
    if any(
        r["date"] > END or datetime.fromisoformat(r["label_end"]).astimezone(EASTERN).date().isoformat() > END
        for r in rows
    ):
        raise ValueError("Exposed labels forbidden")
    starts = ("2024-04-22", "2024-10-14")
    tr = [r for r in rows if r["date"] <= "2024-04-11" and r["label_end"][:10] < starts[0]]
    cal = [r for r in rows if starts[0] <= r["date"] <= "2024-10-03" and r["label_end"][:10] < starts[1]]
    select = [r for r in rows if starts[1] <= r["date"] <= END]
    if any(len({r["date"] for r in group}) < 40 for group in (tr, cal, select)):
        raise ValueError("Insufficient partition dates")
    return tr, cal, select


def train(method, horizon, rows, calibration):
    if method not in METHODS or horizon not in HORIZONS:
        raise ValueError("Undeclared candidate")
    if max(r["label_end"] for r in rows) >= min(r["decision"] for r in calibration):
        raise ValueError("Unpurged training labels")
    if any(r["horizon"] != horizon or r["date"] > END for r in [*rows, *calibration]):
        raise ValueError("Wrong horizon or exposed data")
    artifact = fit_heads(method, rows, calibration)
    return {
        "version": VERSION,
        "method": method,
        "horizon": horizon,
        "model": artifact,
        "execution_authority": False,
        "promotion_authority": False,
    }


def predict(artifact, rows):
    if any(r.get("horizon", artifact["horizon"]) != artifact["horizon"] for r in rows):
        raise ValueError("Forecast horizon mismatch")
    return predict_heads(artifact["model"], rows)
