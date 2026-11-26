"""Content addressed local research evidence, isolated from broker state."""

import json
import os
from pathlib import Path

from chart_intelligence.core import identity
from intelligence.config import ROOT

STORE = ROOT / ".local" / "chart-intelligence"


def put(kind, payload, root=None):
    root = Path(root or STORE)
    record = {"kind": kind, "payload": payload}
    key = identity(record)
    folder = root / "records"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (key + ".json")
    content = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        with path.open("x", encoding="utf-8") as out:
            out.write(content)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != content:
            raise ValueError("Immutable chart evidence differs")
    return key


def read(key, root=None):
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid record ID")
    record = json.loads((Path(root or STORE) / "records" / (key + ".json")).read_text(encoding="utf-8"))
    if identity(record) != key:
        raise ValueError("Chart evidence integrity failure")
    return record


def publish(key, root=None):
    root = Path(root or STORE)
    read(key, root)
    temp = root / ("latest-" + key + ".tmp")
    temp.write_text(json.dumps({"id": key}), encoding="utf-8")
    os.replace(temp, root / "latest.json")


def latest(root=None):
    root = Path(root or STORE)
    if not (root / "latest.json").exists():
        return {"status": "NOT_RUN", "execution_authority": False}
    key = json.loads((root / "latest.json").read_text())["id"]
    return {"record_id": key, **read(key, root)["payload"]}
