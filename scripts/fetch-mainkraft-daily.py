
"""Freeze daily SIP model inputs separately from intraday outcome bars."""
import sys
from datetime import date, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
from intelligence.calendar import EASTERN
from intelligence.config import settings
from intelligence.providers import fetch_bars, normalize_bars
from intelligence.util import canonical, digest, now
from paperpilot.config import SYMBOLS
from paperpilot.intraday import save_snapshot

if settings.feed != "sip":
    raise ValueError("Mainkraft research requires SIP daily evidence")
end = datetime(2026, 11, 13, 16, tzinfo=EASTERN)
root = Path(".local/mainkraft/history")
for symbol in (*SYMBOLS, "SPY", "XLK"):
    raw, document, url = fetch_bars(symbol, date(2022, 9, 1), end, end.date())
    acquired = now()
    bars = normalize_bars(symbol, raw, "sip", end.date(), acquired)
    identity = save_snapshot(root, dict(bars=bars, symbol=symbol, acquired_at=acquired,
                            url=url, request=document["request"], response_hash=digest(document)))
    with (root / "daily.jsonl").open("a", encoding="utf-8") as output:
        output.write(canonical(dict(symbol=symbol, hash=identity, sessions=len(bars))) + "\n")
    print(symbol, len(bars), flush=True)
