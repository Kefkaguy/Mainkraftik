from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from intelligence.util import canonical
from paperpilot.config import settings


def jb(value):
    return Jsonb(value, dumps=canonical)


@contextmanager
def connect():
    name = urlsplit(settings.paper_database_url).path.removeprefix("/")
    if name != "intelligence_paper" and not name.startswith("intelligence_paper_test_"):
        raise ValueError("Paper database must be isolated from research and legacy databases")
    with psycopg.connect(settings.paper_database_url, row_factory=dict_row, connect_timeout=5) as conn:
        yield conn


def migrate():
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194100)")
        conn.execute(Path(__file__).with_name("schema.sql").read_text(), prepare=False)


def event(kind, payload):
    with connect() as conn:
        conn.execute("INSERT INTO pilot_events(kind,payload) VALUES(%s,%s)", (kind, jb(payload)))
