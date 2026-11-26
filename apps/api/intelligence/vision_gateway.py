import json
import math
import time
from uuid import UUID

from intelligence.config import settings
from intelligence.db import connection
from intelligence.ingestion import resource_check
from intelligence.storage import jsonb
from intelligence.util import canonical, now
from intelligence.vision import RENDERER_VERSION, model_status, prepare_job


def validate_boxes(boxes):
    if not isinstance(boxes, list) or len(boxes) > 1000:
        raise ValueError("Invalid detector result")
    for b in boxes:
        if not isinstance(b.get("label"), str) or len(b["label"]) > 100:
            raise ValueError("Invalid detector label")
        values = [*b.get("xyxy", []), b.get("confidence")]
        if len(values) != 5 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ValueError("Detector coordinates must be finite")
        x1, y1, x2, y2 = b["xyxy"]
        if not (0 <= x1 <= x2 <= 640 and 0 <= y1 <= y2 <= 640 and 0 <= b["confidence"] <= 1):
            raise ValueError("Detector result outside image bounds")
    return boxes


def submit_and_collect(symbol, model_id):
    resource_check(settings.vision_max_bytes)
    content, spec = prepare_job(symbol, model_id)
    job_id = str(UUID(spec["id"]))
    directory = settings.storage / "vision" / job_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "input.png").write_bytes(content)
    request = {**spec, "model": dict(spec["model"])}
    temporary = directory / "request.pending"
    temporary.write_text(canonical(request), encoding="utf-8")
    temporary.replace(directory / "request.json")
    deadline = time.monotonic() + 45
    output = directory / "result.json"
    while not output.exists() and time.monotonic() < deadline:
        time.sleep(0.25)
    if not output.exists():
        raise ValueError("Isolated vision worker did not return within 45 seconds; inspect its runtime")
    result = json.loads(output.read_text(encoding="utf-8"))
    if result.get("error"):
        raise ValueError("Isolated model inference failed; review compatibility before retrying")
    if (
        result.get("id") != job_id
        or result.get("image_hash") != spec["image_hash"]
        or result.get("weight_sha256") != spec["model"]["weight_sha256"]
    ):
        raise ValueError("Detector artifact identity mismatch")
    boxes = validate_boxes(result.get("boxes"))
    with connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(753194013)")
        current = conn.execute("SELECT * FROM vision_models WHERE id=%s", (model_id,)).fetchone()
        if not model_status(current)["available"] or current["weight_sha256"] != result["weight_sha256"]:
            raise ValueError("Model review identity changed while the worker ran")
        conn.execute(
            "INSERT INTO chart_detections VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                job_id,
                symbol,
                model_id,
                spec["created_at"],
                spec["cutoff"],
                RENDERER_VERSION,
                spec["input_hash"],
                spec["image_hash"],
                result["weight_sha256"],
                jsonb(
                    {
                        "manifest": spec["manifest"],
                        "boxes": boxes,
                        "runtime": result.get("runtime"),
                        "completed_at": now(),
                        "interpretation": "Detector confidence, not probability of positive return",
                    }
                ),
            ),
        )
    return {"detection_id": job_id, "boxes": len(boxes)}
