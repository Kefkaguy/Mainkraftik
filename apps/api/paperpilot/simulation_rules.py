"""Shared portfolio and session semantics for paper execution and research."""

RULE_VERSION = "portfolio-observation-v2"


def daily_change(equity, first_verified_equity):
    return 0.0 if first_verified_equity is None else equity - first_verified_equity


def position_exit(return_on_cost, stop, take, timed=False, forced=False):
    return forced or timed or return_on_cost <= -stop or return_on_cost >= take


def can_enter(cash, exposure, reserved, notional, count, daily_pnl, loss_limit, drawdown, review_limit):
    return (
        count < 2
        and daily_pnl > -loss_limit
        and drawdown < review_limit
        and cash - reserved >= notional
        and exposure + reserved + notional <= 5000
    )


def bar_trigger(o, high, low, entry, stop, take, timed=False):
    if position_exit(o / entry - 1, stop, take, timed):
        return o, "open_or_time"
    if position_exit(low / entry - 1, stop, take):
        return entry * (1 - stop), "stop"
    if position_exit(high / entry - 1, stop, take):
        return entry * (1 + take), "target"
    return None
