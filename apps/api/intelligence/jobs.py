import time
from datetime import time as daytime
from uuid import uuid4

from intelligence.calendar import EASTERN, schedule
from intelligence.db import connection
from intelligence.forecasting import issue_forecasts, resolve_outcomes
from intelligence.ingestion import ingest_all, resource_check
from intelligence.research import run_evaluation
from intelligence.storage import jsonb, watchlist
from intelligence.util import canonical, now
from intelligence.vision import resolve_detection_outcomes

KINDS = {
    "refresh",
    "issue",
    "resolve",
    "evaluate",
    "vision",
    "research",
    "audit",
    "monitor",
    "information",
    "tune",
    "nominate",
}


def enqueue(kind, payload=None, conn=None):
    if kind not in KINDS:
        raise ValueError("Unsupported job kind")
    job_id = uuid4()

    def insert(c):
        c.execute("INSERT INTO jobs(id,kind,payload) VALUES(%s,%s,%s)", (job_id, kind, jsonb(payload or {})))
        c.execute(
            "INSERT INTO job_events(job_id,event,detail) VALUES(%s,'queued',%s)",
            (job_id, jsonb({"kind": kind})),
        )

    if conn:
        insert(conn)
    else:
        with connection() as c:
            insert(c)
    return str(job_id)


def dispatch(kind, payload):
    resource_check()
    if kind == "refresh":
        with connection() as conn:
            symbols = payload.get("symbols") or [*watchlist(conn)["symbols"], "SPY"]
            known = {r["symbol"] for r in conn.execute("SELECT symbol FROM securities").fetchall()}
        if not set(symbols) <= known or len(symbols) > 26:
            raise ValueError("Refresh symbols must belong to the verified securities registry")
        result = ingest_all(symbols)
        from intelligence.information import refresh_information
        from intelligence.ir_metadata import enrich_ir_publications

        try:
            result["information"] = refresh_information()
            result["information"]["publication_metadata"] = enrich_ir_publications()
            if result["information"]["news"]["status"] != "complete":
                result["status"] = "partial"
        except Exception:
            result["information"] = {
                "status": "unavailable",
                "reason": "Information refresh failed; reference data preserved",
            }
            result["status"] = "partial"
        result["outcomes"] = resolve_outcomes()
        result["detection_outcomes"] = resolve_detection_outcomes()
        return result
    if kind == "issue":
        result = issue_forecasts()
        from intelligence.prospective import issue_shadow

        try:
            result["shadow"] = issue_shadow()
        except Exception as exc:
            result["shadow"] = {
                "status": "skipped",
                "reason": f"{type(exc).__name__}: frozen shadow failed; reference retained",
            }
        return result
    if kind == "resolve":
        return {"forecasts": resolve_outcomes(), "detections": resolve_detection_outcomes()}
    if kind == "evaluate":
        return run_evaluation(payload["symbol"])
    if kind in {"research", "audit"}:
        from intelligence.experiments import audit_original, run_suite

        return run_suite() if kind == "research" else audit_original()
    if kind == "monitor":
        from intelligence.monitoring import monitor
        from intelligence.prospective import progress_and_verdict

        return {**monitor(), "shadow": progress_and_verdict(finalize=True)}
    if kind == "information":
        from intelligence.information import refresh_information
        from intelligence.ir_metadata import enrich_ir_publications

        result = refresh_information()
        result["publication_metadata"] = enrich_ir_publications()
        return result
    if kind == "tune":
        from intelligence.tuning import run_tuning

        return run_tuning()
    if kind == "nominate":
        from intelligence.prospective import nominate

        return nominate()
    if kind == "vision":
        from intelligence.vision_gateway import submit_and_collect

        return submit_and_collect(payload["symbol"], payload["model_id"])
    raise ValueError("Unsupported job")


def run_once():
    # Session advisory lock spans external calls. A second worker cannot
    # reclaim a slow active job. An exited process releases the lock itself.
    with connection() as lease:
        if not lease.execute("SELECT pg_try_advisory_lock(753194020) AS acquired").fetchone()["acquired"]:
            return None
        lease.commit()
        try:
            with connection() as conn:
                recovered = conn.execute(
                    "UPDATE jobs SET status='queued',error='Previous worker exited; idempotent retry' WHERE status='running' RETURNING id"
                ).fetchall()
                for previous in recovered:
                    conn.execute(
                        "INSERT INTO job_events(job_id,event,detail) VALUES(%s,'recovered','{}')",
                        (previous["id"],),
                    )
                job = conn.execute(
                    "SELECT * FROM jobs WHERE status='queued' ORDER BY CASE kind WHEN 'issue' THEN 0 WHEN 'refresh' THEN 1 WHEN 'resolve' THEN 2 ELSE 3 END,created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                ).fetchone()
                if not job:
                    return None
                conn.execute(
                    "UPDATE jobs SET status='running',started_at=now(),heartbeat_at=now(),attempts=attempts+1 WHERE id=%s",
                    (job["id"],),
                )
                conn.execute(
                    "INSERT INTO job_events(job_id,event,detail) VALUES(%s,'started','{}')", (job["id"],)
                )
            try:
                result = dispatch(job["kind"], job["payload"])
                canonical(result)  # Fail safely before the persistence transaction.
                status = "completed"
                if job["kind"] == "refresh" and result.get("status") == "partial":
                    status = "partial"
                if job["kind"] == "issue" and result.get("status") == "skipped":
                    status = "skipped"
                error = None
            except Exception as exc:
                import logging

                logging.getLogger("intelligence.worker").exception("Research job %s failed", job["id"])
                # External messages are deliberately sanitized by provider clients.
                result, status, error = (
                    None,
                    "failed",
                    str(exc)[:500]
                    if isinstance(exc, ValueError)
                    else f"{type(exc).__name__}: inspect local worker logs",
                )
            with connection() as conn:
                conn.execute(
                    "UPDATE jobs SET status=%s,completed_at=now(),heartbeat_at=now(),result=%s,error=%s WHERE id=%s",
                    (status, jsonb(result), error, job["id"]),
                )
                conn.execute(
                    "INSERT INTO job_events(job_id,event,detail) VALUES(%s,%s,%s)",
                    (job["id"], status, jsonb({"error": error})),
                )
            return {"id": str(job["id"]), "status": status, "error": error, "result": result}
        finally:
            lease.execute("SELECT pg_advisory_unlock(753194020)")


def schedule_jobs():
    instant = now()
    local = instant.astimezone(EASTERN)
    day = local.date()
    if day not in schedule(day, day):
        return
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194021)")
        for task, kind, after, before in [
            ("refresh", "refresh", daytime(19), daytime(19, 30)),
            ("refresh_1930", "refresh", daytime(19, 30), daytime(19, 50)),
            ("refresh_1950", "refresh", daytime(19, 50), daytime(20)),
            ("issue", "issue", daytime(20, 30), daytime(23, 59, 59)),
            ("monitor", "monitor", daytime(20, 35), daytime(23, 59, 59)),
        ]:
            if (
                after <= local.time().replace(tzinfo=None) < before
                and not conn.execute(
                    "SELECT 1 FROM scheduler_marks WHERE task=%s AND session_date=%s", (task, day)
                ).fetchone()
            ):
                # A slow refresh must not create duplicate pending refreshes.
                if (
                    kind == "refresh"
                    and conn.execute(
                        "SELECT 1 FROM jobs WHERE kind='refresh' AND status IN ('queued','running')"
                    ).fetchone()
                ):
                    continue
                job_id = enqueue(kind, conn=conn)
                conn.execute(
                    "INSERT INTO scheduler_marks(task,session_date,job_id) VALUES(%s,%s,%s)",
                    (task, day, job_id),
                )


def worker():
    while True:
        result = None
        try:
            with connection() as conn:
                conn.execute("""INSERT INTO worker_status VALUES('research-worker',now(),'{"status":"polling"}')
                    ON CONFLICT(id) DO UPDATE SET heartbeat_at=now(),detail=EXCLUDED.detail""")
            schedule_jobs()
            result = run_once()
            if result:
                print({k: v for k, v in result.items() if k != "result"}, flush=True)
        except Exception:
            import logging

            logging.getLogger("intelligence.worker").exception(
                "Worker polling failed; retrying without changing evidence"
            )
        time.sleep(2 if result else 30)
