#!/usr/bin/env python3
"""
relabel_workbench_events.py — correct events attributed to an Operator that
did not perform them.

    docker compose exec backend python /app/scripts/relabel_workbench_events.py --check
    docker compose exec backend python /app/scripts/relabel_workbench_events.py --apply

The Command Center's Workbench notifier recorded its own Slack messages under
the name "RF-06 Change and Recovery Controller". No Operator sent them. This
moves those rows to WORKBENCH, which is the truth and which the Operator counts
already exclude.

Only NOTIFICATION_SENT rows are touched. If a real RF-06 has since reported
work of its own, those events are left exactly as they are.
"""
from __future__ import annotations

import argparse, sys
sys.path.insert(0, "/app")

from app.core.database import SessionLocal                      # noqa: E402
from app.models.service_desk import OperatorEvent               # noqa: E402

WRONG = "RF-06 Change and Recovery Controller"
G, Y, D, X = "\033[32m", "\033[33m", "\033[90m", "\033[0m"

ap = argparse.ArgumentParser()
ap.add_argument("--check", action="store_true")
ap.add_argument("--apply", action="store_true")
a = ap.parse_args()

db = SessionLocal()
rows = db.query(OperatorEvent).filter(OperatorEvent.operator_name == WRONG).all()
notifications = [e for e in rows if e.event_type == "NOTIFICATION_SENT"]
other = [e for e in rows if e.event_type != "NOTIFICATION_SENT"]

print()
print(f"  events named {WRONG!r}: {len(rows)}")
print(f"    NOTIFICATION_SENT (the Command Center's own Slack posts): {len(notifications)}")
print(f"    everything else (real Operator work, left alone):        {len(other)}")
for e in other:
    print(f"      {D}{e.event_type} on run {str(e.run_id)[:8]}{X}")

if a.apply:
    for e in notifications:
        e.operator_name = "WORKBENCH"
    db.commit()
    print(f"\n  {G}{len(notifications)} row(s) relabelled to WORKBENCH.{X}")
    print(f"  {D}Operator counts will now reflect Operators that actually reported.{X}")
else:
    print(f"\n  {Y}Nothing changed. Re-run with --apply to correct them.{X}")
print()
db.close()
