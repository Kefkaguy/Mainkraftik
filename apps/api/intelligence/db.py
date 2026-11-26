from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from intelligence.config import ROOT, settings


@contextmanager
def connection():
    from urllib.parse import urlsplit

    name = urlsplit(settings.database_url).path.removeprefix("/")
    if name != "intelligence" and not name.startswith(("intelligence_test_", "intelligence_restore_")):
        raise ValueError("Database target must belong to the independent intelligence application")
    with psycopg.connect(settings.database_url, row_factory=dict_row, connect_timeout=4) as conn:
        yield conn


def migrate():
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194002)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())"
        )
        for p in sorted((ROOT / "database/migrations").glob("*.sql")):
            if not conn.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (p.name,)).fetchone():
                conn.execute(p.read_text(encoding="utf-8"), prepare=False)
                conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (p.name,))
