#!/usr/bin/env python3
"""
demo_smoke.py — exercise every surface the demo touches, in demo order.

    docker compose exec backend python /app/scripts/demo_smoke.py
    docker compose exec backend python /app/scripts/demo_smoke.py --live

Default mode is READ ONLY: it creates nothing, changes nothing, and sends
nothing. --live additionally opens one real case, which is exactly what
new-run.ps1 does during the demo, so the write path is covered too.

The point is not to prove the code is correct - the tests do that. It is to
prove the specific paths a judge will drive still answer, in the order they
will be driven, against the data that is loaded right now. A route that works
in isolation and 500s when the page renders it is the failure this catches.
"""
from __future__ import annotations

import argparse, json, os, sys, time

import httpx

BACKEND = "http://localhost:8000"
FRONTEND = "http://frontend:3000"
PUBLIC = os.getenv("PUBLIC_BACKEND_URL", "").rstrip("/")
H = {"ngrok-skip-browser-warning": "1"}

G, Y, R, D, X = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"
results: list[tuple[str, bool, str]] = []


def check(label: str, fn) -> None:
    started = time.perf_counter()
    try:
        detail = fn()
        ok = True
    except AssertionError as exc:
        detail, ok = str(exc), False
    except Exception as exc:
        detail, ok = f"{type(exc).__name__}: {exc}", False
    ms = (time.perf_counter() - started) * 1000
    results.append((label, ok, detail))
    mark = f"{G}ok  {X}" if ok else f"{R}FAIL{X}"
    print(f"  {mark} {label:52s} {D}{ms:6.0f}ms{X}  {detail}")


def get(url, expect=200, headers=None):
    r = httpx.get(url, timeout=25.0, headers=headers or H)
    assert r.status_code == expect, f"HTTP {r.status_code} {r.text[:90]}"
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="also open one real case, as new-run.ps1 does")
    live = ap.parse_args().live

    print("\n" + "=" * 78)
    print("  BEAT 0 - the stack answers at all")
    print("=" * 78)
    check("backend /api/health", lambda: get(f"{BACKEND}/api/health").text[:40])
    check("frontend renders", lambda: f"{len(get(FRONTEND).text)} bytes")
    if PUBLIC:
        check("public URL judges use", lambda: get(f"{PUBLIC}/api/health").text[:40])
    else:
        results.append(("public URL judges use", False, "PUBLIC_BACKEND_URL unset"))
        print(f"  {R}FAIL{X} public URL judges use — PUBLIC_BACKEND_URL is not set")

    print("\n" + "=" * 78)
    print("  BEAT 1 - the Dashboard")
    print("=" * 78)

    def summary():
        s = get(f"{BACKEND}/api/agent/summary").json()
        assert s.get("source_tickets"), "no source ticket count - Supabase unreachable?"
        assert s.get("reconciles") is not False, "tiles do not reconcile with run statuses"
        return (f"{s['open_agent_cases']} open of {s['source_tickets']} · "
                f"{s['verified_resolved']} verified · {s['auto_resolution_rate']}%")
    check("summary tiles reconcile", summary)
    check("activity feed (agent runs only)",
          lambda: f"{len(get(f'{BACKEND}/api/agent/runs?agent_only=true&limit=10').json())} runs")

    print("\n" + "=" * 78)
    print("  BEAT 3/4 - a case, its Passport, and the human loop")
    print("=" * 78)

    runs = get(f"{BACKEND}/api/agent/runs?limit=50").json()
    assert runs, "no runs exist at all"
    terminal = [r for r in runs if r.get("status") in ("RESOLVED", "DENIED")]
    sample = (terminal or runs)[0]
    rid = sample["id"]

    check(f"run detail  {sample['issue_key']}",
          lambda: get(f"{BACKEND}/api/agent/runs/{rid}").json()["status"])

    def passport():
        p = get(f"{BACKEND}/api/agent/runs/{rid}/passport").json()
        assert p, "empty passport"
        blob = json.dumps(p)
        assert "policy" in blob.lower(), "passport names no policy"
        return f"{len(blob)} bytes, renders"
    check("Decision Passport (the thing judges read)", passport)

    check("events on that run",
          lambda: ", ".join(e["event_type"] for e in
                            get(f"{BACKEND}/api/agent/runs/{rid}/events").json()[:4]) or "none")

    def workbench():
        items = get(f"{BACKEND}/api/workbench").json()
        pending = [i for i in items if i.get("status") == "PENDING"]
        assert pending, "nothing pending - the 'nothing auto-approves' point has no exhibit"
        return f"{len(pending)} pending of {len(items)}"
    check("Workbench has a live pending item", workbench)

    print("\n" + "=" * 78)
    print("  BEAT 5 - the policy dial, and what backs it")
    print("=" * 78)

    def policies():
        ps = get(f"{BACKEND}/api/ai/policies").json()
        assert len(ps) >= 3, f"only {len(ps)} policies"
        sar = next((p for p in ps if p["policy_key"] == "safe_auto_remediation"), None)
        assert sar, "safe_auto_remediation missing"
        conf = (sar.get("configuration") or {}).get("minimum_confidence")
        assert conf == 0.85, f"minimum_confidence is {conf}, expected 0.85 for the demo"
        return f"{len(ps)} policies · safe_auto_remediation v{sar.get('active_version')} at {conf}"
    check("policies load and the dial is at 0.85", policies)

    check("integration health",
          lambda: ", ".join(f"{i['integration_key']}={i['status']}"
                            for i in get(f"{BACKEND}/api/integrations").json()))
    check("AI insights render",
          lambda: f"{len(get(f'{BACKEND}/api/ai/insights').json())} insights")

    print("\n" + "=" * 78)
    print("  FRONTEND PAGES - every tab you will open")
    print("=" * 78)
    for path, label in (("/", "Dashboard"), ("/workbench", "Workbench"),
                        ("/ai/policies", "AI Policies"), ("/ai/insights", "AI Insights"),
                        ("/data-manager", "Data Manager"), (f"/runs/{rid}", "Passport page")):
        check(f"page {label}", lambda p=path: f"{len(get(FRONTEND + p).text)} bytes")

    if live:
        print("\n" + "=" * 78)
        print("  WRITE PATH - opening one real case, as new-run.ps1 does")
        print("=" * 78)

        def open_case():
            r = httpx.post(f"{BACKEND}/api/agent/runs", timeout=30.0,
                           json={"issue_key": "ITSM-2231", "trigger_source": "smoke_test"})
            assert r.status_code in (200, 201), f"HTTP {r.status_code} {r.text[:120]}"
            body = r.json()
            assert body.get("id"), "no run id returned"
            return f"run {body['id'][:8]} created for {body['issue_key']}"
        check("POST /api/agent/runs", open_case)

    print("\n" + "=" * 78)
    failed = [(l, d) for l, ok, d in results if not ok]
    print(f"  {len(results) - len(failed)} ok · {len(failed)} failed")
    if failed:
        print(f"\n  {R}These would break in front of a judge:{X}")
        for label, detail in failed:
            print(f"    - {label}: {detail}")
    else:
        print(f"  {G}Every surface the demo touches answered.{X}")
    print("=" * 78 + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
