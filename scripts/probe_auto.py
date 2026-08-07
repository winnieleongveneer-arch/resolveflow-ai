#!/usr/bin/env python3
"""
probe_auto.py — two questions the workflow list left open.

    docker compose exec backend python /app/scripts/probe_auto.py

READ ONLY. Asks Auto about version history and about other organisations the
key can see. Executes nothing.

Q1  Which RF-03 does workflow 019f79d0 actually run today? If the R2 import
    landed as a new VERSION of that workflow, the Supervisor calling it by id
    is calling the policy-gated Operator, not the Round 1 one.

Q2  Where are RF-05 and RF-06? They report events into the Command Center, so
    they exist. If the workflow list cannot see them, the list is scoped to one
    organisation and they live in another.
"""
from __future__ import annotations

import json, sys

sys.path.insert(0, "/app")
from app.services import auto_client                                # noqa: E402
from app.services.auto_client import _request                       # noqa: E402

G, Y, R, D, X = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"
WATCH = {
    "019f79d0-b179-7000-b06f-4db4dec8040f": "RF-03 Resolution Specialist",
    "019f7903-ab7c-7000-9726-0c2854c04d7b": "RF-01 SLA Rescue Coordinator",
    "019f7a48-8189-7000-80e8-27b369dfc62a": "ResolveFlow Supervisor",
}
# A policy-gated Operator reaches back to the Command Center. That string is the
# fingerprint — if a version body contains it, that version asks permission.
GATE_MARKS = ("command_center_url", "/api/agent/", "policy-gate", "policy_gate")


def q1() -> None:
    print("=" * 74)
    print("  Q1. VERSION HISTORY — is the ticket-driven RF-03 a version of 019f79d0?")
    print("=" * 74)
    for wid, label in WATCH.items():
        print(f"\n  {label}  {D}{wid}{X}")
        try:
            payload, _ = _request("GET", f"/api/v1/workflows/{wid}/versions", retries=1)
        except Exception as exc:
            print(f"     {R}versions unavailable: {str(exc)[:100]}{X}")
            continue
        versions = payload.get("versions", payload) if isinstance(payload, dict) else payload
        if not isinstance(versions, list):
            print(f"     {Y}unexpected shape: {str(payload)[:120]}{X}")
            continue
        for v in versions:
            num = v.get("versionNumber", v.get("version", "?"))
            msg = (v.get("commitMessage") or "").strip()[:54]
            blob = json.dumps(v)
            gated = any(m in blob for m in GATE_MARKS)
            inputs = v.get("definition", {}).get("inputs") or []
            names = ",".join(i.get("name", "") for i in inputs) or "(none)"
            flag = f"{G}asks the gate{X}" if gated else f"{D}no gate call{X}"
            print(f"     v{num:<3} {flag:28s} inputs: {names[:52]}")
            if msg:
                print(f"          {D}{msg}{X}")
        print(f"     {D}The Supervisor invokes whichever version is DEFAULT."
              f" Check that in the Auto UI.{X}")


def q2() -> None:
    print("\n" + "=" * 74)
    print("  Q2. WHERE ARE RF-05 AND RF-06?")
    print("=" * 74)
    try:
        payload, _ = auto_client.list_workflows(limit=100)
        items = payload.get("workflows") if isinstance(payload, dict) else payload
        print(f"  default organisation sees {len(items or [])} workflow(s)")
    except Exception as exc:
        print(f"  {R}{str(exc)[:120]}{X}")

    print(f"  configured org key: {auto_client.org_key() or D + '(none set)' + X}")
    try:
        report = auto_client.diagnose_auth("/api/v1/workflows")
        print(f"  auth probe: {json.dumps(report)[:300]}")
    except Exception as exc:
        print(f"  {D}auth probe unavailable: {type(exc).__name__}{X}")

    print(f"\n  {D}If RF-05 and RF-06 are not listed anywhere, they still work —")
    print(f"  they reported events today. It means the API key's active org does")
    print(f"  not include them, which affects listing only, not the Auto UI.{X}")


if __name__ == "__main__":
    print()
    q1()
    q2()
    print()
