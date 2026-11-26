"""Isolated scoring service. Deployment supplies only a private network and a model bundle."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, FiniteFloat, StringConstraints

from paperpilot.strategy import bundle, forecast


class ScoreRequest(BaseModel):
    bundle_id: str
    inputs: dict[
        Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}$")],
        Annotated[list[FiniteFloat], Field(min_length=11, max_length=11)],
    ] = Field(max_length=20)


@asynccontextmanager
async def lifespan(app):
    app.state.bundle = bundle()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"bundle_id": app.state.bundle["bundle_id"], "authority": "simulation_only"}


@app.post("/score")
def score(request: ScoreRequest):
    models = app.state.bundle
    if request.bundle_id != models["bundle_id"]:
        raise HTTPException(409, "Unknown frozen bundle")
    return {
        "bundle_id": models["bundle_id"],
        "scores": {
            kind: {
                symbol: forecast(spec["challenger"], {"x": columns})
                for symbol, columns in request.inputs.items()
            }
            for kind, spec in models["strategies"].items()
        },
    }
