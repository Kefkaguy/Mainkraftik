"""Explicit SIP daily features and separately labeled IEX execution marks."""

from datetime import timedelta

import httpx

from intelligence.calendar import EASTERN
from intelligence.financials import calculate_metrics, normalize_facts
from intelligence.providers import fetch_bars, fetch_sec, get_json, lookup_company, normalize_bars
from intelligence.util import digest, now
from paperpilot.config import current_symbols, quote_symbols, settings
from paperpilot.db import connect, event, jb


def store(symbol, provider, payload, identity_payload=None):
    identity = digest([symbol, provider, identity_payload if identity_payload is not None else payload])
    with connect() as conn:
        conn.execute(
            "INSERT INTO pilot_sources VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (identity, symbol, provider, now(), jb(payload)),
        )
    return identity


def latest_sources(provider, symbols=None, acquired_by=None):
    """Latest actually acquired version, optionally as known at a fixed cutoff."""
    with connect() as conn:
        return {
            r["symbol"]: r
            for r in conn.execute(
                "SELECT DISTINCT ON(symbol) * FROM pilot_sources WHERE provider=%s AND (%s::text[] IS NULL OR symbol=ANY(%s::text[])) AND (%s::timestamptz IS NULL OR acquired_at<=%s::timestamptz) ORDER BY symbol,acquired_at DESC,id DESC",
                (provider, symbols, symbols, acquired_by, acquired_by),
            ).fetchall()
        }


def refresh_prices(symbols=None):
    instant = now()
    day = instant.astimezone(EASTERN).date()
    results = []
    extra = ("QQQ", "IWM", "TLT") if settings.paper_market_shadow_enabled else ()
    if settings.paper_mainkraft_enabled:
        extra += ("XLK",)
    for symbol in dict.fromkeys(symbols if symbols is not None else (*current_symbols(), "SPY", *extra)):
        try:
            raw, payload, url = fetch_bars(
                symbol, day - timedelta(days=160 if settings.paper_mainkraft_enabled else 5 * 366 + 70),
                instant - timedelta(minutes=20), day
            )
            acquired = now()
            bars = normalize_bars(symbol, raw, settings.feed, day, acquired)
            if len(bars) < (21 if settings.paper_mainkraft_enabled else 575):
                raise ValueError("Insufficient complete history for the declared per-stock model")
            identity = store(
                symbol,
                "daily_prices",
                {"bars": bars, "feed": settings.feed, "adjustment": "split", "url": url},
                {
                    "prices": [
                        {k: b[k] for k in ("session_date", "open", "high", "low", "close", "volume")}
                        for b in bars
                    ],
                    "feed": settings.feed,
                    "snapshot_date": day,
                },
            )
            results.append(
                {
                    "symbol": symbol,
                    "status": "available",
                    "sessions": len(bars),
                    "source_id": identity,
                    "last_session": bars[-1]["session_date"] if bars else None,
                }
            )
        except Exception as exc:
            results.append({"symbol": symbol, "status": "unavailable", "reason": type(exc).__name__})
    event("price_refresh", results)
    return results


def refresh_company_information():
    results = []
    for symbol in current_symbols():
        item = {"symbol": symbol}
        try:
            company = lookup_company(symbol)
            store(symbol, "identity", company)
            facts, submissions, url, _ = fetch_sec(company["cik"])
            acquired = now()
            selected = normalize_facts(symbol, facts, submissions, acquired)
            metrics = calculate_metrics(selected, acquired)
            data = {
                "identity": company,
                "source_url": url,
                "filing_forms": sorted(set(submissions.get("filings", {}).get("recent", {}).get("form", []))),
                "facts": metrics,
                "supported_facts": len(selected),
                "taxonomies": list(facts.get("facts", {})),
                "status": "available" if selected else "unavailable",
                "reason": None
                if selected
                else "Unsupported IFRS/foreign filing taxonomy; no values fabricated",
            }
            store(
                symbol,
                "fundamentals",
                data,
                {"company": company, "facts_hash": digest(facts), "submissions_hash": digest(submissions)},
            )
            item["fundamentals"] = data["status"]
        except Exception as exc:
            item["fundamentals"] = "unavailable"
            item["reason"] = type(exc).__name__
        try:
            params = {
                "symbols": symbol,
                "start": (now() - timedelta(days=7)).isoformat(),
                "end": (now() - timedelta(minutes=20)).isoformat(),
                "include_content": "false",
                "limit": 50,
                "sort": "desc",
            }
            headers = {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                news = get_json(
                    client, "https://data.alpaca.markets/v1beta1/news", params=params, headers=headers
                )
            metadata = [
                {
                    k: r.get(k)
                    for k in ("id", "created_at", "updated_at", "symbols", "source", "author", "url")
                }
                for r in news.get("news", [])
            ]
            store(
                symbol,
                "news",
                {"articles": metadata, "truncated": bool(news.get("next_page_token")), "window_days": 7},
                metadata,
            )
            item["news_records"] = len(metadata)
        except Exception as exc:
            item["news_status"] = "unavailable"
            item["news_error"] = type(exc).__name__
        results.append(item)
    event("company_information_refresh", results)
    return results


def save_quote(symbol, stamp, bid, ask, feed="iex", authorized_symbols=None):
    if (
        symbol not in (authorized_symbols if authorized_symbols is not None else quote_symbols())
        or bid <= 0
        or ask < bid
    ):
        return
    with connect() as conn:
        conn.execute(
            """INSERT INTO pilot_quotes VALUES(%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(symbol) DO UPDATE SET source_time=EXCLUDED.source_time,acquired_at=EXCLUDED.acquired_at,
            price=EXCLUDED.price,bid=EXCLUDED.bid,ask=EXCLUDED.ask,feed=EXCLUDED.feed
            WHERE pilot_quotes.source_time<=EXCLUDED.source_time""",
            (symbol, stamp, now(), (bid + ask) / 2, bid, ask, feed),
        )


def poll_quotes():
    desired = quote_symbols()
    headers = {
        "APCA-API-KEY-ID": settings.paper_key or settings.alpaca_key,
        "APCA-API-SECRET-KEY": settings.paper_secret or settings.alpaca_secret,
    }
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        result = get_json(
            client,
            "https://data.alpaca.markets/v2/stocks/quotes/latest",
            params={"symbols": ",".join(desired), "feed": "iex"},
            headers=headers,
        )
    from datetime import datetime

    for symbol, row in result.get("quotes", {}).items():
        save_quote(
            symbol,
            datetime.fromisoformat(row["t"].replace("Z", "+00:00")),
            float(row["bp"]),
            float(row["ap"]),
            "iex",
            desired,
        )
    return len(result.get("quotes", {}))
