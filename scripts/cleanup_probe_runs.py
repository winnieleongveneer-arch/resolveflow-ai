#!/usr/bin/env python3
"""
cleanup_probe_runs.py — remove runs created by the test harnesses.

    docker compose exec backend python /app/scripts/cleanup_probe_runs.py --check
    docker compose exec backend python /app/scripts/cleanup_probe_runs.py --apply

The smoke test and the edge probe open real cases, which is the point - it is
the only way to test the write path honestly. But they leave rows behind, and
those rows are not agent work. A case opened by a test harness against a ticket
that does not exist is noise in the activity feed, and on a dashboard shown to
someone else, noise reads as sloppiness.

STRICTLY SCOPED. Only runs whose trigger_source is one of the harness sources
are touched. Anything opened by a person, by the Supervisor, or by the drill
stays exactly where it is - including the three seed-drawn drill tickets, which
are evidence and must survive.

--check prints what would go and changes nothing.
"""
from __future__ import annotations

import argparse, sys
sys.path.insert(0, "/app")

from app.core.database import SessionLocal                          # noqa: E402
from app.models.service_desk import (                               # noqa: E402
    WorkflowRun, OperatorEvent, PolicyEvaluation, WorkbenchItem,
)

# Only these. Never "manual", "command_center", "supervisor" or "swap_drill".
HARNESS = ("edge_probe", "smoke_test")

G, Y, R, D, X = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"

ap = argparse.ArgumentParser()
ap.add_argument("--check", action="store_true")
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

db = SessionLocal()
doomed = db.query(WorkflowRun).filter(WorkflowRun.trigger_source.in_(HARNESS)).all()
ids = [r.id for r in doomed]

print()
print("=" * 78)
print(f"  HARNESS RUNS  —  trigger_source in {HARNESS}")
print("=" * 78)
if not doomed:
    print(f"  {G}none — nothing to clean{X}")
else:
    for r in doomed[:40]:
        print(f"  {str(r.issue_key)[:44]:46s} {str(r.status):18s} {D}{str(r.id)[:8]}{X}")
    if len(doomed) > 40:
        print(f"  {D}... and {len(doomed) - 40} more{X}")

# Never remove a run a person decided on, whatever its trigger_source says.
protected = set()
if ids:
    for item in db.query(WorkbenchItem).filter(WorkbenchItem.run_id.in_(ids)).all():
        if item.human_decision:
            protected.add(item.run_id)
if protected:
    print(f"\n  {Y}{len(protected)} run(s) carry a human decision and will be KEPT.{X}")
    ids = [i for i in ids if i not in protected]

total_runs = db.query(WorkflowRun).count()
print(f"\n  {len(ids)} of {total_runs} runs would be removed. "
      f"{total_runs - len(ids)} remain.")

if not a.apply:
    print(f"\n  {Y}Nothing changed. Re-run with --apply to remove them.{X}\n")
    db.close(); sys.exit(0)

if ids:
    ev = db.query(OperatorEvent).filter(OperatorEvent.run_id.in_(ids)).delete(
        synchronize_session=False)
    pe = db.query(PolicyEvaluation).filter(PolicyEvaluation.run_id.in_(ids)).delete(
        synchronize_session=False)
    wb = db.query(WorkbenchItem).filter(WorkbenchItem.run_id.in_(ids)).delete(
        synchronize_session=False)
    rn = db.query(WorkflowRun).filter(WorkflowRun.id.in_(ids)).delete(
        synchronize_session=False)
    db.commit()
    print(f"\n  {G}removed{X}  {rn} run(s), {ev} event(s), {pe} evaluation(s), "
          f"{wb} workbench item(s)")

print(f"  {D}runs remaining: {db.query(WorkflowRun).count()}{X}")
print("=" * 78 + "\n")
db.close()
