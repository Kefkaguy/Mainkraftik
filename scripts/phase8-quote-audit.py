"""Read-only cost-evidence summary; quoted spread is not realized slippage."""

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence.phase8_evidence import verify

records = []
for folder in (ROOT / ".local/chart-intelligence", ROOT / ".local/phase8/forward"):
    path = folder / "controller.sqlite"
    if path.exists():
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
            records += [
                json.loads(r[0])
                for r in db.execute("SELECT payload FROM evidence WHERE kind='quote'")
            ]
valid = []
rejected = []
for q in records:
    try:
        source = datetime.fromisoformat(q["source_time"])
        acquired = datetime.fromisoformat(q["acquired_at"])
        if not 0 < q["bid"] <= q["ask"]:
            raise ValueError("INVALID_PRICE")
        age = (acquired - source).total_seconds()
        row = {
            "symbol": q["symbol"],
            "source_time": source.isoformat(),
            "acquired_at": acquired.isoformat(),
            "age_seconds": age,
            "spread_return": q["ask"] / q["bid"] - 1,
            "feed": q.get("feed", "iex"),
        }
        if not 0 <= age <= 15:
            rejected.append(row | {"reason": "STALE_OR_FUTURE_QUOTE"})
        else:
            valid.append(row)
    except (KeyError, ValueError, TypeError):
        rejected.append({"reason": "MALFORMED"})
result = {
    "observed_at": datetime.now(UTC).isoformat(),
    "observations": len(records),
    "fresh": len(valid),
    "unusable": len(rejected),
    "fresh_median_spread_bps": float(
        np.median([q["spread_return"] * 10000 for q in valid])
    )
    if valid
    else None,
    "fresh_p90_spread_bps": float(
        np.quantile([q["spread_return"] * 10000 for q in valid], 0.9)
    )
    if valid
    else None,
    "fresh_records": valid,
    "unusable_records": rejected,
    "measured_slippage": None,
    "actual_fills": None,
    "limitation": "IEX single-venue quote snapshots, not NBBO or guaranteed fills. Stale off-hours spreads cannot calibrate execution costs.",
}
folder = ROOT / "reports/phase8"
(folder / "quote-coverage.json").write_text(json.dumps(result, indent=2))
verify(json.loads((folder / "phase7-freeze.json").read_text()))
print(
    json.dumps({k: v for k, v in result.items() if not k.endswith("records")}, indent=2)
)
