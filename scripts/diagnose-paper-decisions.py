"""Read-only VPS evidence export and offline reconstruction of legacy signal gates.

Does not import the trading engine, contact Alpaca, or change runtime policy.
"""
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from vps_access import run

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "autonomy-diagnosis-2026-11-17"
SQL = """SELECT coalesce(json_agg(t),'[]'::json) FROM (
SELECT id,session_date,symbol,issued_at,action,
payload->>'model' AS model,payload->>'reason' AS recorded_reason,
payload->>'account_ready_before_cutoff' AS ready,
payload->'horizons'->'1'->>'expected_return' AS expected_return,
payload->'horizons'->'1'->>'probability_positive' AS probability,
payload->>'volatility' AS volatility,payload->>'news_stance' AS news,
payload->>'hf_news_assessment' AS sentiment
FROM pilot_decisions ORDER BY session_date,symbol) t"""


def reconstruct(row):
    reasons = []
    if row["ready"] != "true":
        reasons.append("readiness_not_verified_before_cutoff")
    if row["expected_return"] is None:
        reasons.append("missing_data: " + (row["recorded_reason"] or "unknown"))
    else:
        discovery = "news-screened" in row["model"]
        if float(row["probability"]) < (0.70 if discovery else 0.55):
            reasons.append("prediction_confidence_insufficient")
        if float(row["expected_return"]) <= 0.0015:
            reasons.append("gross_expected_return_insufficient")
        if float(row["volatility"]) > 0.8:
            reasons.append("volatility_risk_limit")
        if discovery and row["news"] != "bullish":
            reasons.append("news_not_bullish")
        if "hf-news" in row["model"] and row["sentiment"] != "review":
            reasons.append("sentiment_veto_or_unavailable")
    return reasons


def main():
    result = run('docker exec keftrade-intelligence-paper-postgres-1 psql -U paperpilot -d intelligence_paper -Atc "' + SQL + '"')
    if result["exit_code"]:
        raise RuntimeError(result["stderr"])
    OUT.mkdir(parents=True, exist_ok=True)
    raw = result["stdout"].encode()
    (OUT / "decision-evidence.json").write_bytes(raw)
    rows = json.loads(raw)
    counts = Counter()
    for row in rows:
        row["reconstructed_rejections"] = "; ".join(reconstruct(row))
        counts.update(reconstruct(row))
        row["downstream_gates"] = "not_reached; costs/window/capital/order submission not established"
    with (OUT / "candidate-rejections.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)
    summary = {"captured_at": datetime.now(timezone.utc).isoformat(),
               "evidence_sha256": hashlib.sha256(raw).hexdigest(),
               "decisions": len(rows), "actions": dict(Counter(r["action"] for r in rows)),
               "overlapping_rejections": dict(counts),
               "method": "Reconstructed legacy signal conjunction; not a replay of downstream execution gates"}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
