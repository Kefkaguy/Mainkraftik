"""Offline, bounded text classification. No broker, database or remote model code."""

import hashlib
import json
import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from tokenizers import Tokenizer

ROOT = Path("/models")
manifest = json.loads((ROOT / "manifest.json").read_text())
for filename, expected in manifest["sha256"].items():
    path = ROOT / filename
    if path.resolve().parent not in {ROOT.resolve(), (ROOT / "onnx").resolve()}:
        raise ValueError("Unexpected artifact path")
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
            raise ValueError("Model artifact hash mismatch")
tokenizer = Tokenizer.from_file(str(ROOT / "tokenizer.json"))
tokenizer.enable_truncation(max_length=128)
tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
options = ort.SessionOptions()
options.intra_op_num_threads = 1
options.inter_op_num_threads = 1
options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
session = ort.InferenceSession(
    str(ROOT / "onnx/model_quantized.onnx"), options, providers=["CPUExecutionProvider"]
)
labels = json.loads((ROOT / "config.json").read_text())["id2label"]
if set(labels.values()) != {"negative", "neutral", "positive"}:
    raise ValueError("Unrecognized sentiment label mapping")
lock = threading.Lock()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1500)


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Item] = Field(min_length=1, max_length=120)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "revision": manifest["revision"],
        "purpose": "text_sentiment_only",
    }


@app.post("/score")
def score(batch: Batch):
    if len({r.id for r in batch.items}) != len(batch.items):
        raise HTTPException(422, "Duplicate article IDs")
    if not lock.acquire(blocking=False):
        raise HTTPException(429, "Single inference job already running")
    try:
        scores = []
        for start in range(0, len(batch.items), 4):
            items = batch.items[start : start + 4]
            enc = tokenizer.encode_batch([r.text for r in items])
            values = {
                "input_ids": np.asarray([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.asarray(
                    [e.attention_mask for e in enc], dtype=np.int64
                ),
                "token_type_ids": np.asarray([e.type_ids for e in enc], dtype=np.int64),
            }
            raw = session.run(
                None, {i.name: values[i.name] for i in session.get_inputs()}
            )[0]
            if raw.shape != (len(items), 3) or not np.isfinite(raw).all():
                raise ValueError("Invalid model output")
            exp = np.exp(raw - raw.max(axis=1, keepdims=True))
            probabilities = exp / exp.sum(axis=1, keepdims=True)
            for item, row in zip(items, probabilities):
                scores.append(
                    {
                        "id": item.id,
                        "text_hash": hashlib.sha256(item.text.encode()).hexdigest(),
                        "scores": {labels[str(i)]: float(v) for i, v in enumerate(row)},
                    }
                )
        return {
            "model": manifest,
            "scores": scores,
            "meaning": "Text sentiment; not return probability",
        }
    finally:
        lock.release()
