import shutil
from datetime import timedelta
from uuid import uuid4

from intelligence.calendar import EASTERN, schedule
from intelligence.config import settings
from intelligence.db import connection
from intelligence.financials import VERSION, calculate_metrics, normalize_facts
from intelligence.providers import ProviderError, fetch_actions, fetch_bars, fetch_sec, normalize_bars
from intelligence.storage import jsonb, store_document
from intelligence.util import digest, now


def resource_check(projected_bytes=32 * 1024**2):
    settings.storage.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(settings.storage).free
    reserve = settings.min_free_gb * 1024**3
    if free - projected_bytes < reserve:
        raise ProviderError("Insufficient storage headroom; no cleanup or deletion was attempted")
    with connection() as conn:
        size = conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"]
    if size + projected_bytes > 256 * 1024**2:
        raise ProviderError("Independent research database reached its 256 MiB growth gate; ingestion paused")


def ingest_prices(symbol, client=None):
    resource_check()
    acquired = now()
    asof = acquired.astimezone(EASTERN).date()
    start = asof - timedelta(days=5 * 366 + 70)
    # Explicit end older than 15 minutes also works with delayed historical SIP access.
    raw, payload, url = fetch_bars(symbol, start, acquired - timedelta(minutes=20), asof, client)
    acquired = now()  # Availability cannot predate completion of the fetch.
    bars = normalize_bars(symbol, raw, settings.feed, asof, acquired)
    if not bars:
        raise ProviderError("No complete exchange sessions returned")
    with connection() as conn:
        sid = store_document(conn, "alpaca_bars", url, payload, acquired)
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,
                open,high,low,close,volume,available_at,acquired_at,source_id,content_hash)
                VALUES(%(symbol)s,%(session_date)s,%(feed)s,%(adjustment)s,%(adjustment_asof)s,
                %(open)s,%(high)s,%(low)s,%(close)s,%(volume)s,%(available_at)s,%(acquired_at)s,%(source_id)s,%(content_hash)s)
                ON CONFLICT DO NOTHING""",
                [{**b, "source_id": sid} for b in bars],
            )
    action_status = "available"
    try:
        actions, action_url = fetch_actions(symbol, start, asof, client)
        with connection() as conn:
            sid = store_document(conn, "alpaca_corporate_actions", action_url, actions, now())
            for page in actions["pages"]:
                for kind, rows in (page.get("corporate_actions") or {}).items():
                    for row in rows:
                        h = digest({"symbol": symbol, "kind": kind, "row": row})
                        effective = row.get("ex_date") or row.get("process_date") or row.get("effective_date")
                        conn.execute(
                            "INSERT INTO corporate_actions(symbol,action_type,effective_date,acquired_at,source_id,content_hash,payload) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                            (symbol, kind, effective, now(), sid, h, jsonb(row)),
                        )
    except ProviderError as exc:
        action_status = str(exc)
    expected = set(schedule(bars[0]["session_date"], bars[-1]["session_date"]))
    missing = sorted(expected - {r["session_date"] for r in bars})
    return {
        "symbol": symbol,
        "count": len(bars),
        "first": bars[0]["session_date"],
        "last": bars[-1]["session_date"],
        "feed": settings.feed,
        "adjustment": "split",
        "missing_sessions": missing,
        "corporate_actions": action_status,
        "price_convention": "Alpaca daily eligible-trade OHLC; no dividend adjustment",
    }


def ingest_financials(symbol, client=None):
    resource_check(20 * 1024**2)
    with connection() as conn:
        security = conn.execute("SELECT * FROM securities WHERE symbol=%s", (symbol,)).fetchone()
    if not security or not security["cik"]:
        raise ProviderError("A verified SEC CIK is required for financial ingestion")
    facts, submissions, facts_url, submissions_url = fetch_sec(security["cik"], client)
    acquired = now()
    rows = normalize_facts(symbol, facts, submissions, acquired)
    if not rows:
        raise ProviderError("SEC returned no supported GAAP facts")
    with connection() as conn:
        sid = store_document(conn, "sec_companyfacts", facts_url, facts, acquired)
        store_document(conn, "sec_submissions", submissions_url, submissions, acquired)
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO financial_facts(symbol,metric,tag,unit,value,period_start,period_end,
                fiscal_year,fiscal_period,form,accession,filed_date,available_at,acquired_at,source_id,source_url,content_hash)
                VALUES(%(symbol)s,%(metric)s,%(tag)s,%(unit)s,%(value)s,%(period_start)s,%(period_end)s,
                %(fiscal_year)s,%(fiscal_period)s,%(form)s,%(accession)s,%(filed_date)s,%(available_at)s,%(acquired_at)s,
                %(source_id)s,%(source_url)s,%(content_hash)s) ON CONFLICT DO NOTHING""",
                [{**r, "source_id": sid} for r in rows],
            )
        all_rows = conn.execute("SELECT * FROM financial_facts WHERE symbol=%s", (symbol,)).fetchall()
        metrics = calculate_metrics(all_rows, acquired)
        inputs_hash = digest(sorted(r["content_hash"] for r in all_rows if r["available_at"] <= acquired))
        conn.execute(
            "INSERT INTO calculated_metrics VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (uuid4(), symbol, acquired, VERSION, inputs_hash, jsonb(metrics)),
        )
    return {"symbol": symbol, "facts": len(rows), "metrics": len(metrics["metrics"]), "as_of": acquired}


def ingest_all(symbols):
    run_id = uuid4()
    with connection() as conn:
        conn.execute(
            "INSERT INTO ingest_runs(id,kind,symbols,status) VALUES(%s,'refresh',%s,'running')",
            (run_id, jsonb(symbols)),
        )
    results = []
    for symbol in symbols:
        item = {"symbol": symbol}
        for kind, operation in [("prices", ingest_prices), ("financials", ingest_financials)]:
            if symbol == "SPY" and kind == "financials":
                continue
            try:
                item[kind] = operation(symbol)
            except (ProviderError, ValueError) as exc:
                item[kind] = {"status": "unavailable", "reason": str(exc)}
        results.append(item)
    status = (
        "completed"
        if all("status" not in i.get(k, {}) for i in results for k in ["prices", "financials"])
        else "partial"
    )
    with connection() as conn:
        conn.execute(
            "UPDATE ingest_runs SET completed_at=now(),status=%s,result=%s WHERE id=%s",
            (status, jsonb(results), run_id),
        )
    return {"run_id": str(run_id), "status": status, "results": results}
