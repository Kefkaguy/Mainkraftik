"""Deterministic chart input and import-free, separately executed vision gateway."""

import hashlib
import io
from datetime import timedelta
from uuid import uuid4

from PIL import Image, ImageDraw

from intelligence.calendar import future_sessions, schedule
from intelligence.db import connection
from intelligence.forecasting import continuity
from intelligence.storage import bars_at, jsonb
from intelligence.util import digest, now

RENDERER_VERSION = "candles-90-square-v1"


def model_status(model):
    if (
        not model["license_cleared"]
        or not model.get("license_review_reference")
        or not model.get("weight_sha256")
    ):
        return {
            "available": False,
            "status": "blocked_license",
            "message": "Model unavailable — licensing review required",
        }
    if not model["runtime_verified"]:
        return {
            "available": False,
            "status": "blocked_runtime",
            "message": "Model unavailable — runtime verification required",
        }
    return {
        "available": True,
        "status": "experimental",
        "message": "Detector confidence is not return probability",
    }


def render_chart(bars, cutoff):
    rows = [r for r in bars if r["available_at"] <= cutoff and r["acquired_at"] <= cutoff][-90:]
    if len(rows) != 90:
        raise ValueError("90 cutoff-safe bars are required for the fixed renderer")
    valid, reason = continuity(rows)
    if not valid:
        raise ValueError(reason)
    image = Image.new("RGB", (640, 640), "#f5f8fa")
    draw = ImageDraw.Draw(image)
    high, low = max(float(r["high"]) for r in rows), min(float(r["low"]) for r in rows)
    if high <= low:
        raise ValueError("A non-degenerate price range is required")

    def y(value):
        return 612 - int((float(value) - low) / (high - low) * 584)

    for i, r in enumerate(rows):
        x = 24 + i * 6.6
        color = "#168675" if r["close"] >= r["open"] else "#b65a62"
        draw.line((x, y(r["high"]), x, y(r["low"])), fill=color, width=1)
        top, bottom = sorted([y(r["open"]), y(r["close"])])
        draw.rectangle((x - 2, top, x + 2, max(bottom, top + 1)), fill=color)
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False)
    content = stream.getvalue()
    manifest = {
        "renderer_version": RENDERER_VERSION,
        "cutoff": cutoff,
        "bar_ids": [r["id"] for r in rows],
        "bar_hashes": [r["content_hash"] for r in rows],
        "width": 640,
        "height": 640,
        "last_session": rows[-1]["session_date"],
    }
    return content, manifest, hashlib.sha256(content).hexdigest()


def prepare_job(symbol, model_id):
    with connection() as conn:
        model = conn.execute("SELECT * FROM vision_models WHERE id=%s", (model_id,)).fetchone()
        if not model:
            raise ValueError("Unknown vision model")
        status = model_status(model)
        if not status["available"]:
            raise ValueError(status["message"])
        instant = now()
        bars = bars_at(conn, symbol, instant, instant)
    if bars:
        next_session = future_sessions(bars[-1]["session_date"], 1)[0]
        if instant >= schedule(next_session, next_session)[next_session]["open"]:
            raise ValueError(
                "Wait for a newly completed session before recording the next-open chart experiment"
            )
    content, manifest, image_hash = render_chart(bars, instant)
    return content, {
        "id": str(uuid4()),
        "symbol": symbol,
        "model_id": model_id,
        "model": model,
        "created_at": instant,
        "cutoff": instant,
        "manifest": manifest,
        "input_hash": digest(manifest),
        "image_hash": image_hash,
    }


def resolve_detection_outcomes():
    count = 0
    instant = now()
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194012)")
        for detection in conn.execute("SELECT * FROM chart_detections").fetchall():
            origin = detection["payload"]["manifest"]["last_session"]
            from datetime import date

            targets = future_sessions(date.fromisoformat(origin))
            rows = {r["session_date"]: r for r in bars_at(conn, detection["symbol"], instant)}
            for horizon in (1, 5):
                entry, exit_ = rows.get(targets[0]), rows.get(targets[horizon - 1])
                if (
                    not entry
                    or not exit_
                    or instant
                    < schedule(targets[horizon - 1], targets[horizon - 1])[targets[horizon - 1]]["close"]
                    + timedelta(minutes=15)
                ):
                    continue
                if detection["created_at"] >= schedule(targets[0], targets[0])[targets[0]]["open"]:
                    continue  # No retroactive chart predictions for an entry already passed.
                inserted = conn.execute(
                    "INSERT INTO detection_outcomes VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                    (
                        uuid4(),
                        detection["id"],
                        horizon,
                        float(exit_["close"] / entry["open"] - 1),
                        instant,
                        jsonb(
                            {"entry_bar_id": entry["id"], "exit_bar_id": exit_["id"], "feed": entry["feed"]}
                        ),
                    ),
                ).fetchone()
                count += bool(inserted)
    return {"resolved": count}
