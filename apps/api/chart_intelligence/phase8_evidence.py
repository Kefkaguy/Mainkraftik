"""Phase 8 evidence boundary. Phase 7 files and existing ledger rows stay frozen."""

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from chart_intelligence.core import identity
from intelligence.config import ROOT


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def snapshot(root=ROOT):
    files = {}
    for directory in ("reports/phase7", ".local/phase7"):
        for p in (root / directory).rglob("*"):
            if p.is_file():
                files[str(p.relative_to(root))] = sha(p)
    # Include the immutable enrollment/model record in the shared research store.
    enrollment = json.loads((root / ".local/phase7/enrollment.json").read_text())
    for key in (enrollment["report_id"], enrollment["model_id"]):
        p = root / ".local/chart-intelligence/records" / (key + ".json")
        files[str(p.relative_to(root))] = sha(p)
    ledger = {}
    path = root / ".local/chart-intelligence/controller.sqlite"
    if path.exists():
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
            ledger = {
                key: identity([kind, json.loads(payload)])
                for key, kind, payload in db.execute("SELECT key,kind,payload FROM evidence")
            }
    return {"files": files, "existing_ledger_rows": ledger}


def verify(frozen, root=ROOT):
    current = snapshot(root)
    for key, value in frozen["files"].items():
        if current["files"].get(key) != value:
            raise ValueError("Frozen Phase 7 file changed: " + key)
    for key, value in frozen["existing_ledger_rows"].items():
        if current["existing_ledger_rows"].get(key) != value:
            raise ValueError("Existing prospective evidence changed")
    return True


def put_compressed(kind, payload, root):
    record = {"kind": kind, "payload": payload}
    key = identity(record)
    folder = Path(root) / "compressed"
    folder.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    path = folder / (key + ".json.gz")
    if path.exists():
        if gzip.decompress(path.read_bytes()) != encoded:
            raise ValueError("Immutable compressed evidence mismatch")
    else:
        with path.open("xb") as f:
            f.write(gzip.compress(encoded, mtime=0))
    return key


def read_compressed(key, root):
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid evidence ID")
    record = json.loads(gzip.decompress((Path(root) / "compressed" / (key + ".json.gz")).read_bytes()))
    if identity(record) != key:
        raise ValueError("Compressed evidence hash mismatch")
    return record["payload"]
