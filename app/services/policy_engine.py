# app/services/policy_engine.py
"""
ResolveFlow NEXUS — Policy Engine.

The autonomy envelope. Every external side effect must pass through here
BEFORE it executes, and every evaluation is persisted.

    Operator proposes an action
        -> backend calls evaluate()
        -> deterministic evaluator returns ALLOW / REQUIRE_HUMAN_REVIEW / DENY
        -> the verdict is written to policy_evaluations (append only)
        -> only ALLOW, or an explicit human approval, permits execution

Three rules this module never breaks
------------------------------------
1. FAIL CLOSED. An unknown policy, an inactive policy, or a policy that
   cannot be loaded returns DENY. It never falls through to ALLOW.
2. NEVER INVENT A MISSING FIELD. Absent required evidence returns
   REQUIRE_HUMAN_REVIEW naming the missing field, so the case reaches a human
   with the reason. It is not treated as zero, false, or "probably fine".
3. EVERY VERDICT CARRIES REASONS. Each reason cites the actual numbers that
   drove it, so a business user can read the decision without an engineer.

Evaluators are PURE functions of (context, configuration). No database, no
network, no clock. That is what makes them unit-testable and what makes
simulation against historical proposals safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models.service_desk import PolicyDefinition, PolicyEvaluation, Verdict

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class PolicyResult:
    verdict: str
    reasons: List[str] = field(default_factory=list)
    policy_key: str = ""
    policy_version: int = 0
    configuration_snapshot: Dict[str, Any] = field(default_factory=dict)
    missing_fields: List[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.verdict == Verdict.ALLOW

    @property
    def needs_human(self) -> bool:
        return self.verdict == Verdict.REQUIRE_HUMAN_REVIEW

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "policy_key": self.policy_key,
            "policy_version": self.policy_version,
            "reasons": self.reasons,
            "missing_fields": self.missing_fields,
        }


class MissingEvidence(Exception):
    """Raised by an evaluator when required context is absent."""

    def __init__(self, fields: List[str]):
        self.fields = fields
        super().__init__(f"missing required evidence: {', '.join(fields)}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require(context: Dict[str, Any], *names: str) -> None:
    """Raise MissingEvidence if any named field is absent or None."""
    missing = [n for n in names if context.get(n) is None]
    if missing:
        raise MissingEvidence(missing)


def _num(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise MissingEvidence([name])


# ---------------------------------------------------------------------------
# POLICY 1 — Major Incident Declaration
# ---------------------------------------------------------------------------


def evaluate_major_incident_declaration(
    context: Dict[str, Any], config: Dict[str, Any]
) -> PolicyResult:
    """
    May this ticket cluster be declared a major incident automatically?

    Expected context
    ----------------
    correlated_ticket_count   int    related tickets found by RF-02
    detection_window_minutes  number span the correlation was observed over
    correlation_confidence    float  0..1 semantic correlation confidence
    shared_system             str?   affected system common to the cluster
    shared_root_cause         str?   root cause common to the cluster

    Configurable thresholds
    -----------------------
    minimum_correlated_ticket_count
    detection_window_minutes
    minimum_correlation_confidence
    require_shared_system_or_root_cause
    """
    _require(
        context,
        "correlated_ticket_count",
        "detection_window_minutes",
        "correlation_confidence",
    )

    count = _num(context["correlated_ticket_count"], "correlated_ticket_count")
    window = _num(context["detection_window_minutes"], "detection_window_minutes")
    confidence = _num(context["correlation_confidence"], "correlation_confidence")

    min_count = float(config.get("minimum_correlated_ticket_count", 5))
    max_window = float(config.get("detection_window_minutes", 20))
    min_conf = float(config.get("minimum_correlation_confidence", 0.80))
    require_shared = bool(config.get("require_shared_system_or_root_cause", True))

    shared_system = context.get("shared_system")
    shared_root_cause = context.get("shared_root_cause")
    has_shared = bool(shared_system) or bool(shared_root_cause)

    count_ok = count >= min_count
    window_ok = window <= max_window
    conf_ok = confidence >= min_conf
    shared_ok = has_shared or not require_shared

    reasons: List[str] = []

    # --- hard stops: not enough evidence to declare anything --------------
    if not count_ok:
        reasons.append(
            f"Only {int(count)} correlated tickets were found; the threshold "
            f"is {int(min_count)}."
        )
        reasons.append(
            "Not declared as a major incident. The tickets remain individually "
            "triaged."
        )
        return PolicyResult(verdict=Verdict.DENY, reasons=reasons)

    if not window_ok:
        reasons.append(
            f"The {int(count)} correlated tickets span {window:g} minutes, "
            f"outside the {max_window:g}-minute detection window."
        )
        reasons.append(
            "Tickets spread over a long period usually indicate a recurring "
            "known error rather than one live outage."
        )
        return PolicyResult(verdict=Verdict.DENY, reasons=reasons)

    reasons.append(
        f"{int(count)} correlated tickets were detected within {window:g} "
        f"minutes, meeting the threshold of {int(min_count)} within "
        f"{max_window:g} minutes."
    )

    # --- confidence below bar: evidence exists, certainty does not --------
    if not conf_ok:
        reasons.append(
            f"Correlation confidence {confidence:.2f} is below the required "
            f"{min_conf:.2f}."
        )
        reasons.append(
            "Volume suggests an incident but the correlation is not strong "
            "enough to declare automatically. Routed for human confirmation."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    reasons.append(
        f"Correlation confidence {confidence:.2f} met the required "
        f"{min_conf:.2f}."
    )

    # --- no shared cause: refuse to declare on similarity alone -----------
    if not shared_ok:
        reasons.append(
            "No shared affected system or root cause was identified across the "
            "cluster."
        )
        reasons.append(
            "Policy requires shared causal evidence, so the tickets are not "
            "declared a major incident on textual similarity alone. Routed for "
            "human confirmation."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if shared_system:
        reasons.append(f"All correlated tickets affect the same system: {shared_system}.")
    if shared_root_cause:
        reasons.append(f"A shared root cause was identified: {shared_root_cause}.")
    if not require_shared and not has_shared:
        reasons.append(
            "Shared system or root cause is not required by the current policy "
            "configuration."
        )

    reasons.append(
        "All conditions met. A major incident may be declared and child tickets "
        "linked to the parent."
    )
    return PolicyResult(verdict=Verdict.ALLOW, reasons=reasons)


# ---------------------------------------------------------------------------
# POLICY 2 — Safe Auto-Remediation
# ---------------------------------------------------------------------------


def evaluate_safe_auto_remediation(
    context: Dict[str, Any], config: Dict[str, Any]
) -> PolicyResult:
    """
    May the agent apply this fix on its own?

    This is the autonomy dial. Every condition below is a reason a human
    would want to be asked, and each one is editable without code.

    Expected context
    ----------------
    confidence          float 0..1  RF-03's confidence in the proposed fix
    kb_auto_safe        bool        Knowledge_Base.x_auto_safe for the matched article
    is_reopened         bool        Issues.x_reopened
    in_major_incident   bool        ticket belongs to an unresolved major incident
    reversible          bool        the action can be undone
    production_impact   bool        the action touches production
    required_fields_complete bool   no evidence was missing upstream
    """
    _require(context, "confidence", "kb_auto_safe", "reversible")

    confidence = _num(context["confidence"], "confidence")
    kb_auto_safe = bool(context["kb_auto_safe"])
    reversible = bool(context["reversible"])
    is_reopened = bool(context.get("is_reopened", False))
    in_major = bool(context.get("in_major_incident", False))
    production = bool(context.get("production_impact", False))
    complete = bool(context.get("required_fields_complete", True))

    min_conf = float(config.get("minimum_confidence", 0.85))
    require_kb = bool(config.get("require_kb_auto_safe", True))
    block_reopened = bool(config.get("block_if_reopened", True))
    block_major = bool(config.get("block_if_major_incident", True))
    require_reversible = bool(config.get("require_reversible", True))
    block_production = bool(config.get("block_if_production_impact", True))

    reasons: List[str] = []

    # --- hard denials ------------------------------------------------------
    if require_kb and not kb_auto_safe:
        reasons.append(
            "The matched knowledge base article is not marked auto-safe "
            "(x_auto_safe = false)."
        )
        reasons.append(
            "Only fixes a knowledge author has explicitly cleared may run "
            "unattended."
        )
        return PolicyResult(verdict=Verdict.DENY, reasons=reasons)

    if require_reversible and not reversible:
        reasons.append("The proposed action is not reversible.")
        reasons.append(
            "An irreversible action is never taken autonomously, whatever the "
            "confidence."
        )
        return PolicyResult(verdict=Verdict.DENY, reasons=reasons)

    # --- human review ------------------------------------------------------
    if not complete:
        reasons.append("Required case evidence was incomplete.")
        reasons.append(
            "The agent will not act on a partial picture. Routed for a human "
            "to supply what is missing."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if block_major and in_major:
        reasons.append(
            "This ticket belongs to an unresolved major incident."
        )
        reasons.append(
            "Individual fixes during a live incident can mask the root cause "
            "or conflict with the incident response. Routed to the incident "
            "owner."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if block_reopened and is_reopened:
        reasons.append("This ticket has been reopened at least once.")
        reasons.append(
            "A previous fix did not hold, so repeating it automatically risks "
            "repeating the failure. Routed for human judgement."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if block_production and production:
        reasons.append("The proposed action affects production.")
        reasons.append(
            "Production changes require a human decision under the current "
            "autonomy settings."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if confidence < min_conf:
        reasons.append(
            f"Confidence {confidence:.2f} is below the autonomy threshold of "
            f"{min_conf:.2f}."
        )
        reasons.append(
            "The evidence supports the fix but not strongly enough to apply it "
            "unattended."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    # --- allow -------------------------------------------------------------
    reasons.append(
        f"Confidence {confidence:.2f} met the autonomy threshold of "
        f"{min_conf:.2f}."
    )
    reasons.append("The matched knowledge base article is marked auto-safe.")
    reasons.append("The action is reversible, so a failed fix can be rolled back.")
    if not is_reopened:
        reasons.append("The ticket has not been reopened before.")
    if not in_major:
        reasons.append("The ticket is not part of an unresolved major incident.")
    reasons.append(
        "All autonomy conditions met. The fix may be applied and then verified."
    )
    return PolicyResult(verdict=Verdict.ALLOW, reasons=reasons)


# ---------------------------------------------------------------------------
# POLICY 3 — Change and CAB Control
# ---------------------------------------------------------------------------


def evaluate_change_and_cab_control(
    context: Dict[str, Any], config: Dict[str, Any]
) -> PolicyResult:
    """
    Does this action need a change approval before it touches anything?

    Expected context
    ----------------
    production_impact     bool
    cab_approval_required bool   Change_Requests.cab_approval_required
    risk                  str    Low | Medium | High
    blast_radius          int    users or systems affected
    action_category       str    access | infrastructure | critical_service | routine
    change_status         str?   e.g. Pending CAB Approval | Rolled Back | Implemented
    previous_rollback     bool?  a prior attempt on this change was rolled back
    """
    _require(context, "production_impact", "risk", "blast_radius")

    production = bool(context["production_impact"])
    risk = str(context["risk"]).strip().title()
    blast = _num(context["blast_radius"], "blast_radius")
    cab_required = bool(context.get("cab_approval_required", False))
    category = str(context.get("action_category", "routine")).lower()
    change_status = str(context.get("change_status") or "").strip()
    prior_rollback = bool(context.get("previous_rollback", False))

    review_risks = [str(r).title() for r in
                    config.get("risk_levels_requiring_approval", ["Medium", "High"])]
    max_blast = float(config.get("max_blast_radius", 25))
    restricted = [str(c).lower() for c in config.get(
        "restricted_categories", ["access", "infrastructure", "critical_service"])]
    deny_on_rollback = bool(config.get("deny_if_previously_rolled_back", True))
    require_on_production = bool(config.get("require_approval_if_production", True))

    reasons: List[str] = []

    # --- denials -----------------------------------------------------------
    if deny_on_rollback and prior_rollback:
        reasons.append(
            "A previous attempt at this change was rolled back."
        )
        reasons.append(
            "Re-running a change that already failed needs a new plan, not a "
            "retry. Blocked pending investigation."
        )
        return PolicyResult(verdict=Verdict.DENY, reasons=reasons)

    if change_status.lower() == "rejected":
        reasons.append("The associated change request was rejected.")
        reasons.append("A rejected change cannot be executed.")
        return PolicyResult(verdict=Verdict.DENY, reasons=reasons)

    # --- approval required -------------------------------------------------
    if cab_required:
        reasons.append(
            "The associated change request is flagged cab_approval_required."
        )
        if change_status:
            reasons.append(f"Current change status: {change_status}.")
        reasons.append("CAB approval must be recorded before the action runs.")
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if require_on_production and production:
        reasons.append("The action affects production systems.")
        reasons.append(
            "Production changes require an approver on record under the "
            "current change-control settings."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if risk in review_risks:
        reasons.append(
            f"Change risk is {risk}, which requires approval "
            f"(configured: {', '.join(review_risks)})."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if blast > max_blast:
        reasons.append(
            f"Blast radius of {int(blast)} exceeds the unattended limit of "
            f"{int(max_blast)}."
        )
        reasons.append(
            "Wide-reaching changes need a human to confirm the scope is intended."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    if category in restricted:
        reasons.append(
            f"The action changes {category}, which is a restricted category."
        )
        reasons.append(
            "Access, infrastructure and critical-service changes are never "
            "made without an approver."
        )
        return PolicyResult(verdict=Verdict.REQUIRE_HUMAN_REVIEW, reasons=reasons)

    # --- allow -------------------------------------------------------------
    reasons.append(f"Change risk is {risk} and no CAB approval is flagged.")
    reasons.append(
        f"Blast radius of {int(blast)} is within the unattended limit of "
        f"{int(max_blast)}."
    )
    reasons.append("The action does not affect production or a restricted category.")
    reasons.append("No change approval is required. The action may proceed.")
    return PolicyResult(verdict=Verdict.ALLOW, reasons=reasons)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

Evaluator = Callable[[Dict[str, Any], Dict[str, Any]], PolicyResult]

EVALUATORS: Dict[str, Evaluator] = {
    "major_incident_declaration": evaluate_major_incident_declaration,
    "safe_auto_remediation": evaluate_safe_auto_remediation,
    "change_and_cab_control": evaluate_change_and_cab_control,
}


def registered_policy_keys() -> List[str]:
    return sorted(EVALUATORS.keys())


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def evaluate(
    db: Session,
    policy_key: str,
    context: Dict[str, Any],
    *,
    run_id: Optional[Any] = None,
    issue_key: Optional[str] = None,
    proposed_action: Optional[Dict[str, Any]] = None,
    simulate: bool = False,
    persist: bool = True,
) -> PolicyResult:
    """
    Evaluate one policy against one case and persist the verdict.

    Set ``simulate=True`` to evaluate without the result counting as a real
    gate — the row is still written, flagged ``is_simulation``, so the Policy
    Lab can compare draft against active with a full audit trail and no side
    effects.
    """
    policy: Optional[PolicyDefinition] = (
        db.query(PolicyDefinition)
        .filter(PolicyDefinition.policy_key == policy_key)
        .first()
    )

    # --- fail closed -------------------------------------------------------
    if policy is None:
        log.error("Policy '%s' not found — failing closed.", policy_key)
        result = PolicyResult(
            verdict=Verdict.DENY,
            reasons=[
                f"Policy '{policy_key}' is not configured.",
                "The engine fails closed: no external action may execute when "
                "its governing policy cannot be loaded.",
            ],
            policy_key=policy_key,
        )
        if persist:
            _persist(db, result, context, run_id, issue_key, proposed_action, simulate)
        return result

    if not policy.is_active:
        result = PolicyResult(
            verdict=Verdict.DENY,
            reasons=[
                f"Policy '{policy_key}' is currently deactivated.",
                "The engine fails closed while a governing policy is inactive.",
            ],
            policy_key=policy_key,
            policy_version=policy.active_version,
            configuration_snapshot=dict(policy.configuration or {}),
        )
        if persist:
            _persist(db, result, context, run_id, issue_key, proposed_action, simulate)
        return result

    evaluator = EVALUATORS.get(policy_key)
    if evaluator is None:
        log.error("No evaluator registered for '%s' — failing closed.", policy_key)
        result = PolicyResult(
            verdict=Verdict.DENY,
            reasons=[
                f"No deterministic evaluator is registered for '{policy_key}'.",
                "The engine fails closed rather than approving an ungoverned action.",
            ],
            policy_key=policy_key,
            policy_version=policy.active_version,
            configuration_snapshot=dict(policy.configuration or {}),
        )
        if persist:
            _persist(db, result, context, run_id, issue_key, proposed_action, simulate)
        return result

    config = dict(policy.configuration or {})

    # --- run the evaluator -------------------------------------------------
    try:
        result = evaluator(context, config)
    except MissingEvidence as exc:
        # Required evidence is absent. Pause and ask a human — never guess.
        result = PolicyResult(
            verdict=Verdict.REQUIRE_HUMAN_REVIEW,
            reasons=[
                "Required evidence is missing: " + ", ".join(exc.fields) + ".",
                "The agent does not infer or default a missing field. The case "
                "is routed to the Workbench for a human to supply it.",
            ],
            missing_fields=exc.fields,
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("Evaluator '%s' raised.", policy_key)
        result = PolicyResult(
            verdict=Verdict.DENY,
            reasons=[
                f"The policy evaluator failed: {type(exc).__name__}.",
                "The engine fails closed when a verdict cannot be computed.",
            ],
        )

    result.policy_key = policy_key
    result.policy_version = policy.active_version
    result.configuration_snapshot = config

    if persist:
        _persist(db, result, context, run_id, issue_key, proposed_action, simulate)

    log.info(
        "Policy %s v%s -> %s (issue=%s, simulate=%s)",
        policy_key, policy.active_version, result.verdict, issue_key, simulate,
    )
    return result


def _persist(
    db: Session,
    result: PolicyResult,
    context: Dict[str, Any],
    run_id: Optional[Any],
    issue_key: Optional[str],
    proposed_action: Optional[Dict[str, Any]],
    simulate: bool,
) -> PolicyEvaluation:
    """Append the evaluation. Never updates an existing row."""
    row = PolicyEvaluation(
        run_id=run_id,
        issue_key=issue_key,
        policy_key=result.policy_key,
        policy_version=result.policy_version,
        input_context=context,
        configuration_snapshot=result.configuration_snapshot,
        verdict=result.verdict,
        reasons=result.reasons,
        proposed_action=proposed_action,
        is_simulation=simulate,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
