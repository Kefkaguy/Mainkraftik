"""Post-hoc conservative OHLC envelope accounting for frozen research trades.

This does not select models. Simultaneous bar highs/lows are an adverse envelope,
not a claim about the true intrabar path or attainable liquidation prices.
"""

from datetime import datetime, timedelta


def portfolio_envelope(rows, trades, capital=600):
    paths = {(r["symbol"], r["decision"]): r["path"] for r in rows}
    events = []
    for i, t in enumerate(trades):
        start = datetime.fromisoformat(t["decision"])
        end = datetime.fromisoformat(t["exit_time"])
        events.append((start, 2, i, "entry", t))
        events.append((end, 1, i, "exit", t))
        for bar in paths[t["symbol"], t["decision"]]:
            stamp = datetime.fromisoformat(bar[0]) + timedelta(minutes=5)
            if start < stamp <= end:
                events.append((stamp, 0, i, "mark", bar))
    cash = float(capital)
    positions = {}
    peak = float(capital)
    drawdown = 0.0
    points = []
    for stamp, _, i, kind, item in sorted(events, key=lambda e: e[:3]):
        if kind == "entry":
            fee = item["notional"] * 0.0001
            cash -= item["notional"] + fee
            positions[i] = {
                "qty": item["notional"] / item["entry"],
                "low": item["entry"],
                "high": item["entry"],
            }
        elif kind == "exit":
            cash += item["notional"] + item["net_pnl"] + item["notional"] * 0.0001
            positions.pop(i)
        elif i in positions:
            positions[i]["high"] = item[2]
            positions[i]["low"] = item[3]
        high = cash + sum(p["qty"] * p["high"] for p in positions.values())
        low = cash + sum(p["qty"] * p["low"] for p in positions.values())
        peak = max(peak, high)
        drawdown = max(drawdown, peak - low)
        points.append({"time": stamp.isoformat(), "low_equity": low, "high_equity": high})
    expected = capital + sum(t["net_pnl"] for t in trades)
    if abs(cash - expected) > 1e-7 or positions:
        raise ValueError("Portfolio ledger did not reconcile")
    return {
        "max_drawdown_dollars": drawdown,
        "max_drawdown_fraction_of_initial": drawdown / capital,
        "final_cash": cash,
        "basis": "Conservative joint OHLC envelope; true intrabar order and executable exit prices unknown",
        "points": points,
    }
