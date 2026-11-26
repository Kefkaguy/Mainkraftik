from datetime import UTC, datetime, timedelta

import pytest
from test_paperpilot import paperdb as _paperdb

from paperpilot import engine
from paperpilot.config import settings
from paperpilot.data import latest_sources
from paperpilot.db import connect, jb

paperdb = _paperdb
CUTOFF = datetime(2026, 11, 16, 14, 15, tzinfo=UTC)


def insert(identity, symbol, acquired_at, payload, provider="daily_prices"):
    with connect() as c:
        c.execute(
            "INSERT INTO pilot_sources VALUES(%s,%s,%s,%s,%s)",
            (identity, symbol, provider, acquired_at, jb(payload)),
        )


def test_later_acquisition_does_not_hide_cutoff_safe_version(paperdb):
    insert("known-before", "NVDA", CUTOFF - timedelta(seconds=10), {"close": 100})
    insert("revised-after", "NVDA", CUTOFF + timedelta(seconds=1), {"close": 300})
    insert("other-provider", "NVDA", CUTOFF, {"close": 900}, provider="outcome_prices")
    safe = latest_sources("daily_prices", ["NVDA"], acquired_by=CUTOFF)
    assert safe["NVDA"]["id"] == "known-before"
    assert safe["NVDA"]["payload"]["close"] == 100
    # Current displays/ingestion callers keep their original latest-version behavior.
    assert latest_sources("daily_prices")["NVDA"]["id"] == "revised-after"


def test_future_only_source_is_unavailable_and_exact_cutoff_is_eligible(paperdb):
    insert("future", "NVDA", CUTOFF + timedelta(microseconds=1), {"close": 200})
    insert("exact-cutoff", "SPY", CUTOFF, {"close": 600})
    assert latest_sources("daily_prices", ["NVDA"], acquired_by=CUTOFF) == {}
    assert set(latest_sources("daily_prices", acquired_by=CUTOFF)) == {"SPY"}
    assert latest_sources("daily_prices", [], acquired_by=CUTOFF) == {}


@pytest.mark.parametrize("discovery", [False, True])
def test_forecast_source_query_explicitly_uses_issuance_cutoff(paperdb, monkeypatch, discovery):
    monkeypatch.setattr(settings, "paper_discovery_enabled", discovery)
    monkeypatch.setattr(engine, "current_symbols", lambda *args: ("NVDA",))
    monkeypatch.setattr(engine, "now", lambda: CUTOFF + timedelta(seconds=10))
    calls = []

    def source_query(provider, symbols=None, acquired_by=None):
        calls.append((provider, symbols, acquired_by))
        return {}

    monkeypatch.setattr(engine, "latest_sources", source_query)
    engine.create_signals()
    assert calls == [("daily_prices", ["NVDA", "SPY"] if discovery else None, CUTOFF)]
