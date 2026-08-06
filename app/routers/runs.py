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
import os
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
from ..services import auto_client, integrations, passport

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

    # ---- invoke the Supervity Auto Orchestrator --------------------------
    # POST /api/v1/workflow-runs/execute {workflowId, inputs}
    # auto_run_id is written ONLY if Auto actually returns one. A missing id
    # is recorded as an error, never invented.
    if os.getenv("AUTO_TRIGGER_ON_RUN", "true").lower() == "true":
        try:
            result = auto_client.execute_workflow(
                inputs={
                    "issue_key": payload.issue_key,
                    "run_id": str(run.id),
                    "trigger_source": payload.trigger_source,
                    # Where the Orchestrator calls back for the policy verdict.
                    "policy_gate_url": (
                        os.getenv("PUBLIC_BACKEND_URL", "").rstrip("/")
                        + "/api/agent/gate"
                    ),
                    **(payload.trigger_payload or {}),
                }
            )
            auto_run_id = auto_client.extract_run_id(result.get("response"))
            run.auto_run_id = auto_run_id
            run.status = RunStatus.CONTEXTUALISING
            run.current_stage = "CONTEXTUALISING"
            db.add(OperatorEvent(
                run_id=run.id,
                operator_name="ORCHESTRATOR",
                event_type="AUTO_INVOKED",
                event_status="ok" if auto_run_id else "no_run_id",
                sequence=2,
                duration_ms=int(result.get("latency_ms") or 0),
                payload={
                    "workflow_id": auto_client.orchestrator_id(),
                    "auto_run_id": auto_run_id,
                },
            ))
            integrations.record_write(db, "supervity_auto", 1)
        except auto_client.AutoNotConfigured as exc:
            run.error_message = str(exc)
            db.add(OperatorEvent(
                run_id=run.id, operator_name="ORCHESTRATOR",
                event_type="AUTO_NOT_CONFIGURED", event_status="skipped",
                sequence=2, payload={"detail": str(exc)},
            ))
            log.warning("Auto not configured; run %s persisted without it.", run.id)
        except auto_client.AutoError as exc:
            run.status = RunStatus.FAILED
            run.current_stage = "FAILED"
            run.error_message = auto_client.redact(str(exc))
            db.add(OperatorEvent(
                run_id=run.id, operator_name="ORCHESTRATOR",
                event_type="AUTO_INVOKE_FAILED", event_status="error",
                sequence=2, payload={"detail": auto_client.redact(str(exc))},
            ))
            # Reads against Auto succeed (that is how the Orchestrator was
            # discovered); only workflow execution is failing. Report that
            # precisely instead of marking the whole integration dead.
            integrations.record_degraded(
                db, "supervity_auto",
                "Authenticated reads succeed (GET /api/v1/workflows). "
                "POST /api/v1/workflow-runs/execute returns HTTP 500 for every "
                "payload shape tried — platform-side. Trigger the Orchestrator "
                "from the Auto UI as a fallback. Detail: "
                + auto_client.redact(str(exc))[:200])
        db.commit()
        db.refresh(run)

    log.info("Run %s created for %s (auto_run_id=%s)",
             run.id, run.issue_key, run.auto_run_id)
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


# ---------------------------------------------------------------------------
# Supervity Auto diagnostics
# ---------------------------------------------------------------------------


@router.get("/auto/diagnose")
def diagnose_auto(org: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Work out which x-active-org value the Auto API accepts.

    The docs require the header on every endpoint but never say where the
    value comes from, so this probes the plausible candidates and reports the
    evidence rather than guessing.
    """
    candidates = []
    if org:
        candidates.append(org)
    for c in [
        os.getenv("SUPERVITY_ORG_KEY", "").strip(),
        "",
        "alpha",
        "winnieleongveneer_gm",
        os.getenv("SUPERVITY_WORKSPACE_ID", "").strip(),
    ]:
        if c not in candidates:
            candidates.append(c)
    return auto_client.diagnose_org_keys(candidates)


@router.get("/auto/workflows")
def list_auto_workflows(org: Optional[str] = None):
    """
    List the workflows this API key can see, so the Orchestrator's UUID can be
    copied into SUPERVITY_ORCHESTRATOR_ID without hunting through editor URLs.
    """
    try:
        payload, latency = auto_client.list_workflows(
            limit=100, org_override=org if org is not None else None
        )
        items = payload.get("workflows", []) if isinstance(payload, dict) else []
        return {
            "count": len(items),
            "latency_ms": round(latency, 1),
            "workflows": [
                {"id": w.get("id"), "name": w.get("name"),
                 "description": w.get("description")}
                for w in items
            ],
        }
    except auto_client.AutoNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except auto_client.AutoError as exc:
        raise HTTPException(status_code=502,
                            detail=auto_client.redact(str(exc)))


@router.get("/auto/diagnose-auth")
def diagnose_auto_auth(path: str = "/api/v1/workflows"):
    """Try every documented credential transport and report what Auto says."""
    return auto_client.diagnose_auth(path)


@router.post("/auto/probe-execute")
def probe_auto_execute(issue_key: str = "PROBE-0001"):
    """Find which `inputs` shape POST /workflow-runs/execute accepts."""
    return auto_client.probe_execute_shapes(issue_key=issue_key)


@router.get("/auto/inspect")
def inspect_auto_workflow(workflow_id: Optional[str] = None):
    """
    Read-only inspection of the Orchestrator: its detail record and its
    version history. If there is no default/published version, that explains
    a 500 from /workflow-runs/execute.
    """
    wf = (workflow_id or auto_client.orchestrator_id()).strip()
    out = {"workflow_id": wf}
    for label, path in (
        ("detail", f"/api/v1/workflows/{wf}"),
        ("versions", f"/api/v1/workflows/{wf}/versions"),
        ("upcoming_runs", f"/api/v1/workflows/{wf}/upcoming-runs"),
        ("recent_runs", "/api/v1/workflow-runs"),
    ):
        try:
            params = {"workflowId": wf, "limit": 5} if label == "recent_runs" else None
            payload, latency = auto_client._request(
                "GET", path, params=params, retries=1
            )
            out[label] = {"ok": True, "latency_ms": round(latency, 1),
                          "data": payload}
        except Exception as exc:
            out[label] = {"ok": False,
                          "error": auto_client.redact(str(exc))[:400]}
    return out


@router.get("/runs/{run_id}/passport")
def decision_passport(run_id: UUID, db: Session = Depends(get_db)):
    """
    The Decision Passport for one run — readable in the Command Center and
    exportable as JSON. Every field is read from persisted rows; nothing is
    generated prose without an evidence reference behind it.
    """
    doc = passport.build(db, run_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return doc
