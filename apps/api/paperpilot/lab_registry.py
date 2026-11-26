"""Content addressed research records and authenticated report reading."""

import json
import os

from intelligence.config import ROOT
from intelligence.util import canonical, digest, now

LAB = ROOT / ".local" / "strategy-lab"


def put(kind, payload):
    record = {"kind": kind, "payload": payload}
    encoded = canonical(record).encode()
    identity = digest(record)
    folder = LAB / "records"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (identity + ".json")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise ValueError("Research record corruption")
    else:
        with os.fdopen(fd, "wb") as output:
            output.write(encoded)
    return identity


def read(identity):
    if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("Invalid research record id")
    data = json.loads((LAB / "records" / (identity + ".json")).read_text())
    if digest(data) != identity:
        raise ValueError("Research record integrity failed")
    return data["payload"]


def publish(report):
    identity = put("report", report)
    temporary = LAB / ("latest-" + identity + ".tmp")
    temporary.write_text(canonical({"id": identity, "published": now()}), encoding="utf-8")
    os.replace(temporary, LAB / "latest.json")
    return identity


def latest():
    pointer = LAB / "latest.json"
    if not pointer.exists():
        return {"status": "not_run", "prospective": {"status": "not_nominated"}}
    identity = json.loads(pointer.read_text())["id"]
    return read(identity) | {"report_id": identity}
