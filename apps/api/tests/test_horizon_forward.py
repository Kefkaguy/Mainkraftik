import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from chart_intelligence import store
from chart_intelligence.controller import connection
from chart_intelligence.horizon_forward import targets, tick
from chart_intelligence.phase8 import HORIZONS
from intelligence.calendar import EASTERN, schedule


@pytest.fixture
def fixture(tmp_path):
    cal = schedule(date(2026, 8, 1), date(2026, 12, 1))
    state = SimpleNamespace(
        time=datetime(2026, 11, 16, 12, 0, 1, tzinfo=EASTERN), missing=None, stale=False, late=False
    )
    models = {h: store.put("candidate", {"horizon": h}, tmp_path / "models") for h in HORIZONS}
    bundle = {
        "id": "fixture-only",
        "not_before": datetime(2026, 11, 16, 0, tzinfo=EASTERN).isoformat(),
        "model_root": str(tmp_path / "models"),
        "models": models,
        "roles": {h: "FIXTURE_ONLY" for h in HORIZONS},
    }
    daily = {
        s: {str(d): {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000000} for d in cal}
        for s in ("AMD", "SPY", "XLK")
    }
    market = [
        [(c["open"] + timedelta(minutes=5 * i)).isoformat(), 100, 101, 99, 100, 100000]
        for d, c in cal.items()
        if date(2026, 11, 12) <= d <= date(2026, 11, 27)
        for i in range((c["close"] - c["open"]) // timedelta(minutes=5))
    ]

    def collect(symbols, instant):
        if state.late:
            state.time += timedelta(minutes=3)
        return {
            "daily": daily,
            "markets": {"AMD": [b for b in market if b[0] != state.missing]},
            "quotes": {},
            "acquired_at": (state.time - timedelta(seconds=121 if state.stale else 0)).isoformat(),
            "feed": "sip",
            "adjustment": "split",
        }

    def predict(model, features):
        return [
            {
                "expected_return": 0.01,
                "probability_up": 0.7,
                "probability_down": 0.3,
                "expected_downside": -0.01,
                "probability_target_before_stop": 0.5,
                "lower_return": -0.03,
                "upper_return": 0.05,
            }
        ]

    def run():
        return tick(
            bundle, ["AMD"], collect, clock=lambda: state.time, root=tmp_path / "ledger", predictor=predict
        )

    def rows(kind):
        db = connection(tmp_path / "ledger")
        try:
            return [
                json.loads(r[0]) for r in db.execute("SELECT payload FROM evidence WHERE kind=?", (kind,))
            ]
        finally:
            db.close()

    return state, run, rows, cal


def test_hourly_forecast_precedes_target_and_survives_restart(fixture):
    state, run, rows, cal = fixture
    assert run()["issued"] == 1
    original = rows("prediction")[0]
    assert original["issued_at"] < original["target_time"]
    assert run()["issued"] == 0
    state.time = datetime(2026, 11, 16, 13, 20, 10, tzinfo=EASTERN)
    assert run()["resolved"] == 1
    assert rows("prediction")[0] == original
    assert rows("outcome")[0]["hypothetical_quote_outcome"]["status"] == "UNPRICED_ENTRY"


def test_long_horizons_resolve_exact_intraday_endpoints(fixture):
    state, run, rows, cal = fixture
    state.time = datetime(2026, 11, 16, 20, 30, 1, tzinfo=EASTERN)
    assert run()["issued"] == 4
    assert {r["horizon"] for r in rows("prediction")} == set(HORIZONS) - {"one_hour"}
    _, end = targets("five_sessions", state.time, cal)
    state.time = end + timedelta(minutes=21)
    assert run()["resolved"] == 4
    assert all(r["actual_return"] == 0 for r in rows("outcome"))
    assert run()["resolved"] == 0


def test_stale_late_and_missing_observations_never_fabricate_results(fixture):
    state, run, rows, cal = fixture
    state.stale = True
    with pytest.raises(ValueError, match="Stale"):
        run()
    state.stale = False
    state.late = True
    assert run()["issued"] == 0
    state.late = False
    state.time = datetime(2026, 11, 16, 12, 0, 1, tzinfo=EASTERN)
    assert run()["issued"] == 1
    end = datetime(2026, 11, 16, 13, 0, tzinfo=EASTERN)
    state.missing = end.astimezone(cal[date(2026, 11, 16)]["open"].tzinfo).isoformat()
    state.time = end + timedelta(minutes=21)
    assert run()["resolved"] == 0 and not rows("outcome")
    state.missing = None
    assert run()["resolved"] == 1
