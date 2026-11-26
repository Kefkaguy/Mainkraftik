"""Frozen daily execution membership; model prose cannot add a trading symbol."""

from intelligence.calendar import EASTERN
from intelligence.util import now
from paperpilot.db import connect


def selection(session=None):
    session = session or now().astimezone(EASTERN).date()
    with connect() as conn:
        return conn.execute("SELECT * FROM pilot_selections WHERE session_date=%s", (session,)).fetchone()


def selected_symbols(session=None):
    row = selection(session)
    return row["symbols"] if row else []


def display_research():
    with connect() as conn:
        return conn.execute("SELECT * FROM pilot_research_runs ORDER BY completed_at DESC LIMIT 1").fetchone()
