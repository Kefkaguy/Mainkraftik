"""Bounded rule families, overlays and observation-based shared portfolio replay."""

import re
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from paperpilot.simulation_rules import bar_trigger, can_enter, daily_change

SIZES = (800, 1500, 2500)
OVERLAYS = ("technical", "veto", "ranked")


@dataclass(frozen=True)
class Configuration:
    family: str
    variant: int
    volatility_exit: bool
    horizon: int

    @property
    def key(self):
        return f"{self.family}-{self.variant}-{int(self.volatility_exit)}-{self.horizon}"


def configurations():
    return [
        Configuration(family, variant, exits, horizon)
        for family in ("momentum", "pullback", "breakout", "event")
        for variant in (0, 1)
        for exits in (False, True)
        for horizon in (1, 5)
    ]


def event_category(text):
    return {
        "earnings_guidance": bool(
            re.search(r"\b(earnings|quarterly results|guidance|outlook)\b", text, re.I)
        ),
        "acquisition_contract": bool(re.search(r"\b(acqui\w*|merger|contract|agreement)\b", text, re.I)),
    }


def rule_score(cfg, row, news, events):
    x = row["x"]
    if row["volatility"] > 0.8 or row["last_close"] < 5 or row["liquidity"] < 20_000_000:
        return None
    if cfg.family == "momentum":
        eligible = x[10] > 0 and row["above_mean"] and (cfg.variant == 0 or x[3] - x[8] > 0)
        score = x[10]
    elif cfg.family == "pullback":
        lower, upper = (-0.02, -0.005) if cfg.variant == 0 else (-0.04, -0.02)
        eligible = x[3] > 0 and row["above_mean"] and lower <= x[0] < upper
        score = -x[0]
    elif cfg.family == "breakout":
        eligible = cfg.variant == 0 or row["volume_ratio"] > 1
        score = x[10]
    else:
        category = "earnings_guidance" if cfg.variant == 0 else "acquisition_contract"
        eligible = x[3] > 0 and row["above_mean"] and bool(events.get(category))
        score = x[10]
    return float(score) if eligible else None


def summarize(timeline, trades, skipped, paused):
    equity = np.array([r["pnl"] for r in timeline])
    changes = np.diff(np.r_[0, equity])
    closed = [t for t in trades if "pnl" in t]
    wins = sum(t["pnl"] for t in closed if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
    values = np.r_[0, equity]
    monthly = {}
    for row, change in zip(timeline, changes, strict=True):
        key = str(row["date"])[:7]
        monthly[key] = monthly.get(key, 0) + float(change)
    streak = longest = 0
    for value in changes:
        streak = streak + 1 if value < 0 else 0
        longest = max(streak, longest)
    attribution = {}
    for t in closed:
        attribution[t["symbol"]] = attribution.get(t["symbol"], 0) + t["pnl"]
    return {
        "net_pnl": float(equity[-1]) if len(equity) else 0,
        "average_daily_pnl": float(changes.mean()) if len(changes) else 0,
        "max_drawdown": float(np.max(np.maximum.accumulate(values) - values)),
        "profit_factor": wins / losses if losses else None,
        "entries": len(trades),
        "closed": len(closed),
        "costs": sum(t.get("fees", 0) for t in trades),
        "turnover": sum(t["notional"] + t.get("exit_value", 0) for t in trades),
        "dates": len(timeline),
        "effective_20_session_blocks": len(timeline) / 20,
        "positive_dates": int(sum(changes > 1e-8)),
        "zero_dates": int(sum(abs(changes) <= 1e-8)),
        "days_at_20": int(sum(changes >= 20)),
        "longest_losing_streak": longest,
        "positive_months": sum(v > 0 for v in monthly.values()),
        "months": monthly,
        "stock_pnl": attribution,
        "paused_at_drawdown": paused,
        "skipped": skipped,
    }


def replay(
    cfg, overlay, notional, dates, calendar, panel, bars, news, events, cost=0.0025, delay=1, ai=False, monthly=None, entry_dates=None
):
    cash = 5000.0
    lots = {}
    peak = 5000.0
    paused = False
    intraday_drawdown = 0.0
    trades, timeline = [], []
    skipped = 0
    all_days = list(calendar)
    day_index = {d: i for i, d in enumerate(all_days)}
    for day in dates:
        if monthly is not None:
            choice = monthly[str(day)[:7]]
            cfg = Configuration(**choice["configuration"])
            overlay, notional = choice["overlay"], choice["size"]
        opened = {t["symbol"] for t in trades if t["entry_date"] == day}
        opening_equity = cash
        # First verified observation of the regular session, including overnight gaps.
        missing = False
        for name, lot in lots.items():
            value = bars[name].get(day, {}).get(0)
            if value is None:
                missing = True
                opening_equity += lot["qty"] * lot["mark"]
            else:
                opening_equity += lot["qty"] * value[0]
        count = (calendar[day]["close"] - calendar[day]["open"]) // timedelta(minutes=5)
        last_equity = opening_equity
        session_halted = False
        prepared = []
        for name, row in panel.get(day, {}).items():
            n = news.get(day, {}).get(name)
            score = rule_score(cfg, row, n, events.get(day, {}).get(name, {}))
            if score is None or (overlay == "veto" and n and n["veto"]):
                continue
            if overlay == "ranked" and n:
                score += .0015 * (n["positive"] - n["negative"])
            if ai:
                pred = row.get("prediction")
                if not pred or pred["net"] <= .0005:
                    continue
                score = pred["net"] - .0025 * pred["adverse_move"]
            prepared.append((name, row, score))
        for minute in range(count):
            # Process pending exits at the first subsequent executable open.
            for name, lot in list(lots.items()):
                tick = bars[name].get(day, {}).get(minute)
                if not tick:
                    continue
                lot["mark"] = tick[0]
                if lot["pending"] is not None and (day, minute) >= lot["pending"]:
                    value = lot["qty"] * tick[0]
                    fee = value * cost / 2
                    cash += value - fee
                    lot["trade"].update(
                        pnl=value - lot["notional"] - lot["trade"]["fees"] - fee,
                        exit_value=value,
                        exit_date=day,
                        exit_index=minute,
                        fees=lot["trade"]["fees"] + fee,
                        exit_reason=lot["reason"],
                    )
                    del lots[name]
            equity = cash + sum(item["qty"] * item["mark"] for item in lots.values())
            peak = max(peak, equity)
            dd = peak - equity
            intraday_drawdown = max(intraday_drawdown, dd)
            paused = paused or dd >= 500
            daily = daily_change(equity, opening_equity)
            candidates = []
            stale_inventory = any(not bars[name].get(day, {}).get(minute) for name in lots)
            if not paused and not session_halted and not stale_inventory and (entry_dates is None or day in entry_dates) and ((cfg.family == "breakout" and 1 <= minute <= 18) or minute == 1):
                for name, row, score in prepared:
                    if name in lots or name in opened or missing:
                        continue
                    tick = bars[name].get(day, {}).get(minute)
                    if not tick or (cfg.family == "breakout" and tick[0] <= row["prior_high"]):
                        continue
                    candidates.append((-score, name, row, tick))
            for _, name, row, tick in sorted(candidates, key=lambda v: v[:2]):
                exposure = sum(item["qty"] * item["mark"] for item in lots.values())
                if paused or not can_enter(cash, exposure, 0, notional, len(lots), daily, 50, dd, 500):
                    skipped += 1
                    continue
                span = all_days[day_index[day] : day_index[day] + cfg.horizon]
                if len(span) != cfg.horizon:
                    continue
                stop = float(np.clip(row["x"][5], 0.01, 0.02)) if cfg.volatility_exit else 0.02
                take = float(np.clip(1.5 * row["x"][5], 0.02, 0.04)) if cfg.volatility_exit else 0.03
                fee = notional * cost / 2
                if cash < notional + fee:
                    continue
                cash -= notional + fee
                t = {
                    "symbol": name,
                    "entry_date": day,
                    "notional": notional,
                    "entry_index": minute,
                    "entry": tick[0],
                    "fees": fee,
                }
                trades.append(t)
                lots[name] = {
                    "qty": notional / tick[0],
                    "notional": notional,
                    "entry": tick[0],
                    "expiry": span[-1],
                    "mark": tick[0],
                    "pending": None,
                    "reason": None,
                    "stop": stop,
                    "take": take,
                    "trade": t,
                }
                opened.add(name)
                equity = cash + sum(item["qty"] * item["mark"] for item in lots.values())
                daily = daily_change(equity, opening_equity)
            # Observe all lows together for a conservative portfolio loss test.
            worst = cash
            for name, lot in lots.items():
                tick = bars[name].get(day, {}).get(minute)
                worst += lot["qty"] * (tick[2] if tick else lot["mark"])
            intraday_drawdown = max(intraday_drawdown, peak - worst)
            paused = paused or peak - worst >= 500
            forced = daily_change(worst, opening_equity) <= -50 or paused
            session_halted = session_halted or forced
            for name, lot in lots.items():
                tick = bars[name].get(day, {}).get(minute)
                if tick is None:
                    continue
                timed = day >= lot["expiry"] and minute >= count - 4
                trigger = bar_trigger(*tick[:3], lot["entry"], lot["stop"], lot["take"], timed)
                if lot["pending"] is None and (trigger or forced):
                    # Next executable open. At least one observation of delay.
                    next_index = minute + delay
                    if next_index < count:
                        lot["pending"] = (day, next_index)
                    else:
                        later = all_days[day_index[day] + 1 : day_index[day] + 2]
                        lot["pending"] = (later[0], 0) if later else (day, count)
                    lot["reason"] = "portfolio_risk" if forced else trigger[1]
                lot["mark"] = tick[3]
            last_equity = cash + sum(item["qty"] * item["mark"] for item in lots.values())
            peak = max(peak, last_equity)
            paused = paused or peak - last_equity >= 500
        timeline.append({"date": day, "pnl": last_equity - 5000, "open_positions": len(lots), "cash": cash})
    summary = summarize(timeline, trades, skipped, paused)
    summary["end_of_day_drawdown"] = summary["max_drawdown"]
    summary["max_drawdown"] = max(intraday_drawdown, summary["max_drawdown"])
    summary["fraction_days_at_20"] = summary["days_at_20"] / len(dates) if dates else 0
    return {
        "summary": summary,
        "timeline": timeline,
        "trades": trades,
        "open_positions": list(lots),
    }
