# app/routers/policies.py
"""
AI Policies API — the rules a business user owns.

    GET    /api/ai/policies                       list
    GET    /api/ai/policies/{policy_key}          one
    PUT    /api/ai/policies/{policy_key}          edit thresholds (bumps version)
    GET    /api/ai/policies/{policy_key}/versions history
    POST   /api/ai/policies/evaluate              evaluate + persist a verdict
    GET    /api/ai/policy-evaluations             audit trail

The path prefix reuses /api/ai/policies, which app/authz.map.json already
declares for approved users.

Editing a policy bumps active_version and writes a policy_versions row. The
next evaluation records the new version, which is how a judge sees the change
take effect on the following run.
"""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.service_desk import (
    PolicyDefinition,
    PolicyEvaluation,
    PolicyVersion,
)
from ..schemas.service_desk import (
    EvaluateRequest,
    EvaluateResponse,
    PolicyEvaluationOut,
    PolicyOut,
    PolicyUpdate,
    PolicyVersionOut,
)
from ..services import policy_engine

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/policies", tags=["AI Policies"])
eval_router = APIRouter(prefix="/ai/policy-evaluations", tags=["AI Policies"])


@router.get("", response_model=List[PolicyOut])
def list_policies(db: Session = Depends(get_db)):
    """All policies, with their current editable configuration."""
    return db.query(PolicyDefinition).order_by(PolicyDefinition.policy_key).all()


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate_policy(payload: EvaluateRequest, db: Session = Depends(get_db)):
    """
    Evaluate a policy against a case and persist the verdict.

    This is the gate. Auto calls it BEFORE performing an external action.
    Only ALLOW — or a later explicit human approval — permits execution.

    Set ``simulate: true`` to evaluate with no gating effect; the row is still
    written and flagged, so the Policy Lab can compare configurations safely.
    """
    result = policy_engine.evaluate(
        db,
        payload.policy_key,
        payload.context,
        run_id=payload.run_id,
        issue_key=payload.issue_key,
        proposed_action=payload.proposed_action,
        simulate=payload.simulate,
    )
    return EvaluateResponse(
        verdict=result.verdict,
        policy_key=result.policy_key,
        policy_version=result.policy_version,
        reasons=result.reasons,
        missing_fields=result.missing_fields,
        is_simulation=payload.simulate,
    )


@router.get("/{policy_key}", response_model=PolicyOut)
def get_policy(policy_key: str, db: Session = Depends(get_db)):
    policy = (
        db.query(PolicyDefinition)
        .filter(PolicyDefinition.policy_key == policy_key)
        .first()
    )
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Policy '{policy_key}' not found")
    return policy


@router.put("/{policy_key}", response_model=PolicyOut)
def update_policy(
    policy_key: str, payload: PolicyUpdate, db: Session = Depends(get_db)
):
    """
    Change a policy's thresholds with no code.

    Merges the supplied keys into the existing configuration, bumps
    active_version, and appends an immutable policy_versions row.
    """
    policy = (
        db.query(PolicyDefinition)
        .filter(PolicyDefinition.policy_key == policy_key)
        .first()
    )
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Policy '{policy_key}' not found")

    current = dict(policy.configuration or {})
    unknown = [k for k in payload.configuration if k not in current] if current else []
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown configuration key(s): {', '.join(unknown)}. "
                f"Valid keys: {', '.join(sorted(current))}"
            ),
        )

    current.update(payload.configuration)
    new_version = int(policy.active_version) + 1

    policy.configuration = current
    policy.active_version = new_version
    policy.updated_by = payload.updated_by or "command_center"
    if payload.is_active is not None:
        policy.is_active = payload.is_active

    db.add(
        PolicyVersion(
            policy_key=policy_key,
            version=new_version,
            configuration=current,
            change_note=payload.change_note,
            created_by=payload.updated_by or "command_center",
        )
    )
    db.commit()
    db.refresh(policy)

    log.info("Policy %s updated to v%s by %s", policy_key, new_version, policy.updated_by)
    return policy


@router.get("/{policy_key}/versions", response_model=List[PolicyVersionOut])
def list_policy_versions(policy_key: str, db: Session = Depends(get_db)):
    return (
        db.query(PolicyVersion)
        .filter(PolicyVersion.policy_key == policy_key)
        .order_by(PolicyVersion.version.desc())
        .all()
    )


@eval_router.get("", response_model=List[PolicyEvaluationOut])
def list_evaluations(
    run_id: Optional[UUID] = None,
    issue_key: Optional[str] = None,
    policy_key: Optional[str] = None,
    include_simulations: bool = Query(True),
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
):
    """Audit trail. Every verdict the engine has ever produced."""
    q = db.query(PolicyEvaluation)
    if run_id:
        q = q.filter(PolicyEvaluation.run_id == run_id)
    if issue_key:
        q = q.filter(PolicyEvaluation.issue_key == issue_key)
    if policy_key:
        q = q.filter(PolicyEvaluation.policy_key == policy_key)
    if not include_simulations:
        q = q.filter(PolicyEvaluation.is_simulation.is_(False))
    return q.order_by(PolicyEvaluation.evaluated_at.desc()).limit(limit).all()
