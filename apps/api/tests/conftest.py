import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from intelligence.config import settings
from intelligence.db import migrate


@pytest.fixture(scope="session")
def test_database():
    original = settings.database_url
    uri = urlsplit(original)
    if (
        uri.hostname not in {"localhost", "127.0.0.1"}
        or uri.port != 5543
        or settings.environment == "production"
    ):
        pytest.fail("Integration tests require the independent LOCAL PostgreSQL cluster on port 5543")
    dbname = "intelligence_test_" + secrets.token_hex(5)
    admin_uri = urlunsplit(uri._replace(path="/postgres"))
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    settings.database_url = urlunsplit(uri._replace(path="/" + dbname))
    migrate()
    yield dbname
    settings.database_url = original
    assert dbname.startswith("intelligence_test_") and uri.port == 5543
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname)))


@pytest.fixture
def db(test_database):
    from intelligence.db import connection

    with connection() as conn:
        conn.execute("""TRUNCATE shadow_verdicts,shadow_events,shadow_artifacts,shadow_protocols,candidate_results,tuning_trials,tuning_events,tuning_plans,feature_snapshots,information_versions,information_fetches,filing_events,research_audits,worker_status,job_events,scheduler_marks,jobs,forecast_outcomes,forecasts,forecast_runs,
          chart_detections,detection_outcomes,evaluation_reports,dataset_manifests,
          calculated_metrics,financial_facts,corporate_actions,daily_bars,source_documents,
          ingest_runs,sessions,login_attempts RESTART IDENTITY CASCADE""")
        conn.execute("INSERT INTO watchlist_versions(symbols) VALUES('[\"MSI\",\"AXON\"]')")
    yield
