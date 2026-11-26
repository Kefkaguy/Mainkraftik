from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from test_paperpilot import paperdb as _paperdb

from opsbackup import export, verify_local
from paperpilot import engine
from paperpilot.config import settings
from paperpilot.db import connect

paperdb = _paperdb
HEALTHY = {"status": "verified", "cash_consistent": True, "execution_enabled": True}
BLOCKED = {"status": "blocked", "cash_consistent": False, "execution_enabled": False}


def observe(monkeypatch, stamp, payload=HEALTHY):
    monkeypatch.setattr(engine, "now", lambda: stamp)
    engine.Engine(broker=object()).connection("paper", payload)


def issue(monkeypatch):
    monkeypatch.setattr(settings, "paper_execution_permitted", True)
    monkeypatch.setattr(settings, "paper_discovery_enabled", False)
    monkeypatch.setattr(settings, "paper_sentiment_enabled", False)
    monkeypatch.setattr(engine, "now", lambda: datetime(2026, 11, 16, 14, 15, 15, tzinfo=UTC))
    monkeypatch.setattr(engine, "latest_sources", lambda provider, **kwargs: {})
    with connect() as c:
        c.execute(
            "UPDATE pilot_settings SET enabled=true,updated_at=%s WHERE id=1",
            (datetime(2026, 11, 15, 12, tzinfo=UTC),),
        )
    engine.create_signals()
    with connect() as c:
        rows = c.execute("SELECT * FROM pilot_decisions ORDER BY symbol").fetchall()
        assert len(rows) == 6
        # No price fixture exists, so no orders/signals are fabricated.
        assert all(r["action"] == "SKIP" for r in rows)
        assert c.execute("SELECT count(*) n FROM pilot_intents").fetchone()["n"] == 0
    return rows


def test_postcutoff_status_cannot_erase_genuine_precutoff_readiness(paperdb, monkeypatch):
    before = datetime(2026, 11, 16, 14, 14, 59, tzinfo=UTC)
    after = datetime(2026, 11, 16, 14, 15, 1, tzinfo=UTC)
    observe(monkeypatch, before)
    observe(monkeypatch, after, BLOCKED)
    rows = issue(monkeypatch)
    assert all(r["payload"]["account_ready_before_cutoff"] for r in rows)
    assert all(r["payload"]["readiness_observation_id"] for r in rows)
    assert all(datetime.fromisoformat(r["payload"]["readiness_observed_at"]) == before for r in rows)
    with connect() as c:
        # Current operational status remains current, independently of history.
        assert (
            c.execute("SELECT observed_at FROM pilot_connection WHERE id='paper'").fetchone()["observed_at"]
            == after
        )
        assert c.execute("SELECT count(*) n FROM pilot_readiness").fetchone()["n"] == 1


def test_latest_precutoff_failure_cannot_be_hidden_by_earlier_or_later_success(paperdb, monkeypatch):
    observe(monkeypatch, datetime(2026, 11, 16, 14, 14, 40, tzinfo=UTC))
    observe(monkeypatch, datetime(2026, 11, 16, 14, 14, 59, tzinfo=UTC), BLOCKED)
    observe(monkeypatch, datetime(2026, 11, 16, 14, 15, 1, tzinfo=UTC))
    assert all(not r["payload"]["account_ready_before_cutoff"] for r in issue(monkeypatch))


@pytest.mark.parametrize(
    "stamp",
    [
        datetime(2026, 11, 16, 14, 12, 59, tzinfo=UTC),
        datetime(2026, 11, 16, 14, 15, 1, tzinfo=UTC),
    ],
)
def test_stale_or_first_postcutoff_verification_is_not_backdated(paperdb, monkeypatch, stamp):
    observe(monkeypatch, stamp)
    rows = issue(monkeypatch)
    assert all(not r["payload"]["account_ready_before_cutoff"] for r in rows)
    assert all(r["payload"]["readiness_observation_id"] is None for r in rows)


def test_readiness_evidence_is_immutable(paperdb, monkeypatch):
    observe(monkeypatch, datetime(2026, 11, 16, 14, 14, 59, tzinfo=UTC))
    for mutation in ["DELETE FROM pilot_readiness", "UPDATE pilot_readiness SET payload='{}'"]:
        with connect() as c, pytest.raises(psycopg.errors.RaiseException):
            c.execute(mutation)


def test_readiness_survives_binary_backup_and_fresh_restore(paperdb, monkeypatch, tmp_path):
    stamp = datetime(2026, 11, 16, 14, 14, 59, tzinfo=UTC)
    observe(monkeypatch, stamp)
    archive = tmp_path / "paper.zip"
    export(archive, "paper")
    receipt = verify_local(archive, "paper")
    assert receipt["row_counts"]["pilot_readiness"] == 1
    target = urlsplit(settings.paper_database_url)
    name = receipt["restored_database"]
    assert name.startswith("intelligence_paper_test_")
    try:
        with psycopg.connect(urlunsplit(target._replace(path="/" + name))) as c:
            assert c.execute("SELECT observed_at FROM pilot_readiness").fetchone()[0] == stamp
    finally:
        with psycopg.connect(urlunsplit(target._replace(path="/postgres")), autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
