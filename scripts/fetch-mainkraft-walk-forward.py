"""Acquire shared Mainkraft walk-forward evidence, with resumable provider snapshots."""

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from datetime import time as daytime
from pathlib import Path

import httpx

sys.path[:0] = ["apps/api"]
from intelligence.calendar import EASTERN, schedule
from intelligence.config import settings
from intelligence.providers import fetch_bars, get_json, normalize_bars
from intelligence.util import canonical, digest, now
from paperpilot import intraday
from paperpilot.intraday import save_snapshot

ROOT = Path(".local/mainkraft/walk-forward-v1")
ROOT.mkdir(parents=True, exist_ok=True)
COHORT = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOG",
    "META",
    "TSM",
    "AMD",
    "AVGO",
    "JPM",
    "XOM",
    "CVX",
    "JNJ",
    "UNH",
    "PG",
    "KO",
    "COST",
    "WMT",
    "HD",
    "MCD",
    "V",
    "MA",
    "GS",
    "BAC",
    "WFC",
    "CAT",
    "GE",
    "LLY",
    "PEP",
    "NKE",
    "DIS",
    "CRM",
    "ADBE",
    "QCOM",
    "AMAT",
    "AXP",
)
START, EVALUATE, STOP, RESOLVE = map(
    date.fromisoformat, ("2025-03-03", "2026-05-04", "2026-11-10", "2026-11-17")
)
PLAN = {
    "cohort": COHORT,
    "training_data_start": START,
    "first_test_date": EVALUATE,
    "last_test_date": STOP,
    "last_outcome_date": RESOLVE,
    "provenance": "Fixed liquid-stock cohort composed from currently known listings; no historical membership claim",
    "prediction": "Monthly refit, 250 most recent mature dates, final 55 reserved for calibration; five-session purge at calibration boundary",
    "strategies": {
        "technical_only": "Rank predicted five-session net, require >0.05%, volatility <=80%, prices >=5 and 20-day dollar volume >=20m",
        "news_filtered": "Same predictions and filters plus at least 50% mean positive headline sentiment and no sentiment veto",
        "news_ranked": "Same technical eligibility; adjust ranking by up to +/- 15 basis points using mean positive minus negative sentiment; missing news neutral",
    },
    "news": "Use original publication and latest revision <= morning cutoff; latest three qualifying stories in the 72-hour lookback; use pinned local HF scorer; Groq stances unavailable",
    "selection": "One $800 trial at a time, no reentry on any day that starts invested, rank ties by symbol",
    "execution": "9:35 NY SIP five-minute bar open proxy; 2% stop, 3% take; exit five-session close minus 15 min, $12 daily loss, prior regular mark overnight, 25bp fees and stress 50bp; stop wins ambiguous bars",
    "alternative": "Technical ranking with opening gap within +/-1%, positive prior 5-session momentum, and volatility bands (stop 1.0–2.0%, target 2.0–4.0%). Primary comparisons remain fixed",
    "stats": "126 whole evaluation sessions minimum for interpretation, report cash benchmark, daily equity, drawdown, profit factor, consistency and paired 20-session 2000-sample bootstrap",
}
path = ROOT / "protocol.json"
if path.exists():
    assert json.loads(path.read_text()) == json.loads(canonical(PLAN))
else:
    with path.open("x", encoding="utf-8") as out:
        out.write(canonical(PLAN) + "\n")
lock = threading.Lock()
last = 0.0


def request(client, url, **kwargs):
    global last
    with lock:
        due = max(time.monotonic(), last + 0.39)
        last = due
    remaining = due - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    return get_json(client, url, **kwargs)


intraday.get_json = request
import intelligence.providers as providers

providers.get_json = request
HEADERS = {
    "APCA-API-KEY-ID": settings.alpaca_key,
    "APCA-API-SECRET-KEY": settings.alpaca_secret,
}


def write_record(key, record):
    target = ROOT / (key + ".json")
    if target.exists():
        raise ValueError("Cache already exists")
    with target.open("x", encoding="utf-8") as file:
        file.write(canonical(record) + "\n")


def intraday_job(symbol):
    key = "intraday-" + symbol
    p = ROOT / (key + ".json")
    if p.exists():
        return key, json.loads(p.read_text()), "cached"
    cal = schedule(START, RESOLVE)
    d = intraday.fetch_intraday(symbol, cal[START]["open"], cal[RESOLVE]["close"])
    v = {
        "symbol": symbol,
        "snapshot": save_snapshot(ROOT, d),
        "bars": len(d["bars"]),
        "missing": len(d["missing"]),
    }
    write_record(key, v)
    return key, v, "fetched"


def daily_job(symbol):
    key = "daily-" + symbol
    p = ROOT / (key + ".json")
    if p.exists():
        return key, json.loads(p.read_text()), "cached"
    cal = schedule(RESOLVE, RESOLVE)
    start = START - timedelta(days=45)
    raw, doc, url = fetch_bars(symbol, start, cal[RESOLVE]["close"], RESOLVE)
    acquired = now()
    bars = normalize_bars(symbol, raw, settings.feed, RESOLVE, acquired)
    d = {
        "symbol": symbol,
        "url": url,
        "request": doc["request"],
        "page_hash": digest(doc),
        "acquired_at": acquired,
        "bars": bars,
    }
    v = {"symbol": symbol, "snapshot": save_snapshot(ROOT, d), "bars": len(bars)}
    write_record(key, v)
    return key, v, "fetched"


def news_job(window):
    start, end = window
    key = "news-" + str(start)
    p = ROOT / (key + ".json")
    if p.exists():
        return key, json.loads(p.read_text()), "cached"
    url = "https://data.alpaca.markets/v1beta1/news"
    args = {
        "sort": "asc",
        "symbols": ",".join(COHORT),
        "start": datetime.combine(start, daytime.min, EASTERN).isoformat(),
        "end": datetime.combine(end, daytime.min, EASTERN).isoformat(),
        "include_content": "false",
        "limit": 50,
    }
    hashes, articles, seen = [], {}, set()
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        params = dict(args)
        for _ in range(550):
            page = request(client, url, params=params, headers=HEADERS)
            hashes.append(digest(page))
            for a in page.get("news", []):
                if a.get("headline"):
                    articles[str(a["id"])] = {
                        k: a.get(k)
                        for k in (
                            "id",
                            "headline",
                            "summary",
                            "created_at",
                            "updated_at",
                            "symbols",
                            "source",
                            "url",
                        )
                    }
            token = page.get("next_page_token")
            if not token:
                break
            if token in seen:
                raise ValueError("Repeated historical-news page")
            seen.add(token)
            params["page_token"] = token
        else:
            raise ValueError("News query exceeds replay page limit")
    d = {"request": args, "acquired_at": now(), "hashes": hashes, "articles": articles}
    v = {
        "snapshot": save_snapshot(ROOT, d),
        "articles": len(articles),
        "pages": len(hashes),
    }
    write_record(key, v)
    return key, v, "fetched"


def main(group):
    assert settings.feed == "sip"
    windows = []
    day = EVALUATE - timedelta(days=4)
    while day <= STOP:
        end = min(day + timedelta(days=10), STOP + timedelta(days=1))
        windows.append((day, end))
        day = end
    jobs = []
    if group in {"all", "prices"}:
        jobs.extend((daily_job, s) for s in (*COHORT, "SPY", "XLK"))
        jobs.extend((intraday_job, s) for s in COHORT)
    if group in {"all", "news"}:
        jobs.extend((news_job, w) for w in windows)
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn, x): (fn.__name__, x) for fn, x in jobs}
        for f in as_completed(futures):
            try:
                key, value, status = f.result()
                print(
                    key,
                    status,
                    {k: v for k, v in value.items() if k != "snapshot"},
                    flush=True,
                )
            except (ValueError, OSError, httpx.HTTPError) as exc:
                label = futures[f]
                print("ERROR", label, str(exc), flush=True)
                errors.append((label, str(exc)))
    if errors:
        raise SystemExit("Fetch failures: " + str(len(errors)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
