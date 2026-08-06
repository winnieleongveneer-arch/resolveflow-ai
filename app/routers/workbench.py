# app/routers/workbench.py
"""
The Workbench — the human decision queue — and the policy gate that feeds it.

    POST /api/agent/gate                     evaluate -> maybe escalate
    GET  /api/workbench                      the queue
    GET  /api/workbench/{item_id}            one item, full context
    POST /api/workbench                      create an item directly
    POST /api/workbench/{item_id}/decision   Approve / Modify / Reject

Rules enforced here
-------------------
* Silence is never approval. An undecided item stays PENDING for ever; there
  is no timeout that grants permission.
* Only a PENDING item can be decided. Deciding twice is a 409, so a stale
  browser tab cannot overwrite a recorded decision.
* MODIFY must supply a modified action. The original recommendation is kept
  alongside it, so the delta between what the agent proposed and what the
  human allowed is preserved — that delta is the learning signal.
* The workflow resumes using ``approved_scope`` only. A human who narrows an
  action narrows what actually executes.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.service_desk import (
    OperatorEvent,
    RunStatus,
    Verdict,
    WorkbenchItem,
    WorkbenchStatus,
    WorkflowRun,
)
from ..schemas.service_desk import (
    DecisionRequest,
    DecisionResponse,
    GateRequest,
    GateResponse,
    WorkbenchItemCreate,
    WorkbenchItemOut,
)
from ..services import auto_client, notifications, policy_engine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/workbench", tags=["Workbench"])
gate_router = APIRouter(prefix="/agent", tags=["Policy Gate"])

DECISIONS = {"APPROVE", "MODIFY", "REJECT"}


def _event(db: Session, run_id, operator: str, event_type: str, payload: dict) -> None:
    db.add(
        OperatorEvent(
            run_id=run_id,
            operator_name=operator,
            event_type=event_type,
            event_status="ok",
            payload=payload,
        )
    )


def _create_item(
    db: Session,
    *,
    run_id,
    issue_key: str,
    request_type: str,
    case_context: dict,
    proposed_action: dict,
    policy_result: Optional[dict],
    agent_recommendation: Optional[str],
    verification_plan: Optional[dict],
    rollback_plan: Optional[dict],
    notify: bool,
) -> tuple[WorkbenchItem, Optional[dict]]:
    item = WorkbenchItem(
        run_id=run_id,
        issue_key=issue_key,
        request_type=request_type,
        status=WorkbenchStatus.PENDING,
        case_context=case_context or {},
        proposed_action=proposed_action or {},
        policy_result=policy_result,
        agent_recommendation=agent_recommendation,
        verification_plan=verification_plan,
        rollback_plan=rollback_plan,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if run:
        run.status = RunStatus.WAITING_FOR_HUMAN
        run.current_stage = "WAITING_FOR_HUMAN"
    _event(
        db, run_id, "POLICY_ENGINE", "HUMAN_REQUESTED",
        {"workbench_item_id": str(item.id), "request_type": request_type},
    )
    db.commit()

    delivery = None
    if notify:
        reasons = (policy_result or {}).get("reasons") or []
        delivery = notifications.notify_workbench_item(
            db,
            item_id=str(item.id),
            issue_key=issue_key,
            request_type=request_type,
            summary=str(case_context.get("summary") or issue_key),
            reason=" ".join(reasons) if reasons else "Policy requires human review.",
            proposed_action=str(
                (proposed_action or {}).get("description")
                or (proposed_action or {}).get("action")
                or "See Workbench for the full proposal."
            ),
            risk=str((proposed_action or {}).get("risk", "unspecified")),
            recommendation=agent_recommendation or "No recommendation supplied.",
        )
        item.notification_ref = (
            "slack:delivered" if delivery.get("delivered") else "slack:failed"
        )
        _event(
            db, run_id, "RF-06 Change and Recovery Controller", "NOTIFICATION_SENT",
            {"channel": "slack", **delivery},
        )
        db.commit()
        db.refresh(item)

    return item, delivery


# ---------------------------------------------------------------- the gate


@gate_router.post("/gate", response_model=GateResponse)
def policy_gate(payload: GateRequest, db: Session = Depends(get_db)):
    """
    THE GATE. Called by an Operator before any external side effect.

    ALLOW                -> may_execute true, nothing escalates
    REQUIRE_HUMAN_REVIEW -> Workbench item created, Slack sent, may_execute false
    DENY                 -> may_execute false, nothing executes

    The verdict is persisted either way, so the audit trail records refusals
    as well as approvals.
    """
    run = db.query(WorkflowRun).filter(WorkflowRun.id == payload.run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    result = policy_engine.evaluate(
        db,
        payload.policy_key,
        payload.context,
        run_id=payload.run_id,
        issue_key=payload.issue_key,
        proposed_action=payload.proposed_action,
    )

    _event(
        db, payload.run_id, "POLICY_ENGINE", "POLICY_EVALUATED",
        {
            "policy_key": result.policy_key,
            "policy_version": result.policy_version,
            "verdict": result.verdict,
            "reasons": result.reasons,
        },
    )
    run.status = RunStatus.POLICY_GATED
    run.current_stage = "POLICY_GATED"
    db.commit()

    item_id = None
    delivery = None

    if result.verdict == Verdict.REQUIRE_HUMAN_REVIEW:
        # Duplicate-exception protection (guide 15).
        # Re-evaluating the same case must not stack identical items in the
        # queue. If this run already has a PENDING item of the same type, we
        # return it instead of raising another — one pending decision per
        # case, however many times an Operator retries the gate.
        existing = (
            db.query(WorkbenchItem)
            .filter(
                WorkbenchItem.run_id == payload.run_id,
                WorkbenchItem.request_type == payload.request_type,
                WorkbenchItem.status == WorkbenchStatus.PENDING,
            )
            .first()
        )
        if existing is not None:
            log.info(
                "Gate re-evaluated for run %s; reusing pending item %s.",
                payload.run_id, existing.id,
            )
            return GateResponse(
                verdict=result.verdict,
                policy_key=result.policy_key,
                policy_version=result.policy_version,
                reasons=result.reasons + [
                    "An identical review is already pending in the Workbench; "
                    "no duplicate was created."
                ],
                missing_fields=result.missing_fields,
                may_execute=False,
                workbench_item_id=existing.id,
                notification={"delivered": False,
                              "detail": "Suppressed: this exception is already pending."},
            )

        item, delivery = _create_item(
            db,
            run_id=payload.run_id,
            issue_key=payload.issue_key,
            request_type=payload.request_type,
            case_context=payload.case_context,
            proposed_action=payload.proposed_action,
            policy_result=result.to_dict(),
            agent_recommendation=payload.agent_recommendation,
            verification_plan=payload.verification_plan,
            rollback_plan=payload.rollback_plan,
            notify=payload.notify,
        )
        item_id = item.id
    elif result.verdict == Verdict.DENY:
        run.status = RunStatus.DENIED
        run.current_stage = "DENIED"
        run.completed_at = datetime.now(timezone.utc)
        _event(db, payload.run_id, "ORCHESTRATOR", "DENIED",
               {"reasons": result.reasons})
        db.commit()
    else:
        run.status = RunStatus.APPROVED
        run.current_stage = "APPROVED"
        _event(db, payload.run_id, "ORCHESTRATOR", "APPROVED",
               {"basis": "policy ALLOW", "reasons": result.reasons})
        db.commit()

    return GateResponse(
        verdict=result.verdict,
        policy_key=result.policy_key,
        policy_version=result.policy_version,
        reasons=result.reasons,
        missing_fields=result.missing_fields,
        may_execute=result.verdict == Verdict.ALLOW,
        workbench_item_id=item_id,
        notification=delivery,
    )


# ------------------------------------------------------------- the queue


@router.get("", response_model=List[WorkbenchItemOut])
def list_items(
    status: Optional[str] = Query(None, examples=["PENDING"]),
    issue_key: Optional[str] = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(WorkbenchItem)
    if status:
        q = q.filter(WorkbenchItem.status == status)
    if issue_key:
        q = q.filter(WorkbenchItem.issue_key == issue_key)
    return q.order_by(WorkbenchItem.created_at.desc()).limit(limit).all()


@router.post("", response_model=WorkbenchItemOut, status_code=201)
def create_item(payload: WorkbenchItemCreate, db: Session = Depends(get_db)):
    """Create an exception directly (missing data, low confidence, rollback failure)."""
    if not db.query(WorkflowRun).filter(WorkflowRun.id == payload.run_id).first():
        raise HTTPException(status_code=404, detail="Run not found")
    item, _ = _create_item(
        db,
        run_id=payload.run_id,
        issue_key=payload.issue_key,
        request_type=payload.request_type,
        case_context=payload.case_context,
        proposed_action=payload.proposed_action,
        policy_result=payload.policy_result,
        agent_recommendation=payload.agent_recommendation,
        verification_plan=payload.verification_plan,
        rollback_plan=payload.rollback_plan,
        notify=payload.notify,
    )
    return item


@router.get("/{item_id}", response_model=WorkbenchItemOut)
def get_item(item_id: UUID, db: Session = Depends(get_db)):
    item = db.query(WorkbenchItem).filter(WorkbenchItem.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Workbench item not found")
    return item


@router.post("/{item_id}/decision", response_model=DecisionResponse)
def decide(item_id: UUID, payload: DecisionRequest, db: Session = Depends(get_db)):
    """
    Record a human decision and resume the workflow.

    APPROVE -> the proposed action becomes the approved scope
    MODIFY  -> the human's amended action becomes the approved scope; the
               original is preserved for the learning signal
    REJECT  -> nothing executes, the run is marked DENIED
    """
    decision = payload.decision.upper().strip()
    if decision not in DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"decision must be one of {sorted(DECISIONS)}",
        )

    item = db.query(WorkbenchItem).filter(WorkbenchItem.id == item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Workbench item not found")

    if item.status != WorkbenchStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This item was already decided ({item.human_decision} by "
                f"{item.reviewer}). Decisions are immutable."
            ),
        )

    if decision == "MODIFY" and not payload.modified_action:
        raise HTTPException(
            status_code=422,
            detail="modified_action is required when the decision is MODIFY.",
        )

    run = db.query(WorkflowRun).filter(WorkflowRun.id == item.run_id).first()

    item.human_decision = decision
    item.reviewer = payload.reviewer
    item.reviewer_notes = payload.reviewer_notes
    item.decided_at = datetime.now(timezone.utc)

    if decision == "APPROVE":
        item.status = WorkbenchStatus.APPROVED
        item.approved_scope = item.proposed_action
        run_status = RunStatus.APPROVED
        message = "Approved. The workflow resumes with the proposed action."
    elif decision == "MODIFY":
        item.status = WorkbenchStatus.MODIFIED
        item.modified_action = payload.modified_action
        item.approved_scope = payload.modified_action
        run_status = RunStatus.APPROVED
        message = (
            "Modified. The workflow resumes with the amended scope only; the "
            "original recommendation is retained as a learning candidate."
        )
    else:
        item.status = WorkbenchStatus.REJECTED
        item.approved_scope = None
        run_status = RunStatus.DENIED
        message = "Rejected. No external action will be taken."

    if run:
        run.status = run_status
        run.current_stage = run_status
        if run_status == RunStatus.DENIED:
            run.completed_at = datetime.now(timezone.utc)

    # ---- resume the paused Auto run --------------------------------------
    # Answering the user form signals the workflow to continue. This is what
    # makes the human decision resume the SAME run rather than start a new one.
    resume: Optional[dict] = None
    if item.auto_activity_run_id and auto_client.is_configured():
        try:
            resume = auto_client.submit_form_decision(
                item.auto_activity_run_id,
                approve=(decision in ("APPROVE", "MODIFY")),
                fields={
                    "decision": decision,
                    "reviewer": payload.reviewer,
                    "notes": payload.reviewer_notes or "",
                    "approved_scope": str(item.approved_scope or {}),
                },
            )
            _event(db, item.run_id, "ORCHESTRATOR", "AUTO_RESUMED",
                   {"activity_run_id": item.auto_activity_run_id, **resume})
        except Exception as exc:
            resume = {"submitted": False,
                      "detail": auto_client.redact(str(exc))}
            _event(db, item.run_id, "ORCHESTRATOR", "AUTO_RESUME_FAILED",
                   {"activity_run_id": item.auto_activity_run_id, **resume})
            log.error("Failed to resume Auto run for item %s: %s",
                      item.id, resume["detail"])
        item.auto_resume_result = resume

    _event(
        db, item.run_id, "WORKBENCH", "HUMAN_DECIDED",
        {
            "workbench_item_id": str(item.id),
            "decision": decision,
            "reviewer": payload.reviewer,
            "notes": payload.reviewer_notes,
            "original_action": item.proposed_action,
            "approved_scope": item.approved_scope,
            "auto_resume": resume,
        },
    )
    db.commit()
    db.refresh(item)

    log.info("Workbench %s decided %s by %s", item.id, decision, payload.reviewer)
    return DecisionResponse(
        item_id=item.id,
        run_id=item.run_id,
        issue_key=item.issue_key,
        status=item.status,
        human_decision=decision,
        approved_scope=item.approved_scope,
        run_status=run_status,
        message=message,
    )
