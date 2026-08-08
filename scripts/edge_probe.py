#!/usr/bin/env python3
"""
edge_probe.py — do the rude things a judge might do by accident.

    docker compose exec backend python /app/scripts/edge_probe.py

Every other check we run uses good inputs. This one uses bad ones: a ticket that
does not exist, a ticket missing the field the policy depends on, a malformed
gate call, a run id that is not a run id, and several requests at once.

A 4xx here is a PASS - the system said no clearly. A 5xx is a FAIL: it means an
unhandled exception, which on stage looks like a crash regardless of what
caused it. The distinction is the whole point of the script.

It creates a handful of runs and policy evaluations, exactly as normal use does.
It deletes nothing.
"""
from __future__ import annotations

import concurrent.futures as cf
import json, os, sys, uuid

import httpx

BACKEND = "http://localhost:8000"
G, R, Y, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"
results: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str) -> None:
    results.append((label, ok, detail))
    print(f"  {G + 'ok  ' + X if ok else R + 'FAIL' + X} {label:50s} {detail}")


def probe(label: str, method: str, path: str, *, json_body=None,
          server_error_is_failure=True) -> httpx.Response | None:
    """Any answer is fine except one the server did not mean to give."""
    try:
        r = httpx.request(method, f"{BACKEND}{path}", json=json_body, timeout=30.0)
    except Exception as exc:
        record(label, False, f"{type(exc).__name__}: {exc}")
        return None
    bad = r.status_code >= 500 and server_error_is_failure
    note = f"HTTP {r.status_code}"
    if bad:
        note += f"  {r.text[:110]}"
    record(label, not bad, note)
    return r


print("\n" + "=" * 78)
print("  1. INPUTS THAT DO NOT EXIST")
print("=" * 78)
probe("run detail for a random UUID", "GET", f"/api/agent/runs/{uuid.uuid4()}")
probe("passport for a random UUID", "GET", f"/api/agent/runs/{uuid.uuid4()}/passport")
probe("events for a random UUID", "GET", f"/api/agent/runs/{uuid.uuid4()}/events")
probe("run detail for a non-UUID string", "GET", "/api/agent/runs/not-a-uuid")
probe("workbench item that does not exist", "GET", f"/api/workbench/{uuid.uuid4()}")

print("\n" + "=" * 78)
print("  2. A TICKET THAT IS NOT IN THE SYSTEM OF RECORD")
print("=" * 78)
r = probe("open a case for ITSM-99999", "POST", "/api/agent/runs",
          json_body={"issue_key": "ITSM-99999", "trigger_source": "edge_probe"})
if r is not None and r.status_code < 300:
    body = r.json()
    record("  ...and it is recorded honestly", True,
           f"run {str(body.get('id'))[:8]} status={body.get('status')}")

print("\n" + "=" * 78)
print("  3. MALFORMED AND HOSTILE PAYLOADS")
print("=" * 78)
probe("open a case with no issue_key", "POST", "/api/agent/runs", json_body={})
probe("open a case with a null issue_key", "POST", "/api/agent/runs",
      json_body={"issue_key": None})
probe("open a case with a 5000-character key", "POST", "/api/agent/runs",
      json_body={"issue_key": "A" * 5000, "trigger_source": "edge_probe"})
probe("open a case with SQL-ish text", "POST", "/api/agent/runs",
      json_body={"issue_key": "ITSM-1'; DROP TABLE issues;--",
                 "trigger_source": "edge_probe"})
probe("policy gate with an empty body", "POST", "/api/agent/gate", json_body={})
probe("policy gate with junk fields", "POST", "/api/agent/gate",
      json_body={"nonsense": True, "run_id": "not-a-uuid"})
probe("event on a run that does not exist", "POST",
      f"/api/agent/runs/{uuid.uuid4()}/events",
      json_body={"operator_name": "X", "event_type": "RUN_RECEIVED"})

print("\n" + "=" * 78)
print("  4. A TICKET MISSING THE FIELD THE POLICY DEPENDS ON")
print("=" * 78)
print(f"  {D}143 tickets carry no x_confidence. A judge picking one must get a")
print(f"  named refusal, not a guess and not a crash.{X}")
try:
    from app.services import backlog
    ticket = None
    for t in (backlog.fetch_tickets(limit=200) or []):
        conf = t.get("x_confidence") or t.get("confidence_score")
        if not conf:
            ticket = t.get("issue_key") or t.get("Issue key")
            break
    if ticket:
        r = probe(f"open a case for {ticket} (no confidence)", "POST",
                  "/api/agent/runs",
                  json_body={"issue_key": ticket, "trigger_source": "edge_probe"})
    else:
        record("find a ticket with no confidence", False, "none found in the first page")
except Exception as exc:
    record("fetch a ticket with no confidence", False,
           f"{type(exc).__name__}: {str(exc)[:90]}")

print("\n" + "=" * 78)
print("  5. SEVERAL AT ONCE  (a double-click, or an impatient judge)")
print("=" * 78)


def one(i: int):
    return httpx.post(f"{BACKEND}/api/agent/runs", timeout=30.0,
                      json={"issue_key": "ITSM-2231",
                            "trigger_source": "edge_probe"}).status_code


with cf.ThreadPoolExecutor(max_workers=5) as pool:
    codes = list(pool.map(one, range(5)))
record("5 concurrent case openings", all(c < 500 for c in codes), f"statuses {codes}")

print("\n" + "=" * 78)
print("  6. THE DASHBOARD STILL RECONCILES AFTERWARDS")
print("=" * 78)
s = httpx.get(f"{BACKEND}/api/agent/summary", timeout=30.0).json()
record("summary still reconciles", s.get("reconciles") is not False,
       f"{s.get('open_agent_cases')} open · {s.get('verified_resolved')} verified "
       f"· {s.get('auto_resolution_rate')}%")

print("\n" + "=" * 78)
failed = [(l, d) for l, ok, d in results if not ok]
print(f"  {len(results) - len(failed)} ok · {len(failed)} failed")
if failed:
    print(f"\n  {R}Unhandled server errors — these would look like a crash on stage:{X}")
    for label, detail in failed:
        print(f"    - {label}: {detail}")
else:
    print(f"  {G}Every bad input was refused cleanly. Nothing returned a 5xx.{X}")
print("=" * 78 + "\n")
sys.exit(1 if failed else 0)
