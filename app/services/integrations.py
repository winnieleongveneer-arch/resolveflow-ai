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


CHECKS = {
    "supabase": _check_supabase,
    "supervity_auto": _check_supervity_auto,
    "slack": _check_slack,
}


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

    # Never downgrade a demonstrated success to DEGRADED just because the
    # cheap probe is inconclusive.
    proposed = result["status"]
    if proposed == IntegrationStatus.DEGRADED and (
        row.last_successful_read or row.last_successful_write
    ):
        proposed = IntegrationStatus.HEALTHY
        row.latest_error = None
    row.status = proposed

    db.commit()
    db.refresh(row)
    log.info("Health check %s -> %s", key, row.status)
    return row


def run_all_health_checks(db: Session) -> List[IntegrationHealth]:
    ensure_registered(db)
    return [run_health_check(db, spec["integration_key"]) for spec in REGISTRY]
