"""Conservative research opportunities. Assumed costs never become measured fills."""

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

import numpy as np


@dataclass(frozen=True)
class Execution:
    capital: float = 600
    notional: float = 25
    total_cap: float = 100
    daily_loss: float = 12
    max_positions: int = 2
    stop: float = 0.02
    target: float = 0.03
    spread: float = 0.001
    slippage_per_leg: float = 0.0005
    fee_per_leg: float = 0.0001
    participation: float = 0.001
    reject_fraction: float = 0.05
    partial_fraction: float = 0.10
    probability_floor: float = 0.55
    net_margin: float = 0.0005

    @property
    def round_trip(self):
        return self.spread + 2 * (self.slippage_per_leg + self.fee_per_leg)


def bootstrap_daily(values, seed=20261118):
    values = np.asarray(values, dtype=float)
    if len(values) < 40:
        return None
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(values), (2000, (len(values) + 19) // 20))
    ix = ((starts[:, :, None] + np.arange(20)) % len(values)).reshape(2000, -1)[:, : len(values)]
    return np.quantile(values[ix].mean(1), [0.00625, 0.99375]).tolist()


def quote_at(quotes, symbol, instant):
    q = quotes.get((symbol, instant.isoformat()))
    if not q:
        return None
    try:
        age = (instant - datetime.fromisoformat(q["source_time"])).total_seconds()
        acquired = datetime.fromisoformat(q["acquired_at"])
        if not 0 <= age <= 15 or acquired > instant or not 0 < q["bid"] <= q["ask"]:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return q


def replay(
    rows,
    predictions,
    execution=Execution(),
    mode="STRICT_QUOTES",
    quotes=None,
    holding_bars=6,
    benchmark=False,
):
    if mode not in {"STRICT_QUOTES", "ASSUMPTION_ONLY"}:
        raise ValueError("Unknown execution evidence")
    if (
        not 0 < execution.notional <= 25
        or execution.total_cap > 600
        or execution.daily_loss > 12
        or holding_bars not in (3, 6)
    ):
        raise ValueError("Research risk limits exceeded")
    if len(rows) != len(predictions):
        raise ValueError("Prediction alignment mismatch")
    quotes = quotes or {}
    reasons = Counter()
    orders = []
    trades = []
    cash = execution.capital
    active = []
    used = set()
    daily = {}
    curve = []
    last_day = None
    opening = cash
    pairs = sorted(
        zip(rows, predictions), key=lambda rp: (rp[0]["decision"], -rp[1]["expected_return"], rp[0]["symbol"])
    )

    def settle(now):
        nonlocal cash, active
        keep = []
        for trade in active:
            if trade["exit_time"] <= now:
                cash += trade["notional"] + trade["net_pnl"]
                trades.append(trade)
            else:
                keep.append(trade)
        active = keep

    for row, p in pairs:
        now = datetime.fromisoformat(row["decision"])
        day = row["date"]
        settle(now)
        if day != last_day:
            if last_day is not None:
                if active:
                    raise ValueError("Unexpected overnight research position")
                daily[last_day] = cash - opening
                curve.append(cash)
            last_day = day
            opening = cash
        key = (day, row["symbol"])
        if not benchmark and p["probability_up"] < execution.probability_floor:
            reasons["PREDICTION_CONFIDENCE"] += 1
            continue
        if not benchmark and p["expected_return"] <= execution.round_trip + execution.net_margin:
            reasons["EXPECTED_RETURN_AFTER_ASSUMED_COSTS"] += 1
            continue
        if cash + sum(t["notional"] for t in active) - opening <= -execution.daily_loss:
            reasons["DAILY_LOSS"] += 1
            continue
        if key in used:
            reasons["NO_REENTRY"] += 1
            continue
        if (
            len(active) >= execution.max_positions
            or sum(t["notional"] for t in active) + execution.notional > execution.total_cap
            or cash < execution.notional
        ):
            reasons["CAPITAL_OR_POSITION_LIMIT"] += 1
            continue
        if row.get("known_dollar_volume", 0) * execution.participation < execution.notional:
            reasons["LIQUIDITY"] += 1
            continue
        path = row.get("path", [])[:holding_bars]
        if len(path) < holding_bars:
            reasons["INCOMPLETE_EXIT_PATH"] += 1
            continue
        q = quote_at(quotes, row["symbol"], now)
        if mode == "STRICT_QUOTES" and q is None:
            reasons["EXECUTABLE_ENTRY_QUOTE_UNAVAILABLE"] += 1
            continue
        # Deterministic exchange scenarios use the same draws for every model.
        draw = int(hashlib.sha256((row["symbol"] + row["decision"]).encode()).hexdigest()[:8], 16) / 2**32
        if mode == "ASSUMPTION_ONLY" and draw < execution.reject_fraction:
            used.add(key)
            orders.append({"symbol": row["symbol"], "decision": row["decision"], "state": "REJECTED"})
            reasons["SIMULATED_ORDER_REJECTION"] += 1
            continue
        fraction = (
            0.5
            if mode == "ASSUMPTION_ONLY" and draw < execution.reject_fraction + execution.partial_fraction
            else 1.0
        )
        notional = execution.notional * fraction
        raw_entry = q["ask"] if q else path[0][1] * (1 + execution.spread / 2)
        entry = raw_entry * (1 + execution.slippage_per_leg)
        exit_index = holding_bars - 1
        exit_reason = "TIME"
        signal_seen = False
        for i, bar in enumerate(path):
            stamp, o, h, low, c, v = bar
            if signal_seen:
                exit_index = i
                break
            # Software observes a completed five-minute bar, then exits at the
            # next available bar open; no perfect intrabar stop fills.
            if low / entry - 1 <= -execution.stop or h / entry - 1 >= execution.target:
                exit_reason = "STOP" if low / entry - 1 <= -execution.stop else "TARGET"
                signal_seen = True
        bar = path[exit_index]
        exit_time = datetime.fromisoformat(bar[0]) + (
            timedelta(minutes=5) if exit_index == holding_bars - 1 else timedelta()
        )
        eq = quote_at(quotes, row["symbol"], exit_time)
        if mode == "STRICT_QUOTES" and eq is None:
            reasons["EXECUTABLE_EXIT_QUOTE_UNAVAILABLE"] += 1
            continue
        raw_exit = (
            eq["bid"]
            if eq
            else (bar[4] if exit_index == holding_bars - 1 else bar[1]) * (1 - execution.spread / 2)
        )
        exit_price = raw_exit * (1 - execution.slippage_per_leg)
        qty = notional / entry
        fees = execution.fee_per_leg * (notional + qty * exit_price)
        pnl = qty * exit_price - notional - fees
        used.add(key)
        cash -= notional
        order = {
            "symbol": row["symbol"],
            "decision": row["decision"],
            "state": "PARTIAL_THEN_REMAINDER_CANCELED" if fraction < 1 else "FILLED",
            "notional": notional,
        }
        orders.append(order)
        active.append(
            {
                **order,
                "exit_time": exit_time,
                "net_pnl": pnl,
                "fees": fees,
                "entry": entry,
                "exit": exit_price,
                "exit_reason": exit_reason,
                "evidence_type": mode,
                "regime": row["regime"],
            }
        )
    settle(datetime.max.replace(tzinfo=now.tzinfo) if pairs else datetime.max)
    if last_day is not None:
        daily[last_day] = cash - opening
        curve.append(cash)
    # Missing/tradeless dates remain in the denominator and uncertainty sample.
    days = sorted({r["date"] for r in rows})
    values = [daily.get(d, 0.0) for d in days]
    equity = np.asarray([execution.capital, *curve])
    peak = np.maximum.accumulate(equity)
    # Include entry cash marked at cost and conservative intratrade low marks.
    adverse_marks = [
        t["notional"]
        * max(
            0,
            1
            - min(
                b[3]
                for b in next(
                    r["path"] for r in rows if r["symbol"] == t["symbol"] and r["decision"] == t["decision"]
                )
            )
            / t["entry"],
        )
        for t in trades
    ]
    return {
        "mode": mode,
        "execution": asdict(execution),
        "holding_bars": holding_bars,
        "trade_count": len(trades),
        "net_pnl": cash - execution.capital,
        "net_return": cash / execution.capital - 1,
        "daily_mean_net_pnl_ci_98_75": bootstrap_daily(values),
        "daily_net_pnl": dict(zip(days, values)),
        "realized_exit_max_drawdown": float(np.max(peak - equity)),
        "max_single_trade_adverse_mark": max(adverse_marks, default=0.0),
        "daily_sharpe": float(np.mean(values) / np.std(values) * np.sqrt(252))
        if np.std(values) > 0
        else None,
        "rejections": dict(reasons),
        "orders": orders,
        "trades": [{**t, "exit_time": t["exit_time"].isoformat()} for t in trades],
        "regime_pnl": {
            k: sum(t["net_pnl"] for t in trades if t["regime"] == k)
            for k in sorted({r["regime"] for r in rows})
        },
        "quote_coverage_complete": mode == "STRICT_QUOTES"
        and bool(trades)
        and not any("QUOTE_UNAVAILABLE" in k for k in reasons),
        "quote_coverage_status": "NOT_MEASURED"
        if mode != "STRICT_QUOTES"
        else (
            "UNAVAILABLE"
            if any("QUOTE_UNAVAILABLE" in k for k in reasons)
            else "COVERED_OBSERVATIONS_ONLY"
            if trades
            else "NOT_EVALUATED_NO_ELIGIBLE_OPPORTUNITIES"
        ),
        "limitation": "Exit-quote screening uses future quote availability; unresolved real exposures need a tick ledger. Strict mode is a coverage audit, not executable P/L."
        if mode == "STRICT_QUOTES"
        else "Synthetic spread/slippage/fee/fill scenarios; not measured executable costs. Drawdown is realized exits plus separately reported adverse marks.",
    }
