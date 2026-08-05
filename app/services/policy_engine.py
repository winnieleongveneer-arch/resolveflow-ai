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
# Registry
# ---------------------------------------------------------------------------

Evaluator = Callable[[Dict[str, Any], Dict[str, Any]], PolicyResult]

EVALUATORS: Dict[str, Evaluator] = {
    "major_incident_declaration": evaluate_major_incident_declaration,
    # P0 ships one working policy. Register the remaining two here:
    #   "safe_auto_remediation":  evaluate_safe_auto_remediation,
    #   "change_and_cab_control": evaluate_change_and_cab_control,
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
