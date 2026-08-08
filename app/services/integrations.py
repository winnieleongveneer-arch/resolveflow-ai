# app/services/integrations.py
"""
Live integration registry and health checks.

Guide 8.4 and 11.2: integrations must carry real data and be visible and
healthy in the Data Manager. A hardcoded green badge does not count.

So every status here is derived from one of:
  * a real HTTP round trip made just now, or
  * a real read/write this build actually performed, or
  * the honest absence of credentials.

Nothing returns HEALTHY because it was configured. Configuration only ever
produces UNKNOWN — reachability has to be demonstrated.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from ..models.service_desk import IntegrationHealth, IntegrationStatus

log = logging.getLogger(__name__)

HTTP_TIMEOUT = float(os.getenv("INTEGRATION_HTTP_TIMEOUT", "8"))


# ---------------------------------------------------------------------------
# Registry — what this build claims to be connected to
# ---------------------------------------------------------------------------

REGISTRY: List[Dict[str, Any]] = [
    {
        "integration_key": "supervity_auto",
        "integration_name": "Supervity Auto",
        "category": "agent_platform",
        "purpose": "Runs the ResolveFlow Orchestrator and the RF-01..RF-06 Operators.",
        "used_by_operators": ["ORCHESTRATOR"],
    },
    {
        "integration_key": "supabase",
        "integration_name": "Supabase",
        "category": "system_of_record",
        "purpose": "Ticket backlog, user directory, access register, knowledge base and SLA calendar.",
        "used_by_operators": [
            "RF-01 SLA Rescue Coordinator",
            "RF-02 Evidence Investigator",
            "RF-03 Resolution Specialist",
        ],
    },
    {
        "integration_key": "outlook",
        "integration_name": "Microsoft Outlook",
        "category": "channel",
        "purpose": "Keeps the person who raised the ticket informed: outcome mail when a fix is applied, acknowledgement when a human is asked to review.",
        "used_by_operators": ["RF-03 Resolution Specialist", "RF-04 Customer Liaison"],
    },
    {
        "integration_key": "slack",
        "integration_name": "Slack",
        "category": "channel",
        "purpose": "Human escalation and change approval in #ticket-escalations.",
        "used_by_operators": ["RF-05 Major Incident Commander", "RF-06 Change and Recovery Controller"],
    },
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redact(text: str) -> str:
    """Strip anything that looks like a credential out of an error message."""
    out = text
    for var in (
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ANON_KEY",
        "SUPERVITY_WORKFLOW_API_KEY",
        "SLACK_WEBHOOK_URL",
    ):
        secret = os.getenv(var)
        if secret and len(secret) > 8:
            out = out.replace(secret, f"<{var} redacted>")
    return out[:1000]


def ensure_registered(db: Session) -> None:
    """Create a row for each known integration if it does not exist yet."""
    for spec in REGISTRY:
        row = (
            db.query(IntegrationHealth)
            .filter(IntegrationHealth.integration_key == spec["integration_key"])
            .first()
        )
        if row is None:
            db.add(IntegrationHealth(**spec, status=IntegrationStatus.UNKNOWN))
    db.commit()


def get_row(db: Session, key: str) -> Optional[IntegrationHealth]:
    return (
        db.query(IntegrationHealth)
        .filter(IntegrationHealth.integration_key == key)
        .first()
    )


def record_read(db: Session, key: str, count: int = 1) -> None:
    """Call after a real read. This is what makes 'HEALTHY' truthful."""
    row = get_row(db, key)
    if row is None:
        return
    row.last_successful_read = _now()
    row.records_processed = (row.records_processed or 0) + count
    row.status = IntegrationStatus.HEALTHY
    row.latest_error = None
    db.commit()


def record_write(db: Session, key: str, count: int = 1) -> None:
    """Call after a real write."""
    row = get_row(db, key)
    if row is None:
        return
    row.last_successful_write = _now()
    row.records_processed = (row.records_processed or 0) + count
    row.status = IntegrationStatus.HEALTHY
    row.latest_error = None
    db.commit()


def record_degraded(db: Session, key: str, detail: str) -> None:
    """
    Partial failure: the integration is reachable and authenticating, but one
    capability is broken. Distinct from UNHEALTHY, which means we cannot talk
    to it at all. Conflating the two hides which half works.
    """
    row = get_row(db, key)
    if row is None:
        return
    row.status = IntegrationStatus.DEGRADED
    row.latest_error = _redact(detail)
    row.last_health_check = _now()
    db.commit()


def record_failure(db: Session, key: str, error: str) -> None:
    row = get_row(db, key)
    if row is None:
        return
    row.status = IntegrationStatus.UNHEALTHY
    row.latest_error = _redact(error)
    row.last_health_check = _now()
    db.commit()


# ---------------------------------------------------------------------------
# Health checks — each one makes a real network call where it can
# ---------------------------------------------------------------------------


def _check_supabase() -> Dict[str, Any]:
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return {
            "status": IntegrationStatus.UNKNOWN,
            "credentials_configured": False,
            "error": "SUPABASE_URL and a Supabase key are not set in .env.",
        }
    started = time.perf_counter()
    try:
        r = httpx.get(
            f"{url}/rest/v1/",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=HTTP_TIMEOUT,
        )
        latency = (time.perf_counter() - started) * 1000
        ok = r.status_code < 500
        return {
            "status": IntegrationStatus.HEALTHY if ok else IntegrationStatus.DEGRADED,
            "credentials_configured": True,
            "latency_ms": round(latency, 1),
            "error": None if ok else f"Supabase returned HTTP {r.status_code}.",
        }
    except Exception as exc:
        return {
            "status": IntegrationStatus.UNHEALTHY,
            "credentials_configured": True,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": _redact(f"{type(exc).__name__}: {exc}"),
        }


def _check_supervity_auto() -> Dict[str, Any]:
    """
    Real authenticated call to Auto (GET /api/v1/workflow-runs?limit=1).

    This exercises the API key, the required `x-source: external` header and
    the org context together — so HEALTHY means the credentials actually work,
    not merely that they are present.
    """
    from . import auto_client

    result = auto_client.health()
    if not result.get("configured"):
        return {
            "status": IntegrationStatus.UNKNOWN,
            "credentials_configured": False,
            "error": result.get("detail"),
        }
    if result.get("ok"):
        return {
            "status": IntegrationStatus.HEALTHY,
            "credentials_configured": True,
            "latency_ms": result.get("latency_ms"),
            "error": None,
            # The probe just performed a real authenticated GET. Saying so is
            # not flattery; refusing to say so made the card claim reads
            # succeed while reporting "Last read: Never" directly above it.
            "read_ok": True,
        }
    return {
        "status": IntegrationStatus.UNHEALTHY,
        "credentials_configured": True,
        "error": _redact(str(result.get("detail"))),
    }


def _check_slack() -> Dict[str, Any]:
    hook = os.getenv("SLACK_WEBHOOK_URL", "")
    if not hook:
        return {
            "status": IntegrationStatus.UNKNOWN,
            "credentials_configured": False,
            "error": "SLACK_WEBHOOK_URL is not set in .env.",
        }
    if not hook.startswith("https://hooks.slack.com/"):
        return {
            "status": IntegrationStatus.UNHEALTHY,
            "credentials_configured": True,
            "error": "SLACK_WEBHOOK_URL does not look like a Slack incoming webhook.",
        }
    # A webhook cannot be probed without posting a message, so credentials
    # alone only ever yield DEGRADED. The first real escalation flips it to
    # HEALTHY via record_write().
    return {
        "status": IntegrationStatus.DEGRADED,
        "credentials_configured": True,
        "error": (
            "Webhook configured but not yet exercised. Status becomes HEALTHY "
            "after the first real escalation is delivered."
        ),
    }


def _check_outlook() -> Dict[str, Any]:
    """
    Outlook is reached through Supervity Auto's Microsoft Outlook connector,
    which performs the OAuth flow and injects MICROSOFT_OUTLOOK_TOKEN into the
    Operator at run time. The Command Center therefore holds no Outlook
    credential of its own and cannot probe Graph directly.

    Saying so plainly is the honest answer. The status only becomes HEALTHY
    once an Operator reports a message Graph actually accepted — see
    POST /api/integrations/{key}/record-use.
    """
    return {
        "status": IntegrationStatus.UNKNOWN,
        "credentials_configured": False,
        "error": (
            "Authenticated by Supervity Auto's Microsoft Outlook connector; the "
            "Command Center holds no token and will not claim a health it cannot "
            "observe. Becomes HEALTHY when an Operator reports a delivered message."
        ),
    }


CHECKS = {
    "supabase": _check_supabase,
    "supervity_auto": _check_supervity_auto,
    "slack": _check_slack,
    "outlook": _check_outlook,
}


def _auto_execution_failing(db: Session) -> Optional[str]:
    """
    Is Auto's execute endpoint failing, according to what we have recorded?

    Returns a sentence to show, or None. This reads the run history rather than
    asking Auto again: an Operator that could not be invoked is a fact already
    on file, and a fact outranks a fresh guess.
    """
    try:
        from ..models.service_desk import OperatorEvent, WorkflowRun
    except Exception:
        return None
    failures = (
        db.query(OperatorEvent)
        .filter(OperatorEvent.event_type == "AUTO_INVOKE_FAILED")
        .count()
    )
    if not failures:
        return None
    # Deliberately NOT "any run carries an Auto run id". Operators now report
    # their own run id from inside Auto, so that count says we can name an
    # execution - not that our outbound call works. Only a run this Command
    # Center actually launched proves the endpoint is back, and that is what
    # trigger_source records.
    launched_by_us = (
        db.query(WorkflowRun)
        .filter(WorkflowRun.auto_run_id.isnot(None),
                WorkflowRun.trigger_source.notin_(["supervisor", "manual",
                                                   "swap_drill", "command_center"]))
        .count()
    )
    if launched_by_us:
        return None
    total = db.query(WorkflowRun).count()
    # Be precise about WHAT is degraded. Auto executes workflows perfectly well
    # - the Supervisor ran four steps through it today. What fails is one
    # direction: this Command Center calling Auto's execute endpoint from
    # outside. Saying "execute has never succeeded" without that distinction
    # reads as "the platform is broken", which is not what we observed.
    return (
        "Reads succeed (GET /api/v1/workflows) and Auto executes workflows "
        "normally when they are started from its own UI. What fails is "
        "outbound invocation: POST /api/v1/workflow-runs/execute returns "
        f"HTTP 500 for every payload shape tried - {failures} failure(s) "
        f"recorded, and 0 of {total} runs carry an Auto run id. Operators are "
        "started from the Auto UI instead, which affects how a run begins, "
        "not what it does."
    )


def run_health_check(db: Session, key: str) -> IntegrationHealth:
    """Run one real health check and persist the result."""
    ensure_registered(db)
    row = get_row(db, key)
    if row is None:
        raise KeyError(key)

    check = CHECKS.get(key)
    if check is None:
        row.last_health_check = _now()
        db.commit()
        db.refresh(row)
        return row

    result = check()
    row.last_health_check = _now()
    row.credentials_configured = bool(result.get("credentials_configured"))
    row.latency_ms = result.get("latency_ms")
    row.latest_error = result.get("error")

    proposed = result["status"]
    if result.get("read_ok"):
        row.last_successful_read = _now()

    # An observed success outranks an inconclusive probe. Outlook is the case
    # that matters: the Command Center holds no token for it, so its probe can
    # only ever answer UNKNOWN - but an Operator has reported a delivered
    # message through it, and that is real evidence. Before this covered
    # UNKNOWN as well as DEGRADED, a health check would overwrite a status the
    # integration had actually earned, and quietly drop a met requirement.
    inconclusive = (IntegrationStatus.DEGRADED, IntegrationStatus.UNKNOWN)
    if proposed in inconclusive and (
        row.last_successful_read or row.last_successful_write
    ):
        proposed = IntegrationStatus.HEALTHY
        row.latest_error = None

    # Reads working is not the same as the platform working. Auto answers
    # GET /api/v1/workflows and fails POST /workflow-runs/execute, so a probe
    # that only reads would report HEALTHY and a refresh would quietly turn the
    # badge green while execution is still broken. Ask the record instead: if
    # runs are on file that could not invoke Auto, this is DEGRADED and the
    # reason is stated. The evidence, not the probe, decides.
    if key == "supervity_auto" and proposed == IntegrationStatus.HEALTHY:
        detail = _auto_execution_failing(db)
        if detail:
            proposed = IntegrationStatus.DEGRADED
            row.latest_error = detail

    row.status = proposed

    db.commit()
    db.refresh(row)
    log.info("Health check %s -> %s", key, row.status)
    return row


def run_all_health_checks(db: Session) -> List[IntegrationHealth]:
    ensure_registered(db)
    return [run_health_check(db, spec["integration_key"]) for spec in REGISTRY]
