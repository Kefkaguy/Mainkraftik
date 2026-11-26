from uuid import uuid4

from psycopg.types.json import Jsonb

from intelligence.config import settings
from intelligence.util import canonical, digest


def jsonb(value):
    return Jsonb(value, dumps=canonical)


def store_document(conn, provider, url, payload, acquired):
    content_hash = digest(payload)
    row = conn.execute(
        "SELECT id FROM source_documents WHERE provider=%s AND url=%s AND content_hash=%s",
        (provider, url, content_hash),
    ).fetchone()
    if row:
        return row["id"]
    document_id = uuid4()
    conn.execute(
        "INSERT INTO source_documents VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(provider,url,content_hash) DO NOTHING",
        (document_id, provider, url, content_hash, acquired, jsonb(payload)),
    )
    return conn.execute(
        "SELECT id FROM source_documents WHERE provider=%s AND url=%s AND content_hash=%s",
        (provider, url, content_hash),
    ).fetchone()["id"]


def bars_at(conn, symbol, cutoff=None, acquired_before=None):
    clauses = ["symbol=%s", "feed=%s", "adjustment='split'"]
    args = [symbol, settings.feed]
    if cutoff:
        clauses.append("available_at<=%s")
        args.append(cutoff)
    if acquired_before:
        clauses.append("acquired_at<=%s")
        args.append(acquired_before)
    where = " AND ".join(clauses)
    basis = conn.execute(f"SELECT max(adjustment_asof) AS d FROM daily_bars WHERE {where}", args).fetchone()[
        "d"
    ]
    if basis is None:
        return []
    return conn.execute(
        f"SELECT DISTINCT ON(session_date) * FROM daily_bars WHERE {where} AND adjustment_asof=%s ORDER BY session_date,acquired_at DESC,id DESC",
        [*args, basis],
    ).fetchall()


def save_manifest(conn, kind, bars, **extra):
    payload = {
        "kind": kind,
        "bar_ids": [r["id"] for r in bars],
        "hashes": [r["content_hash"] for r in bars],
        "feed": settings.feed,
        "adjustment": "split",
        "adjustment_asof": bars[-1]["adjustment_asof"] if bars else None,
        "availability_basis": "Archived acquisition times for live forecasts; reconstructed exchange availability for historical evaluation",
        **extra,
    }
    dataset_id = digest(payload)
    conn.execute(
        "INSERT INTO dataset_manifests(id,kind,feed,adjustment,payload) VALUES(%s,%s,%s,'split',%s) ON CONFLICT DO NOTHING",
        (dataset_id, kind, settings.feed, jsonb(payload)),
    )
    return dataset_id


def watchlist(conn):
    return conn.execute("SELECT * FROM watchlist_versions ORDER BY id DESC LIMIT 1").fetchone()


def forecast_history(conn, symbol=None, limit=200):
    return conn.execute(
        """SELECT f.*, o.realized_return, o.spy_return, o.resolved_at
        FROM forecasts f LEFT JOIN LATERAL
        (SELECT * FROM forecast_outcomes WHERE forecast_id=f.id ORDER BY resolved_at DESC LIMIT 1) o ON true
        WHERE (%s::text IS NULL OR f.symbol=%s) ORDER BY f.issued_at DESC,f.symbol,f.horizon LIMIT %s""",
        (symbol, symbol, limit),
    ).fetchall()
