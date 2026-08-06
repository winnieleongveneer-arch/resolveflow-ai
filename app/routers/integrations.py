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
from ..schemas.service_desk import IntegrationOut
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
