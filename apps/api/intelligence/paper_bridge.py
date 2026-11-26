"""Authenticated narrow bridge; the research process receives no execution keys."""

from datetime import date as SessionDate

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from intelligence.config import settings
from intelligence.security import require_session

router = APIRouter(prefix="/v1/paper", dependencies=[Depends(require_session)])


def target(path):
    if settings.paper_service_url not in {"http://127.0.0.1:8200", "http://paper:8200"}:
        raise HTTPException(503, "Private paper service address is invalid")
    return settings.paper_service_url + path


def headers():
    return {"X-Pilot-Token": settings.paper_internal_token}


@router.get("/status")
async def status():
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(target("/status"), headers=headers())
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        raise HTTPException(
            503, "Isolated paper pilot unavailable; trading state is not assumed safe"
        ) from None


@router.get("/daily-report")
async def daily_report(date: SessionDate | None = None):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(target("/daily-report"), headers=headers(),
                                        params={"date": date.isoformat()} if date else {})
            if response.status_code == 422:
                raise HTTPException(422, "Report date must not be in the future")
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        raise HTTPException(503, "Paper session report unavailable") from None


class Control(BaseModel):
    enabled: bool


@router.get("/decision-traces")
async def decision_traces():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response=await client.get(target("/decision-traces"),headers=headers())
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError:
        raise HTTPException(503,"Decision trace evidence unavailable") from None


@router.post("/control")
async def control(body: Control):
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(target("/control"), headers=headers(), json=body.model_dump())
            if response.status_code != 200:
                raise HTTPException(
                    response.status_code, response.json().get("detail", "Paper control unavailable")
                )
            return response.json()
    except httpx.HTTPError:
        raise HTTPException(
            503, "Control acknowledgement unavailable; verify pending orders and positions"
        ) from None


@router.get("/events")
async def events(request: Request):
    async def messages():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(70, connect=5)) as client:
                async with client.stream("GET", target("/events"), headers=headers()) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        if await request.is_disconnected():
                            break
                        yield chunk
        except httpx.HTTPError:
            yield 'event: unavailable\ndata: {"reason":"Paper bridge disconnected; polling fallback"}\n\n'

    return StreamingResponse(
        messages(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
