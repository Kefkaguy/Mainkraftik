import asyncio
import contextlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import date as SessionDate
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from websockets.asyncio.client import connect as ws_connect

from intelligence.calendar import EASTERN
from intelligence.util import canonical, now
from paperpilot.broker import PaperBroker, verify_account
from paperpilot.config import current_symbols, quote_symbols, settings
from paperpilot.data import latest_sources, save_quote
from paperpilot.db import connect, event, migrate
from paperpilot.engine import Engine, disable, record_order
from paperpilot.outcomes import AGGREGATE_SCOPE, metric_views

engine = Engine()


def trace_records():
    """Read immutable evidence; never backfill or rewrite original decisions."""
    with connect() as conn:
        signals=conn.execute("SELECT id,symbol,strategy,role,action,issued_at,payload->'decision_trace' AS trace FROM pilot_strategy_signals ORDER BY issued_at DESC,id LIMIT 100").fetchall()
        traces=conn.execute("SELECT created_at,kind,payload FROM pilot_events WHERE kind IN ('decision_trace','strategy_entry_gate') ORDER BY id DESC LIMIT 100").fetchall()
    return {"signals":signals,"events":traces,"historical_policy":"Original records preserved; absent traces mean NOT_RECORDED, not passed gates"}


def authorization(x_pilot_token: str = Header(default="")):
    if not settings.paper_internal_token or not secrets.compare_digest(
        x_pilot_token, settings.paper_internal_token
    ):
        raise HTTPException(403, "Private pilot bridge authorization required")


async def stream(kind):
    delay = 2
    while True:
        try:
            with connect() as conn:
                verified = conn.execute("SELECT payload FROM pilot_connection WHERE id='paper'").fetchone()
            if not settings.paper_key or not verified or verified["payload"].get("status") != "verified":
                await asyncio.sleep(30)
                continue
            url = (
                "wss://stream.data.alpaca.markets/v2/iex"
                if kind == "market"
                else "wss://paper-api.alpaca.markets/stream"
            )
            async with ws_connect(
                url, open_timeout=10, ping_interval=20, ping_timeout=20, max_queue=32
            ) as ws:
                await ws.send(
                    json.dumps({"action": "auth", "key": settings.paper_key, "secret": settings.paper_secret})
                )
                authenticated = False
                while not authenticated:
                    payload = json.loads(await asyncio.wait_for(ws.recv(), 15))
                    messages = payload if isinstance(payload, list) else [payload]
                    for message in messages:
                        if (
                            message.get("T") == "error"
                            or message.get("data", {}).get("status") == "unauthorized"
                        ):
                            raise ValueError("Streaming entitlement/authentication unavailable")
                        if (
                            message.get("msg") == "authenticated"
                            or message.get("data", {}).get("status") == "authorized"
                        ):
                            authenticated = True
                subscription = (
                    {"action": "subscribe", "quotes": list(quote_symbols())}
                    if kind == "market"
                    else {"action": "listen", "data": {"streams": ["trade_updates"]}}
                )
                await ws.send(json.dumps(subscription))
                engine.connection(
                    kind + "_stream",
                    {"status": "connected", "feed": "iex" if kind == "market" else "paper_only"},
                )
                delay = 2
                subscribed = set(subscription.get("quotes", []))
                saved = {}
                recheck = 0
                while True:
                    if kind == "market" and asyncio.get_running_loop().time() - recheck >= 5:
                        recheck = asyncio.get_running_loop().time()
                        desired = set(await asyncio.to_thread(quote_symbols))
                        if desired != subscribed:
                            if subscribed - desired:
                                await ws.send(
                                    json.dumps(
                                        {"action": "unsubscribe", "quotes": sorted(subscribed - desired)}
                                    )
                                )
                            if desired - subscribed:
                                await ws.send(
                                    json.dumps(
                                        {"action": "subscribe", "quotes": sorted(desired - subscribed)}
                                    )
                                )
                            subscribed = desired
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 5)
                    except TimeoutError:
                        continue
                    payload = json.loads(raw)
                    messages = payload if isinstance(payload, list) else [payload]
                    for message in messages:
                        if message.get("T") == "error":
                            raise ValueError("Stream rejected; polling fallback active")
                        if kind == "market" and message.get("T") == "q":
                            tick = datetime.fromisoformat(message["t"].replace("Z", "+00:00"))
                            previous = saved.get(message["S"])
                            if previous and (tick - previous).total_seconds() < 1:
                                continue
                            saved[message["S"]] = tick
                            await asyncio.to_thread(
                                save_quote,
                                message["S"],
                                datetime.fromisoformat(message["t"].replace("Z", "+00:00")),
                                float(message["bp"]),
                                float(message["ap"]),
                                "iex",
                                subscribed,
                            )
                        if kind == "orders" and message.get("stream") == "trade_updates":
                            await asyncio.to_thread(record_order, message["data"]["order"])
        except Exception as exc:
            engine.connection(
                kind + "_stream",
                {"status": "reconnecting", "reason": type(exc).__name__, "polling_fallback": True},
            )
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def session_evidence():
    from datetime import timedelta

    from intelligence.calendar import EASTERN, schedule
    from paperpilot.comparison import compare_latest
    from paperpilot.daily_report import archive_final, session_report
    from paperpilot.strategy_runtime import resolve_strategies

    while True:
        instant = now()
        day = instant.astimezone(EASTERN).date()
        session = schedule(day, day).get(day)
        if session and instant >= session["close"]:
            try:
                await asyncio.to_thread(lambda: archive_final(day, session_report(day, instant)))
            except Exception as exc:
                engine.connection("daily_report", {"status": "unavailable", "reason": type(exc).__name__})
            if settings.paper_mainkraft_enabled and instant >= session["close"] + timedelta(minutes=20):
                try:
                    await asyncio.to_thread(resolve_strategies, instant)
                    await asyncio.to_thread(compare_latest, True)
                except Exception as exc:
                    event("strategy_resolution_failed", {"reason": type(exc).__name__})
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    migrate()
    if settings.paper_experiment_enabled:
        from paperpilot import experiment
        experiment.migrate()
        tasks = [asyncio.create_task(experiment.Engine().run()),asyncio.create_task(experiment.research_loop())]
    else:
        tasks = [
        asyncio.create_task(engine.run()),
        asyncio.create_task(stream("market")),
        asyncio.create_task(stream("orders")),
        asyncio.create_task(session_evidence()),
    ]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Isolated PAPER pilot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)


@app.get("/health")
def health():
    return {"status": "ok", "execution": "paper_only"}


@app.get("/daily-report", dependencies=[Depends(authorization)])
def daily_report(date: SessionDate | None = None):
    from intelligence.calendar import EASTERN
    from paperpilot.daily_report import session_report

    today = now().astimezone(EASTERN).date()
    if date and date > today:
        raise HTTPException(422, "Report date must not be in the future")
    return session_report(date or today)


@app.get("/decision-traces", dependencies=[Depends(authorization)])
def decision_traces():
    return trace_records()


@app.get("/status", dependencies=[Depends(authorization)])
def status():
    if settings.paper_experiment_enabled:
        from paperpilot import experiment
        return experiment.status()
    from paperpilot.discovery import PROMPT_VERSION
    from paperpilot.universe import display_research, selection

    research = display_research() if settings.paper_discovery_enabled else None
    chosen = selection() if settings.paper_discovery_enabled else None
    symbols = list(current_symbols())
    if settings.paper_discovery_enabled and not symbols and research:
        symbols = [p["symbol"] for p in research["payload"]["picks"]]
    prices = latest_sources("daily_prices", symbols)
    fundamentals = latest_sources("fundamentals", symbols)
    news = latest_sources("news", symbols)
    identities = latest_sources("identity", symbols)
    with connect() as conn:
        controls = conn.execute("SELECT * FROM pilot_settings WHERE id=1").fetchone()
        connections = conn.execute("SELECT * FROM pilot_connection").fetchall()
        quotes = {r["symbol"]: r for r in conn.execute("SELECT * FROM pilot_quotes").fetchall()}
        latest = conn.execute("SELECT * FROM pilot_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        history = conn.execute(
            "SELECT observed_at,payload FROM pilot_snapshots ORDER BY id DESC LIMIT 360"
        ).fetchall()[::-1]
        decisions = conn.execute(
            "SELECT DISTINCT ON(symbol) * FROM pilot_decisions ORDER BY symbol,issued_at DESC"
        ).fetchall()
        assets = conn.execute(
            "SELECT DISTINCT ON(symbol) * FROM pilot_asset_versions ORDER BY symbol,observed_at DESC"
        ).fetchall()
        intents = conn.execute("SELECT * FROM pilot_intents ORDER BY created_at DESC LIMIT 100").fetchall()
        events = conn.execute("SELECT * FROM pilot_events ORDER BY id DESC LIMIT 60").fetchall()
        benchmarks = conn.execute(
            "SELECT observed_at,payload FROM pilot_benchmarks ORDER BY id DESC LIMIT 360"
        ).fetchall()[::-1]
        evidence, evidence_by_model = metric_views(conn)
        peak_before = conn.execute(
            "SELECT max((payload->>'strategy_equity')::float) AS n FROM pilot_snapshots WHERE observed_at < %s",
            (history[0]["observed_at"] if history else now(),),
        ).fetchone()["n"]
        all_drawdown = conn.execute("""WITH p AS (SELECT (payload->>'strategy_equity')::float AS e,
        max((payload->>'strategy_equity')::float) OVER(ORDER BY id) AS peak FROM pilot_snapshots)
        SELECT min(e/greatest(600,peak)-1) AS n FROM p""").fetchone()["n"]
    stocks = []
    for symbol in symbols:
        daily = prices.get(symbol, {}).get("payload", {}).get("bars", [])
        stocks.append(
            {
                "symbol": symbol,
                "name": research["payload"].get("candidates", {}).get(symbol, {}).get("name", symbol)
                if research
                else symbol,
                "news_research": next(
                    (p for p in research["payload"]["picks"] if p["symbol"] == symbol), None
                )
                if research
                else None,
                "identity": identities.get(symbol, {}).get("payload"),
                "quote": quotes.get(symbol),
                "daily_bars": daily[-90:],
                "sessions": len(daily),
                "daily_change": float(daily[-1]["close"]) / float(daily[-2]["close"]) - 1
                if len(daily) >= 2
                else None,
                "fundamentals": fundamentals.get(symbol, {}).get("payload"),
                "news_records": len(news.get(symbol, {}).get("payload", {}).get("articles", [])),
                "asset": next((a["payload"] for a in assets if a["symbol"] == symbol), None),
                "decision": next(
                    (
                        d
                        for d in decisions
                        if d["symbol"] == symbol
                        and (
                            not settings.paper_discovery_enabled
                            or d["session_date"] == now().astimezone(EASTERN).date()
                        )
                    ),
                    None,
                ),
            }
        )
    curve = [
        {
            "time": r["observed_at"],
            "equity": r["payload"]["strategy_equity"],
            "daily_pnl": r["payload"]["daily_pnl"],
            "realized": r["payload"]["realized_pnl"],
            "unrealized": r["payload"]["unrealized_pnl"],
        }
        for r in history
    ]
    peak = max(600, peak_before or 600)
    drawdown = 0
    for r in curve:
        peak = max(peak, r["equity"])
        r["drawdown"] = r["equity"] / peak - 1
        drawdown = min(drawdown, r["drawdown"])
    return {
        "generated_at": now(),
        "active_research_prompt_version": PROMPT_VERSION,
        "paper_only": True,
        "mainkraft_enabled": settings.paper_mainkraft_enabled,
        "swing_expectancy_trial_start": settings.paper_swing_trial_start,
        "execution_permitted": settings.paper_execution_permitted,
        "controls": controls,
        "connections": connections,
        "stocks": stocks,
        "research": research,
        "selection_active": bool(chosen) if settings.paper_discovery_enabled else True,
        "discovery_enabled": settings.paper_discovery_enabled,
        "market_shadow_enabled": settings.paper_market_shadow_enabled,
        "snapshot": latest,
        "curve": curve,
        "benchmarks": benchmarks,
        "max_observed_drawdown": all_drawdown,
        "forecast_metrics": evidence,
        "forecast_metrics_aggregation": AGGREGATE_SCOPE,
        "forecast_metrics_by_model": evidence_by_model,
        "intents": intents,
        "events": events,
        "risk": {
            "per_stock_cap": 100,
            "combined_cap": 600,
            "swing_trial_notional": settings.paper_swing_trial_notional,
            "swing_trial_exclusive": settings.paper_swing_trial_notional > 100,
            "swing_trial_market_value_cap": 850 if settings.paper_swing_trial_notional > 100 else 100,
            "buy_notional": 25 if settings.paper_discovery_enabled else 90,
            "max_positions": 20 if settings.paper_discovery_enabled else 6,
            "minimum_probability": (
                None
                if settings.paper_mainkraft_enabled
                else 0.70
                if settings.paper_discovery_enabled
                else 0.55
            ),
            "strategies": {
                "day": {"daily_entries": 4, "minimum_probability": 0.50},
                "swing": {"daily_entries": 2, "minimum_probability": 0.55},
            }
            if settings.paper_mainkraft_enabled
            else None,
            "daily_loss": settings.paper_daily_loss_limit,
            "stop_loss": 0.02,
            "take_profit": 0.03,
            "order_timeout_seconds": settings.paper_order_timeout_seconds,
            "long_only": True,
            "leverage": False,
            "entry_limit_per_stock_per_session": 1,
            "execution_cost_gate": {
                "enabled": settings.paper_cost_gate_enabled,
                "version": "iex-roundtrip-planning-cost-v1",
                "spread_basis": "Observed IEX ask/bid minus one",
                "slippage_allowance": 0.0010,
                "required_net_edge": 0.0005,
                "limitation": "Planning allowances, not NBBO, actual fees or guaranteed fill costs",
            },
        },
        "schedule": {
            "research": "07:30–09:00 America/New_York"
            if settings.paper_discovery_enabled
            else "Fixed universe",
            "cutoff": "09:15 America/New_York",
            "issue": "09:15–09:25 America/New_York",
            "entry": ("09:35–09:36" if settings.paper_mainkraft_enabled else "09:35–10:00")
            + " America/New_York",
            "exit": (
                "15 minutes before actual close on session 1 (Day) or 5 (Swing)"
                if settings.paper_mainkraft_enabled
                else "15 minutes before actual close"
            ),
        },
        "limitations": [
            "Models are experimental; no established predictive edge",
            "IEX-only execution marks, SIP daily features; coverage is not full-market real time",
            "Paper fills differ from real execution",
            "Missing dedicated account credentials leave balances and P/L unavailable, never fabricated",
        ],
    }


class Control(BaseModel):
    enabled: bool


@app.post("/control", dependencies=[Depends(authorization)])
def control(body: Control):
    if settings.paper_experiment_enabled:
        from paperpilot import experiment
        try:return experiment.control(body.enabled)
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
    if not body.enabled:
        return disable()
    if not settings.paper_execution_permitted:
        raise HTTPException(409, "Deployment/account acceptance has not permitted execution")
    broker = PaperBroker()
    try:
        positions, orders = broker.positions(), broker.orders()
        verify_account(broker.account(), positions, orders)
        with connect() as conn:
            first = conn.execute("SELECT 1 FROM pilot_snapshots LIMIT 1").fetchone()
            if not first and (positions or orders):
                raise ValueError("New dedicated account must be empty")
            for symbol in current_symbols():
                asset = broker.request("GET", "/v2/assets/" + symbol)
                if not asset or not asset.get("tradable") or asset.get("class") != "us_equity":
                    raise ValueError("Six-stock asset verification incomplete")
            conn.execute(
                "UPDATE pilot_settings SET enabled=true,reason='Dedicated PAPER account verified; deterministic risk gates remain required',updated_at=now() WHERE id=1"
            )
        event("paper_enabled", {"account_id": settings.paper_account_id, "budget": 600})
        return {"enabled": True, "mode": "paper_only"}
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/events", dependencies=[Depends(authorization)])
async def events():
    async def messages():
        for _ in range(20):
            payload = await asyncio.to_thread(status)
            yield "data: " + canonical(payload) + "\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        messages(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
