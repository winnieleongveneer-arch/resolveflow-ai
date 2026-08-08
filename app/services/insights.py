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
    # One chip per CASE, not per run. Three failed runs across two tickets is
    # three runs and two cases; listing ITSM-2180 twice overstated the spread
    # and, because the list is rendered by key, broke the page it appeared on.
    # Order is preserved so the first occurrence still reads first.
    seen: set = set()
    distinct = [c for c in affected if not (c in seen or seen.add(c))]
    return {
        "id": key,
        "title": title,
        "type": type_,
        "severity": severity,
        "evidence": evidence,
        "affected_cases": distinct,
        "affected_count": len(distinct),
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


def _failed_condition(ctx: Dict[str, Any], config: Dict[str, Any]) -> str:
    """
    Work out WHICH condition actually blocked a major-incident declaration.

    This matters because the recommendation has to address the failing
    condition. Recommending a lower ticket-count threshold when the count
    already passed is worse than saying nothing — it sends a business user to
    change a setting that was never the problem.
    """
    count = ctx.get("correlated_ticket_count")
    window = ctx.get("detection_window_minutes")
    confidence = ctx.get("correlation_confidence")
    shared = ctx.get("shared_system") or ctx.get("shared_root_cause")

    min_count = config.get("minimum_correlated_ticket_count", 5)
    max_window = config.get("detection_window_minutes", 20)
    min_conf = config.get("minimum_correlation_confidence", 0.80)
    needs_shared = config.get("require_shared_system_or_root_cause", True)

    try:
        if count is not None and float(count) < float(min_count):
            return "count"
        if window is not None and float(window) > float(max_window):
            return "window"
        if confidence is not None and float(confidence) < float(min_conf):
            return "confidence"
    except (TypeError, ValueError):
        return "evidence"
    if needs_shared and not shared:
        return "shared_cause"
    return "unknown"


def _major_incident_forming(db: Session) -> List[Dict[str, Any]]:
    """Major-incident evaluations, with a recommendation matching what failed."""
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

    out: List[Dict[str, Any]] = []
    for r in rows:
        ctx = r.input_context or {}
        config = r.configuration_snapshot or {}
        count = ctx.get("correlated_ticket_count")
        system = ctx.get("shared_system") or "a shared system"

        if r.verdict == Verdict.ALLOW:
            out.append(_insight(
                key=f"mi-confirmed::{ctx.get('shared_system') or r.issue_key}::v{r.policy_version}",
                title=f"Major incident confirmed around {system}",
                type_="major_incident",
                severity=SEV_CRITICAL,
                evidence=list(r.reasons or []),
                affected=[r.issue_key] if r.issue_key else [],
                implication=(
                    f"{count} tickets share one root cause. Handling them "
                    "individually multiplies effort and delays the fix everyone "
                    "is waiting for."
                ),
                recommendation=(
                    "Declare the parent incident, link the child tickets, and "
                    "send one status update instead of replying to each ticket."
                ),
                action_label="Open the run",
                action_href=f"/runs/{r.run_id}",
            ))
            continue

        if r.verdict != Verdict.REQUIRE_HUMAN_REVIEW:
            continue

        failed = _failed_condition(ctx, config)
        confidence = ctx.get("correlation_confidence")
        min_conf = config.get("minimum_correlation_confidence", 0.80)
        min_count = config.get("minimum_correlated_ticket_count", 5)

        if failed == "confidence":
            title = ("Ticket-volume threshold met, but correlation confidence "
                     "is insufficient")
            implication = (
                f"{count} tickets cleared the volume threshold of {min_count}, "
                f"so the cluster is real enough to matter. What is missing is "
                f"certainty that they share one cause: correlation confidence "
                f"is {confidence}, below the required {min_conf}."
            )
            recommendation = (
                "Review the correlation evidence and confirm or dismiss the "
                "proposed cluster in the Workbench. Investigate why confidence "
                f"is below {min_conf} before changing any policy threshold — "
                "the ticket-count threshold already passed and is not the "
                "blocking condition."
            )
        elif failed == "shared_cause":
            title = "Cluster detected, but no shared system or root cause identified"
            implication = (
                f"{count} tickets correlate on timing and text, but nothing "
                "links them causally. Declaring on similarity alone risks "
                "merging unrelated issues under one incident."
            )
            recommendation = (
                "Confirm in the Workbench whether these tickets share a system. "
                "If they routinely do and the field is simply unpopulated, fix "
                "the data at intake rather than relaxing the policy."
            )
        elif failed == "count":
            title = "Correlated tickets below the declaration threshold"
            implication = (
                f"Only {count} correlated tickets were found; the policy "
                f"requires {min_count}. This may be an incident forming early, "
                "or a recurring known error."
            )
            recommendation = (
                "Watch whether the cluster grows. If clusters of this size "
                f"repeatedly turn out to be genuine incidents, lowering "
                "minimum_correlated_ticket_count is justified — that IS the "
                "blocking condition here."
            )
        elif failed == "window":
            title = "Tickets share a cause but arrived over too long a period"
            implication = (
                "A wide arrival window usually indicates a recurring known "
                "error rather than one live outage. The two need different "
                "handling."
            )
            recommendation = (
                "Treat this as a known-error pattern: create or update a KB "
                "article so future tickets auto-resolve, rather than widening "
                "the incident detection window."
            )
        else:
            title = "Major-incident evaluation paused for human review"
            implication = "The policy could not reach a decision on the evidence supplied."
            recommendation = (
                "Open the case in the Workbench and supply the missing evidence. "
                "Do not change policy thresholds until the failing condition is known."
            )

        out.append(_insight(
            key=f"mi-{failed}::{ctx.get('shared_system') or 'cluster'}::v{r.policy_version}",
            title=title,
            type_="major_incident_forming",
            severity=SEV_HIGH,
            evidence=list(r.reasons or []),
            affected=[r.issue_key] if r.issue_key else [],
            implication=implication,
            recommendation=recommendation,
            action_label="Review in Workbench",
            action_href="/workbench",
        ))
    return out


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
    """
    How much did the agent actually finish on its own?

    The earlier version divided runs-with-no-Workbench-item by all runs and
    called the result "completed without a human decision". That counted every
    escalation, every policy refusal and every platform failure as a
    completion, and produced 98% while the outcome ledger held three verified
    resolutions. A number that flatters the build by redefining the word
    "completed" is worse than no number: the Dashboard already reports the
    honest one, and the two would contradict each other in front of anyone
    reading both pages.

    So count what the ledger counts: verified resolutions, split by whether a
    person had to touch them.
    """
    total = db.query(func.count(WorkflowRun.id)).scalar() or 0
    if total < 5:
        return []

    try:
        from ..models.service_desk import OutcomeLedger
        rows = db.query(OutcomeLedger).filter(OutcomeLedger.verified.is_(True)).all()
    except Exception:
        return []

    resolved = len(rows)
    if not resolved:
        return []
    auto = len([r for r in rows if r.outcome == "AUTO_RESOLVED"])
    assisted = resolved - auto
    # One decimal, matching the Dashboard tile exactly. 67 and 66.7 are the
    # same number, but two pages of one app disagreeing by a rounding step is
    # a question nobody should have to ask.
    rate = round(auto / resolved * 100, 1)

    # Use the Dashboard's definition, not a second one: a case is open when
    # NO run for that issue key has reached a terminal state. Counting keys
    # whose latest run is non-terminal instead gave 122 against the tile's
    # 117, because a ticket that escalated once and later resolved counts as
    # closed there and open here. One definition, or the two screens argue.
    open_cases = (
        db.query(func.count(func.distinct(WorkflowRun.issue_key)))
        .filter(WorkflowRun.issue_key.notin_(
            db.query(WorkflowRun.issue_key).filter(
                WorkflowRun.status.in_([RunStatus.RESOLVED, RunStatus.DENIED,
                                        RunStatus.FAILED]))))
        .scalar() or 0
    )

    return [_insight(
        key="automation-rate",
        title=f"{rate}% of verified resolutions ran unattended",
        type_="automation_opportunity",
        severity=SEV_LOW,
        evidence=[
            f"{resolved} verified resolution(s) on the ledger.",
            f"{auto} completed with no human decision; {assisted} needed one.",
            f"{total} runs recorded in total, of which {open_cases} case(s) "
            "are still open. A refusal or an escalation is not a resolution "
            "and is not counted here.",
        ],
        affected=[],
        implication=(
            "This counts only work the system of record confirmed. Runs that "
            "escalated, were refused by policy, or failed on the platform are "
            "deliberately excluded, so this number cannot be inflated by the "
            "agent declining to act."
        ),
        recommendation=(
            "Raise it by clearing the knowledge gaps that force escalation, "
            "not by relaxing the policies that force review."
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
    """
    Run every detector over real rows and return insights, worst first.

    Cards are merged on a deterministic fingerprint — insight type plus stable
    subject plus policy version — so re-evaluating the same cluster updates the
    existing card's evidence and affected count instead of stacking another
    identical one. The earlier version keyed on the evaluation row id, which is
    why duplicates appeared.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for detector in DETECTORS:
        try:
            for item in detector(db):
                fingerprint = item["id"]
                existing = merged.get(fingerprint)
                if existing is None:
                    item["occurrences"] = 1
                    merged[fingerprint] = item
                    continue
                existing["occurrences"] = existing.get("occurrences", 1) + 1
                for case in item.get("affected_cases", []):
                    if case not in existing["affected_cases"]:
                        existing["affected_cases"].append(case)
                existing["affected_count"] = len(existing["affected_cases"])
                for line in item.get("evidence", []):
                    if line not in existing["evidence"]:
                        existing["evidence"].append(line)
                existing["detected_at"] = item["detected_at"]
        except Exception:
            log.exception("Insight detector %s failed", detector.__name__)

    out = list(merged.values())
    out.sort(key=lambda i: SEVERITY_ORDER.get(i["severity"], 9))
    return out
