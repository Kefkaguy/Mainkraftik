"""Risk arbitration for the two paper strategies."""

from datetime import timedelta
from decimal import Decimal

from intelligence.calendar import schedule
from paperpilot.risk import TERMINAL, entry_permission
from paperpilot.strategy import STRATEGIES, SWING_EXPECTANCY_TRIAL, planning_return


def owned_signals(intents):
    """An open lot has had exactly one buy. Canceled partial buys still own stock."""
    owners = {}
    for order in sorted(intents, key=lambda row: row["created_at"]):
        if order["side"] == "buy" and Decimal(str(order["payload"].get("filled_qty") or 0)) > 0:
            owners[order["symbol"]] = order
    return owners


def planned_exit(owner, session, instant, session_close):
    if owner["strategy"] == "day":
        return session > owner["session_date"] or instant >= session_close - timedelta(minutes=15)
    days = list(schedule(owner["session_date"], session))
    return len(days) > 5 or (len(days) == 5 and instant >= session_close - timedelta(minutes=15))


def can_retry_exit(intents, symbol, session, instant):
    sells = [i for i in intents if i["symbol"] == symbol and i["side"] == "sell"]
    if any(i["state"] not in TERMINAL for i in sells):
        return False
    round_trips = [i for i in sells if i["session_date"] == session]
    return len(round_trips) < 3 and all(
        instant - i["created_at"] >= timedelta(seconds=15) for i in round_trips
    )


def ranked_entries(rows, quotes):
    def sort_key(row):
        quote = quotes.get(row["symbol"], {})
        try:
            net = planning_return(row["payload"], float(quote["bid"]), float(quote["ask"]))
        except (KeyError, TypeError, ValueError):
            net = float("-inf")
        trial = row["payload"].get("entry_policy") == SWING_EXPECTANCY_TRIAL
        return (row["strategy"] != "day", trial, -net, row["symbol"])

    return sorted((r for r in rows if r["action"] == "BUY" and r["role"] == "paper"), key=sort_key)


def strategy_entry(row, quote, asset, instant, positions, intents, account, daily_pnl, enabled, trace=None):
    if trace is not None:
        trace.expect(
            [
                "stock_already_assigned_today",
                "daily_strategy_entry_limit",
                "one_expectancy_trial_entry_per_session",
                "concurrent_overnight_swing_limit",
            ]
        )

    def _blocked(key, condition, reason):
        return trace.blocked(key, condition, reason) if trace is not None else condition

    kind, symbol = row["strategy"], row["symbol"]
    todays = [i for i in intents if i["side"] == "buy" and i["session_date"] == row["session_date"]]
    if _blocked(
        "stock_already_assigned_today",
        (any(i["symbol"] == symbol for i in todays)),
        "Stock already assigned today",
    ):
        return False, "Stock already assigned today"
    if _blocked(
        "daily_strategy_entry_limit",
        (sum(i["strategy"] == kind for i in todays) >= STRATEGIES[kind]["limit"]),
        "Daily strategy entry limit",
    ):
        return False, "Daily strategy entry limit"
    if _blocked(
        "one_expectancy_trial_entry_per_session",
        (
            row["payload"].get("entry_policy") == SWING_EXPECTANCY_TRIAL
            and any(
                (i.get("strategy_payload") or {}).get("entry_policy") == SWING_EXPECTANCY_TRIAL
                for i in todays
            )
        ),
        "One expectancy trial entry per session",
    ):
        return False, "One expectancy trial entry per session"
    if kind == "swing":
        owners = owned_signals(intents)
        active = {p["symbol"] for p in positions if owners.get(p["symbol"], {}).get("strategy") == "swing"}
        active |= {
            i["symbol"]
            for i in intents
            if i["side"] == "buy" and i["strategy"] == "swing" and i["state"] not in TERMINAL
        }
        if _blocked(
            "concurrent_overnight_swing_limit", (len(active) >= 2), "Concurrent overnight swing limit"
        ):
            return False, "Concurrent overnight swing limit"
    return entry_permission(
        symbol,
        row["payload"],
        asset,
        quote,
        instant,
        positions,
        intents,
        account,
        enabled,
        daily_pnl,
        strategy=kind,
        trace=trace,
    )
