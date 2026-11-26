"""Freeze once; subsequent runs verify, never replace the Phase 7 boundary."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence.phase8_evidence import snapshot, verify

folder = ROOT / "reports/phase8"
folder.mkdir(parents=True, exist_ok=True)
path = folder / "phase7-freeze.json"
if path.exists():
    value = json.loads(path.read_text())
    verify(value)
    print("Verified frozen Phase 7 evidence")
else:
    value = {"frozen_at": datetime.now(UTC).isoformat(), **snapshot()}
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, indent=2)
    print(
        "Frozen",
        len(value["files"]),
        "files and",
        len(value["existing_ledger_rows"]),
        "ledger rows",
    )
