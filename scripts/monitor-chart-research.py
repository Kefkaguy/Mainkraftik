"""Opt-in local read-only research loop; never starts a trading service."""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.controller import tick


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    store.read(args.report_id)
    while True:
        try:
            print(tick(args.report_id, args.symbols), flush=True)
        except Exception as exc:
            print(
                {"status": "RESEARCH_UNAVAILABLE", "reason": type(exc).__name__},
                flush=True,
            )
            if not args.watch:
                raise
        if not args.watch:
            break
        time.sleep(60)


if __name__ == "__main__":
    main()
