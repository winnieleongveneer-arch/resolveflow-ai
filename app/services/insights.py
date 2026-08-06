# app/services/insights.py
"""
AI Insights — computed from what the agent actually did.

Guide 7.4 and 11.2: insights must come from real processed data, be
non-trivial, carry severity, and end in an action a person can take. Static
seeded insights score nothing.

So every insight here is derived from rows in policy_evaluations,
workbench_items, workflow_runs and operator_events. If the agent has not run,
the list is empty — which is the honest answer, not a reason to invent one.

Each insight carries:
    title, type, severity, evidence, affected_cases, detected_at,
    business_implication, recommended_action, action_label, action_href,
    status, owner
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.service_desk import (
    OperatorEvent,
    PolicyEvaluation,
    RunStatus,
    Verdict,
    WorkbenchItem,
    WorkbenchStatus,
    WorkflowRun,
)

log = logging.getLogger(__name__)

SEV_CRITICAL = "critical"
SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"


def _insight(
    *,
    key: str,
    title: str,
    type_: str,
    severity: str,
    evidence: List[str],
    affected: List[str],
    implication: str,
    recommendation: str,
    action_label: str,
    action_href: str,
) -> Dict[str, Any]:
    return {
        "id": key,
        "title": title,
        "type": type_,
        "severity": severity,
        "evidence": evidence,
        "affected_cases": affected,
        "affected_count": len(affected),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "business_implication": implication,
        "recommended_action": recommendation,
        "action_label": action_label,
        "action_href": action_href,
        "status": "open",
        "owner": "Service Desk Lead",
        "is_demo": False,
    }


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _major_incident_forming(db: Session) -> List[Dict[str, Any]]:
    """A major-incident policy evaluation that ALLOWed, or nearly did."""
    rows = (
        db.query(PolicyEvaluation)
        .filter(
            PolicyEvaluation.policy_key == "major_incident_declaration",
            PolicyEvaluation.is_simulation.is_(False),
        )
        .order_by(PolicyEvaluation.evaluated_at.desc())
        .limit(50)
        .all()
    )
    out = []
    for r in rows:
        ctx = r.input_context or {}
        count = ctx.get("correlated_ticket_count")
        if r.verdict == Verdict.ALLOW:
            out.append(_insight(
                key=f"major-incident-{r.id}",
                title=f"Major incident confirmed around {ctx.get('shared_system', 'a shared system')}",
                type_="major_incident",
                severity=SEV_CRITICAL,
                evidence=list(r.reasons or []),
                affected=[r.issue_key] if r.issue_key else [],
                implication=(
                    f"{count} tickets share one root cause. Treating them "
                    "individually multiplies handling effort and delays the fix "
                    "everyone is waiting for."
                ),
                recommendation=(
                    "Declare the parent incident, link the child tickets, and "
                    "send one status update rather than replying to each ticket."
                ),
                action_label="Open the run",
                action_href=f"/workbench?run={r.run_id}",
            ))
        elif r.verdict == Verdict.REQUIRE_HUMAN_REVIEW and isinstance(count, (int, float)) and count >= 3:
            out.append(_insight(
                key=f"major-incident-forming-{r.id}",
                title="A ticket cluster is forming but has not met the declaration threshold",
                type_="major_incident_forming",
                severity=SEV_HIGH,
                evidence=list(r.reasons or []),
                affected=[r.issue_key] if r.issue_key else [],
                implication=(
                    "Volume is building on one system. If it is a genuine "
                    "outage, every minute before declaration is duplicated "
                    "triage work."
                ),
                recommendation=(
                    "Confirm or dismiss in the Workbench. If this pattern keeps "
                    "recurring, lower minimum_correlated_ticket_count."
                ),
                action_label="Review in Workbench",
                action_href="/workbench",
            ))
    return out[:3]


def _blocked_by_policy(db: Session) -> List[Dict[str, Any]]:
    """Which policy is stopping the most automation?"""
    rows = (
        db.query(
            PolicyEvaluation.policy_key,
            PolicyEvaluation.verdict,
            func.count(PolicyEvaluation.id),
        )
        .filter(PolicyEvaluation.is_simulation.is_(False))
        .group_by(PolicyEvaluation.policy_key, PolicyEvaluation.verdict)
        .all()
    )
    if not rows:
        return []

    by_policy: Dict[str, Counter] = defaultdict(Counter)
    for key, verdict, count in rows:
        by_policy[key][verdict] += count

    out = []
    for key, counts in by_policy.items():
        total = sum(counts.values())
        blocked = counts[Verdict.REQUIRE_HUMAN_REVIEW] + counts[Verdict.DENY]
        if total >= 3 and blocked / total >= 0.6:
            pct = round(blocked / total * 100)
            out.append(_insight(
                key=f"policy-friction-{key}",
                title=f"'{key}' is blocking {pct}% of the actions it governs",
                type_="automation_opportunity",
                severity=SEV_MEDIUM,
                evidence=[
                    f"{total} evaluations recorded.",
                    f"{counts[Verdict.ALLOW]} allowed, "
                    f"{counts[Verdict.REQUIRE_HUMAN_REVIEW]} sent to a human, "
                    f"{counts[Verdict.DENY]} denied.",
                ],
                affected=[],
                implication=(
                    "Most work governed by this policy still reaches a person. "
                    "If those reviews are routinely approved unchanged, the "
                    "threshold is stricter than the risk warrants."
                ),
                recommendation=(
                    "Compare approvals against modifications in the Workbench. "
                    "If reviewers rarely change anything, relax the threshold "
                    "and re-run."
                ),
                action_label="Open AI Policies",
                action_href="/ai/policies",
            ))
    return out


def _repeated_human_overrides(db: Session) -> List[Dict[str, Any]]:
    """Reviewers changing the agent's proposal in the same way = a policy gap."""
    modified = (
        db.query(WorkbenchItem)
        .filter(WorkbenchItem.status == WorkbenchStatus.MODIFIED)
        .order_by(WorkbenchItem.decided_at.desc())
        .limit(50)
        .all()
    )
    if len(modified) < 2:
        return []

    by_type = defaultdict(list)
    for item in modified:
        by_type[item.request_type].append(item)

    out = []
    for request_type, items in by_type.items():
        if len(items) < 2:
            continue
        out.append(_insight(
            key=f"override-pattern-{request_type}",
            title=f"Reviewers keep amending '{request_type}' proposals",
            type_="learning_candidate",
            severity=SEV_HIGH,
            evidence=[
                f"{len(items)} proposals of this type were modified rather than approved as-is.",
                "Reviewers: " + ", ".join(sorted({i.reviewer for i in items if i.reviewer})),
            ],
            affected=[i.issue_key for i in items],
            implication=(
                "A repeated correction means the agent's default proposal is "
                "systematically wrong for this case type. Every occurrence costs "
                "reviewer time that a rule change would remove."
            ),
            recommendation=(
                "Review the amended scopes side by side and encode the common "
                "correction as a policy change or a KB update. Requires human "
                "approval before it affects future runs."
            ),
            action_label="Review the overrides",
            action_href="/workbench?status=MODIFIED",
        ))
    return out


def _stalled_reviews(db: Session) -> List[Dict[str, Any]]:
    """Exceptions sitting in the queue with nobody deciding."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    stale = (
        db.query(WorkbenchItem)
        .filter(
            WorkbenchItem.status == WorkbenchStatus.PENDING,
            WorkbenchItem.created_at < cutoff,
        )
        .order_by(WorkbenchItem.created_at.asc())
        .all()
    )
    if not stale:
        return []
    oldest = stale[0]
    age_h = round(
        (datetime.now(timezone.utc) - oldest.created_at).total_seconds() / 3600, 1
    )
    return [_insight(
        key="stalled-reviews",
        title=f"{len(stale)} exception(s) waiting more than 4 hours for a decision",
        type_="sla_risk",
        severity=SEV_HIGH if len(stale) > 2 else SEV_MEDIUM,
        evidence=[
            f"Oldest item has been pending {age_h} hours ({oldest.issue_key}).",
            "No timeout auto-approves these; they wait indefinitely by design.",
        ],
        affected=[i.issue_key for i in stale],
        implication=(
            "The agent stopped correctly, but the handover stalled. SLA clocks "
            "keep running while an item sits undecided."
        ),
        recommendation=(
            "Clear the queue, then check whether the escalation reached the "
            "right on-call person in Slack."
        ),
        action_label="Open Workbench",
        action_href="/workbench?status=PENDING",
    )]


def _missing_evidence_pattern(db: Session) -> List[Dict[str, Any]]:
    """Which fields keep being absent? That's a data-quality problem upstream."""
    rows = (
        db.query(PolicyEvaluation)
        .filter(
            PolicyEvaluation.verdict == Verdict.REQUIRE_HUMAN_REVIEW,
            PolicyEvaluation.is_simulation.is_(False),
        )
        .order_by(PolicyEvaluation.evaluated_at.desc())
        .limit(100)
        .all()
    )
    fields = Counter()
    cases = defaultdict(set)
    for r in rows:
        for reason in (r.reasons or []):
            if reason.startswith("Required evidence is missing:"):
                for f in reason.split(":", 1)[1].strip(" .").split(","):
                    name = f.strip()
                    if name:
                        fields[name] += 1
                        if r.issue_key:
                            cases[name].add(r.issue_key)
    if not fields:
        return []
    field, count = fields.most_common(1)[0]
    if count < 2:
        return []
    return [_insight(
        key=f"missing-field-{field}",
        title=f"'{field}' is missing on {count} cases the agent had to pause on",
        type_="knowledge_gap",
        severity=SEV_MEDIUM,
        evidence=[
            f"{count} evaluations paused because {field} was absent.",
            "The agent escalated rather than assuming a value.",
        ],
        affected=sorted(cases[field]),
        implication=(
            "A field the automation depends on is not being captured at intake. "
            "Every missing value converts an automatable ticket into manual work."
        ),
        recommendation=(
            f"Make {field} required at intake, or add a derivation rule so the "
            "agent can compute it from evidence it already has."
        ),
        action_label="See the evaluations",
        action_href="/ai/policies",
    )]


def _automation_rate(db: Session) -> List[Dict[str, Any]]:
    """How much is actually being handled without a human?"""
    total = db.query(func.count(WorkflowRun.id)).scalar() or 0
    if total < 5:
        return []
    needed_human = (
        db.query(func.count(func.distinct(WorkbenchItem.run_id))).scalar() or 0
    )
    autonomous = max(total - needed_human, 0)
    rate = round(autonomous / total * 100)
    severity = SEV_HIGH if rate < 25 else SEV_LOW
    return [_insight(
        key="automation-rate",
        title=f"{rate}% of cases completed without a human decision",
        type_="automation_opportunity",
        severity=severity,
        evidence=[
            f"{total} runs processed.",
            f"{needed_human} required a Workbench decision.",
            f"{autonomous} completed under policy alone.",
        ],
        affected=[],
        implication=(
            "This is the headline autonomy number. Every point of improvement "
            "is service desk capacity returned to the team."
        ),
        recommendation=(
            "Look at which policy sends the most work to humans and whether "
            "those reviews are routinely approved unchanged."
        ),
        action_label="Open AI Policies",
        action_href="/ai/policies",
    )]


def _failed_runs(db: Session) -> List[Dict[str, Any]]:
    failed = (
        db.query(WorkflowRun)
        .filter(WorkflowRun.status == RunStatus.FAILED)
        .order_by(WorkflowRun.started_at.desc())
        .limit(20)
        .all()
    )
    if len(failed) < 2:
        return []
    messages = Counter(
        (r.error_message or "unknown")[:120] for r in failed
    )
    top, count = messages.most_common(1)[0]
    return [_insight(
        key="failed-runs",
        title=f"{len(failed)} runs failed, {count} with the same error",
        type_="reliability",
        severity=SEV_CRITICAL if count >= 3 else SEV_HIGH,
        evidence=[f"Most common error: {top}"],
        affected=[r.issue_key for r in failed],
        implication=(
            "Failed runs are tickets the agent silently did not handle. They do "
            "not appear in the backlog as blocked, so they are easy to miss."
        ),
        recommendation=(
            "Fix the underlying integration error, then re-run the affected "
            "cases and confirm they complete."
        ),
        action_label="Open Data Manager",
        action_href="/data-manager",
    )]


DETECTORS = [
    _major_incident_forming,
    _failed_runs,
    _stalled_reviews,
    _repeated_human_overrides,
    _missing_evidence_pattern,
    _blocked_by_policy,
    _automation_rate,
]

SEVERITY_ORDER = {SEV_CRITICAL: 0, SEV_HIGH: 1, SEV_MEDIUM: 2, SEV_LOW: 3}


def generate(db: Session) -> List[Dict[str, Any]]:
    """Run every detector over real rows and return insights, worst first."""
    out: List[Dict[str, Any]] = []
    for detector in DETECTORS:
        try:
            out.extend(detector(db))
        except Exception:
            log.exception("Insight detector %s failed", detector.__name__)
    out.sort(key=lambda i: SEVERITY_ORDER.get(i["severity"], 9))
    return out
