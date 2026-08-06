# app/services/passport.py
"""
Decision Passport — the evidence record for one run.

Guide 11.2 asks that a business user can trace a decision without an
engineer. So this assembles, from rows that already exist, an answer to each
question a reviewer or auditor would ask:

    What happened?
    What did the system know, and where did each fact come from?
    Which Operators took part, and what did each return?
    What was proposed, and what uncertainty remained?
    Which policy versions were evaluated, and why that verdict?
    Did a human intervene, and what exactly did they change?
    What was the final outcome?

Nothing here is generated prose. Every field is read from workflow_runs,
operator_events, policy_evaluations or workbench_items — if a fact was not
recorded, the passport says so rather than filling the gap.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..models.service_desk import (
    OperatorEvent,
    PolicyEvaluation,
    RunStatus,
    Verdict,
    WorkbenchItem,
    WorkflowRun,
)

NOT_RECORDED = "Not recorded."


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _duration_seconds(run: WorkflowRun) -> Optional[float]:
    if run.started_at and run.completed_at:
        return round((run.completed_at - run.started_at).total_seconds(), 1)
    return None


def build(db: Session, run_id: UUID) -> Optional[Dict[str, Any]]:
    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if run is None:
        return None

    events: List[OperatorEvent] = (
        db.query(OperatorEvent)
        .filter(OperatorEvent.run_id == run_id)
        .order_by(OperatorEvent.event_timestamp.asc())
        .all()
    )
    evaluations: List[PolicyEvaluation] = (
        db.query(PolicyEvaluation)
        .filter(PolicyEvaluation.run_id == run_id)
        .order_by(PolicyEvaluation.evaluated_at.asc())
        .all()
    )
    items: List[WorkbenchItem] = (
        db.query(WorkbenchItem)
        .filter(WorkbenchItem.run_id == run_id)
        .order_by(WorkbenchItem.created_at.asc())
        .all()
    )

    operators = sorted({
        e.operator_name for e in events
        if e.operator_name not in ("ORCHESTRATOR", "POLICY_ENGINE", "WORKBENCH")
    })

    # ---- what the system knew, and from where ---------------------------
    facts: List[Dict[str, Any]] = []
    for ev in evaluations:
        for key, value in (ev.input_context or {}).items():
            facts.append({
                "fact": key,
                "value": value,
                "supplied_to": ev.policy_key,
                "recorded_at": _iso(ev.evaluated_at),
            })

    # ---- decisions -------------------------------------------------------
    decisions = []
    for ev in evaluations:
        decisions.append({
            "policy_key": ev.policy_key,
            "policy_version": ev.policy_version,
            "verdict": ev.verdict,
            "reasons": ev.reasons or [],
            "configuration_at_the_time": ev.configuration_snapshot,
            "proposed_action": ev.proposed_action,
            "is_simulation": ev.is_simulation,
            "evaluated_at": _iso(ev.evaluated_at),
        })

    human = []
    for item in items:
        human.append({
            "workbench_item_id": str(item.id),
            "request_type": item.request_type,
            "status": item.status,
            "decision": item.human_decision or "still pending",
            "reviewer": item.reviewer or NOT_RECORDED,
            "notes": item.reviewer_notes,
            "original_recommendation": item.proposed_action,
            "amended_action": item.modified_action,
            "approved_scope": item.approved_scope,
            "changed_by_human": bool(item.modified_action),
            "escalation": item.notification_ref,
            "raised_at": _iso(item.created_at),
            "decided_at": _iso(item.decided_at),
            "auto_resume": item.auto_resume_result,
        })

    verdicts = [e.verdict for e in evaluations if not e.is_simulation]
    blocked = [v for v in verdicts if v != Verdict.ALLOW]

    # ---- the plain-language answers -------------------------------------
    if run.status == RunStatus.RESOLVED:
        outcome = "The case was resolved."
    elif run.status == RunStatus.DENIED:
        outcome = "No action was taken: policy denied it or a reviewer rejected it."
    elif run.status == RunStatus.WAITING_FOR_HUMAN:
        outcome = "Paused. A human decision is required before anything else happens."
    elif run.status == RunStatus.FAILED:
        outcome = f"The run failed. {run.error_message or ''}".strip()
    else:
        outcome = f"In progress at stage {run.current_stage or run.status}."

    if not evaluations:
        why = "No policy was evaluated during this run."
    elif blocked:
        last = evaluations[-1]
        why = (
            f"The action was not taken automatically because "
            f"{last.policy_key} v{last.policy_version} returned {last.verdict}. "
            + " ".join(last.reasons or [])
        )
    else:
        why = (
            "Every applicable policy returned ALLOW, so the action was permitted "
            "without a human decision."
        )

    intervened = any(i.human_decision for i in items)
    if not items:
        human_summary = "No human was asked; the policies decided this case."
    elif intervened:
        parts = []
        for i in items:
            if i.human_decision:
                verb = {"APPROVE": "approved", "MODIFY": "amended",
                        "REJECT": "rejected"}.get(i.human_decision, i.human_decision)
                parts.append(f"{i.reviewer or 'A reviewer'} {verb} the proposal")
                if i.modified_action:
                    parts[-1] += " and narrowed its scope"
        human_summary = ". ".join(parts) + "."
    else:
        human_summary = "A human was asked but has not yet decided."

    return {
        "run_id": str(run.id),
        "issue_key": run.issue_key,
        "auto_run_id": run.auto_run_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "trigger_source": run.trigger_source,
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "duration_seconds": _duration_seconds(run),
        "error_message": run.error_message,

        "summary": {
            "what_happened": outcome,
            "why_this_outcome": why,
            "did_a_human_intervene": human_summary,
            "operators_involved": operators or ["None recorded"],
            "policies_evaluated": sorted({e.policy_key for e in evaluations})
                                  or ["None"],
            "external_changes": (
                [e.payload for e in events if e.event_type == "ACTION_EXECUTED"]
                or "No external system was modified during this run."
            ),
            "verification": (
                [e.payload for e in events if e.event_type == "VERIFICATION"]
                or "No verification step was recorded."
            ),
        },

        "timeline": [{
            "at": _iso(e.event_timestamp),
            "actor": e.operator_name,
            "event": e.event_type,
            "status": e.event_status,
            "duration_ms": e.duration_ms,
            "detail": e.payload,
        } for e in events],

        "facts_used": facts,
        "policy_decisions": decisions,
        "human_decisions": human,

        "counts": {
            "events": len(events),
            "policy_evaluations": len(evaluations),
            "human_reviews": len(items),
            "blocked_actions": len(blocked),
        },
    }
