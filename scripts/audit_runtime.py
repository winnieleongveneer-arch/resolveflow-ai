#!/usr/bin/env python3
"""
Runtime evidence collector — Phases 1, 3, 6 and 8 of the audit.

    docker compose exec backend python /app/scripts/audit_runtime.py

READ ONLY. It writes nothing, changes nothing, and deletes nothing. Every number
it prints is queried live from PostgreSQL or Supabase at the moment it runs, so
the output is evidence rather than assertion.

Sections:
  A  service and migration state
  B  Supabase row counts vs the committed workbook  (Phase 3)
  C  source tickets vs open agent cases             (Phase 6)
  D  the tested tickets, reconstructed from records (Phase 8)
  E  integration health, as stored
  F  policies and versions
  G  human-loop history
"""
from __future__ import annotations

import csv, os, sys, json, collections
from pathlib import Path

import httpx
from sqlalchemy import func, text

sys.path.insert(0, "/app")
from app.core.database import SessionLocal                      # noqa: E402
from app.models.service_desk import (                           # noqa: E402
    WorkflowRun, OperatorEvent, WorkbenchItem, PolicyDefinition,
    PolicyEvaluation, IntegrationHealth, RunStatus, WorkbenchStatus,
)

DATA = Path(os.getenv("ROUND2_DATA_DIR", "/app/data/round2"))
SB = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}

TESTED = ["ITSM-2230", "ITSM-2231", "ITSM-2180"]
PAIRS = [
    ("Issues.csv", "issues", "issue_key"),
    ("Knowledge_Base.csv", "knowledge_base", "article_id"),
    ("Ticket_Comments.csv", "ticket_comments", "comment_id"),
    ("CSAT_Surveys.csv", "csat_surveys", "survey_id"),
    ("Change_Requests.csv", "change_requests", "change_id"),
    ("Incident_Problem_Links.csv", "incident_problem_links", "link_id"),
    ("Users_Directory.csv", "users_directory", "account_id"),
    ("Assets_Access.csv", "assets_access", "object_key"),
    ("SLA_Calendar.csv", "sla_calendar", None),
    ("Team_Roster.csv", "team_roster", None),
]


def rule(t):
    print("\n" + "=" * 76); print(t); print("=" * 76)


def sb_count(client, table):
    r = client.get(f"{SB}/rest/v1/{table}", params={"select": "*", "limit": "1"},
                   headers={**H, "Prefer": "count=exact"})
    # PostgREST answers a counted, limited read with 206 Partial Content, not 200.
    # Treating 206 as failure made every table read UNREACHABLE on the first run.
    if r.status_code not in (200, 206):
        return None
    cr = r.headers.get("content-range", "")
    return int(cr.split("/")[-1]) if "/" in cr else None


def sb_all(client, table, select="*"):
    out, step, off = [], 1000, 0
    while True:
        r = client.get(f"{SB}/rest/v1/{table}",
                       params={"select": select, "limit": str(step), "offset": str(off)},
                       headers=H)
        if r.status_code not in (200, 206):
            return out
        page = r.json()
        out += page
        if len(page) < step:
            return out
        off += step


db = SessionLocal()
client = httpx.Client(timeout=60.0)

# ---------------------------------------------------------------- A
rule("A. SERVICE AND MIGRATION STATE")
print(f"  Postgres         {'reachable' if db.execute(text('select 1')).scalar()==1 else 'UNREACHABLE'}")
head = db.execute(text("select version_num from alembic_version")).scalar()
print(f"  Alembic head     {head}")
print(f"  Supabase URL     {'configured' if SB else 'MISSING'}")
print(f"  Supabase key     {'configured' if KEY else 'MISSING'}")
for var in ("SLACK_WEBHOOK_URL", "SUPERVITY_WORKFLOW_API_KEY", "PUBLIC_BACKEND_URL"):
    v = os.getenv(var, "")
    print(f"  {var:28s} {'configured' if v else 'MISSING'}"
          + (f"  ({v})" if var == "PUBLIC_BACKEND_URL" and v else ""))
print(f"  AUTO_TRIGGER_ON_RUN          {os.getenv('AUTO_TRIGGER_ON_RUN','(unset)')}")

# ---------------------------------------------------------------- B
rule("B. WORKBOOK vs SUPABASE  (Phase 3)")
print(f"  {'workbook file':28s} {'csv':>6s} {'uniq':>6s} {'supabase':>9s} {'diff':>6s}  status")
for fname, table, pk in PAIRS:
    p = DATA / fname
    if not p.exists():
        print(f"  {fname:28s} DATASET_NOT_PROVIDED"); continue
    rows = list(csv.DictReader(p.open(encoding="utf-8-sig")))
    csv_n = len(rows)
    if pk:
        col = next((c for c in rows[0] if c.strip().lower().replace(" ", "_") == pk), None)
        uniq = len({r[col] for r in rows}) if col else csv_n
    else:
        uniq = csv_n
    sb_n = sb_count(client, table)
    if sb_n is None:
        print(f"  {fname:28s} {csv_n:6d} {uniq:6d} {'UNREACHABLE':>9s}"); continue
    diff = sb_n - uniq
    status = "match" if diff == 0 else ("EXTRA in Supabase" if diff > 0 else "MISSING from Supabase")
    if pk and uniq != csv_n:
        status += f"  ({csv_n-uniq} duplicate row(s) in the workbook, collapsed by upsert)"
    print(f"  {fname:28s} {csv_n:6d} {uniq:6d} {sb_n:9d} {diff:+6d}  {status}")

# ---------------------------------------------------------------- C
rule("C. SOURCE TICKETS vs OPEN AGENT CASES  (Phase 6)")
issues = sb_all(client, "issues", "issue_key,status")
source_total = len(issues)
resolved = sum(1 for r in issues if str(r.get("status") or "").strip().lower()
               in ("resolved", "closed", "done"))
source_open = source_total - resolved
print(f"  SOURCE TICKETS   total in Supabase issues        {source_total}")
print(f"                   resolved/closed                 {resolved}")
print(f"                   open backlog                    {source_open}")

TERMINAL = {RunStatus.RESOLVED, RunStatus.DENIED, RunStatus.FAILED}
runs = db.query(WorkflowRun).all()
by_issue = collections.defaultdict(list)
for r in runs:
    by_issue[r.issue_key].append(r)
open_cases = [k for k, rs in by_issue.items()
              if not any(r.status in TERMINAL for r in rs)]
print(f"\n  AGENT CASES      workflow_run rows (all attempts) {len(runs)}")
print(f"                   distinct issue keys              {len(by_issue)}")
print(f"                   OPEN AGENT CASES (unique, not terminal) {len(open_cases)}")
print(f"\n  Proposed card:   OPEN AGENT CASES  {len(open_cases)} of {source_total} source tickets")
print("\n  run status histogram (why the two numbers differ):")
for s, n in collections.Counter(r.status for r in runs).most_common():
    print(f"     {str(s):22s} {n:5d}")

# ---------------------------------------------------------------- D
rule("D. TESTED TICKETS — reconstructed from stored records  (Phase 8)")
for key in TESTED:
    rs = sorted(by_issue.get(key, []), key=lambda r: r.started_at or 0)
    print(f"\n  ---- {key}  ({len(rs)} attempt(s)) ----")
    if not rs:
        print("     no runs recorded"); continue
    for i, r in enumerate(rs, 1):
        evs = db.query(OperatorEvent).filter(OperatorEvent.run_id == r.id)\
                .order_by(OperatorEvent.event_timestamp).all()
        evals = db.query(PolicyEvaluation).filter(PolicyEvaluation.run_id == r.id).all()
        items = db.query(WorkbenchItem).filter(WorkbenchItem.run_id == r.id).all()
        ops = sorted({e.operator_name for e in evs} - {"ORCHESTRATOR", "POLICY_ENGINE"})
        print(f"     attempt {i}  run={str(r.id)[:8]}  auto_run_id={r.auto_run_id or 'NONE'}")
        print(f"        status={r.status}  started={r.started_at}  completed={r.completed_at}")
        print(f"        operators={ops or 'none recorded'}")
        for ev in evals:
            print(f"        policy: {ev.policy_key} v{ev.policy_version} -> {ev.verdict}")
        for it in items:
            print(f"        workbench {str(it.id)[:8]} {it.request_type} status={it.status} "
                  f"decision={it.human_decision} by={it.reviewer} at={it.decided_at}")
        print(f"        events: {[e.event_type for e in evs]}")
        if r.error_message:
            print(f"        error: {r.error_message[:90]}")

# ---------------------------------------------------------------- E
rule("E. INTEGRATION HEALTH — as stored")
for row in db.query(IntegrationHealth).order_by(IntegrationHealth.category).all():
    print(f"  {row.integration_key:16s} {row.category:16s} {str(row.status):10s} "
          f"reads={row.last_successful_read} writes={row.last_successful_write} "
          f"records={row.records_processed}")
    if row.latest_error:
        print(f"       last error: {row.latest_error[:100]}")

# ---------------------------------------------------------------- F
rule("F. POLICIES")
for p in db.query(PolicyDefinition).all():
    n = db.query(func.count(PolicyEvaluation.id)).filter(
        PolicyEvaluation.policy_key == p.policy_key).scalar()
    print(f"  {p.policy_key:28s} v{p.active_version}  evaluations={n}")
    print(f"     config: {json.dumps(p.configuration)[:120]}")

# ---------------------------------------------------------------- G
rule("G. HUMAN-LOOP HISTORY")
items = db.query(WorkbenchItem).order_by(WorkbenchItem.created_at.desc()).all()
pending = [i for i in items if i.status == WorkbenchStatus.PENDING]
decided = [i for i in items if i.human_decision]
print(f"  workbench items total {len(items)} | pending now {len(pending)} | decided {len(decided)}")
for it in decided[:6]:
    print(f"     {it.issue_key:12s} {str(it.id)[:8]} decision={it.human_decision} "
          f"by={it.reviewer} at={it.decided_at} notify={it.notification_ref}")

db.close(); client.close()
print("\nDone. Read-only: nothing was created, modified or deleted.")
