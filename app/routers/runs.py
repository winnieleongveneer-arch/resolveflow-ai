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
from ..services import auto_client, integrations, maintenance, passport

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
    agent_only: bool = False,
    limit: int = Query(25, le=200),
    db: Session = Depends(get_db),
):
    """
    Recent runs.

    ``agent_only`` filters to runs an Operator actually worked: those with at
    least one recorded event. A reconciliation sweep writes a run row and a
    policy evaluation but never calls an Operator, so it has no events. Those
    rows are real history and stay queryable — they simply are not agent
    activity, and showing them in a feed labelled "recent agent activity"
    overstates what the agent did.
    """
    q = db.query(WorkflowRun)
    if status:
        q = q.filter(WorkflowRun.status == status)
    if issue_key:
        q = q.filter(WorkflowRun.issue_key == issue_key)
    if agent_only:
        q = q.filter(
            db.query(OperatorEvent.id)
            .filter(OperatorEvent.run_id == WorkflowRun.id)
            .exists()
        )
    return q.order_by(WorkflowRun.started_at.desc()).limit(limit).all()


@router.get("/summary")
def run_summary(db: Session = Depends(get_db)):
    """
    Dashboard metrics with defensible definitions.

    Each tile answers a different question, and none of them conflate
    "imported", "waiting" and "executing":

      backlog            cases known but not yet executing
      executing_now      runs genuinely in flight on Auto
      awaiting_human     UNIQUE pending Workbench decisions
      verified_resolved  completed cases with verification evidence
      technical_failures runs Auto reported failed, or launches that never started
      auto_resolution    verified automatic / all verified resolutions, or None

    A policy REVIEW or DENY is a business decision, not a technical failure,
    and is counted separately. auto_resolution returns None — rendered N/A —
    when the denominator is zero, because 0% would imply the agent tried and
    failed rather than that nothing has completed yet.
    """
    by_status = dict(
        db.query(WorkflowRun.status, func.count(WorkflowRun.id))
        .group_by(WorkflowRun.status)
        .all()
    )
    total = sum(by_status.values())

    EXECUTING = {RunStatus.CONTEXTUALISING, RunStatus.ANALYSING,
                 RunStatus.PLANNING, RunStatus.EXECUTING,
                 RunStatus.VERIFYING, RunStatus.ROLLING_BACK,
                 RunStatus.COMMUNICATING, RunStatus.LEARNING}
    WAITING = {RunStatus.WAITING_FOR_HUMAN}
    FAILED = {RunStatus.FAILED}
    # Backlog is everything known but not executing and not finished. Waiting
    # cases belong here too: they are real work in the system, and leaving them
    # out of every tile made 100 runs invisible on the dashboard.
    QUEUED = {RunStatus.RECEIVED, RunStatus.POLICY_GATED, RunStatus.ESCALATED,
              RunStatus.WAITING_FOR_HUMAN, RunStatus.APPROVED}

    executing = sum(v for k, v in by_status.items() if k in EXECUTING)
    waiting_runs = sum(v for k, v in by_status.items() if k in WAITING)
    failed = sum(v for k, v in by_status.items() if k in FAILED)
    backlog = sum(v for k, v in by_status.items() if k in QUEUED)

    # Unique pending decisions, not raw item count.
    awaiting_human = (
        db.query(func.count(func.distinct(WorkbenchItem.run_id)))
        .filter(WorkbenchItem.status == WorkbenchStatus.PENDING)
        .scalar()
    ) or 0

    # Verified outcomes come from the ledger, never from run status alone.
    verified_resolved = 0
    verified_auto = 0
    try:
        from ..models.service_desk import OutcomeLedger
        verified_resolved = (
            db.query(func.count(OutcomeLedger.id))
            .filter(OutcomeLedger.verified.is_(True),
                    OutcomeLedger.outcome.in_(("AUTO_RESOLVED", "HUMAN_RESOLVED")))
            .scalar()
        ) or 0
        verified_auto = (
            db.query(func.count(OutcomeLedger.id))
            .filter(OutcomeLedger.verified.is_(True),
                    OutcomeLedger.outcome == "AUTO_RESOLVED")
            .scalar()
        ) or 0
    except Exception:
        pass

    auto_rate = (round(verified_auto / verified_resolved * 100, 1)
                 if verified_resolved else None)

    latest = db.query(WorkflowRun).order_by(WorkflowRun.started_at.desc()).first()

    # ---- source tickets vs open agent cases -----------------------------
    # Two different questions, and conflating them under one "Backlog" number
    # invites the obvious challenge: "you said 460 tickets, why does this say
    # 123?" Both answers are true; they count different things.
    #
    #   source_tickets    what exists in the system of record
    #   open_agent_cases  UNIQUE cases the agent has taken in and not finished
    #
    # The second deduplicates by issue key on purpose. One ticket may have
    # several attempts; a dashboard that counts attempts is counting its own
    # retries as work.
    open_agent_cases = (
        db.query(func.count(func.distinct(WorkflowRun.issue_key)))
        .filter(~WorkflowRun.issue_key.in_(
            db.query(WorkflowRun.issue_key).filter(
                WorkflowRun.status.in_([RunStatus.RESOLVED, RunStatus.DENIED,
                                        RunStatus.FAILED]))
        ))
        .scalar()
    ) or 0

    # Counted live from the system of record. If Supabase is unreachable we
    # return None rather than a stale or invented number — the tile then just
    # omits the denominator instead of asserting one.
    source_tickets = None
    try:
        from ..services import backlog as backlog_svc
        source_tickets = backlog_svc.source_ticket_count()
    except Exception as exc:
        log.warning("Source ticket count unavailable: %s", exc)

    return {
        "total_runs": total,
        "backlog": backlog,
        "open_agent_cases": open_agent_cases,
        "source_tickets": source_tickets,
        "executing_now": executing,
        "awaiting_human": awaiting_human,
        "waiting_runs": waiting_runs,
        "verified_resolved": verified_resolved,
        "verified_auto_resolved": verified_auto,
        "technical_failures": failed,
        "auto_resolution_rate": auto_rate,
        "auto_resolution_note": (
            None if auto_rate is not None else
            "No verified resolutions yet, so the rate is not computable. "
            "Shown as N/A rather than 0% to avoid implying failed attempts."
        ),
        "runs_by_status": by_status,
        # Kept for backwards compatibility with existing callers.
        "active_runs": executing,
        "pending_human_reviews": awaiting_human,
        "latest_run": {
            "id": str(latest.id),
            "issue_key": latest.issue_key,
            "status": latest.status,
            "started_at": latest.started_at.isoformat() if latest.started_at else None,
        } if latest else None,
        "reconciles": (
            backlog + executing + failed
            + sum(v for k, v in by_status.items()
                  if k in (RunStatus.RESOLVED, RunStatus.DENIED)) == total
        ),
        "definitions": {
            "backlog": "Known cases not executing and not finished, including "
                       "those waiting on a human.",
            "executing_now": "Runs in an actively executing stage. Waiting and "
                             "failed-to-start runs are excluded.",
            "awaiting_human": "Distinct runs with a pending Workbench decision.",
            "verified_resolved": "Ledger rows marked verified with a resolved outcome.",
            "technical_failures": "Runs Auto reported failed. Policy DENY and "
                                  "human REJECT are business decisions, not failures.",
        },
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

    _close_out(db, run, payload)

    db.refresh(event)
    return event


# ---------------------------------------------------------------------------
# Closing a case out
# ---------------------------------------------------------------------------


def _close_out(db: Session, run: WorkflowRun, payload: OperatorEventCreate) -> None:
    """
    Turn an Operator's terminal event into a run outcome and a ledger row.

    The Operator reports what it did; the Command Center decides what that
    means for the record. Keeping the decision here rather than trusting the
    Operator to declare its own success is the point — an Operator cannot
    write itself into the resolution figures.

    Three rules, and each one is a claim we are willing to defend:

      * A fix only counts as resolved when the Operator confirmed the write
        landed. 'verified: false' means it could not confirm its own work, and
        that is an escalation, not a resolution.
      * AUTO_RESOLVED requires that no human touched the case. If a Workbench
        item was decided, it is HUMAN_RESOLVED however small the intervention.
      * A refusal is a business outcome, not a technical failure.
    """
    body = payload.payload or {}
    event_type = payload.event_type
    terminal = {"REMEDIATION_APPLIED", "REMEDIATION_REFUSED"}
    if event_type not in terminal:
        return

    # Never write a second ledger row for the same run.
    try:
        from ..models.service_desk import OutcomeLedger
        if db.query(OutcomeLedger).filter(OutcomeLedger.run_id == run.id).first():
            return
    except Exception:
        return

    if event_type == "REMEDIATION_APPLIED":
        confirmed = bool(body.get("verified")) and payload.event_status == "ok"
        if not confirmed:
            # The system of record did not back the claim. Say so and stop.
            run.status = RunStatus.ESCALATED
            run.current_stage = RunStatus.ESCALATED
            db.commit()
            log.info("Run %s reported a fix but could not confirm it; not counted.",
                     run.id)
            return
        run.status = RunStatus.RESOLVED
        run.current_stage = RunStatus.RESOLVED
        outcome, verified = None, True
        note = str(body.get("verification") or "Confirmed by the system of record.")
    else:
        run.status = RunStatus.DENIED
        run.current_stage = RunStatus.DENIED
        outcome, verified = "DENIED", False
        note = "; ".join(body.get("reasons") or []) or "Refused by policy."

    run.completed_at = datetime.now(timezone.utc)
    db.commit()

    try:
        from ..services import outcomes
        from ..services.backlog import classify
        task_type = "known_error_remediation"
        try:
            task_type = classify({"summary": run.issue_key,
                                  **(run.trigger_payload or {})})[0]
        except Exception:
            pass
        outcomes.record(
            db,
            run=run,
            task_type=task_type,
            outcome=outcome,
            verified=verified,
            verification_note=note,
        )
    except Exception as exc:  # never let bookkeeping break the trace
        log.warning("Could not write a ledger row for run %s: %s", run.id, exc)


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


@router.get("/auto/validate")
def validate_auto_workflow(workflow_id: Optional[str] = None):
    """Is the configured ID a real, published, executable Orchestrator?"""
    return auto_client.validate_workflow(workflow_id)


@router.post("/auto/execute-stream-test")
def execute_stream_test(issue_key: str = "ITSM-2211"):
    """
    Canonical execution smoke test: multipart/form-data to
    /api/v1/workflow-runs/execute/stream.

    Returns the HTTP status, the Auto workflowRunId if one is issued, and the
    first SSE events. This is the test that decides whether the platform
    blocker is real or whether we were simply sending the wrong content type.
    """
    return auto_client.execute_stream(inputs={
        "issue_key": issue_key,
        "trigger_source": "command_center",
    })


@router.post("/auto/execute-matrix")
def execute_matrix(issue_key: str = "ITSM-2211"):
    """Try the remaining execution shapes and report which one Auto accepts."""
    return auto_client.execute_matrix(issue_key)


@router.get("/auto/support-bundle")
def auto_support_bundle(issue_key: str = "ITSM-2211"):
    """Credential-free diagnostic bundle for the Supervity execution 500."""
    return auto_client.support_bundle(issue_key)


@router.post("/maintenance")
def run_maintenance(dry_run: bool = True, db: Session = Depends(get_db)):
    """
    Find and optionally correct data-integrity problems.

    Defaults to a dry run. Nothing is ever deleted: duplicates are expired or
    cancelled with a maintenance note and impossible states are corrected, so
    the audit trail survives and repeat runs are safe.
    """
    return maintenance.run(db, dry_run=dry_run)
