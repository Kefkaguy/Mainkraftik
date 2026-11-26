"""Run only inside the opt-in, network-disabled CPU worker container.

No application configuration, database libraries, or provider credentials are imported.
Weights must be separately reviewed and mounted read-only; there is no download path.
"""

import hashlib
import json
import os
import time
from pathlib import Path


def infer(directory):
    request = json.loads((directory / "request.json").read_text())
    model = request["model"]
    if (
        not model["license_cleared"]
        or not model["runtime_verified"]
        or not model.get("license_review_reference")
    ):
        raise ValueError("Model has not cleared review")
    if model["id"] not in {"chart-patterns", "chart-direction"}:
        raise ValueError("Unknown model")
    image = directory / "input.png"
    image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    if image_hash != request["image_hash"]:
        raise ValueError("Input image identity mismatch")
    weights = Path("/models") / model["id"] / model["weight_file"]
    if (
        not weights.is_file()
        or hashlib.sha256(weights.read_bytes()).hexdigest() != model["weight_sha256"]
    ):
        raise ValueError("Reviewed weights are absent or do not match")
    import torch
    import ultralytics

    torch.set_num_threads(2)
    detector = ultralytics.YOLO(str(weights), task="detect")
    prediction = detector.predict(
        source=str(image),
        device="cpu",
        imgsz=640,
        save=False,
        verbose=False,
        max_det=1000,
    )[0]
    boxes = [
        {
            "label": str(prediction.names[int(b.cls.item())]),
            "confidence": float(b.conf.item()),
            "xyxy": [max(0.0, min(640.0, float(v))) for v in b.xyxy[0].tolist()],
        }
        for b in prediction.boxes
    ]
    return {
        "id": request["id"],
        "image_hash": image_hash,
        "weight_sha256": model["weight_sha256"],
        "boxes": boxes,
        "runtime": {
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
            "device": "cpu",
        },
    }


def main():
    for name in list(os.environ):
        if any(
            word in name.upper()
            for word in ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "DATABASE_URL")
        ):
            os.environ.pop(name, None)
    root = Path(os.environ.get("VISION_WORK_DIR", "/work"))
    while True:
        for request in sorted(root.glob("*/request.json")):
            directory = request.parent
            if (directory / "result.json").exists():
                continue
            try:
                result = infer(directory)
            except Exception as exc:
                result = {"error": type(exc).__name__}
            pending = directory / "result.pending"
            pending.write_text(json.dumps(result))
            pending.replace(directory / "result.json")
        time.sleep(1)


if __name__ == "__main__":
    main()
