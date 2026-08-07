#!/usr/bin/env python3
"""
check_supervisor.py — does the ResolveFlow Supervisor still have its Operators?

    docker compose exec backend python /app/scripts/check_supervisor.py

READ ONLY. It asks Auto what workflows exist and compares that against the four
subworkflow ids the Supervisor export calls. Nothing is executed, nothing is
changed, and no ticket is touched.

Why this is worth a script: the Supervisor calls each Operator by UUID, not by
name. Re-importing an Operator creates a NEW workflow with a NEW id, so a
Supervisor can look perfectly healthy in the sidebar while pointing at four
workflows that no longer exist. The only way to know is to ask.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")
from app.services import auto_client                                # noqa: E402
from app.services.auto_client import _request                       # noqa: E402

G, Y, R, D, X = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"

# Taken from resolveflow-supervisor-export.json, steps[].subworkflow_call.
REQUIRED = [
    ("RF-01 SLA Rescue Coordinator", "019f7903-ab7c-7000-9726-0c2854c04d7b"),
    ("RF-02 Evidence Investigator",  "019f7965-4917-7000-85dd-47b9163d45e8"),
    ("RF-03 Resolution Specialist",  "019f79d0-b179-7000-b06f-4db4dec8040f"),
    ("RF-04 Customer Liaison",       "019f7a01-d801-7000-bc84-8701a6cb2e11"),
]
SUPERVISOR = "019f7a48-8189-7000-80e8-27b369dfc62a"


def probe(wid: str):
    """Ask Auto about one workflow by id. Returns (found, name_or_reason)."""
    try:
        payload, _ = _request("GET", f"/api/v1/workflows/{wid}", retries=1)
    except Exception as exc:
        text = str(exc)
        # A 404 is the answer, not an error: the workflow is gone.
        if "404" in text or "not found" in text.lower():
            return False, "no such workflow"
        return None, text[:90]
    body = payload.get("workflow", payload) if isinstance(payload, dict) else {}
    return True, (body.get("name") if isinstance(body, dict) else None) or "(unnamed)"


def main() -> None:
    print()
    print("=" * 74)
    print("  DOES THE SUPERVISOR STILL REACH ITS FOUR OPERATORS?")
    print("=" * 74)
    print(f"  {D}Each id is queried directly. A 404 means the workflow no longer")
    print(f"  exists under that id — which is what re-importing an Operator does.{X}")
    print()

    found, detail = probe(SUPERVISOR)
    if found is None:
        print(f"  {R}Cannot reach Auto: {detail}{X}")
        print(f"  {D}Reads normally succeed even while execute is down.{X}\n")
        return
    print(f"  {'Supervisor':12s} {G + 'present' + X if found else R + 'NOT FOUND' + X}"
          f"  {D}{detail}{X}\n")

    missing = []
    for name, wid in REQUIRED:
        ok, detail = probe(wid)
        if ok:
            print(f"  {G}reachable{X}  {name:34s} {D}{detail}{X}")
        elif ok is False:
            missing.append(name)
            print(f"  {R}MISSING  {X}  {name:34s} {D}{wid}{X}")
        else:
            print(f"  {Y}unclear  {X}  {name:34s} {D}{detail}{X}")

    print()
    print("=" * 74)
    if not missing:
        print(f"  {G}All four subworkflows resolve. The chain can run.{X}")
        print(f"  {D}Still true regardless: the RF-03 it calls is the Round 1")
        print(f"  Operator, which does not ask the policy gate.{X}")
    else:
        print(f"  {R}{len(missing)} of 4 subworkflows are gone: {', '.join(missing)}.{X}")
        print(f"  {D}The Supervisor fails at the first missing step. Nothing to fix")
        print(f"  before Sunday — present the Command Center as the orchestrator.{X}")
    print("=" * 74)

    # The name/id table is useful for pasting run ids into the right Operator
    # tomorrow, so print it — but it is not what the verdict above rests on.
    try:
        payload, latency = auto_client.list_workflows(limit=100)
    except Exception as exc:
        print(f"\n  {D}(could not also list all workflows: {str(exc)[:70]}){X}\n")
        return
    items = payload.get("workflows") if isinstance(payload, dict) else payload
    print(f"\n  WORKFLOWS ON AUTO — {len(items or [])} in {latency:.0f} ms")
    for w in sorted(items or [], key=lambda w: (w.get("name") or "").lower()):
        wid = w.get("id") or w.get("workflowId") or w.get("workflow_id")
        print(f"    {(w.get('name') or '(unnamed)'):44s} {D}{wid}{X}")
    print()


if __name__ == "__main__":
    main()
