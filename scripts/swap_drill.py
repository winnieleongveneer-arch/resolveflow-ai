#!/usr/bin/env python3
"""
swap_drill.py — prove the build is not tuned to a handful of chosen rows.

    docker compose exec backend python /app/scripts/swap_drill.py --scan
    docker compose exec backend python /app/scripts/swap_drill.py --pick 3 --seed 1742
    docker compose exec backend python /app/scripts/swap_drill.py --report

Three modes, each answering a different judge question.

  --scan    "Is your logic hardcoded to specific tickets?"
            Greps the shipped Python for literal ticket keys and classifies
            every hit. A key inside an example, a docstring or a debug endpoint
            cannot change a verdict; a key inside a branch can. The scan says
            which is which instead of asserting that the code is clean.

  --pick    "Run one I choose."
            Draws tickets uniformly from the WHOLE issues table using a seed the
            judge names out loud, so the sample is visibly not curated. Creates
            a run for each and prints the ids to paste into Auto.

  --report  Reads back what those runs actually decided. It reports refusals and
            escalations as results, not as failures — on this dataset most
            tickets have no auto-safe article and DENY is the correct answer.

READ ONLY except for --pick, which creates workflow runs exactly as the demo
does. Nothing here can be made to print a better answer than the records hold.
"""
from __future__ import annotations

import argparse, json, os, random, re, sys, collections
from pathlib import Path

import httpx

sys.path.insert(0, "/app")
from app.core.database import SessionLocal                        # noqa: E402
from app.models.service_desk import (                             # noqa: E402
    WorkflowRun, OperatorEvent, WorkbenchItem, PolicyEvaluation,
)

SB = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
BACKEND = "http://localhost:8000"
STATE = Path("/app/data/swap_drill.json")

G, Y, R, D, X = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"


def rule(t):
    print("\n" + "=" * 74); print("  " + t); print("=" * 74)


# --------------------------------------------------------------------- scan
# A literal ticket key is only a problem where it can steer a decision. These
# are the places it demonstrably cannot.
BENIGN = (
    (re.compile(r"examples\s*=\s*\["),      "schema example"),
    (re.compile(r"^\s*#"),                  "comment"),
    (re.compile(r'^\s*"""'),                "docstring"),
    (re.compile(r'"Why is |"Which policy '), "AI assistant suggestion text"),
    (re.compile(r"def \w*(test|matrix|smoke)\w*\("), "debug endpoint default"),
    (re.compile(r"issue_key: str = "),      "debug endpoint default"),
)


def scan():
    rule("A. LITERAL TICKET KEYS IN SHIPPED PYTHON")
    # Only the shipped application counts. An audit script naming the tickets it
    # audits, or a loader verifying a row it just wrote, is tooling ABOUT the
    # build — flagging it red would teach a reader to ignore the scan.
    root = Path("/app/app")
    hits, steering = [], []
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            for key in re.findall(r"ITSM-\d+|KB-\d+", line):
                why = next((label for rx, label in BENIGN if rx.search(line)), None)
                hits.append((str(p), n, key, why, line.strip()[:70]))
                if why is None:
                    steering.append((str(p), n, key, line.strip()[:70]))
    if not hits:
        print(f"  {G}none{X} — no literal ticket key appears anywhere in the shipped Python")
    for path, n, key, why, line in hits:
        tag = f"{G}{why}{X}" if why else f"{R}UNCLASSIFIED — read this line{X}"
        print(f"  {path.replace('/app/','')}:{n}  {key}  [{tag}]")
        print(f"      {D}{line}{X}")
    print()
    if steering:
        print(f"  {R}{len(steering)} line(s) in the shipped application could not be "
              f"classified as harmless. Read them before claiming nothing is hardcoded.{X}")
    else:
        print(f"  {G}Every occurrence is an example, a comment or a debug default.{X}")
        print(f"  {D}No branch, threshold or verdict depends on a ticket number.{X}")
    print(f"\n  {D}Scope: app/ only. scripts/ holds audit and loader tooling, which names")
    print(f"  specific tickets on purpose — that is tooling about the build, not the")
    print(f"  build keying on rows. Run --scan-tools to see those lines too.{X}")

    rule("B. WHERE THE NUMBERS THAT MATTER COME FROM")
    from app.models.service_desk import PolicyDefinition
    db = SessionLocal()
    for p in db.query(PolicyDefinition).all():
        print(f"  {p.policy_key:30s} v{p.active_version}")
        for k, v in (p.configuration or {}).items():
            print(f"      {D}{k} = {v}{X}")
    db.close()
    print(f"\n  {D}These are rows in the database, editable from the AI Policies page.")
    print(f"  Swap the dataset and they still apply; change one and the next run changes.{X}")


# --------------------------------------------------------------------- pick
def sb_issue_keys(client):
    out, step, off = [], 1000, 0
    while True:
        r = client.get(f"{SB}/rest/v1/issues",
                       params={"select": "issue_key", "limit": str(step), "offset": str(off)},
                       headers=H)
        if r.status_code not in (200, 206):
            return out
        page = r.json()
        out += [row["issue_key"] for row in page]
        if len(page) < step:
            return sorted(out)
        off += step


def pick(n, seed):
    client = httpx.Client(timeout=60.0)
    keys = sb_issue_keys(client)
    if not keys:
        print(f"{R}  Supabase returned no issue keys — check the connection first.{X}")
        return
    rule(f"DRAWING {n} TICKET(S) FROM ALL {len(keys)} — SEED {seed}")
    print(f"  {D}Uniform over the whole table. Same seed, same tickets — a judge")
    print(f"  can name a number and reproduce this draw themselves.{X}\n")
    chosen = random.Random(seed).sample(keys, min(n, len(keys)))
    created = []
    for key in chosen:
        try:
            r = httpx.post(f"{BACKEND}/api/agent/runs", timeout=30.0,
                           json={"issue_key": key, "trigger_source": "swap_drill"})
            run = r.json()
            created.append({"issue_key": key, "run_id": run.get("id")})
            print(f"  {key:14s} run {G}{run.get('id')}{X}")
            print(f"  {'':14s} {D}passport  http://localhost:3001/runs/{run.get('id')}{X}")
        except Exception as exc:
            print(f"  {key:14s} {R}could not create a run: {type(exc).__name__}{X}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"seed": seed, "runs": created}, indent=2))
    print(f"\n  {D}Paste each run id into the Operator on Auto, then:{X}")
    print(f"  docker compose exec backend python /app/scripts/swap_drill.py --report")
    client.close()


# ------------------------------------------------------------------- report
def report():
    if not STATE.exists():
        print(f"{R}  No drill on record. Run --pick first.{X}")
        return
    state = json.loads(STATE.read_text())
    rule(f"WHAT THE DRILL RUNS DECIDED  (seed {state['seed']})")
    db = SessionLocal()
    verdicts = collections.Counter()
    for entry in state["runs"]:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == entry["run_id"]).first()
        if not run:
            print(f"  {entry['issue_key']}  {R}run row missing{X}"); continue
        evs = db.query(OperatorEvent).filter(OperatorEvent.run_id == run.id)\
                .order_by(OperatorEvent.event_timestamp).all()
        evals = db.query(PolicyEvaluation).filter(PolicyEvaluation.run_id == run.id).all()
        items = db.query(WorkbenchItem).filter(WorkbenchItem.run_id == run.id).all()
        ops = sorted({e.operator_name for e in evs} - {"ORCHESTRATOR", "POLICY_ENGINE"})
        print(f"\n  {entry['issue_key']}   run {str(run.id)[:8]}   status {run.status}")
        print(f"      operators   {', '.join(ops) or D + 'none reported yet — trigger it on Auto' + X}")
        for ev in evals:
            verdicts[str(ev.verdict)] += 1
            print(f"      policy      {ev.policy_key} v{ev.policy_version} -> {ev.verdict}")
        for it in items:
            print(f"      workbench   {it.request_type} {it.status} "
                  f"decision={it.human_decision or D + 'awaiting a person' + X}")
        if not evals and not evs:
            print(f"      {D}nothing recorded yet{X}")
    print()
    rule("READ THIS OUT")
    if verdicts:
        parts = ", ".join(f"{n} {v.split('.')[-1]}" for v, n in verdicts.most_common())
        print(f"  {parts}.")
    print(f"  {D}A refusal is an outcome, not a failure. On this backlog most tickets")
    print(f"  have no article marked auto-safe, so DENY is the correct answer and")
    print(f"  the Passport names the condition that was not met.{X}")
    db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--scan-tools", action="store_true")
    ap.add_argument("--pick", type=int, metavar="N")
    ap.add_argument("--seed", type=int, default=1742)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.scan:
        scan()
    elif a.scan_tools:
        rule("TICKET KEYS IN AUDIT / LOADER TOOLING  (scripts/)")
        print(f"  {D}Shown for completeness. None of this runs during a demo.{X}\n")
        for f in sorted(Path("/app/scripts").rglob("*.py")):
            for n, line in enumerate(f.read_text(encoding="utf-8",
                                                 errors="ignore").splitlines(), 1):
                if re.search(r"ITSM-\d+|KB-\d+", line):
                    print(f"  {f.name}:{n}  {D}{line.strip()[:80]}{X}")
    elif a.pick:
        pick(a.pick, a.seed)
    elif a.report:
        report()
    else:
        ap.print_help()
    print()
