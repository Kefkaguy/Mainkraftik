from copy import deepcopy
from datetime import date, timedelta

import numpy as np
import psycopg
import pytest
from sklearn.linear_model import LogisticRegression, Ridge
from test_calendar_and_predictions import make_bars
from test_persistence_and_auth import seed_prices

from intelligence import experiments, forecasting, jobs
from intelligence.calendar import cutoff_for, issuance_for
from intelligence.db import connection
from intelligence.research import fit_heads, predict_heads
from intelligence.research_models import CANDIDATES, fit_predict, metrics
from intelligence.storage import jsonb, save_manifest, store_document
from intelligence.util import now


def test_original_numpy_heads_reconcile_established_library():
    rng = np.random.default_rng(9)
    x = rng.normal(size=(300, 5))
    y = x[:, 0] * 0.02 + rng.normal(size=300) * 0.03
    fitted = fit_heads(x, y)
    mean, p = predict_heads(fitted, x)
    z = (x - fitted["mean"]) / fitted["scale"]
    reg = Ridge(alpha=1).fit(z, y)
    cls = LogisticRegression(C=1, max_iter=1000, tol=1e-10).fit(z, y > 0)
    np.testing.assert_allclose(mean, reg.predict(z), atol=1e-9)
    np.testing.assert_allclose(p, cls.predict_proba(z)[:, 1], atol=1e-6)


def test_metrics_known_values_and_invalid_scores():
    report = metrics([-0.1, 0.1], [0, 0], [0.5, 0.5], [-0.2, -0.2], [0.2, 0.2])
    assert report["mae"] == pytest.approx(0.1)
    assert report["rmse"] == pytest.approx(0.1)
    assert report["bias"] == 0
    assert report["brier"] == 0.25
    assert report["interval_coverage"] == 1
    with pytest.raises(ValueError, match="Probability"):
        metrics([1], [0], [2])


def test_enriched_features_never_read_future_open_or_prices():
    bars = make_bars()
    past = experiments.enriched_panel(bars[:200], bars[:200])
    future = deepcopy(bars)
    for row in future[200:]:
        row["open"] *= 100
        row["close"] *= 100
    assert past == experiments.enriched_panel(future, future)[: len(past)]


def test_stock_and_benchmark_adjustment_snapshots_cannot_mix():
    bars = make_bars()[:200]
    benchmark = deepcopy(bars)
    for row in benchmark:
        row["adjustment_asof"] = date(2030, 1, 1)
    with pytest.raises(ValueError, match="share feed and adjustment snapshot"):
        experiments.enriched_panel(bars, benchmark)


def groups():
    bars = make_bars()
    panel = experiments.enriched_panel(bars, bars)
    boundaries = experiments.partition_dates([panel, panel])
    return experiments.labeled_partitions(panel, bars, 5, boundaries), boundaries


def test_shared_temporal_boundaries_purge_and_contain_labels():
    blocks, boundaries = groups()
    assert max(r["date"] for r in blocks["train"]) < boundaries["train_end_exclusive"]
    assert max(r["end"] for r in blocks["train"]) < boundaries["calibration_start"]
    assert max(r["date"] for r in blocks["calibration"]) < boundaries["calibration_end_exclusive"]
    assert max(r["end"] for r in blocks["calibration"]) < boundaries["evaluation_start"]
    assert min(r["date"] for r in blocks["test"]) == boundaries["evaluation_start"]
    assert not ({r["date"] for r in blocks["train"]} & {r["date"] for r in blocks["test"]})


@pytest.mark.parametrize(
    "candidate",
    ["linear-market-v2", "histboost-market-v2", "forest-market-v2", "volatility-v2", "ensemble-market-v2"],
)
def test_candidates_are_deterministic_and_test_labels_cannot_change_fit(candidate):
    blocks, _ = groups()
    train, cal, test = (blocks[k][:160] for k in ["train", "calibration", "test"])
    original = fit_predict(CANDIDATES[candidate], train, cal, test, 5)
    changed = deepcopy(test)
    for row in changed:
        row["y"] = 200
    second = fit_predict(CANDIDATES[candidate], train, cal, changed, 5)
    for key in ["mean", "probability", "lower", "upper"]:
        np.testing.assert_array_equal(original[key], second[key])
    assert original["artifact"] == second["artifact"]
    assert np.all((original["probability"] >= 0) & (original["probability"] <= 1))


def test_development_success_cannot_become_validation_pass():
    blocks, _ = groups()
    report, records = experiments.evaluate_candidate(CANDIDATES["linear-market-v2"], blocks, "MSI", 5)
    assert report["status"] == "inconclusive"
    assert records
    assert all(c["validation_status"] == "inconclusive" for c in report["comparisons"].values())


def seed_development():
    bars = make_bars(start=date(2021, 8, 30), end=date(2026, 11, 12))
    with connection() as conn:
        source = store_document(conn, "test_only", "https://example.invalid/research", {}, now())
        for symbol in ("MSI", "AXON", "SPY"):
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO daily_bars(symbol,session_date,feed,adjustment,adjustment_asof,open,high,low,close,volume,available_at,acquired_at,source_id,content_hash)
                    VALUES(%s,%s,'sip','split',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (
                            symbol,
                            b["session_date"],
                            b["adjustment_asof"],
                            b["open"],
                            b["high"],
                            b["low"],
                            b["close"],
                            b["volume"],
                            b["available_at"],
                            b["acquired_at"],
                            source,
                            symbol + b["content_hash"],
                        )
                        for b in bars
                    ],
                )
        dataset = save_manifest(conn, "test_only", [])
        from uuid import uuid4

        conn.execute(
            "INSERT INTO evaluation_reports(id,model_id,symbol,dataset_id,status,protocol,report) VALUES(%s,'technical-v1','MSI',%s,'failed','{}',%s)",
            (uuid4(), dataset, jsonb({"horizons": [{"partitions": {"evaluation_start": "2025-11-14"}}]})),
        )


def test_frozen_plan_seals_exposed_dates_and_rejects_code_change(db, monkeypatch):
    seed_development()
    plan = experiments.declare_plan()
    with connection() as conn:
        streams = experiments.frozen_streams(conn, plan)
        assert all(b["session_date"] < date(2025, 11, 14) for stream in streams.values() for b in stream)
        conn.execute("INSERT INTO watchlist_versions(symbols) VALUES('[\"MSI\"]')")
    assert experiments.declare_plan()["dataset_id"] == plan["dataset_id"]
    assert plan["specification"]["membership"] == ["MSI", "AXON"]
    from intelligence.research import run_evaluation

    assert run_evaluation("MSI")["holdout_sealed"]
    with pytest.raises(ValueError, match="holdout is sealed"):
        run_evaluation("AXON")
    monkeypatch.setattr(experiments, "code_hash", lambda: "changed")
    with pytest.raises(ValueError, match="Frozen research code changed"):
        experiments.declare_plan()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with connection() as conn:
            conn.execute("UPDATE research_plans SET protected_start='2030-01-01'")


def test_scheduler_real_dispatch_issues_once_and_late_refresh_never_backfills(db, monkeypatch):
    origin = date(2025, 6, 2)
    seed_prices(origin)
    real_dispatch = jobs.dispatch
    monkeypatch.setattr(
        jobs,
        "dispatch",
        lambda kind, payload: (
            {"fixture_refresh": True} if kind == "refresh" else real_dispatch(kind, payload)
        ),
    )
    monkeypatch.setattr(jobs, "now", lambda: cutoff_for(origin) - timedelta(hours=1))
    jobs.schedule_jobs()
    jobs.schedule_jobs()
    assert jobs.run_once()["status"] == "completed"
    assert jobs.run_once() is None
    monkeypatch.setattr(jobs, "now", lambda: issuance_for(origin))
    monkeypatch.setattr(forecasting, "now", lambda: issuance_for(origin))
    jobs.schedule_jobs()
    jobs.schedule_jobs()
    issued = jobs.run_once()
    assert len(issued["result"]["issued"]) == 6
    assert jobs.run_once() is None
    with connection() as conn:
        assert conn.execute("SELECT count(*) n FROM forecasts").fetchone()["n"] == 6
        events = conn.execute(
            "SELECT event FROM job_events WHERE job_id=%s ORDER BY id", (issued["id"],)
        ).fetchall()
        assert [e["event"] for e in events] == ["queued", "started", "completed"]


def test_scheduler_half_day_and_holiday_retry_slots(db, monkeypatch):
    # Thanksgiving has no jobs; Friday is a shortened session but cutoff stays 20:00 NY.
    monkeypatch.setattr(jobs, "now", lambda: cutoff_for(date(2026, 11, 26)) - timedelta(minutes=5))
    jobs.schedule_jobs()
    assert jobs.run_once() is None
    monkeypatch.setattr(jobs, "now", lambda: cutoff_for(date(2026, 11, 27)) - timedelta(minutes=5))
    jobs.schedule_jobs()
    with connection() as conn:
        assert conn.execute("SELECT task FROM scheduler_marks").fetchone()["task"] == "refresh_1950"
    monkeypatch.setattr(jobs, "now", lambda: cutoff_for(date(2026, 11, 27)) + timedelta(minutes=1))
    jobs.schedule_jobs()
    with connection() as conn:
        assert conn.execute("SELECT count(*) n FROM jobs").fetchone()["n"] == 1


def test_failed_job_and_unserializable_result_do_not_poison_next_job(db, monkeypatch):
    from uuid import uuid4

    jobs.enqueue("audit")
    jobs.enqueue("monitor")
    monkeypatch.setattr(
        jobs,
        "dispatch",
        lambda kind, payload: {"bad": object()} if kind == "audit" else {"identifier": uuid4()},
    )
    assert jobs.run_once()["status"] == "failed"
    assert jobs.run_once()["status"] == "completed"
    assert jobs.run_once() is None
    with connection() as conn:
        assert conn.execute("SELECT count(*) n FROM job_events WHERE event='failed'").fetchone()["n"] == 1


def test_provider_partial_and_missed_cutoff_are_explicit_job_states(db, monkeypatch):
    jobs.enqueue("refresh")
    monkeypatch.setattr(
        jobs, "dispatch", lambda kind, payload: {"status": "partial", "reason": "Provider unavailable"}
    )
    assert jobs.run_once()["status"] == "partial"
    jobs.enqueue("issue")
    monkeypatch.setattr(jobs, "dispatch", lambda kind, payload: {"status": "skipped", "issued": []})
    assert jobs.run_once()["status"] == "skipped"


def test_research_and_monitor_routes_require_authentication(db):
    from fastapi.testclient import TestClient

    from intelligence.main import app

    with TestClient(app) as client:
        for path in ["/v1/research", "/v1/monitor", "/v1/research/experiments/" + "a" * 64 + "/predictions"]:
            assert client.get(path).status_code == 401
