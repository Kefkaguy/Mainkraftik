from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from test_calendar_and_predictions import make_bars

from intelligence import forecasting
from intelligence.calendar import cutoff_for, issuance_for
from intelligence.config import settings
from intelligence.db import connection
from intelligence.main import app
from intelligence.security import hasher
from intelligence.storage import bars_at, jsonb, store_document
from intelligence.util import digest


def seed_prices(origin=date(2025, 6, 2)):
    rows = make_bars(date(2021, 1, 4), date(2025, 6, 12))
    with connection() as conn:
        sid = store_document(
            conn,
            "test_fixture",
            "https://example.invalid/test",
            {"synthetic": True},
            datetime(2021, 1, 1, tzinfo=UTC),
        )
        with conn.cursor() as cur:
            for symbol in ["MSI", "AXON", "SPY"]:
                records = []
                for row in rows:
                    r = {**row, "symbol": symbol, "adjustment_asof": origin, "source_id": sid}
                    r["content_hash"] = digest({"fixture": True, "symbol": symbol, "date": r["session_date"]})
                    records.append(r)
                cur.executemany(
                    """INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,
                    open,high,low,close,volume,available_at,acquired_at,source_id,content_hash)
                    VALUES(%(symbol)s,%(session_date)s,%(feed)s,%(adjustment)s,%(adjustment_asof)s,%(open)s,
                    %(high)s,%(low)s,%(close)s,%(volume)s,%(available_at)s,%(acquired_at)s,%(source_id)s,%(content_hash)s)""",
                    records,
                )
    return rows


def test_prospective_vertical_slice_and_idempotency(db, monkeypatch):
    origin = date(2025, 6, 2)
    seed_prices(origin)
    with connection() as conn:
        # A wildly different future opening/closing price must never affect
        # this evening's AXON forecast relative to the identical MSI history.
        conn.execute(
            """INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,
            open,high,low,close,volume,available_at,acquired_at,source_id,content_hash)
            SELECT symbol,session_date,feed,adjustment,adjustment_asof,
            10000,10002,9998,9999,volume,available_at,acquired_at+interval '1 second',source_id,content_hash||'_future'
            FROM daily_bars WHERE symbol='AXON' AND session_date>%s""",
            (origin,),
        )
    monkeypatch.setattr(forecasting, "now", lambda: issuance_for(origin))
    result = forecasting.issue_forecasts()
    assert len(result["issued"]) == 6
    assert forecasting.issue_forecasts()["issued"] == []
    with connection() as conn:
        rows = conn.execute("SELECT * FROM forecasts").fetchall()
        assert all(r["issued_at"] >= r["data_cutoff"] for r in rows)
        assert all(r["entry_session"] == date(2025, 6, 3) for r in rows)
        assert all(r["provenance"]["kind"] == "prospective" for r in rows)
        for horizon in (1, 5):
            paired = [r for r in rows if r["horizon"] == horizon and r["symbol"] in {"MSI", "AXON"}]
            assert paired[0]["expected_return"] == paired[1]["expected_return"]
            assert paired[0]["probability_positive"] == paired[1]["probability_positive"]
        assert conn.execute("SELECT count(*) AS n FROM forecast_outcomes").fetchone()["n"] == 0
    monkeypatch.setattr(forecasting, "now", lambda: datetime(2025, 6, 12, 23, tzinfo=UTC))
    assert forecasting.resolve_outcomes()["count"] == 6
    assert forecasting.resolve_outcomes()["count"] == 0
    with connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) AS n FROM forecast_outcomes WHERE spy_return IS NOT NULL"
            ).fetchone()["n"]
            == 6
        )


def test_late_acquisition_cannot_enter_forecast(db, monkeypatch):
    origin = date(2025, 6, 2)
    seed_prices(origin)
    # Earlier acquired rows are immutable. Insert a later basis: it must not
    # replace the snapshot available at cutoff.
    with connection() as conn:
        rows = bars_at(conn, "MSI", cutoff_for(origin), cutoff_for(origin))
        assert rows[-1]["session_date"] == origin
        future_rows = bars_at(conn, "MSI")
        assert future_rows[-1]["session_date"] > origin
    monkeypatch.setattr(forecasting, "now", lambda: issuance_for(origin) - timedelta(minutes=1))
    assert forecasting.issue_forecasts()["status"] == "skipped"
    with connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM forecasts").fetchone()["n"] == 0


def test_evidence_is_append_only(db):
    with connection() as conn:
        sid = store_document(conn, "fixture", "https://example.invalid/immutable", {}, datetime.now(UTC))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with connection() as conn:
            conn.execute("UPDATE source_documents SET provider='changed' WHERE id=%s", (sid,))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with connection() as conn:
            conn.execute("DELETE FROM source_documents WHERE id=%s", (sid,))


def test_migration_rerun_preserves_evidence(db):
    from intelligence.db import migrate

    with connection() as conn:
        sid = store_document(conn, "fixture", "https://example.invalid/persist", {}, datetime.now(UTC))
    migrate()
    with connection() as conn:
        assert conn.execute("SELECT 1 FROM source_documents WHERE id=%s", (sid,)).fetchone()


def test_authentication_csrf_logout_and_proxies(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", hasher.hash("test-password-12345"))
    origin = {"Origin": settings.origin}
    with TestClient(app) as client:
        assert client.get("/v1/workspace").status_code == 401
        assert client.get("/v1/companies/MSI").status_code == 401
        assert client.get("/v1/models/performance").status_code == 401
        assert client.get("/v1/jobs/" + str(uuid4())).status_code == 401
        assert (
            client.post(
                "/v1/auth/login", json={"username": "admin", "password": "test-password-12345"}
            ).status_code
            == 403
        )
        response = client.post(
            "/v1/auth/login", headers=origin, json={"username": "admin", "password": "test-password-12345"}
        )
        assert response.status_code == 200
        assert (
            "HttpOnly" in response.headers["set-cookie"]
            and "Path=/intelligence" in response.headers["set-cookie"]
        )
        token = response.cookies.get("intelligence_session")
        cookie = {"Cookie": "intelligence_session=" + token}
        session = client.get("/v1/auth/session", headers=cookie)
        assert session.status_code == 200
        assert (
            client.post("/v1/jobs", headers={**origin, **cookie}, json={"kind": "refresh"}).status_code == 403
        )
        headers = {**origin, **cookie, "x-csrf-token": session.json()["csrf_token"]}
        assert client.post("/v1/jobs", headers=headers, json={"kind": "refresh"}).status_code == 202
        assert (
            client.post(
                "/v1/jobs", headers={**headers, "Origin": "https://evil.invalid"}, json={"kind": "refresh"}
            ).status_code
            == 403
        )
        assert client.post("/v1/auth/logout", headers=headers).status_code == 200
        assert client.get("/v1/auth/session", headers=cookie).status_code == 401


def test_login_attempts_are_persisted_and_throttled(db, monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", hasher.hash("test-password-12345"))
    with TestClient(app) as client:
        for _ in range(8):
            assert (
                client.post(
                    "/v1/auth/login",
                    headers={"Origin": settings.origin},
                    json={"username": "admin", "password": "wrong"},
                ).status_code
                == 401
            )
        assert (
            client.post(
                "/v1/auth/login",
                headers={"Origin": settings.origin},
                json={"username": "admin", "password": "wrong"},
            ).status_code
            == 429
        )


def test_watchlist_cannot_change_training_cohort(db, monkeypatch):
    with connection() as conn:
        before = conn.execute("SELECT * FROM training_universe_versions ORDER BY id DESC LIMIT 1").fetchone()
        conn.execute("INSERT INTO watchlist_versions(symbols) VALUES(%s)", (jsonb(["MSI"]),))
        after = conn.execute("SELECT * FROM training_universe_versions ORDER BY id DESC LIMIT 1").fetchone()
        assert before == after
        # Restore by appending, exactly as the application does.
        conn.execute("INSERT INTO watchlist_versions(symbols) VALUES(%s)", (jsonb(["MSI", "AXON"]),))


def test_interrupted_job_is_recovered_once(db, monkeypatch):
    from intelligence import jobs

    job_id = jobs.enqueue("resolve")
    with connection() as conn:
        conn.execute("UPDATE jobs SET status='running',attempts=1 WHERE id=%s", (job_id,))
    monkeypatch.setattr(jobs, "dispatch", lambda kind, payload: {"fixture": True})
    assert jobs.run_once()["status"] == "completed"
    assert jobs.run_once() is None
    with connection() as conn:
        assert conn.execute("SELECT attempts FROM jobs WHERE id=%s", (job_id,)).fetchone()["attempts"] == 2
