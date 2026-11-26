"""Authenticated access to immutable local research reports."""

from fastapi import APIRouter, Depends, HTTPException

from intelligence.security import require_session
from paperpilot.lab_registry import latest

router = APIRouter(prefix="/v1/research-lab", dependencies=[Depends(require_session)])


@router.get("/results")
def results():
    try:
        report = latest()
    except (ValueError, OSError):
        raise HTTPException(503, "Research report integrity could not be verified") from None
    return {
        key: value for key, value in report.items() if key not in {"coverage", "news", "frozen_comparison", "monthly_walk_forward"}
    } | {
        "monthly_walk_forward": {"summary": report.get("monthly_walk_forward", {}).get("summary")},
        "frozen_comparison": {
            key: ({"summary": value["summary"]} if isinstance(value, dict) and "summary" in value else value)
            for key, value in report.get("frozen_comparison", {}).items()
        }
    }
