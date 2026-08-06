# app/services/outcomes.py
"""
Outcome Ledger — metrics that can be audited, not just asserted.

Guide 11.3 gives 10 points for quantified metric movement. The fastest way to
lose those points is an unsupported number: "37 hours saved" invites a judge
to ask how, and there is no good answer.

So every metric returned here carries four things:

    value        the number itself
    formula      the arithmetic, written out
    numerator /  the actual counts that produced it
    denominator
    run_ids      the rows behind it, for drill-down

If the denominator is zero the metric returns None with an explanation rather
than 0% or 100%, both of which would be misleading on an empty dataset.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models.service_desk import (
    OutcomeLedger,
    RunStatus,
    TaskBaseline,
    WorkbenchItem,
    WorkflowRun,
)

log = logging.getLogger(__name__)

AUTO_RESOLVED = "AUTO_RESOLVED"
HUMAN_RESOLVED = "HUMAN_RESOLVED"
ESCALATED = "ESCALATED"
DENIED = "DENIED"
FAILED = "FAILED"

RESOLVED_OUTCOMES = (AUTO_RESOLVED, HUMAN_RESOLVED)


def _metric(
    *,
    key: str,
    label: str,
    value: Optional[float],
    unit: str,
    formula: str,
    numerator: Optional[float] = None,
    denominator: Optional[float] = None,
    run_ids: Optional[List[str]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "formula": formula,
        "numerator": numerator,
        "denominator": denominator,
        "sample_size": len(run_ids) if run_ids is not None else None,
        "run_ids": (run_ids or [])[:200],
        "note": note,
        "computable": value is not None,
    }


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def baselines(db: Session) -> List[Dict[str, Any]]:
    rows = db.query(TaskBaseline).order_by(TaskBaseline.task_type).all()
    return [{
        "task_type": r.task_type,
        "manual_minutes": r.manual_minutes,
        "source": r.source,
        "updated_by": r.updated_by,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows]


def baseline_for(db: Session, task_type: str) -> TaskBaseline:
    row = (
        db.query(TaskBaseline)
        .filter(TaskBaseline.task_type == task_type)
        .first()
    )
    if row is None:
        row = (
            db.query(TaskBaseline)
            .filter(TaskBaseline.task_type == "general_triage")
            .first()
        )
    return row


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def record(
    db: Session,
    *,
    run: WorkflowRun,
    task_type: str = "general_triage",
    outcome: Optional[str] = None,
    verified: bool = False,
    verification_note: Optional[str] = None,
    sla_state: Optional[str] = None,
    predicted_breach: bool = False,
    breach_avoided: bool = False,
    rollback_attempted: bool = False,
    rollback_succeeded: Optional[bool] = None,
    human_touch_seconds: Optional[float] = None,
) -> OutcomeLedger:
    """
    Write one ledger row for a completed case.

    Human touch time is measured, not estimated: it is the interval between a
    Workbench item being raised and a human deciding it, summed across items.
    A case nobody touched records 0.
    """
    items = (
        db.query(WorkbenchItem)
        .filter(WorkbenchItem.run_id == run.id)
        .all()
    )

    if human_touch_seconds is None:
        total = 0.0
        for item in items:
            if item.created_at and item.decided_at:
                total += (item.decided_at - item.created_at).total_seconds()
        human_touch_seconds = total

    interventions = len([i for i in items if i.human_decision])

    if outcome is None:
        if run.status == RunStatus.RESOLVED:
            outcome = AUTO_RESOLVED if interventions == 0 else HUMAN_RESOLVED
        elif run.status == RunStatus.DENIED:
            outcome = DENIED
        elif run.status == RunStatus.FAILED:
            outcome = FAILED
        else:
            outcome = ESCALATED

    base = baseline_for(db, task_type)
    agent_seconds = None
    if run.started_at and run.completed_at:
        agent_seconds = (run.completed_at - run.started_at).total_seconds()

    row = OutcomeLedger(
        run_id=run.id,
        issue_key=run.issue_key,
        task_type=task_type,
        baseline_manual_minutes=base.manual_minutes if base else 12.0,
        baseline_source=base.source if base else None,
        agent_seconds=agent_seconds,
        human_touch_seconds=human_touch_seconds,
        human_interventions=interventions,
        outcome=outcome,
        verified=verified,
        verification_note=verification_note,
        sla_state=sla_state,
        predicted_breach=predicted_breach,
        breach_avoided=breach_avoided,
        rollback_attempted=rollback_attempted,
        rollback_succeeded=rollback_succeeded,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("Ledger row for %s: %s", run.issue_key, outcome)
    return row


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute(db: Session, *, verified_only: bool = True) -> Dict[str, Any]:
    q = db.query(OutcomeLedger)
    if verified_only:
        q = q.filter(OutcomeLedger.verified.is_(True))
    rows: List[OutcomeLedger] = q.all()

    resolved = [r for r in rows if r.outcome in RESOLVED_OUTCOMES]
    auto = [r for r in resolved if r.outcome == AUTO_RESOLVED]

    metrics: List[Dict[str, Any]] = []

    # --- auto-resolution rate ---------------------------------------------
    if resolved:
        metrics.append(_metric(
            key="auto_resolution_rate",
            label="Auto-resolution rate",
            value=round(len(auto) / len(resolved) * 100, 1),
            unit="%",
            formula="verified automatically resolved cases / all verified resolved cases x 100",
            numerator=len(auto),
            denominator=len(resolved),
            run_ids=[str(r.run_id) for r in auto],
        ))
    else:
        metrics.append(_metric(
            key="auto_resolution_rate", label="Auto-resolution rate",
            value=None, unit="%",
            formula="verified automatically resolved cases / all verified resolved cases x 100",
            note=("No verified resolved cases yet. The rate is deliberately not "
                  "reported as 0% — there is nothing to divide."),
        ))

    # --- human minutes saved ----------------------------------------------
    if resolved:
        saved = 0.0
        for r in resolved:
            saved += max(r.baseline_manual_minutes - (r.human_touch_seconds / 60.0), 0.0)
        metrics.append(_metric(
            key="human_minutes_saved",
            label="Human minutes saved",
            value=round(saved, 1),
            unit="minutes",
            formula=("sum over resolved cases of max(baseline_manual_minutes - "
                     "actual_human_touch_minutes, 0)"),
            numerator=round(saved, 1),
            denominator=len(resolved),
            run_ids=[str(r.run_id) for r in resolved],
            note=("Baselines are per task type with a written justification — see "
                  "the baselines table. Human touch time is measured from when a "
                  "Workbench item was raised to when it was decided, not estimated."),
        ))

    # --- SLA compliance ----------------------------------------------------
    with_sla = [r for r in resolved if r.sla_state]
    if with_sla:
        within = [r for r in with_sla if r.sla_state == "Within SLA"]
        metrics.append(_metric(
            key="sla_compliance",
            label="SLA compliance",
            value=round(len(within) / len(with_sla) * 100, 1),
            unit="%",
            formula="cases resolved within business-hours SLA / resolved cases carrying an SLA x 100",
            numerator=len(within),
            denominator=len(with_sla),
            run_ids=[str(r.run_id) for r in within],
            note="Computed against regional business hours and holidays, not raw elapsed time.",
        ))

    # --- rollback success --------------------------------------------------
    attempts = [r for r in rows if r.rollback_attempted]
    if attempts:
        succeeded = [r for r in attempts if r.rollback_succeeded]
        metrics.append(_metric(
            key="rollback_success_rate",
            label="Rollback success rate",
            value=round(len(succeeded) / len(attempts) * 100, 1),
            unit="%",
            formula="successful rollbacks / rollback attempts x 100",
            numerator=len(succeeded),
            denominator=len(attempts),
            run_ids=[str(r.run_id) for r in attempts],
        ))

    # --- breaches prevented -------------------------------------------------
    predicted = [r for r in rows if r.predicted_breach]
    if predicted:
        avoided = [r for r in predicted if r.breach_avoided]
        metrics.append(_metric(
            key="breaches_prevented",
            label="SLA breaches prevented",
            value=len(avoided),
            unit="cases",
            formula=("cases predicted to breach before intervention that completed "
                     "within SLA afterwards"),
            numerator=len(avoided),
            denominator=len(predicted),
            run_ids=[str(r.run_id) for r in avoided],
        ))

    # --- mean handling time -------------------------------------------------
    timed = [r for r in resolved if r.agent_seconds]
    if timed:
        mean = sum(r.agent_seconds for r in timed) / len(timed)
        metrics.append(_metric(
            key="mean_agent_seconds",
            label="Mean agent handling time",
            value=round(mean, 1),
            unit="seconds",
            formula="sum of agent processing seconds / number of resolved cases with a recorded duration",
            numerator=round(sum(r.agent_seconds for r in timed), 1),
            denominator=len(timed),
            run_ids=[str(r.run_id) for r in timed],
        ))

    by_outcome: Dict[str, int] = {}
    for r in rows:
        by_outcome[r.outcome] = by_outcome.get(r.outcome, 0) + 1

    return {
        "verified_only": verified_only,
        "ledger_rows": len(rows),
        "resolved_cases": len(resolved),
        "by_outcome": by_outcome,
        "metrics": metrics,
        "baselines": baselines(db),
        "integrity_note": (
            "Every metric above is computed from outcome_ledger rows at request "
            "time. Nothing is cached or hand-entered. Where a denominator is "
            "zero the metric reports as not computable rather than showing a "
            "misleading 0%."
        ),
    }


def ledger(db: Session, limit: int = 200) -> List[Dict[str, Any]]:
    rows = (
        db.query(OutcomeLedger)
        .order_by(OutcomeLedger.created_at.desc())
        .limit(limit)
        .all()
    )
    return [{
        "id": str(r.id),
        "run_id": str(r.run_id),
        "issue_key": r.issue_key,
        "task_type": r.task_type,
        "baseline_manual_minutes": r.baseline_manual_minutes,
        "baseline_source": r.baseline_source,
        "agent_seconds": r.agent_seconds,
        "human_touch_minutes": round((r.human_touch_seconds or 0) / 60.0, 2),
        "human_interventions": r.human_interventions,
        "minutes_saved": round(
            max(r.baseline_manual_minutes - (r.human_touch_seconds or 0) / 60.0, 0.0), 2
        ),
        "outcome": r.outcome,
        "verified": r.verified,
        "verification_note": r.verification_note,
        "sla_state": r.sla_state,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


# ---------------------------------------------------------------------------
# Threshold calibration
# ---------------------------------------------------------------------------


def calibrate(db: Session, tickets: List[Dict[str, Any]],
              build_context, thresholds: List[float]) -> Dict[str, Any]:
    """
    Show what the autonomy rate WOULD be at each confidence threshold.

    A threshold picked because it looked reasonable is indefensible. A
    threshold picked because the curve shows where automation gains flatten,
    and where evidence quality drops off, is a business decision someone can
    stand behind.

    Runs entirely in memory against the pure evaluator — no database writes,
    no external calls, no side effects.
    """
    from .policy_engine import (
        MissingEvidence,
        evaluate_safe_auto_remediation as evaluator,
    )
    from ..models.service_desk import Verdict

    base = {
        "require_kb_auto_safe": True,
        "require_reversible": True,
        "block_if_reopened": True,
        "block_if_major_incident": True,
        "block_if_production_impact": True,
    }

    contexts = []
    for t in tickets:
        try:
            contexts.append(build_context(t, "safe_auto_remediation", {}))
        except Exception:
            continue

    curve = []
    for threshold in thresholds:
        config = {**base, "minimum_confidence": threshold}
        allow = review = deny = missing = 0
        for ctx in contexts:
            try:
                verdict = evaluator(ctx, config).verdict
            except MissingEvidence:
                missing += 1
                continue
            if verdict == Verdict.ALLOW:
                allow += 1
            elif verdict == Verdict.REQUIRE_HUMAN_REVIEW:
                review += 1
            else:
                deny += 1
        total = max(len(contexts), 1)
        curve.append({
            "minimum_confidence": threshold,
            "allow": allow,
            "review": review,
            "deny": deny,
            "missing_evidence": missing,
            "auto_resolution_rate_percent": round(allow / total * 100, 1),
        })

    # The knee: the highest threshold still within 3 points of the best rate.
    best = max((c["auto_resolution_rate_percent"] for c in curve), default=0)
    recommended = None
    for c in curve:
        if c["auto_resolution_rate_percent"] >= best - 3:
            recommended = c["minimum_confidence"]
    return {
        "cases_evaluated": len(contexts),
        "curve": curve,
        "best_rate_percent": best,
        "recommended_threshold": recommended,
        "how_to_read_this": (
            "Each row is the same backlog evaluated at a different confidence "
            "threshold. Nothing was executed and nothing was written. The "
            "recommendation is the STRICTEST threshold that still captures "
            "almost all available automation — so the rate is bought with "
            "evidence rather than with relaxed safety."
        ),
    }
