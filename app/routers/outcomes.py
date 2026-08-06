# app/routers/outcomes.py
"""
Outcome Ledger API — metrics with their arithmetic attached.

    GET  /api/outcomes/metrics    every metric with formula, counts and run ids
    GET  /api/outcomes/ledger     the per-case rows behind those metrics
    GET  /api/outcomes/baselines  manual-effort assumptions and their sources
    PUT  /api/outcomes/baselines/{task_type}   revise a baseline
    POST /api/outcomes/record     write a ledger row for a finished run

Nothing is cached. Every number is recomputed from rows on request, so the
figures on a dashboard can never drift from the evidence behind them.
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.service_desk import TaskBaseline, WorkflowRun
from ..services import backlog, outcomes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/outcomes", tags=["Outcome Ledger"])


class RecordRequest(BaseModel):
    run_id: UUID
    task_type: str = "general_triage"
    outcome: Optional[str] = None
    verified: bool = False
    verification_note: Optional[str] = None
    sla_state: Optional[str] = None
    predicted_breach: bool = False
    breach_avoided: bool = False
    rollback_attempted: bool = False
    rollback_succeeded: Optional[bool] = None


class BaselineUpdate(BaseModel):
    manual_minutes: float
    source: str
    updated_by: Optional[str] = None


@router.get("/metrics")
def metrics(
    verified_only: bool = Query(True,
        description="Count only cases whose outcome was verified. "
                    "Leaving this on is the honest default."),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return outcomes.compute(db, verified_only=verified_only)


@router.get("/ledger")
def ledger(limit: int = Query(200, le=1000), db: Session = Depends(get_db)):
    return outcomes.ledger(db, limit=limit)


@router.get("/baselines")
def list_baselines(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    return outcomes.baselines(db)


@router.put("/baselines/{task_type}")
def update_baseline(
    task_type: str, payload: BaselineUpdate, db: Session = Depends(get_db)
):
    """
    Revise a manual-effort assumption.

    The source is mandatory: a baseline without a justification is exactly the
    kind of unsupported claim this ledger exists to avoid.
    """
    row = (
        db.query(TaskBaseline).filter(TaskBaseline.task_type == task_type).first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No baseline for '{task_type}'")
    if not payload.source.strip():
        raise HTTPException(
            status_code=422,
            detail="A written source or justification is required for any baseline.",
        )
    row.manual_minutes = payload.manual_minutes
    row.source = payload.source
    row.updated_by = payload.updated_by or "command_center"
    db.commit()
    db.refresh(row)
    return {
        "task_type": row.task_type,
        "manual_minutes": row.manual_minutes,
        "source": row.source,
        "updated_by": row.updated_by,
    }


@router.post("/record", status_code=201)
def record_outcome(payload: RecordRequest, db: Session = Depends(get_db)):
    run = db.query(WorkflowRun).filter(WorkflowRun.id == payload.run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    row = outcomes.record(
        db,
        run=run,
        task_type=payload.task_type,
        outcome=payload.outcome,
        verified=payload.verified,
        verification_note=payload.verification_note,
        sla_state=payload.sla_state,
        predicted_breach=payload.predicted_breach,
        breach_avoided=payload.breach_avoided,
        rollback_attempted=payload.rollback_attempted,
        rollback_succeeded=payload.rollback_succeeded,
    )
    return {
        "id": str(row.id),
        "issue_key": row.issue_key,
        "outcome": row.outcome,
        "baseline_manual_minutes": row.baseline_manual_minutes,
        "human_touch_minutes": round((row.human_touch_seconds or 0) / 60, 2),
        "minutes_saved": round(
            max(row.baseline_manual_minutes - (row.human_touch_seconds or 0) / 60, 0), 2
        ),
        "verified": row.verified,
    }


class SweepRequest(BaseModel):
    limit: int = 50
    offset: int = 0
    table: str = "issues"


@router.post("/sweep")
def sweep_backlog(payload: SweepRequest, db: Session = Depends(get_db)):
    """
    Run a page of the real backlog through the governed path.

    Tickets are read from Supabase, not from the supplied spreadsheet
    (guide 9.2). Each case is classified from its field values, evaluated
    against the appropriate policy, and written to the outcome ledger.

    Nothing branches on an issue key, so a ticket nobody prepared is handled
    exactly like one that was.
    """
    try:
        return backlog.process(
            db, limit=payload.limit, offset=payload.offset, table=payload.table
        )
    except backlog.SupabaseNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:400])


@router.get("/supabase-tables")
def probe_supabase(table: str = "issues", limit: int = 1):
    """
    Check the backlog table is reachable and show its column names, so the
    field mapping can be confirmed before a full sweep.
    """
    try:
        rows = backlog.fetch_tickets(table=table, limit=limit)
        return {
            "table": table,
            "reachable": True,
            "row_count_sampled": len(rows),
            "columns": sorted(rows[0].keys()) if rows else [],
        }
    except backlog.SupabaseNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        return {"table": table, "reachable": False, "error": str(exc)[:300]}


@router.get("/round1-baseline")
def round1_baseline(table: str = "issues"):
    """
    The Round 1 auto-resolution rate, computed from real records.

    This is the denominator for every "metric movement" claim, so it is
    counted rather than remembered (guide 16).
    """
    try:
        return backlog.round1_baseline(table=table)
    except backlog.SupabaseNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:400])


class CalibrateRequest(BaseModel):
    limit: int = 200
    table: str = "issues"
    thresholds: Optional[List[float]] = None


@router.post("/calibrate")
def calibrate_threshold(payload: CalibrateRequest, db: Session = Depends(get_db)):
    """
    Simulate the autonomy rate across a range of confidence thresholds.

    Read-only: no runs are created, no policies are changed, nothing executes.
    Use it to choose a threshold you can justify, then set it on the Policies
    page and re-sweep.
    """
    try:
        tickets = backlog.fetch_tickets(table=payload.table, limit=payload.limit)
        backlog.annotate_clusters(tickets)
        thresholds = payload.thresholds or [
            0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95
        ]
        return outcomes.calibrate(
            db, tickets, backlog.build_context, thresholds
        )
    except backlog.SupabaseNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:400])
