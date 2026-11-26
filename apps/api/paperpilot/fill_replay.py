"""Independent fill-ledger replay. Research only; no order submission APIs."""
from decimal import Decimal


def reconcile(orders, starting_cash):
    cash, realized = Decimal(str(starting_cash)), Decimal(0)
    lots, identities = {}, set()
    for order in sorted(orders, key=lambda x: (x.get("submitted_at", ""), x["client_order_id"])):
        cid = order["client_order_id"]
        if cid in identities:
            raise ValueError("Supply exactly one latest cumulative version per client ID")
        identities.add(cid)
        qty, price = Decimal(str(order.get("filled_qty") or 0)), Decimal(str(order.get("filled_avg_price") or 0))
        if qty == 0:
            continue
        if not qty.is_finite() or not price.is_finite() or qty < 0 or price <= 0:
            raise ValueError("Invalid fill")
        lot = lots.setdefault(order["symbol"], [Decimal(0), Decimal(0)])
        if order["side"] == "buy":
            cash -= qty * price
            lot[0] += qty
            lot[1] += qty * price
        elif order["side"] == "sell" and qty <= lot[0]:
            basis = lot[1] * qty / lot[0]
            cash += qty * price
            realized += qty * price - basis
            lot[0] -= qty
            lot[1] -= basis
        else:
            raise ValueError("Invalid side or inventory")
    return {"cash": float(cash), "realized": float(realized),
            "positions": {s: float(v[0]) for s, v in lots.items()}}
