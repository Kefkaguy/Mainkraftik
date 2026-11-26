from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import httpx
import numpy as np
import pytest
from test_persistence_and_auth import seed_prices

from intelligence import forecasting, prospective, tuning
from intelligence.calendar import cutoff_for, issuance_for
from intelligence.db import connection
from intelligence.information import ingest_news, news_metadata, persist_information
from intelligence.point_features import financial_values, information_at, information_values
from intelligence.portable_models import fit, predict
from intelligence.storage import jsonb, store_document
from intelligence.util import digest


def article(updated="2025-01-01T15:00:00Z"):
    return {
        "id": 123,
        "created_at": "2025-01-01T15:00:00Z",
        "updated_at": updated,
        "headline": "Company raises full-year outlook",
        "symbols": ["MSI"],
        "source": "test",
        "author": "test",
        "url": "https://example.invalid/article",
        "content": "not retained",
        "summary": "not retained",
    }


def test_news_dedup_revisions_and_actual_acquisition_gates(db):
    acquired = datetime(2025, 1, 3, tzinfo=UTC)
    with connection() as conn:
        sid = store_document(conn, "test", "https://example.invalid/news", {}, acquired)
        first = news_metadata(article())
        first_id = persist_information(conn, "alpaca_news", first, sid, acquired)
        assert persist_information(conn, "alpaca_news", first, sid, acquired) == first_id
        changed = news_metadata(article("2025-01-02T15:00:00Z"))
        persist_information(conn, "alpaca_news", changed, sid, acquired)
        rows = conn.execute("SELECT * FROM information_versions").fetchall()
    assert len(rows) == 2
    cutoff = datetime(2025, 1, 1, 20, tzinfo=UTC)
    known = information_at(rows, "alpaca_news", "MSI", cutoff)
    assert [r["id"] for r in known] == [first_id]
    assert information_at(rows, "alpaca_news", "MSI", cutoff, True) == []
    assert "content" not in first and "summary" not in first


def test_news_pagination_failure_is_not_complete_coverage(db):
    def handler(request):
        return httpx.Response(200, json={"news": [article()], "next_page_token": "repeated"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = ingest_news(datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC), client)
    assert result["status"] == "partial"
    with connection() as conn:
        assert conn.execute("SELECT status FROM information_fetches").fetchone()["status"] == "partial"


def test_missing_news_coverage_is_missing_not_zero():
    values, lineage = information_values("MSI", datetime(2025, 1, 2, tzinfo=UTC), [], [], [], [])
    assert values[:4] == [None] * 4
    assert not lineage["news_coverage"]


def rows(seed=8):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(450):
        x = rng.normal(size=36).tolist()
        x[4] = abs(x[4]) * 0.01 + 0.001
        if i % 7 == 0:
            x[25] = None
        out.append({"x": x, "y": float(x[0] * 0.01 + rng.normal() * 0.02)})
    return out[:250], out[250:350], out[350:]


@pytest.mark.parametrize(
    "family,params",
    [
        ("linear", {"alpha": 10, "C": 0.1}),
        ("histboost", {"iterations": 32, "leaves": 3, "learning_rate": 0.05, "l2": 10, "minimum_leaf": 20}),
    ],
)
def test_portable_heads_test_labels_and_future_extremes_cannot_change_artifact(family, params):
    train, cal, test = rows()
    artifact = fit(train, cal, list(range(32)), family, params, 5)
    first = predict(artifact, test)
    changed = deepcopy(test)
    for r in changed:
        r["y"] = 1000
    second = predict(artifact, changed)
    for a, b in zip(first, second, strict=True):
        np.testing.assert_array_equal(a, b)
    assert all(np.isfinite(x).all() for x in first)
    # JSON round trip is the actual artifact representation used by the worker.
    import json

    roundtrip = json.loads(json.dumps(artifact))
    for a, b in zip(first, predict(roundtrip, test), strict=True):
        np.testing.assert_array_equal(a, b)


def test_tuning_inner_boundaries_contain_labels():
    days = [date(2021, 1, 1) + timedelta(days=i) for i in range(1000)]
    data = [{"date": d, "end": d + timedelta(days=5), "x": [0] * 36, "y": 0.01} for d in days]
    outer = tuning.split(data, tuning.boundaries(days))
    inner = tuning.split(outer["train"], tuning.boundaries([r["date"] for r in outer["train"]]))
    assert max(r["end"] for r in inner["train"]) < min(r["date"] for r in inner["cal"])
    assert max(r["end"] for r in inner["cal"]) < min(r["date"] for r in inner["test"])
    assert max(r["end"] for r in inner["test"]) < min(r["date"] for r in outer["cal"])


def test_frozen_shadow_records_precede_outcomes_and_do_not_replace_reference(db, monkeypatch):
    origin = date(2025, 6, 2)
    seed_prices(origin)
    monkeypatch.setattr(forecasting, "now", lambda: issuance_for(origin))
    assert len(forecasting.issue_forecasts()["issued"]) == 6
    train, cal, _ = rows()
    artifact = fit(train, cal, list(range(14)), "linear", {"alpha": 10, "C": 0.1}, 1)
    artifacts = [
        {"symbol": s, "horizon": h, "artifact": {**artifact, "horizon": h}}
        for s in ("AXON", "MSI")
        for h in (1, 5)
    ]
    monkeypatch.setattr(tuning, "implementation_hash", lambda: "frozen")
    monkeypatch.setattr(prospective, "now", lambda: issuance_for(origin))
    with connection() as conn:
        conn.execute(
            "INSERT INTO model_versions(id,kind,specification) VALUES(%s,'experimental_shadow','{}') ON CONFLICT DO NOTHING",
            (prospective.MODEL_ID,),
        )
        conn.execute(
            "INSERT INTO shadow_protocols VALUES(%s,%s,%s,%s,%s,%s)",
            (
                prospective.PROTOCOL_ID,
                issuance_for(origin) - timedelta(days=1),
                origin,
                prospective.MODEL_ID,
                jsonb({"code_hash": "frozen", "feature_group": "price"}),
                digest(artifacts),
            ),
        )
        for a in artifacts:
            conn.execute(
                "INSERT INTO shadow_artifacts VALUES(%s,%s,%s,%s)",
                (prospective.PROTOCOL_ID, a["symbol"], a["horizon"], jsonb(a["artifact"])),
            )
    result = prospective.issue_shadow()
    assert len(result["issued"]) == 4
    assert prospective.issue_shadow()["issued"] == []
    with connection() as conn:
        assert conn.execute("SELECT count(*) n FROM forecasts").fetchone()["n"] == 10
        assert conn.execute("SELECT count(*) n FROM forecast_outcomes").fetchone()["n"] == 0
        assert conn.execute("SELECT max(data_cutoff) c FROM forecasts").fetchone()["c"] == cutoff_for(origin)
    assert prospective.progress_and_verdict()["status"] == "inconclusive"
    assert prospective.progress_and_verdict()["paired_dates"] == 1
    monkeypatch.setattr(forecasting, "now", lambda: datetime(2025, 6, 11, 23, tzinfo=UTC))
    assert forecasting.resolve_outcomes()["count"] == 10
    with connection() as conn:
        assert conn.execute("SELECT count(*) n FROM shadow_verdicts").fetchone()["n"] == 0
    monkeypatch.setattr(tuning, "implementation_hash", lambda: "changed")
    with pytest.raises(ValueError, match="differs from frozen"):
        prospective.issue_shadow()


def test_future_filing_events_cannot_enter_features():
    cutoff = datetime(2025, 1, 1, tzinfo=UTC)
    event = {
        "id": "future",
        "published_at": cutoff + timedelta(days=1),
        "acquired_at": cutoff + timedelta(days=1),
        "items": "2.02",
        "form": "8-K",
    }
    values, _, ids = financial_values([], [event], cutoff)
    assert values[-2:] == [None, None] and ids == []


def test_phase3_endpoints_are_private(db):
    from fastapi.testclient import TestClient

    from intelligence.main import app

    with TestClient(app) as client:
        assert client.get("/v1/phase3").status_code == 401
        assert client.get("/v1/phase3/predictions/" + "a" * 64).status_code == 401


def test_official_publication_property_and_name_are_preserved():
    from intelligence.ir_metadata import publication_metadata

    for attribute in ("property", "name"):
        value, parsed = publication_metadata(
            f'<meta {attribute}="press_release_publish_date" content="2026-11-04T21:10:15.701Z">'
        )
        assert value == "2026-11-04T21:10:15.701Z"
        assert parsed == datetime(2026, 11, 4, 21, 10, 15, 701000, tzinfo=UTC)


def test_confirmation_requires_126_complete_dates_and_freezes_one_verdict(db):
    # Synthetic scorer fixture in intelligence_test_* only, never a live forecast backfill.
    from uuid import uuid4

    from intelligence.calendar import schedule
    from intelligence.storage import save_manifest

    seed_prices()
    days = list(schedule(date(2024, 1, 2), date(2024, 9, 30)))
    run = uuid4()
    with connection() as conn:
        dataset = save_manifest(conn, "synthetic_confirmation_test", [])
        bar = conn.execute("SELECT id FROM daily_bars LIMIT 1").fetchone()["id"]
        conn.execute(
            "INSERT INTO model_versions(id,kind,specification) VALUES(%s,'test_only','{}') ON CONFLICT DO NOTHING",
            (prospective.MODEL_ID,),
        )
        conn.execute(
            "INSERT INTO shadow_protocols VALUES(%s,%s,%s,%s,'{}',%s)",
            (
                prospective.PROTOCOL_ID,
                issuance_for(days[0]) - timedelta(days=1),
                days[0],
                prospective.MODEL_ID,
                "a" * 64,
            ),
        )
        conn.execute(
            "INSERT INTO forecast_runs VALUES(%s,%s,%s,%s,'completed','{}')",
            (run, days[0], issuance_for(days[0]), issuance_for(days[0])),
        )
        forecasts, outcomes = [], []
        for i, day in enumerate(days[:126]):
            truth = 0.01 if i % 2 else -0.01
            for symbol in ("MSI", "AXON"):
                for h in (1, 5):
                    for model in ("historical-baseline-v1", prospective.MODEL_ID):
                        identity = uuid4()
                        shadow = model == prospective.MODEL_ID
                        expected = truth if shadow else truth + 0.02
                        prob = (0.9 if truth > 0 else 0.1) if shadow else (0.1 if truth > 0 else 0.9)
                        forecasts.append(
                            (
                                identity,
                                run,
                                symbol,
                                day,
                                h,
                                issuance_for(day),
                                cutoff_for(day),
                                days[i + 1],
                                days[i + h],
                                day,
                                dataset,
                                model,
                                expected,
                                prob,
                            )
                        )
                        outcomes.append((uuid4(), identity, issuance_for(days[i + h]), truth, bar, bar))
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO forecasts(id,run_id,symbol,origin_session,horizon,issued_at,data_cutoff,entry_session,
                target_session,feed,adjustment,adjustment_asof,dataset_id,model_id,feature_version,expected_return,
                probability_positive,lower_return,upper_return,sample_count,provenance)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'sip','split',%s,%s,%s,'test_only',%s,%s,-0.1,0.1,252,'{"synthetic":true}')""",
                forecasts,
            )
            cursor.executemany(
                "INSERT INTO forecast_outcomes(id,forecast_id,resolved_at,realized_return,entry_bar_id,exit_bar_id,provenance) VALUES(%s,%s,%s,%s,%s,%s,'{\"synthetic\":true}')",
                outcomes[:-1],
            )
    assert prospective.progress_and_verdict(finalize=True)["status"] == "inconclusive"
    with connection() as conn:
        last = outcomes[-1]
        conn.execute(
            "INSERT INTO forecast_outcomes(id,forecast_id,resolved_at,realized_return,entry_bar_id,exit_bar_id,provenance) VALUES(%s,%s,%s,%s,%s,%s,'{\"synthetic\":true}')",
            last,
        )
    result = prospective.progress_and_verdict(finalize=True)
    assert result["status"] == "passed" and result["final_verdict"]["dates"] == 126
    from intelligence.util import canonical

    assert canonical(prospective.progress_and_verdict(finalize=True)) == canonical(result)
    with connection() as conn:
        assert conn.execute("SELECT count(*) n FROM shadow_verdicts").fetchone()["n"] == 1
