#!/usr/bin/env python3
"""
pending.py — the Workbench queue, with the full ids you need to act on it.

    docker compose exec backend python /app/scripts/pending.py

READ ONLY. The Workbench shows a short id because a full UUID is unreadable on
a card. An Operator needs the whole thing. Rather than copy it out of a browser
address bar mid-demo, print it.
"""
from __future__ import annotations

import sys
sys.path.insert(0, "/app")

from app.core.database import SessionLocal                       # noqa: E402
from app.models.service_desk import (                            # noqa: E402
    WorkbenchItem, WorkflowRun, WorkbenchStatus,
)

G, Y, D, X = "\033[32m", "\033[33m", "\033[90m", "\033[0m"

db = SessionLocal()
items = db.query(WorkbenchItem).order_by(WorkbenchItem.created_at).all()
pending = [i for i in items if i.status == WorkbenchStatus.PENDING]

print("\n" + "=" * 78)
print(f"  WORKBENCH — {len(pending)} pending of {len(items)} total")
print("=" * 78)

for i in items:
    run = db.query(WorkflowRun).filter(WorkflowRun.id == i.run_id).first()
    mark = f"{Y}PENDING{X}" if i.status == WorkbenchStatus.PENDING else f"{G}{i.status}{X}"
    print(f"\n  {i.issue_key}   {mark}")
    print(f"     raised     {i.created_at}")
    print(f"     item id    {i.id}")
    print(f"     run id     {G}{run.id if run else 'unknown'}{X}   <- paste this into the Operator")
    print(f"     type       {i.request_type}")
    if i.human_decision:
        print(f"     decided    {i.human_decision} by {i.reviewer} at {i.decided_at}")

print(f"\n  {D}Paste issue_key and the full run id into RF-06 on Auto so it")
print(f"  executes what a person allowed, then reports the outcome back.{X}")
print("=" * 78 + "\n")

db.close()
