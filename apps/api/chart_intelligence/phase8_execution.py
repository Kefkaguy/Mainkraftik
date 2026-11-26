"""Chronological, shared-capital paper-free research execution for multiple horizons."""

import hashlib
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np

from intelligence.calendar import EASTERN


@dataclass(frozen=True)
class Costs:
    spread: float = 0.001
    slippage: float = 0.0005
    fee: float = 0.0001
    reject: float = 0.05
    partial: float = 0.10

    @property
    def round_trip(self):
        return self.spread + 2 * (self.slippage + self.fee)


def draw(key):
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 2**32


def interval(values, family=25):
    a = np.asarray(values, dtype=float)
    if len(a) < 40:
        return None
    rng = np.random.default_rng(20261118)
    starts = rng.integers(0, len(a), (5000, (len(a) + 19) // 20))
    ix = ((starts[:, :, None] + np.arange(20)) % len(a)).reshape(5000, -1)[:, : len(a)]
    alpha = 0.05 / family
    return np.quantile(a[ix].mean(1), [alpha / 2, 1 - alpha / 2]).tolist()


def replay(rows, predictions, markets, costs=Costs(), benchmark=False, strict_quotes=False):
    if len(rows) != len(predictions):
        raise ValueError("Prediction alignment error")
    entries = defaultdict(list)
    for r, p in zip(rows, predictions):
        entries[datetime.fromisoformat(r["entry_time"])].append((r, p))
    start = min(entries) if entries else None
    end = max((datetime.fromisoformat(r["exit_time"]) for r in rows), default=None)
    times = sorted({t for bars in markets.values() for t in bars if start is not None and start <= t <= end})
    positions = {}
    cash = 600.0
    peak = 600.0
    drawdown = 0.0
    envelope_dd = 0.0
    envelope_peak = 600.0
    last_close = 600.0
    daily = {}
    opening = 600.0
    day = None
    latch = False
    used = set()
    orders = []
    closed = []
    reasons = Counter()
    attempts = defaultdict(int)
    qualified = 0

    def equity(field=3):
        return cash + sum(p["qty"] * p.get("mark", p["entry"]) for p in positions.values())

    for stamp in times:
        session = str(stamp.astimezone(EASTERN).date())
        if session != day:
            if day is not None:
                daily[day] = last_close - opening
            day = session
            opening = last_close
            latch = False
        # All exits are at available opens after the previous completed-bar
        # observation, or at the frozen holding deadline. No intrabar fill oracle.
        for symbol, p in list(positions.items()):
            bar = markets[symbol].get(stamp)
            if bar is None:
                continue
            p["mark"] = float(bar[0])
            if not (p.get("pending") or stamp >= p["deadline"]):
                continue
            key = (session, symbol)
            if attempts[key] >= 3:
                continue
            attempts[key] += 1
            d = draw(f"{symbol}:{stamp.isoformat()}:sell:{attempts[key]}")
            if d < costs.reject:
                orders.append(
                    {"time": stamp.isoformat(), "symbol": symbol, "side": "sell", "state": "REJECTED"}
                )
                continue
            fraction = 0.5 if d < costs.reject + costs.partial else 1.0
            qty = p["qty"] * fraction
            price = float(bar[0]) * (1 - costs.spread / 2) * (1 - costs.slippage)
            receipt = qty * price
            fee = receipt * costs.fee
            cash += receipt - fee
            p["receipts"] += receipt - fee
            p["qty"] -= qty
            orders.append(
                {
                    "time": stamp.isoformat(),
                    "symbol": symbol,
                    "side": "sell",
                    "state": "PARTIAL_CANCELED" if fraction < 1 else "FILLED",
                    "quantity": qty,
                    "price": price,
                    "fee": fee,
                }
            )
            p["pending"] = True
            if p["qty"] < 1e-10:
                closed.append(
                    {
                        "symbol": symbol,
                        "entry_time": p["entry_time"],
                        "exit_time": stamp.isoformat(),
                        "net_pnl": p["receipts"] - p["spent"],
                        "regime": p["regime"],
                        "exit_reason": p.get("reason", "TIME"),
                    }
                )
                del positions[symbol]
        for r, pred in sorted(
            entries.get(stamp, []), key=lambda rp: (-rp[1]["expected_return"], rp[0]["symbol"])
        ):
            symbol = r["symbol"]
            bar = markets[symbol].get(stamp)
            if pred["probability_up"] < 0.55 and not benchmark:
                reasons["CONFIDENCE"] += 1
                continue
            if pred["expected_return"] <= costs.round_trip + 0.0005 and not benchmark:
                reasons["EXPECTED_RETURN_AFTER_COSTS"] += 1
                continue
            qualified += 1
            if strict_quotes:
                reasons["EXECUTABLE_QUOTES_UNAVAILABLE"] += 1
                continue
            if bar is None:
                reasons["MISSING_ENTRY_BAR"] += 1
                continue
            if latch or equity() - opening <= -12:
                reasons["DAILY_LOSS"] += 1
                continue
            if symbol in positions:
                reasons["EXISTING_POSITION"] += 1
                continue
            if (session, symbol) in used:
                reasons["NO_REENTRY"] += 1
                continue
            exposure = sum(p["qty"] * p["mark"] for p in positions.values())
            if len(positions) >= 2 or exposure + 25 > 100 or cash < 25 * (1 + costs.fee):
                reasons["CAPITAL_OR_POSITION_LIMIT"] += 1
                continue
            if r["liquidity"] * 0.001 < 25:
                reasons["CAUSAL_LIQUIDITY"] += 1
                continue
            used.add((session, symbol))
            d = draw(f"{symbol}:{stamp.isoformat()}:buy")
            if d < costs.reject:
                reasons["ORDER_REJECTED"] += 1
                orders.append(
                    {"time": stamp.isoformat(), "symbol": symbol, "side": "buy", "state": "REJECTED"}
                )
                continue
            notional = 12.5 if d < costs.reject + costs.partial else 25.0
            price = float(bar[0]) * (1 + costs.spread / 2) * (1 + costs.slippage)
            fee = notional * costs.fee
            cash -= notional + fee
            positions[symbol] = {
                "qty": notional / price,
                "entry": price,
                "mark": float(bar[0]),
                "spent": notional + fee,
                "receipts": 0.0,
                "entry_time": stamp.isoformat(),
                "deadline": datetime.fromisoformat(r["exit_time"]),
                "regime": r["regime"],
            }
            orders.append(
                {
                    "time": stamp.isoformat(),
                    "symbol": symbol,
                    "side": "buy",
                    "state": "PARTIAL_CANCELED" if notional < 25 else "FILLED",
                    "quantity": notional / price,
                    "price": price,
                    "fee": fee,
                }
            )
        low = cash
        high = cash
        for symbol, p in positions.items():
            bar = markets[symbol].get(stamp)
            if bar is None:
                low += p["qty"] * p["mark"]
                high += p["qty"] * p["mark"]
                continue
            low += p["qty"] * float(bar[2])
            high += p["qty"] * float(bar[1])
            p["mark"] = float(bar[3])
            if float(bar[2]) / p["entry"] - 1 <= -0.02:
                p["pending"] = True
                p["reason"] = "STOP"
            elif float(bar[1]) / p["entry"] - 1 >= 0.03:
                p["pending"] = True
                p["reason"] = "TARGET"
        if low - opening <= -12:
            latch = True
            for p in positions.values():
                p["pending"] = True
                p["reason"] = "DAILY_LOSS"
        envelope_peak = max(envelope_peak, high)
        envelope_dd = max(envelope_dd, envelope_peak - low)
        last_close = equity()
        peak = max(peak, last_close)
        drawdown = max(drawdown, peak - last_close)
    if day is not None:
        daily[day] = last_close - opening
    ordered = [daily[d] for d in sorted(daily)]
    net = last_close - 600
    from chart_intelligence.phase8_accounting import split_pnl

    attribution = split_pnl(orders, {s: p["mark"] for s, p in positions.items()})
    assert abs(attribution["net_pnl"] - net) < 1e-6
    assert abs(sum(ordered) - net) < 1e-6
    counts = Counter(o["state"] for o in orders)
    return {
        "evidence_type": "QUOTE_COVERAGE_AUDIT" if strict_quotes else "ASSUMPTION_ONLY",
        "costs": asdict(costs),
        "qualified_opportunities": qualified,
        "filled_entries": sum(o["side"] == "buy" and o["state"] != "REJECTED" for o in orders),
        "closed_trades": len(closed),
        "net_pnl": net,
        "net_return": net / 600,
        "marked_max_drawdown": drawdown,
        "conservative_ohlc_drawdown": envelope_dd,
        "daily_sharpe": float(np.mean(ordered) / np.std(ordered) * np.sqrt(252))
        if ordered and np.std(ordered) > 0
        else None,
        "daily_mean_net_pnl_family_ci": interval(ordered),
        "daily_net_pnl": daily,
        "rejections": dict(reasons),
        "order_states": dict(counts),
        "orders": orders,
        "trades": closed,
        "open_positions": len(positions),
        "unrealized_pnl": attribution["unrealized_pnl"],
        "realized_pnl": attribution["realized_pnl"],
        "regime_pnl": {
            k: sum(t["net_pnl"] for t in closed if t["regime"] == k)
            for k in sorted({r["regime"] for r in rows})
        },
        "quote_coverage": "UNAVAILABLE"
        if qualified and strict_quotes
        else "NOT_EVALUATED"
        if strict_quotes
        else "SYNTHETIC_NOT_MEASURED",
        "limits": {
            "initial_capital": 600,
            "entry": 25,
            "position_cap": 100,
            "total_cap": 100,
            "concurrent": 2,
            "daily_loss": 12,
            "stop": 0.02,
            "target": 0.03,
        },
        "interpretation": "Five-minute observation simulation; no measured quotes, borrow, margin, dividends or live execution. Missing observations never fill. Unresolved positions are marked and separately counted.",
    }
