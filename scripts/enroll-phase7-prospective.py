"""Pin the declared diagnostic candidate in the existing local research controller.

Never publishes/replaces the existing Chart Intelligence model or paper policy.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from chart_intelligence import store
from chart_intelligence.core import Protocol


def main():
    summary = json.loads((ROOT / "reports/phase7/research-results.json").read_text())
    root = ROOT / ".local/phase7"
    frozen = store.read(summary["report_id"], root)["payload"]
    path = root / "enrollment.json"
    if path.exists():
        saved = json.loads(path.read_text())
        if saved["source_report_id"] != summary["report_id"]:
            raise ValueError(
                "A different enrollment already exists; declare a separate protocol"
            )
        print(json.dumps(saved, indent=2))
        return
    candidate = store.read(frozen["winner_model_id"], root)["payload"]
    model_id = store.put("model", candidate)  # Separate local record; do not publish.
    enrollment = {
        "protocol": Protocol().__dict__,
        "not_before": datetime.now(UTC).isoformat(),
        "model_id": model_id,
        "models": {"sequence_mlp": frozen["audit"][frozen["winner"]]["prediction"]},
        "source_report_id": summary["report_id"],
        "candidate_name": frozen["winner"],
        "candidate_status": "FORECAST_DIAGNOSTIC_ONLY_NO_VALIDATED_TRADING_EDGE",
        "minimum_prospective_dates": 126,
        "execution_authority": False,
        "promotion_authority": False,
    }
    key = store.put("prospective_enrollment", enrollment)
    result = {"report_id": key, "source_report_id": summary["report_id"], **enrollment}
    with path.open("x", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    store.publish(summary["report_id"], root)  # Phase 7 research folder only.
    (ROOT / "reports/phase7/prospective-enrollment.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
