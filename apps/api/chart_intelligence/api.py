"""Authenticated read-only research status. No training or promotion HTTP route."""

from fastapi import APIRouter, Depends, HTTPException

from chart_intelligence import store
from chart_intelligence.controller import status
from intelligence.config import ROOT
from intelligence.security import require_session

router = APIRouter(prefix="/v1/chart-intelligence", dependencies=[Depends(require_session)])


@router.get("/phase7")
def phase7_status():
    try:
        report = store.latest(ROOT / ".local/phase7")
    except (ValueError, OSError):
        raise HTTPException(503, "Phase 7 immutable evidence unavailable") from None
    audit = {
        name: {
            "prediction": value["prediction"],
            "base_scenario": {
                k: v
                for k, v in value["scenarios"]["base_30min"].items()
                if k not in {"orders", "trades", "daily_net_pnl"}
            },
        }
        for name, value in report.get("audit", {}).items()
    }
    return {
        "status": report["status"],
        "winner": report.get("winner"),
        "audit": audit,
        "audit_classification": report.get("audit_classification"),
        "acceptance": report.get("acceptance"),
        "prospective": report.get("prospective"),
        "execution_authority": False,
        "promotion_authority": False,
    }


@router.get("/status")
def research_status():
    try:
        report = store.latest()
        monitor = status()
    except (ValueError, OSError):
        raise HTTPException(503, "Research evidence integrity unavailable") from None
    summary = {k: v for k, v in report.items() if k != "predictions"}
    # Features and training artifacts stay out of browser payloads.
    for kind in ("prediction", "outcome"):
        monitor[kind] = [{k: v for k, v in row.items() if k != "features"} for row in monitor.get(kind, [])]
    return {
        "research": summary,
        "monitoring": monitor,
        "deployment": {
            "status": "LOCAL_RESEARCH_ONLY",
            "promotion_authority": False,
            "execution_authority": False,
            "paper_policy": "Existing deployed policy preserved",
        },
    }
