# app/services/maintenance.py
"""
Data integrity maintenance — safe to run repeatedly.

Before a demo the fastest way to look broken is a dashboard that disagrees
with itself: runs marked WAITING_FOR_HUMAN with no Workbench item behind them,
three pending items for one ticket, ledger rows claiming verification that
never happened.

This finds those conditions and reports them. Nothing is deleted — duplicates
are ARCHIVED and impossible states are corrected to the truthful value, so the
audit trail survives and the operation is idempotent.

Always run with dry_run=True first and read the report.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.service_desk import (
    OutcomeLedger,
    RunStatus,
    WorkbenchItem,
    WorkbenchStatus,
    WorkflowRun,
)

log = logging.getLogger(__name__)


def _finding(kind: str, description: str, count: int,
             examples: List[str], action: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "description": description,
        "count": count,
        "examples": examples[:8],
        "action_if_applied": action,
    }


def analyse(db: Session) -> List[Dict[str, Any]]:
    """Identify integrity problems without changing anything."""
    findings: List[Dict[str, Any]] = []

    # 1. Runs claiming to wait on a human with no Workbench item behind them.
    orphan_waiting = (
        db.query(WorkflowRun)
        .outerjoin(WorkbenchItem, WorkbenchItem.run_id == WorkflowRun.id)
        .filter(WorkflowRun.status == RunStatus.WAITING_FOR_HUMAN,
                WorkbenchItem.id.is_(None))
        .all()
    )
    if orphan_waiting:
        findings.append(_finding(
            "waiting_without_item",
            "Runs marked WAITING_FOR_HUMAN with no Workbench item. The "
            "dashboard's 'awaiting human' tile counts items, so these runs are "
            "invisible to it and the two numbers disagree.",
            len(orphan_waiting),
            [f"{r.issue_key} ({str(r.id)[:8]})" for r in orphan_waiting],
            "Set status to ESCALATED — identified as needing a person, but no "
            "decision was ever requested.",
        ))

    # 2. More than one pending Workbench item for the same decision point.
    dupes = (
        db.query(WorkbenchItem.run_id, WorkbenchItem.request_type,
                 func.count(WorkbenchItem.id))
        .filter(WorkbenchItem.status == WorkbenchStatus.PENDING)
        .group_by(WorkbenchItem.run_id, WorkbenchItem.request_type)
        .having(func.count(WorkbenchItem.id) > 1)
        .all()
    )
    if dupes:
        total = sum(c - 1 for _, _, c in dupes)
        findings.append(_finding(
            "duplicate_pending_items",
            "Several pending Workbench items exist for one run and decision "
            "point. A reviewer would be asked the same question repeatedly.",
            total,
            [f"run {str(r)[:8]} / {t}: {c} items" for r, t, c in dupes],
            "Keep the oldest, mark the rest EXPIRED with a maintenance note.",
        ))

    # 3. Ledger rows claiming verification with no verification note.
    unverifiable = (
        db.query(OutcomeLedger)
        .filter(OutcomeLedger.verified.is_(True),
                OutcomeLedger.verification_note.is_(None))
        .all()
    )
    if unverifiable:
        findings.append(_finding(
            "verified_without_evidence",
            "Ledger rows marked verified with no verification note. These "
            "inflate the auto-resolution rate with unevidenced outcomes.",
            len(unverifiable),
            [r.issue_key for r in unverifiable],
            "Set verified = false. A metric without evidence is a claim.",
        ))

    # 4. Terminal runs that never completed.
    impossible = (
        db.query(WorkflowRun)
        .filter(WorkflowRun.status.in_(RunStatus.TERMINAL),
                WorkflowRun.completed_at.is_(None))
        .all()
    )
    if impossible:
        findings.append(_finding(
            "terminal_without_completion",
            "Runs in a terminal status with no completed_at timestamp, so "
            "duration metrics silently exclude them.",
            len(impossible),
            [f"{r.issue_key} ({r.status})" for r in impossible],
            "Stamp completed_at from the last recorded event.",
        ))

    # 5. Several open runs for the same ticket.
    open_states = [s for s in RunStatus.ALL if s not in RunStatus.TERMINAL]
    multi = (
        db.query(WorkflowRun.issue_key, func.count(WorkflowRun.id))
        .filter(WorkflowRun.status.in_(open_states))
        .group_by(WorkflowRun.issue_key)
        .having(func.count(WorkflowRun.id) > 1)
        .all()
    )
    if multi:
        findings.append(_finding(
            "multiple_open_runs",
            "More than one open run for the same ticket. Whichever finishes "
            "last silently overwrites the other's outcome.",
            sum(c - 1 for _, c in multi),
            [f"{k}: {c} open runs" for k, c in multi],
            "Keep the newest, archive the rest as CANCELLED.",
        ))

    return findings


def run(db: Session, dry_run: bool = True) -> Dict[str, Any]:
    findings = analyse(db)
    now = datetime.now(timezone.utc)
    applied: Dict[str, int] = {}

    if not dry_run:
        # 1. waiting -> escalated
        for r in (db.query(WorkflowRun)
                  .outerjoin(WorkbenchItem, WorkbenchItem.run_id == WorkflowRun.id)
                  .filter(WorkflowRun.status == RunStatus.WAITING_FOR_HUMAN,
                          WorkbenchItem.id.is_(None)).all()):
            r.status = RunStatus.ESCALATED
            r.current_stage = RunStatus.ESCALATED
            applied["waiting_without_item"] = applied.get("waiting_without_item", 0) + 1

        # 2. duplicate pending items -> keep oldest
        dupes = (
            db.query(WorkbenchItem.run_id, WorkbenchItem.request_type)
            .filter(WorkbenchItem.status == WorkbenchStatus.PENDING)
            .group_by(WorkbenchItem.run_id, WorkbenchItem.request_type)
            .having(func.count(WorkbenchItem.id) > 1).all()
        )
        for run_id, request_type in dupes:
            items = (db.query(WorkbenchItem)
                     .filter(WorkbenchItem.run_id == run_id,
                             WorkbenchItem.request_type == request_type,
                             WorkbenchItem.status == WorkbenchStatus.PENDING)
                     .order_by(WorkbenchItem.created_at.asc()).all())
            for extra in items[1:]:
                extra.status = WorkbenchStatus.EXPIRED
                extra.reviewer_notes = (
                    (extra.reviewer_notes or "")
                    + " [maintenance] Superseded by the earlier pending item "
                      f"{str(items[0].id)[:8]} for the same decision point."
                ).strip()
                extra.decided_at = now
                applied["duplicate_pending_items"] = \
                    applied.get("duplicate_pending_items", 0) + 1

        # 3. unevidenced verification
        for row in (db.query(OutcomeLedger)
                    .filter(OutcomeLedger.verified.is_(True),
                            OutcomeLedger.verification_note.is_(None)).all()):
            row.verified = False
            row.verification_note = (
                "[maintenance] verified flag cleared: no verification evidence "
                "was recorded for this outcome."
            )
            applied["verified_without_evidence"] = \
                applied.get("verified_without_evidence", 0) + 1

        # 4. terminal without completion
        for r in (db.query(WorkflowRun)
                  .filter(WorkflowRun.status.in_(RunStatus.TERMINAL),
                          WorkflowRun.completed_at.is_(None)).all()):
            r.completed_at = r.updated_at or r.started_at or now
            applied["terminal_without_completion"] = \
                applied.get("terminal_without_completion", 0) + 1

        # 5. multiple open runs -> keep newest
        multi = (db.query(WorkflowRun.issue_key)
                 .filter(WorkflowRun.status.in_(open_states_of()))
                 .group_by(WorkflowRun.issue_key)
                 .having(func.count(WorkflowRun.id) > 1).all())
        for (issue_key,) in multi:
            runs = (db.query(WorkflowRun)
                    .filter(WorkflowRun.issue_key == issue_key,
                            WorkflowRun.status.in_(open_states_of()))
                    .order_by(WorkflowRun.started_at.desc()).all())
            for older in runs[1:]:
                older.status = RunStatus.FAILED
                older.error_message = (
                    "[maintenance] Cancelled: superseded by a newer open run "
                    f"{str(runs[0].id)[:8]} for the same ticket."
                )
                older.completed_at = now
                applied["multiple_open_runs"] = \
                    applied.get("multiple_open_runs", 0) + 1

        db.commit()

    return {
        "dry_run": dry_run,
        "checked_at": now.isoformat(),
        "issues_found": sum(f["count"] for f in findings),
        "findings": findings,
        "changes_applied": applied if not dry_run else None,
        "note": (
            "Dry run — nothing was changed. Re-run with dry_run=false to apply."
            if dry_run else
            "Applied. Nothing was deleted: duplicates were expired or "
            "cancelled with a maintenance note, and impossible states were "
            "corrected to the truthful value. Safe to run again."
        ),
    }


def open_states_of() -> List[str]:
    return [s for s in RunStatus.ALL if s not in RunStatus.TERMINAL]
