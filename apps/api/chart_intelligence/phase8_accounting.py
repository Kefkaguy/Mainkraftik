"""Independent fee-inclusive realized/unrealized attribution for partial fills."""


def split_pnl(orders, marks, capital=600.0):
    cash = capital
    realized = 0.0
    lots = {}
    for order in orders:
        if order["state"] == "REJECTED":
            continue
        qty = order["quantity"]
        value = qty * order["price"]
        fee = order["fee"]
        symbol = order["symbol"]
        lot = lots.setdefault(symbol, {"qty": 0.0, "basis": 0.0})
        if order["side"] == "buy":
            cash -= value + fee
            lot["qty"] += qty
            lot["basis"] += value + fee
        else:
            if qty > lot["qty"] + 1e-9:
                raise ValueError("Oversold research inventory")
            basis = lot["basis"] * qty / lot["qty"]
            cash += value - fee
            realized += value - fee - basis
            lot["qty"] -= qty
            lot["basis"] -= basis
    unrealized = sum(p["qty"] * marks[s] - p["basis"] for s, p in lots.items() if p["qty"] > 1e-10)
    equity = cash + sum(p["qty"] * marks[s] for s, p in lots.items() if p["qty"] > 1e-10)
    if abs(equity - capital - realized - unrealized) > 1e-6:
        raise ValueError("Fill accounting failed to reconcile")
    return {
        "cash": cash,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "net_pnl": equity - capital,
        "open_quantities": {s: p["qty"] for s, p in lots.items() if p["qty"] > 1e-10},
    }
