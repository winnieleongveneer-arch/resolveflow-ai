# app/routers/integrations.py
"""
Data Manager API — the live registry of connected systems.

    GET  /api/integrations                       list, with real health
    POST /api/integrations/health-check          re-check everything
    POST /api/integrations/{key}/health-check    re-check one

Guide 8.4: "An integration that is connected but unused, or a Data Manager
entry that is hardcoded, does not count toward the floor." So nothing here
returns HEALTHY on the strength of configuration alone — a status only
improves when a real round trip or a real read/write happens.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.service_desk import IntegrationHealth
from ..schemas.service_desk import IntegrationOut, IntegrationUseReport
from ..services import integrations as integ

log = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["Data Manager"])


@router.get("", response_model=List[IntegrationOut])
def list_integrations(db: Session = Depends(get_db)):
    """Every connected system, its purpose, and its last observed health."""
    integ.ensure_registered(db)
    return (
        db.query(IntegrationHealth)
        .order_by(IntegrationHealth.category, IntegrationHealth.integration_name)
        .all()
    )


@router.post("/health-check", response_model=List[IntegrationOut])
def health_check_all(db: Session = Depends(get_db)):
    """Run a real connectivity check against every integration, now."""
    return integ.run_all_health_checks(db)


@router.post("/{integration_key}/health-check", response_model=IntegrationOut)
def health_check_one(integration_key: str, db: Session = Depends(get_db)):
    try:
        return integ.run_health_check(db, integration_key)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Unknown integration '{integration_key}'"
        )


@router.post("/{integration_key}/record-use", response_model=IntegrationOut)
def record_use(
    integration_key: str,
    payload: IntegrationUseReport,
    db: Session = Depends(get_db),
):
    """
    An Operator reports a real round trip it just performed.

    This exists because some integrations cannot be probed from here. A Slack
    webhook cannot be tested without posting, and the Outlook token lives
    inside Supervity Auto, not in this process. Rather than award a green badge
    for configuration, the Command Center waits to be told about a round trip
    that actually happened, and records who reported it.

    A failed attempt is recorded too — that is the point of an audit trail.
    """
    integ.ensure_registered(db)
    if integ.get_row(db, integration_key) is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown integration '{integration_key}'"
        )

    if payload.succeeded:
        if payload.direction == "read":
            integ.record_read(db, integration_key, payload.records or 1)
        else:
            integ.record_write(db, integration_key, payload.records or 1)
        log.info("%s reported a successful %s on %s (%s)",
                 payload.operator_name, payload.direction, integration_key,
                 payload.detail)
    else:
        integ.record_failure(
            db, integration_key,
            f"{payload.operator_name} reported a failed {payload.direction}: "
            f"{payload.detail or 'no detail supplied'}",
        )

    return integ.get_row(db, integration_key)
