"""Five-minute SIP evidence and policy-aligned simulated outcomes."""

import gzip
import math
import re
from datetime import datetime, timedelta

import httpx

from intelligence.calendar import EASTERN, schedule
from intelligence.config import settings
from intelligence.providers import ProviderError, get_json
from intelligence.util import canonical, digest, now

BAR = timedelta(minutes=5)
DELAY = timedelta(minutes=15)
COST = 0.0025
DEFAULT_EXIT = (0.02, 0.03)
EXIT_PAIRS = tuple((stop, take) for stop in (0.015, 0.02, 0.025) for take in (0.02, 0.03, 0.04))


def fetch_intraday(symbol, start, end):
    if not re.fullmatch(r"[A-Z]{1,5}", symbol):
        raise ValueError("Unsupported stock symbol")
    if end > now() - DELAY:
        raise ValueError("Historical SIP end must respect the feed delay")
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params = dict(
        timeframe="5Min",
        feed="sip",
        adjustment="split",
        limit=10000,
        sort="asc",
        start=start.isoformat(),
        end=end.isoformat(),
        asof=end.astimezone(EASTERN).date().isoformat(),
    )
    headers = {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
    records, pages, tokens = [], [], set()
    with httpx.Client(timeout=45, follow_redirects=False) as client:
        for _ in range(240):
            page = get_json(client, url, params=params, headers=headers)
            records.extend(page.get("bars") or [])
            pages.append(digest(page))
            token = page.get("next_page_token")
            if not token:
                break
            if token in tokens:
                raise ProviderError("Repeated intraday pagination token")
            tokens.add(token)
            params["page_token"] = token
        else:
            raise ProviderError("Intraday pagination limit reached")
    acquired = now()
    cal = schedule(start.astimezone(EASTERN).date(), end.astimezone(EASTERN).date())
    seen, bars = set(), []
    for row in records:
        stamp = datetime.fromisoformat(row["t"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ProviderError("Intraday timestamp needs a timezone")
        session = cal.get(stamp.astimezone(EASTERN).date())
        if session is None or not session["open"] <= stamp < session["close"]:
            continue
        values = tuple(float(row[k]) for k in ("o", "h", "l", "c", "v"))
        o, h, low, c, v = values
        if (
            stamp in seen
            or not start <= stamp <= end
            or (stamp - session["open"]) % BAR
            or not all(map(math.isfinite, values))
            or not 0 < low <= min(o, c) <= max(o, c) <= h
            or v < 0
        ):
            raise ProviderError("Duplicate or malformed intraday bar")
        seen.add(stamp)
        bars.append([stamp, *values])
    bars.sort()
    expected = {
        s["open"] + n * BAR
        for s in cal.values()
        for n in range((s["close"] - s["open"]) // BAR)
        if start <= s["open"] + n * BAR and s["open"] + (n + 1) * BAR <= end
    }
    return dict(
        symbol=symbol,
        acquired_at=acquired,
        url=url,
        request={k: v for k, v in params.items() if k != "page_token"},
        timestamp_basis="bar start; modeled availability is bar end + 15 minutes",
        page_hashes=pages,
        missing=sorted(expected - seen),
        bars=bars,
    )


def save_snapshot(root, snapshot):
    root.mkdir(parents=True, exist_ok=True)
    identity = digest(snapshot)
    path = root / (identity + ".json.gz")
    try:
        with path.open("xb") as output:
            output.write(gzip.compress(canonical(snapshot).encode(), mtime=0))
    except FileExistsError:
        pass
    return identity


def index_bars(snapshot):
    return {datetime.fromisoformat(str(row[0])): tuple(row[1:]) for row in snapshot["bars"]}


def simulate(bars, sessions, stop, take, cost=COST, virtual_entry=None):
    """Stop wins ambiguous bars. A gap through a stop uses the adverse open."""
    entry_time = sessions[0]["open"] + BAR
    exit_time = sessions[-1]["close"] - 3 * BAR
    stamps = [
        s["open"] + i * BAR
        for s in sessions
        for i in range((s["close"] - s["open"]) // BAR)
        if entry_time <= s["open"] + i * BAR <= exit_time
    ]
    if virtual_entry:
        entry_time, entry = virtual_entry
        stamps = [t for t in stamps if t >= entry_time]
    else:
        entry = bars[entry_time][0] if entry_time in bars else None
    if entry is None or not stamps or any(t not in bars for t in stamps):
        return None
    floor, ceiling = entry * (1 - stop), entry * (1 + take)
    for stamp in stamps:
        o, high, low, _, _ = bars[stamp]
        if o <= floor:
            exit_price, reason = o, "stop_gap"
        elif o >= ceiling:
            exit_price, reason = ceiling, "take_profit"
        elif stamp == exit_time:
            exit_price, reason = o, "time_exit"
        elif low <= floor:
            exit_price, reason = floor, "stop_loss"
        elif high >= ceiling:
            exit_price, reason = ceiling, "take_profit"
        else:
            continue
        gross = exit_price / entry - 1
        return {
            "entry_time": entry_time,
            "exit_bar": stamp,
            "gross_return": gross,
            "net_return": gross - cost,
            "reason": reason,
            "cost": cost,
        }
