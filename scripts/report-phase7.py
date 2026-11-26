"""Report a frozen experiment without re-fitting or selecting against audit data."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.controller import status
from chart_intelligence.phase7_accounting import portfolio_envelope


def main():
    folder = ROOT / "reports/phase7"
    root = ROOT / ".local/phase7"
    summary = json.loads((folder / "research-results.json").read_text())
    report = store.read(summary["report_id"], root)["payload"]
    rows = store.read(summary["forecasts_id"], root)["payload"]["rows"]
    accounting = {}
    for name, model in report["audit"].items():
        accounting[name] = {}
        for scenario, replay in model["scenarios"].items():
            envelope = portfolio_envelope(rows, replay["trades"])
            accounting[name][scenario] = {
                k: v for k, v in envelope.items() if k != "points"
            }
    accounting["always_long"] = portfolio_envelope(
        rows, report["benchmarks"]["always_long"]["trades"]
    )
    accounting["always_long"].pop("points")
    record = {
        "source_report_id": summary["report_id"],
        "accounting": accounting,
        "method": "Frozen trade ledger re-mark only; no model fit or parameter changes",
        "coverage_status": {
            name: (
                "UNAVAILABLE"
                if any(
                    "QUOTE_UNAVAILABLE" in reason
                    for reason in model["strict_quote_audit"]["rejections"]
                )
                else "NOT_EVALUATED_NO_ELIGIBLE_OPPORTUNITIES"
            )
            for name, model in report["audit"].items()
        },
        "coverage_note": "The original frozen report's coverage boolean is vacuous for zero-opportunity candidates. No complete executable history was established. This addendum clarifies that field without rewriting original evidence.",
    }
    key = store.put("accounting_addendum", record, root)
    record["record_id"] = key
    (folder / "accounting-addendum.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    monitor = status()
    enrollment = json.loads((root / "enrollment.json").read_text())
    predictions = [
        p
        for p in monitor.get("prediction", [])
        if p["report_id"] == enrollment["report_id"]
    ]
    outcomes = [
        p
        for p in monitor.get("outcome", [])
        if p["report_id"] == enrollment["report_id"]
    ]
    prospective = {
        "observed_at": datetime.now(UTC).isoformat(),
        "enrollment": enrollment["report_id"],
        "predictions": len(predictions),
        "resolved_outcomes": len(outcomes),
        "status": "INSUFFICIENT_EVIDENCE",
        "latest_run": next(
            (
                r
                for r in monitor.get("controller_run", [])
                if r["report_id"] == enrollment["report_id"]
            ),
            None,
        ),
        "note": "No backdated forecasts. A fresh collection outside issuance hours is not a prospective prediction.",
    }
    (folder / "prospective-status.json").write_text(
        json.dumps(prospective, indent=2), encoding="utf-8"
    )
    print(
        json.dumps({"accounting_record_id": key, "prospective": prospective}, indent=2)
    )


if __name__ == "__main__":
    main()
