import re
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from chart_intelligence.api import router as chart_router
from intelligence.calendar import cutoff_for, issuance_window
from intelligence.config import settings
from intelligence.db import connection
from intelligence.financials import calculate_metrics
from intelligence.forecasting import continuity
from intelligence.jobs import enqueue
from intelligence.lab_api import router as lab_router
from intelligence.paper_bridge import router as paper_router
from intelligence.security import COOKIE, COOKIE_PATH, login, origin_check, require_session
from intelligence.storage import bars_at, forecast_history, jsonb, watchlist
from intelligence.util import now
from intelligence.vision import model_status, render_chart


@asynccontextmanager
async def lifespan(app):
    if settings.environment == "production" and (
        not settings.origin.startswith("https://") or settings.min_free_gb < 10
    ):
        raise RuntimeError("Production requires HTTPS and at least 10 GB storage reserve")
    with connection() as conn:
        conn.execute("SELECT 1")
    yield


app = FastAPI(
    title="KefTrade Intelligence",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.include_router(paper_router)
app.include_router(lab_router)
app.include_router(chart_router)


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(Exception)
async def unhandled(request, exc):
    import logging

    logging.getLogger("intelligence").exception("Request failed", exc_info=exc)
    return JSONResponse(
        {"detail": "The research service is unavailable. Check its local logs and database connection."},
        status_code=503,
    )


@app.get("/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok", "application": "keftrade-intelligence", "execution_authority": "none"}


class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


@app.post("/v1/auth/login")
def sign_in(body: Login, request: Request, response: Response):
    origin_check(request)
    # All UI calls arrive through the local Next BFF; use an account-wide
    # throttle as well as denying caller-controlled forwarding identities.
    token, csrf, expires = login(body.username, body.password, "single-admin")
    response.set_cookie(
        COOKIE,
        token,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        max_age=7 * 86400,
    )
    return {"authenticated": True, "csrf_token": csrf, "expires_at": expires}


@app.get("/v1/auth/session")
def auth_session(session=Depends(require_session)):
    return {"authenticated": True, "username": settings.admin_username, "csrf_token": session["csrf_token"]}


@app.post("/v1/auth/logout")
def logout(response: Response, session=Depends(require_session)):
    with connection() as conn:
        conn.execute("UPDATE sessions SET revoked_at=now() WHERE token_hash=%s", (session["token_hash"],))
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return {"authenticated": False}


def valid_symbol(symbol):
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
        raise HTTPException(422, "Invalid symbol")
    return symbol


@app.get("/v1/workspace", dependencies=[Depends(require_session)])
def workspace():
    with connection() as conn:
        listing = watchlist(conn)
        companies = []
        for symbol in listing["symbols"]:
            security = conn.execute("SELECT * FROM securities WHERE symbol=%s", (symbol,)).fetchone()
            bars = bars_at(conn, symbol)
            latest = bars[-1] if bars else None
            previous = bars[-2] if len(bars) > 1 else None
            facts = conn.execute(
                "SELECT max(acquired_at) AS acquired_at,count(*) AS count FROM financial_facts WHERE symbol=%s",
                (symbol,),
            ).fetchone()
            companies.append(
                {
                    **security,
                    "latest": latest,
                    "daily_change": float(latest["close"] / previous["close"] - 1) if previous else None,
                    "bar_count": len(bars),
                    "financial_fact_count": facts["count"],
                    "financials_acquired_at": facts["acquired_at"],
                }
            )
        universe = conn.execute(
            "SELECT * FROM training_universe_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        jobs = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 15").fetchall()
        runs = conn.execute("SELECT * FROM forecast_runs ORDER BY started_at DESC LIMIT 5").fetchall()
        models = [
            {**m, **model_status(m)}
            for m in conn.execute("SELECT * FROM vision_models ORDER BY id").fetchall()
        ]
        _, waiting = issuance_window(now())
        return {
            "watchlist": listing,
            "companies": companies,
            "training_universe": universe,
            "jobs": jobs,
            "forecast_runs": runs,
            "vision_models": models,
            "feed": settings.feed,
            "generated_at": now(),
            "issuance_status": waiting or "The current prospective issuance window is open",
            "execution_authority": "none",
            "schedule": {"cutoff": "20:00 America/New_York", "issuance": "20:30 America/New_York"},
        }


class WatchlistUpdate(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=25)


@app.put("/v1/watchlist")
def update_watchlist(body: WatchlistUpdate, session=Depends(require_session)):
    symbols = list(dict.fromkeys(valid_symbol(s.upper()) for s in body.symbols))
    with connection() as conn:
        known = {
            r["symbol"] for r in conn.execute("SELECT symbol FROM securities WHERE NOT benchmark").fetchall()
        }
        if not set(symbols) <= known:
            raise HTTPException(
                422, "Add verified company identities through the registry CLI before changing the watchlist"
            )
        row = conn.execute(
            "INSERT INTO watchlist_versions(symbols) VALUES(%s) RETURNING *", (jsonb(symbols),)
        ).fetchone()
    return row


@app.get("/v1/companies/{symbol}", dependencies=[Depends(require_session)])
def company(symbol: str, as_of: datetime | None = None):
    valid_symbol(symbol)
    instant = as_of or now()
    if instant.tzinfo is None or instant > now():
        raise HTTPException(422, "as_of must be an aware timestamp no later than now")
    with connection() as conn:
        security = conn.execute("SELECT * FROM securities WHERE symbol=%s", (symbol,)).fetchone()
        if not security:
            raise HTTPException(404, "Company is not registered")
        bars = bars_at(conn, symbol, instant)
        facts = conn.execute("SELECT * FROM financial_facts WHERE symbol=%s", (symbol,)).fetchall()
        financials = calculate_metrics(facts, instant)
        valid, reason = continuity(bars)
        reports = conn.execute(
            "SELECT * FROM evaluation_reports WHERE symbol=%s ORDER BY created_at DESC LIMIT 5", (symbol,)
        ).fetchall()
        actions = conn.execute(
            "SELECT action_type,effective_date,payload FROM corporate_actions WHERE symbol=%s ORDER BY effective_date DESC LIMIT 20",
            (symbol,),
        ).fetchall()
        detections = conn.execute(
            "SELECT * FROM chart_detections WHERE symbol=%s ORDER BY created_at DESC LIMIT 10", (symbol,)
        ).fetchall()
        source_refs = conn.execute(
            "SELECT id,provider,url,content_hash,acquired_at FROM source_documents WHERE id IN (SELECT source_id FROM financial_facts WHERE symbol=%s) ORDER BY acquired_at DESC LIMIT 5",
            (symbol,),
        ).fetchall()
        from intelligence.monitoring import reliability, risk_summary

        history = forecast_history(conn, symbol, limit=20000)
        experiments = conn.execute(
            "SELECT * FROM research_experiments WHERE symbol=%s ORDER BY horizon,candidate_id", (symbol,)
        ).fetchall()
        return {
            "security": security,
            "bars": bars[-300:],
            "financials": financials,
            "coverage": {
                "bar_count": len(bars),
                "first": bars[0]["session_date"] if bars else None,
                "last": bars[-1]["session_date"] if bars else None,
                "contiguous": valid,
                "reason": reason,
                "feed": settings.feed,
                "adjustment": "split",
                "financial_fact_count": len(facts),
                "last_acquired_at": max([r["acquired_at"] for r in bars], default=None),
            },
            "forecasts": history[:400],
            "reports": reports,
            "corporate_actions": actions,
            "detections": detections,
            "sources": source_refs,
            "reliability": reliability(history),
            "risk": risk_summary(bars),
            "experiments": experiments,
            "research_plans": conn.execute(
                "SELECT * FROM research_plans ORDER BY created_at DESC"
            ).fetchall(),
            "audit": conn.execute(
                "SELECT * FROM research_audits ORDER BY created_at DESC LIMIT 1"
            ).fetchone(),
        }


@app.get("/v1/forecasts", dependencies=[Depends(require_session)])
def forecasts(symbol: str | None = None):
    if symbol:
        valid_symbol(symbol)
    with connection() as conn:
        return {"forecasts": forecast_history(conn, symbol)}


@app.get("/v1/detections/{detection_id}/chart", dependencies=[Depends(require_session)])
def detection_chart(detection_id: UUID):
    import io

    from PIL import Image, ImageDraw

    from intelligence.util import digest
    from intelligence.vision_gateway import validate_boxes

    with connection() as conn:
        detection = conn.execute("SELECT * FROM chart_detections WHERE id=%s", (detection_id,)).fetchone()
        if not detection:
            raise HTTPException(404, "Detection not found")
        manifest = detection["payload"]["manifest"]
        rows = conn.execute(
            "SELECT * FROM daily_bars WHERE id=ANY(%s) ORDER BY session_date", (manifest["bar_ids"],)
        ).fetchall()
    content, rebuilt, image_hash = render_chart(rows, detection["cutoff"])
    if image_hash != detection["image_hash"] or digest(rebuilt) != detection["input_hash"]:
        raise HTTPException(409, "Archived chart renderer or input identity mismatch")
    image = Image.open(io.BytesIO(content)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for box in validate_boxes(detection["payload"]["boxes"]):
        draw.rectangle(box["xyxy"], outline="#326f9a", width=2)
        draw.text(
            (box["xyxy"][0] + 3, max(0, box["xyxy"][1] - 13)),
            f"{box['label']} {box['confidence']:.2f}",
            fill="#20384d",
        )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png")


@app.get("/v1/models/performance", dependencies=[Depends(require_session)])
def performance():
    with connection() as conn:
        return {
            "reports": conn.execute(
                "SELECT * FROM evaluation_reports ORDER BY created_at DESC LIMIT 50"
            ).fetchall(),
            "universe": conn.execute(
                "SELECT * FROM training_universe_versions ORDER BY id DESC LIMIT 1"
            ).fetchone(),
            "models": conn.execute("SELECT * FROM model_versions").fetchall(),
        }


@app.get("/v1/research", dependencies=[Depends(require_session)])
def research_ledger():
    with connection() as conn:
        return {
            "plans": conn.execute("SELECT * FROM research_plans ORDER BY created_at DESC").fetchall(),
            "candidates": conn.execute("SELECT * FROM research_candidates ORDER BY id").fetchall(),
            "experiments": conn.execute(
                "SELECT * FROM research_experiments ORDER BY symbol,horizon,candidate_id"
            ).fetchall(),
            "audits": conn.execute(
                "SELECT * FROM research_audits ORDER BY created_at DESC LIMIT 5"
            ).fetchall(),
        }


@app.get("/v1/research/experiments/{experiment_id}/predictions", dependencies=[Depends(require_session)])
def experiment_predictions(experiment_id: str):
    if not re.fullmatch(r"[a-f0-9]{64}", experiment_id):
        raise HTTPException(422, "Invalid experiment identity")
    with connection() as conn:
        experiment = conn.execute(
            "SELECT * FROM research_experiments WHERE id=%s", (experiment_id,)
        ).fetchone()
        if not experiment:
            raise HTTPException(404, "Experiment not found")
        return {
            "kind": "historical_development_not_prospective",
            "model_version": experiment["candidate_id"],
            "dataset_id": experiment["report"].get("dataset_id"),
            "predictions": [
                {
                    **r,
                    "data_cutoff": cutoff_for(r["origin_session"]),
                    "availability": "reconstructed_historical_not_observed_delivery",
                }
                for r in conn.execute(
                    "SELECT * FROM research_predictions WHERE experiment_id=%s ORDER BY origin_session",
                    (experiment_id,),
                ).fetchall()
            ],
        }


@app.get("/v1/monitor", dependencies=[Depends(require_session)])
def monitoring():
    from intelligence.monitoring import monitor

    return monitor()


@app.get("/v1/phase3", dependencies=[Depends(require_session)])
def phase3(symbol: str | None = None):
    from intelligence.phase3_status import phase3_status

    if symbol:
        valid_symbol(symbol)
    return phase3_status(symbol)


@app.get("/v1/phase3/predictions/{result_id}", dependencies=[Depends(require_session)])
def phase3_predictions(result_id: str):
    if not re.fullmatch(r"[a-f0-9]{64}", result_id):
        raise HTTPException(422, "Invalid result identity")
    with connection() as conn:
        row = conn.execute("SELECT predictions FROM candidate_results WHERE id=%s", (result_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Result not found")
        return {"kind": "historical_development_not_prospective", **row}


@app.get("/v1/companies/{symbol}/chart", dependencies=[Depends(require_session)])
def chart_input(symbol: str):
    valid_symbol(symbol)
    instant = now()
    with connection() as conn:
        bars = bars_at(conn, symbol, instant, instant)
    try:
        content, _, _ = render_chart(bars, instant)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return Response(content, media_type="image/png")


class NewJob(BaseModel):
    kind: str
    symbol: str | None = None
    model_id: str | None = None


@app.post("/v1/jobs", status_code=202)
def new_job(body: NewJob, session=Depends(require_session)):
    if body.kind not in {
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
    }:
        raise HTTPException(422, "Unsupported job kind")
    if body.kind in {"evaluate", "vision"} and not body.symbol:
        raise HTTPException(422, "This job requires a company symbol")
    payload = {}
    with connection() as conn:
        if body.symbol:
            valid_symbol(body.symbol)
            if not conn.execute("SELECT 1 FROM securities WHERE symbol=%s", (body.symbol,)).fetchone():
                raise HTTPException(404, "Unknown company")
            payload["symbol"] = body.symbol
        if body.kind == "vision":
            model = conn.execute("SELECT * FROM vision_models WHERE id=%s", (body.model_id,)).fetchone()
            if not model:
                raise HTTPException(404, "Unknown vision model")
            state = model_status(model)
            if not state["available"]:
                raise HTTPException(409, state["message"])
            payload["model_id"] = body.model_id
        if (
            conn.execute("SELECT count(*) AS n FROM jobs WHERE status IN ('queued','running')").fetchone()[
                "n"
            ]
            >= 5
        ):
            raise HTTPException(429, "Research queue is full; wait for existing jobs")
    return {"id": enqueue(body.kind, payload), "status": "queued"}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_session)])
def job(job_id: UUID):
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    return row
