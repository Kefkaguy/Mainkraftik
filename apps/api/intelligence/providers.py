"""Allowlisted read-only market-data and SEC clients; no trading endpoints.

OHLC validation and explicit feed identity are adapted from KefTrade's provider
normalizers. Request semantics intentionally differ: split-only, fixed asof,
complete pagination, and no implicit feed substitution.
"""

import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as daytime
from decimal import Decimal

import httpx

from intelligence.calendar import EASTERN, schedule
from intelligence.config import settings
from intelligence.util import digest


class ProviderError(ValueError):
    pass


def get_json(client, url, **kwargs):
    if httpx.URL(url).host not in {"data.alpaca.markets", "data.sec.gov", "www.sec.gov"}:
        raise ProviderError("Data provider host is not permitted")
    for attempt in range(4):
        try:
            r = client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Provider connection failed ({type(exc).__name__}); no data substituted"
            ) from None
        if r.status_code in {429, 500, 502, 503, 504} and attempt < 3:
            time.sleep(min(2**attempt, 4))
            continue
        if r.status_code != 200:
            raise ProviderError(
                f"{httpx.URL(url).host} returned HTTP {r.status_code}; check access or retry later"
            )
        return r.json()
    raise ProviderError("Provider retry budget exhausted")


def fetch_bars(symbol, start, end, asof, client=None):
    if not settings.alpaca_key or not settings.alpaca_secret:
        raise ProviderError("Configure INTELLIGENCE_ALPACA_KEY and INTELLIGENCE_ALPACA_SECRET")
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    params = {
        "timeframe": "1Day",
        "start": str(start),
        "end": end.isoformat(),
        "asof": str(asof),
        "feed": settings.feed,
        "adjustment": "split",
        "sort": "asc",
        "limit": 10000,
    }
    headers = {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
    pages, bars, seen_tokens = [], [], set()
    with httpx.Client(timeout=45, follow_redirects=False) if client is None else _borrow(client) as c:
        for _ in range(25):
            payload = get_json(c, url, params=params, headers=headers)
            pages.append(payload)
            bars.extend(payload.get("bars") or [])
            token = payload.get("next_page_token")
            if not token:
                return (
                    bars,
                    {"request": {k: v for k, v in params.items() if k != "page_token"}, "pages": pages},
                    url,
                )
            if token in seen_tokens:
                raise ProviderError("Repeated pagination token; truncated data refused")
            seen_tokens.add(token)
            params["page_token"] = token
    raise ProviderError("Pagination ceiling exceeded; truncated data refused")


def normalize_bars(symbol, raw, feed, asof, acquired):
    if feed not in {"sip", "iex"}:
        raise ProviderError("Unsupported feed")
    parsed = []
    for b in raw:
        stamp = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ProviderError("Provider timestamps must include a timezone")
        d = stamp.astimezone(EASTERN).date()
        values = {k: Decimal(str(b[k[0]])) for k in ["open", "high", "low", "close", "volume"]}
        if not all(v.is_finite() for v in values.values()):
            raise ProviderError("Non-finite OHLCV refused")
        o, h, low, c, v = (values[k] for k in ["open", "high", "low", "close", "volume"])
        if not (0 < low <= min(o, c) <= max(o, c) <= h and v >= 0):
            raise ProviderError("Invalid OHLCV refused")
        parsed.append(
            {
                "symbol": symbol,
                "session_date": d,
                "feed": feed,
                "adjustment": "split",
                "adjustment_asof": asof,
                **values,
            }
        )
    if not parsed:
        raise ProviderError(f"No daily bars returned for {symbol}")
    days = [b["session_date"] for b in parsed]
    if len(days) != len(set(days)):
        raise ProviderError("Duplicate daily timestamps refused")
    cal = schedule(min(days), max(days))
    out = []
    for b in sorted(parsed, key=lambda x: x["session_date"]):
        d = b["session_date"]
        if d not in cal:
            raise ProviderError("Provider returned a bar outside the exchange calendar")
        if acquired < cal[d]["close"] + timedelta(minutes=15):
            continue
        b["available_at"] = cal[d]["close"] + timedelta(minutes=15)
        b["acquired_at"] = acquired
        b["content_hash"] = digest(b | {"acquired_at": None})
        out.append(b)
    return out


def fetch_actions(symbol, start, end, client=None):
    url = "https://data.alpaca.markets/v1/corporate-actions"
    headers = {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
    params = {
        "symbols": symbol,
        "start": str(start),
        "end": str(end),
        "limit": 1000,
        "types": "forward_split,reverse_split,cash_dividend,stock_dividend,spin_off,name_change",
    }
    pages, seen = [], set()
    with httpx.Client(timeout=45) if client is None else _borrow(client) as c:
        for _ in range(20):
            p = get_json(c, url, headers=headers, params=params)
            pages.append(p)
            token = p.get("next_page_token")
            if not token:
                return {"pages": pages, "request": params}, url
            if token in seen:
                raise ProviderError("Repeated corporate-action pagination token")
            seen.add(token)
            params["page_token"] = token
    raise ProviderError("Corporate-action pagination ceiling exceeded")


def fetch_sec(cik, client=None):
    if "@" not in settings.sec_user_agent:
        raise ProviderError("Configure a contact email in INTELLIGENCE_SEC_USER_AGENT")
    headers = {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}
    facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    with httpx.Client(timeout=60, follow_redirects=False) if client is None else _borrow(client) as c:
        submissions = get_json(c, submissions_url, headers=headers)
        time.sleep(0.55)
        facts = get_json(c, facts_url, headers=headers)
    return facts, submissions, facts_url, submissions_url


def lookup_company(symbol):
    if "@" not in settings.sec_user_agent:
        raise ProviderError("Configure the SEC contact User-Agent")
    with httpx.Client(timeout=30) as client:
        mapping = get_json(
            client,
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": settings.sec_user_agent},
        )
    matches = [r for r in mapping.values() if r["ticker"].upper() == symbol]
    if len(matches) != 1:
        raise ProviderError("No unique SEC company identity was found")
    return {"symbol": symbol, "name": matches[0]["title"], "cik": str(matches[0]["cik_str"]).zfill(10)}


class _borrow:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *args):
        return False


def filing_availability(filed, accession, submissions):
    recent = (submissions.get("filings") or {}).get("recent") or {}
    for i, a in enumerate(recent.get("accessionNumber", [])):
        if a == accession:
            timestamps = recent.get("acceptanceDateTime", [])
            if i < len(timestamps) and timestamps[i]:
                stamp = datetime.fromisoformat(timestamps[i].replace("Z", "+00:00"))
                return stamp if stamp.tzinfo else stamp.replace(tzinfo=EASTERN).astimezone(UTC)
    # A filed date alone cannot justify intraday availability. Withhold until
    # the following calendar day, preserving this conservative convention.
    return datetime.combine(date.fromisoformat(filed) + timedelta(days=1), daytime.min, EASTERN).astimezone(
        UTC
    )
