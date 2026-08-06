# app/routers/insights.py
"""
AI Insights API — findings computed from what the agent actually processed.

    GET /api/ai/insights            current insights, worst first
    GET /api/ai/insights/summary    counts by severity

There is no seeding and no cache. Every call re-derives insights from
policy_evaluations, workbench_items and workflow_runs, so the page cannot
drift out of step with reality — and an empty list genuinely means the agent
has not produced anything worth flagging yet.
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..services import insights as insight_engine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/insights", tags=["AI Insights"])


@router.get("")
def list_insights(
    severity: str = Query(None, examples=["critical"]),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    items = insight_engine.generate(db)
    if severity:
        items = [i for i in items if i["severity"] == severity.lower()]
    return items


@router.get("/summary")
def insights_summary(db: Session = Depends(get_db)) -> Dict[str, Any]:
    items = insight_engine.generate(db)
    counts: Dict[str, int] = {}
    for i in items:
        counts[i["severity"]] = counts.get(i["severity"], 0) + 1
    return {
        "total": len(items),
        "by_severity": counts,
        "highest": items[0]["severity"] if items else None,
    }
