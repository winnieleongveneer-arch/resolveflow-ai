# app/models/__init__.py
from .audit import AuditCategory, AuditLog, AuditSeverity
from .item import Item
from .service_desk import (
    IntegrationHealth,
    OutcomeLedger,
    IntegrationStatus,
    OperatorEvent,
    PolicyDefinition,
    PolicyEvaluation,
    PolicyVersion,
    RunStatus,
    TaskBaseline,
    Verdict,
    WorkbenchItem,
    WorkbenchStatus,
    WorkflowRun,
)
from .settings import Settings

# NOTE: alembic/env.py does `from app.models import *`.
# Any model missing from __all__ is invisible to autogenerate and will
# silently produce an EMPTY migration. Add new models here.
__all__ = [
    "Item",
    "Settings",
    "AuditLog",
    "AuditCategory",
    "AuditSeverity",
    # ResolveFlow AI — service desk
    "WorkflowRun",
    "OperatorEvent",
    "PolicyDefinition",
    "PolicyVersion",
    "PolicyEvaluation",
    "WorkbenchItem",
    "IntegrationHealth",
    "OutcomeLedger",
    "TaskBaseline",
    # Controlled vocabularies
    "RunStatus",
    "Verdict",
    "WorkbenchStatus",
    "IntegrationStatus",
]
