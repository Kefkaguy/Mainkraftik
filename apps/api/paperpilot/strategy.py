"""Point-in-time inputs and fixed policy shared by runtime and research."""

import json
import math
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np

from intelligence.calendar import EASTERN, schedule
from intelligence.portable_models import predict
from intelligence.util import digest
from paperpilot.intraday import COST

POLICY = "mainkraft-paper-v1"
SWING_EXPECTANCY_TRIAL = "swing-expectancy-paper-v1"
SWING_EXPECTANCY_SESSIONS = 20
PROBABILITY_BLOCK = "Below strategy probability threshold"
STRATEGIES = {
    "day": {"horizon": 1, "limit": 4, "probability": 0.50},
    "swing": {"horizon": 5, "limit": 2, "probability": 0.55},
}
GROUPS = {"price": [0, 1, 2, 3], "volatility": [4, 5], "volume": [6], "market": [7, 8, 9, 10]}


def features(session, own, spy, technology):
    """Use 21 contiguous completed sessions. Technology is a market proxy, not a sector mapping."""
    days = [d for d in schedule(session - timedelta(days=45), session) if d < session][-21:]

    def values(rows, key):
        indexed = {date.fromisoformat(str(r["session_date"])): float(r[key]) for r in rows}
        return np.asarray([indexed[d] for d in days])

    if len(days) != 21:
        raise ValueError("Incomplete feature calendar")
    try:
        price, market, tech = (values(rows, "close") for rows in (own, spy, technology))
        volume = values(own, "volume")
    except KeyError:
        raise ValueError("Missing prior SIP sessions for stock, SPY or XLK") from None
    if min(price.min(), market.min(), tech.min()) <= 0 or volume[:-1].mean() <= 0:
        raise ValueError("Invalid prices or volume")

    def returns(values):
        return [float(values[-1] / values[-1 - n] - 1) for n in (1, 5, 10, 20)]

    own_returns = returns(price)
    market_returns = returns(market)
    tech_returns = returns(tech)
    changes = price[1:] / price[:-1] - 1
    columns = own_returns + [
        float(changes[-10:].std()),
        float(changes.std()),
        float(volume[-1] / volume[:-1].mean()),
        market_returns[1],
        market_returns[3],
        tech_returns[1] - market_returns[1],
        own_returns[1] - market_returns[1],
    ]
    if not all(map(math.isfinite, columns)):
        raise ValueError("Non-finite market features")
    return {"x": columns, "volatility": columns[5] * math.sqrt(252)}


def forecast(model, row):
    mean, probability, low, high = (float(x[0]) for x in predict(model, [row]))
    if not all(map(math.isfinite, (mean, probability, low, high))):
        raise ValueError("Non-finite strategy forecast")
    return dict(
        expected_net_return=mean,
        probability_positive=probability,
        lower_return=low,
        upper_return=high,
        estimated_training_cost=COST,
    )


def planning_return(signal, bid, ask):
    if not all(map(math.isfinite, (bid, ask))) or not 0 < bid <= ask:
        raise ValueError("Invalid execution quote")
    spread = ask / bid - 1
    required_cost = max(COST, spread + 0.0010)
    return signal["expected_net_return"] - (required_cost - COST)


def swing_trial_active(session, start):
    if not start or not start <= session <= start + timedelta(days=45):
        return False
    first_sessions = list(schedule(start, start + timedelta(days=45)))[:SWING_EXPECTANCY_SESSIONS]
    return session in first_sessions


def entry_reasons(strategy, signal, news, sentiment, *, expectancy_trial=False):
    reasons = []
    if not 0 <= signal["probability_positive"] <= 1:
        reasons.append("Invalid model probability")
    elif not expectancy_trial and signal["probability_positive"] < STRATEGIES[strategy]["probability"]:
        reasons.append(PROBABILITY_BLOCK)
    if signal["expected_net_return"] <= 0.0005:
        reasons.append("Expected net return below minimum margin")
    if signal["volatility"] > 0.8:
        reasons.append("Volatility exceeds pilot limit")
    if news != "bullish":
        reasons.append("Sourced news assessment is not bullish")
    if sentiment != "review":
        reasons.append("News sentiment veto or unavailable")
    return reasons


def decision_cutoff(session):
    return datetime.combine(session, time(9, 15), EASTERN)


def verified_bundle(value):
    unsigned = {k: v for k, v in value.items() if k != "bundle_id"}
    if value.get("protocol") != POLICY or value.get("bundle_id") != digest(unsigned):
        raise ValueError("Strategy bundle integrity check failed")
    if set(value["strategies"]) != set(STRATEGIES):
        raise ValueError("Both strategy artifacts are required")
    return value


def bundle():
    with Path("/models/mainkraft/bundle.json").open(encoding="utf-8") as source:
        return verified_bundle(json.load(source))
