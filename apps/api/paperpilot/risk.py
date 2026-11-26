import math
from datetime import timedelta
from decimal import Decimal

from intelligence.calendar import EASTERN
from intelligence.util import now
from paperpilot.config import current_symbols, managed_symbols, settings

MAX_STOCK = Decimal("100")
MAX_TOTAL = Decimal("600")
SCALED_SWING_NOTIONAL = Decimal("800")
SCALED_SWING_CAP = Decimal("850")
BUY_NOTIONAL = Decimal("25") if settings.paper_discovery_enabled else Decimal("90")
DAILY_LOSS = Decimal(str(settings.paper_daily_loss_limit))
TERMINAL = {"filled", "canceled", "expired", "rejected"}


def swing_trial_notional():
    return Decimal(settings.paper_swing_trial_notional)


def scaled_intent(intent):
    from paperpilot.strategy import SWING_EXPECTANCY_TRIAL

    return (
        intent.get("notional") == SCALED_SWING_NOTIONAL
        and (intent.get("strategy_payload") or {}).get("entry_policy") == SWING_EXPECTANCY_TRIAL
    )


def scaled_position_cap(owner):
    return SCALED_SWING_CAP if scaled_intent(owner) else MAX_STOCK


def scaled_buy_authorized(body, amount):
    """Authorize the exact scaled order saved by the engine immediately before submission."""
    from paperpilot.db import connect
    from paperpilot.strategy import SWING_EXPECTANCY_TRIAL, swing_trial_active

    instant = now().astimezone(EASTERN)
    if (
        amount != SCALED_SWING_NOTIONAL
        or amount != swing_trial_notional()
        or not settings.paper_mainkraft_enabled
        or not settings.paper_execution_permitted
        or not swing_trial_active(instant.date(), settings.paper_swing_trial_start)
        or (instant.hour, instant.minute) != (9, 35)
    ):
        return False
    with connect() as conn:
        row = conn.execute(
            """SELECT i.state,i.notional,i.payload,i.side,i.session_date,s.role,s.strategy,s.action,
            s.payload->>'entry_policy' AS entry_policy,s.payload->>'entry_notional' AS entry_notional,p.enabled
            FROM pilot_intents i JOIN pilot_strategy_signals s ON s.id=i.strategy_signal_id
            CROSS JOIN pilot_settings p
            WHERE i.client_id=%s AND i.symbol=%s AND s.symbol=i.symbol AND s.session_date=i.session_date""",
            (body["client_order_id"], body["symbol"]),
        ).fetchone()
    return bool(
        row
        and row["enabled"]
        and row["side"] == "buy"
        and row["state"] == "submitting"
        and row["session_date"] == instant.date()
        and row["notional"] == amount
        and row["payload"] == body
        and row["role"] == "paper"
        and row["strategy"] == "swing"
        and row["action"] == "BUY"
        and row["entry_policy"] == SWING_EXPECTANCY_TRIAL
        and row["entry_notional"] == str(amount)
    )


def execution_cost_evidence(signal, quote):
    """Conservative planning allowance, not measured future fills or a profit promise."""
    bid, ask = Decimal(str(quote["bid"])), Decimal(str(quote["ask"]))
    expected = Decimal(str(signal["expected_return"]))
    if not all(v.is_finite() for v in (bid, ask, expected)) or bid <= 0 or ask < bid:
        raise ValueError("Invalid price/return for cost estimate")
    spread = ask / bid - 1
    buffer = Decimal("0.0010")
    required_edge = Decimal("0.0005")
    net = expected - spread - buffer
    return {
        "version": "iex-roundtrip-planning-cost-v1",
        "spread_return": float(spread),
        "slippage_allowance": float(buffer),
        "required_net_edge": float(required_edge),
        "expected_net_return": float(net),
        "passes": net > required_edge,
        "limitation": "IEX observed spread plus 10bp round-trip allowance; not NBBO, actual fees, or guaranteed fill costs",
    }


def client_id(session, symbol, side):
    if side not in {"buy", "sell"} or symbol not in (
        current_symbols(session) if side == "buy" else managed_symbols()
    ):
        raise ValueError("Stock/side outside fixed pilot")
    return f"kti4-{session.isoformat()}-{symbol}-{side}"


def entry_permission(
    symbol,
    signal,
    asset,
    quote,
    instant,
    positions,
    intents,
    account,
    enabled,
    daily_pnl,
    strategy=None,
    trace=None,
):
    if trace is not None:
        trace.expect(
            [
                "automatic_paper_execution_is_disabled",
                "outside_frozen_daily_selection",
                "daily_loss_circuit_breaker",
                "sourced_news_assessment_is_not_bullish",
                "local_financial_news_assessment_is_negative_or_unavailable",
                "volatility_exceeds_pilot_limit",
                "tradable_fractional_us_stock_not_verified",
                "execution_quote_stale_or_unavailable",
                "quote_feed_spread_gate_failed",
                "expected_return_does_not_clear_spread,_slippage_allowance_and_minimum_net_edge",
                "existing_position:_no_averaging_down_or_repeated_entry",
                "entry_or_exit_already_attempted_this_session_no_re-entry_after_an_exit",
                "unreconciled_order_acknowledgement_blocks_new_buys",
                "concurrent_position_limit_reached",
                "actual_cash_buying_power_insufficient_borrowing_prohibited",
                "non-finite_forecast",
                "swing_expectancy_trial_is_outside_its_approved_sessions",
                "no_eligible_experimental_signal",
                "the_$800_swing_trial_requires_a_flat_book_and_no_pending_buys",
                "other_entries_pause_while_the_$800_swing_trial_is_open",
                "portfolio_exposure_including_reservations_exceeds_limit",
            ]
        )

    def _blocked(key, condition, reason):
        return trace.blocked(key, condition, reason) if trace is not None else condition

    if _blocked(
        "automatic_paper_execution_is_disabled", (not enabled), "Automatic paper execution is disabled"
    ):
        return False, "Automatic paper execution is disabled"
    if _blocked(
        "outside_frozen_daily_selection",
        (symbol not in current_symbols(instant.astimezone(EASTERN).date())),
        "Outside frozen daily selection",
    ):
        return False, "Outside frozen daily selection"
    try:
        if _blocked(
            "non-finite_forecast",
            (
                not all(
                    math.isfinite(float(signal[k]))
                    for k in (
                        "expected_net_return" if strategy else "expected_return",
                        "probability_positive",
                        "volatility",
                    )
                )
            ),
            "Non-finite forecast",
        ):
            return False, "Non-finite forecast"
    except (KeyError, TypeError, ValueError):
        if trace is not None:
            trace.failure("forecast_completeness", "Incomplete forecast")
        return False, "Incomplete forecast"
    if _blocked(
        "daily_loss_circuit_breaker", (Decimal(str(daily_pnl)) <= -DAILY_LOSS), "Daily loss circuit breaker"
    ):
        return False, "Daily loss circuit breaker"
    trial = False
    if strategy:
        from paperpilot.strategy import SWING_EXPECTANCY_TRIAL, entry_reasons, swing_trial_active

        trial = signal.get("entry_policy") == SWING_EXPECTANCY_TRIAL
        if _blocked(
            "swing_expectancy_trial_is_outside_its_approved_sessions",
            (
                trial
                and (
                    strategy != "swing"
                    or not swing_trial_active(
                        instant.astimezone(EASTERN).date(), settings.paper_swing_trial_start
                    )
                )
            ),
            "Swing expectancy trial is outside its approved sessions",
        ):
            return False, "Swing expectancy trial is outside its approved sessions"
        reasons = entry_reasons(
            strategy, signal, signal.get("news_stance"), signal.get("sentiment"), expectancy_trial=trial
        )
        if reasons:
            if trace is not None:
                trace.failure("strategy_signal", "; ".join(reasons))
            return False, reasons[0]
    elif _blocked(
        "no_eligible_experimental_signal",
        (
            signal.get("expected_return", 0) <= 0.0015
            or signal.get("probability_positive", 0) < (0.70 if settings.paper_discovery_enabled else 0.55)
        ),
        "No eligible experimental signal",
    ):
        return False, "No eligible experimental signal"
    if _blocked(
        "sourced_news_assessment_is_not_bullish",
        (settings.paper_discovery_enabled and signal.get("news_stance") != "bullish"),
        "Sourced news assessment is not bullish",
    ):
        return False, "Sourced news assessment is not bullish"
    if _blocked(
        "local_financial_news_assessment_is_negative_or_unavailable",
        (not strategy and settings.paper_sentiment_enabled and signal.get("hf_news_assessment") != "review"),
        "Local financial news assessment is negative or unavailable",
    ):
        return False, "Local financial news assessment is negative or unavailable"
    if _blocked(
        "volatility_exceeds_pilot_limit",
        (signal.get("volatility", float("inf")) > 0.8),
        "Volatility exceeds pilot limit",
    ):
        return False, "Volatility exceeds pilot limit"
    if _blocked(
        "tradable_fractional_us_stock_not_verified",
        (
            not asset
            or asset.get("symbol") != symbol
            or asset.get("class") != "us_equity"
            or not asset.get("tradable")
            or not asset.get("fractionable")
            or asset.get("status") != "active"
        ),
        "Tradable fractional US stock not verified",
    ):
        return False, "Tradable fractional US stock not verified"
    if _blocked(
        "execution_quote_stale_or_unavailable",
        (
            not quote
            or instant - quote["source_time"] > timedelta(seconds=15)
            or quote["source_time"] > instant + timedelta(seconds=2)
        ),
        "Execution quote stale or unavailable",
    ):
        return False, "Execution quote stale or unavailable"
    try:
        if strategy:
            from paperpilot.strategy import planning_return

            costs = {"passes": planning_return(signal, float(quote["bid"]), float(quote["ask"])) > 0.0005}
        else:
            costs = execution_cost_evidence(signal, quote)
    except (ArithmeticError, KeyError, TypeError, ValueError):
        if trace is not None:
            trace.failure("cost_inputs", "Invalid quote or return for execution planning")
        return False, "Invalid quote or return for execution planning"
    if _blocked(
        "quote_feed_spread_gate_failed",
        (
            quote.get("feed") != "iex"
            or Decimal(str(quote["ask"])) / Decimal(str(quote["bid"])) - 1 > Decimal("0.005")
        ),
        "Quote feed/spread gate failed",
    ):
        return False, "Quote feed/spread gate failed"
    if _blocked(
        "expected_return_does_not_clear_spread,_slippage_allowance_and_minimum_net_edge",
        ((strategy or settings.paper_cost_gate_enabled) and not costs["passes"]),
        "Expected return does not clear spread, slippage allowance and minimum net edge",
    ):
        return False, "Expected return does not clear spread, slippage allowance and minimum net edge"
    if _blocked(
        "existing_position:_no_averaging_down_or_repeated_entry",
        (any(p["symbol"] == symbol and Decimal(str(p["qty"])) != 0 for p in positions)),
        "Existing position: no averaging down or repeated entry",
    ):
        return False, "Existing position: no averaging down or repeated entry"
    if _blocked(
        "entry_or_exit_already_attempted_this_session_no_re-entry_after_an_exit",
        (
            any(
                i["symbol"] == symbol
                and (
                    not strategy
                    or i["session_date"] == instant.astimezone(EASTERN).date()
                    or i["state"] not in TERMINAL
                )
                for i in intents
            )
        ),
        "Entry or exit already attempted this session; no re-entry after an exit",
    ):
        return False, "Entry or exit already attempted this session; no re-entry after an exit"
    if _blocked(
        "unreconciled_order_acknowledgement_blocks_new_buys",
        (any(i["state"] in {"prepared", "unknown", "submitting"} for i in intents)),
        "Unreconciled order acknowledgement blocks new buys",
    ):
        return False, "Unreconciled order acknowledgement blocks new buys"
    occupied = {p["symbol"] for p in positions} | {
        i["symbol"] for i in intents if i["side"] == "buy" and i["state"] not in TERMINAL
    }
    if _blocked(
        "concurrent_position_limit_reached",
        (strategy and len(occupied) >= 20),
        "Concurrent position limit reached",
    ):
        return False, "Concurrent position limit reached"
    exposure = sum(Decimal(str(p["market_value"])) for p in positions)
    reserve = sum(
        Decimal(str(i.get("notional") or 0))
        for i in intents
        if i["side"] == "buy" and i["state"] not in TERMINAL
    )
    notional = swing_trial_notional() if trial else BUY_NOTIONAL
    if notional == SCALED_SWING_NOTIONAL:
        if _blocked(
            "the_$800_swing_trial_requires_a_flat_book_and_no_pending_buys",
            (occupied),
            "The $800 Swing trial requires a flat book and no pending buys",
        ):
            return False, "The $800 Swing trial requires a flat book and no pending buys"
    else:
        filled_by_symbol = {}
        for intent in sorted(intents, key=lambda item: item.get("created_at") or instant):
            if (
                intent["side"] == "buy"
                and Decimal(str((intent.get("payload") or {}).get("filled_qty") or 0)) > 0
            ):
                filled_by_symbol[intent["symbol"]] = intent
        if _blocked(
            "other_entries_pause_while_the_$800_swing_trial_is_open",
            (any(scaled_intent(filled_by_symbol.get(s, {})) for s in occupied)),
            "Other entries pause while the $800 Swing trial is open",
        ):
            return False, "Other entries pause while the $800 Swing trial is open"
        if _blocked(
            "portfolio_exposure_including_reservations_exceeds_limit",
            (exposure + reserve + notional > MAX_TOTAL or notional > MAX_STOCK),
            "Portfolio exposure including reservations exceeds limit",
        ):
            return False, "Portfolio exposure including reservations exceeds limit"
    if _blocked(
        "actual_cash_buying_power_insufficient_borrowing_prohibited",
        (
            min(Decimal(str(account.get("cash", 0))), Decimal(str(account.get("buying_power", 0)))) - reserve
            < notional
        ),
        "Actual cash/buying power insufficient; borrowing prohibited",
    ):
        return False, "Actual cash/buying power insufficient; borrowing prohibited"
    return True, "Return, quote and risk gates passed"
