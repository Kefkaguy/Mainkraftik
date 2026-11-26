
"""Fetch the declared exploratory cohort into the local evidence store."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from intelligence.calendar import EASTERN
from intelligence.util import canonical
from paperpilot.config import SYMBOLS
from paperpilot.intraday import fetch_intraday, save_snapshot

root = Path(".local/mainkraft/history")
start = datetime(2022, 11, 1, 9, 30, tzinfo=EASTERN)
end = datetime(2026, 11, 13, 16, tzinfo=EASTERN)
manifest = root / "coverage.jsonl"
for symbol in (*SYMBOLS, "SPY"):
    snapshot = fetch_intraday(symbol, start, end)
    identity = save_snapshot(root, snapshot)
    record = dict(symbol=symbol, bars=len(snapshot["bars"]), missing=len(snapshot["missing"]), hash=identity)
    with manifest.open("a", encoding="utf-8") as out:
        out.write(canonical(record) + "\n")
    print(canonical(record), flush=True)
