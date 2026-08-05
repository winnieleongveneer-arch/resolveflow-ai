# app/routers/runs.py
"""
Agent run API — the Command Center's handle on an Orchestrator run.

    POST /api/agent/runs                    start a run (creates run_id)
    GET  /api/agent/runs                    list recent runs
    GET  /api/agent/runs/{run_id}           one run
    GET  /api/agent/runs/{run_id}/events    the live Operator trace
    POST /api/agent/runs/{run_id}/events    Auto/Operators report progress
    GET  /api/agent/summary                 dashboard counters

P0 scope: this persists the run and its event trace. Invoking the Supervity
Auto Orchestrator is added in P0-2 once the Auto API contract is confirmed —
it is deliberately NOT stubbed with an invented endpoint here, because a
fabricated ``auto_run_id`` would misrepresent a live integration.

``auto_run_id`` stays NULL until Auto genuinely returns one.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.service_desk import (
    OperatorEvent,
    RunStatus,
    WorkbenchItem,
    WorkbenchStatus,
    WorkflowRun,
)
from ..schemas.service_desk import (
    OperatorEventCreate,
    OperatorEventOut,
    RunCreate,
    RunOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent Runs"])


@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(payload: RunCreate, db: Session = Depends(get_db)):
    """
    Start a run against one case.

    Duplicate-run protection: if ``idempotency_key`` matches an existing run,
    that run is returned rather than a second one being created.
    """
    if payload.idempotency_key:
        existing = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.idempotency_key == payload.idempotency_key)
            .first()
        )
        if existing:
            log.info("Idempotent replay for key=%s -> run %s",
                     payload.idempotency_key, existing.id)
            return existing

    run = WorkflowRun(
        issue_key=payload.issue_key,
        trigger_source=payload.trigger_source,
        trigger_payload=payload.trigger_payload,
        idempotency_key=payload.idempotency_key,
        status=RunStatus.RECEIVED,
        current_stage="RECEIVED",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    db.add(
        OperatorEvent(
            run_id=run.id,
            operator_name="ORCHESTRATOR",
            event_type="RUN_RECEIVED",
            event_status="ok",
            sequence=1,
            payload={
                "issue_key": payload.issue_key,
                "trigger_source": payload.trigger_source,
            },
        )
    )
    db.commit()

    log.info("Run %s created for %s", run.id, run.issue_key)
    return run


@router.get("/runs", response_model=List[RunOut])
def list_runs(
    status: Optional[str] = None,
    issue_key: Optional[str] = None,
    limit: int = Query(25, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(WorkflowRun)
    if status:
        q = q.filter(WorkflowRun.status == status)
    if issue_key:
        q = q.filter(WorkflowRun.issue_key == issue_key)
    return q.order_by(WorkflowRun.started_at.desc()).limit(limit).all()


@router.get("/summary")
def run_summary(db: Session = Depends(get_db)):
    """
    Live dashboard counters, computed from real rows.

    Returns zeros on a fresh database. That is correct — the dashboard must
    move because the agent ran, not because a number was seeded.
    """
    by_status = dict(
        db.query(WorkflowRun.status, func.count(WorkflowRun.id))
        .group_by(WorkflowRun.status)
        .all()
    )
    total = sum(by_status.values())
    active = sum(v for k, v in by_status.items() if k not in RunStatus.TERMINAL)
    pending_reviews = (
        db.query(func.count(WorkbenchItem.id))
        .filter(WorkbenchItem.status == WorkbenchStatus.PENDING)
        .scalar()
    ) or 0
    latest = (
        db.query(WorkflowRun).order_by(WorkflowRun.started_at.desc()).first()
    )
    return {
        "total_runs": total,
        "active_runs": active,
        "pending_human_reviews": pending_reviews,
        "runs_by_status": by_status,
        "latest_run": {
            "id": str(latest.id),
            "issue_key": latest.issue_key,
            "status": latest.status,
            "started_at": latest.started_at.isoformat() if latest.started_at else None,
        } if latest else None,
    }


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: UUID, db: Session = Depends(get_db)):
    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/events", response_model=List[OperatorEventOut])
def list_run_events(run_id: UUID, db: Session = Depends(get_db)):
    """The Operator trace, oldest first — this is the live timeline."""
    if not db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first():
        raise HTTPException(status_code=404, detail="Run not found")
    return (
        db.query(OperatorEvent)
        .filter(OperatorEvent.run_id == run_id)
        .order_by(OperatorEvent.event_timestamp.asc(), OperatorEvent.sequence.asc())
        .all()
    )


@router.post("/runs/{run_id}/events", response_model=OperatorEventOut, status_code=201)
def append_run_event(
    run_id: UUID, payload: OperatorEventCreate, db: Session = Depends(get_db)
):
    """
    Append an Operator event. Auto calls this as the Orchestrator progresses,
    which is what makes the Command Center trace live rather than simulated.
    """
    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    event = OperatorEvent(
        run_id=run_id,
        operator_name=payload.operator_name,
        event_type=payload.event_type,
        event_status=payload.event_status,
        sequence=payload.sequence,
        duration_ms=payload.duration_ms,
        payload=payload.payload,
    )
    db.add(event)

    # Keep the run's coarse state in step with the trace.
    if payload.event_type in RunStatus.ALL:
        run.status = payload.event_type
        run.current_stage = payload.event_type
        if payload.event_type in RunStatus.TERMINAL:
            run.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(event)
    return event
