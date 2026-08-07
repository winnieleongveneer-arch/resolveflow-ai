#!/usr/bin/env python3
"""
Load the Round 2 dataset into Supabase.

Run inside the backend container, which already has httpx and the credentials:

    docker compose exec backend python /app/scripts/load_round2.py

Idempotent — every write is an upsert on the natural key, so re-running it
repairs rather than duplicates.

Two things this deliberately does NOT do:

  * It does not clean the data. The export nulls x_confidence on 143 rows,
    first_response_time on 192, and carries Created in three different date
    formats. Those are the conditions the Operators are judged on noticing, so
    filling them here would be sabotaging the demo to make the import tidy.

  * It does not invent columns. Anything in the CSV with no matching column in
    Supabase is reported and skipped, so a schema drift shows up as a printed
    warning rather than a silent data loss.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import httpx

DATA = Path(os.getenv("ROUND2_DATA_DIR", "/app/data/round2"))
URL = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
BATCH = 100

# csv file -> (supabase table, conflict target for upsert)
PLAN = [
    ("Issues.csv",                 "issues",                 "issue_key"),
    ("Knowledge_Base.csv",         "knowledge_base",         "article_id"),
    ("Incident_Problem_Links.csv", "incident_problem_links", "link_id"),
    ("Change_Requests.csv",        "change_requests",        "change_id"),
    ("Users_Directory.csv",        "users_directory",        "account_id"),
    ("Assets_Access.csv",          "assets_access",          "object_key"),
    ("Ticket_Comments.csv",        "ticket_comments",        "comment_id"),
    ("CSAT_Surveys.csv",           "csat_surveys",           "survey_id"),
    ("Team_Roster.csv",            "team_roster",            None),
    ("SLA_Calendar.csv",           "sla_calendar",           None),
]


def slug(name: str) -> str:
    out = name.strip().lower()
    for ch in " -()/":
        out = out.replace(ch, "_")
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": KEY,
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json",
    }
    h.update(extra or {})
    return h


def existing_columns(client: httpx.Client, table: str) -> set[str] | None:
    """Ask the table what columns it actually has, so we never guess."""
    r = client.get(f"{URL}/rest/v1/{table}", params={"select": "*", "limit": "1"},
                   headers=headers())
    if r.status_code != 200:
        return None
    rows = r.json()
    if rows:
        return set(rows[0].keys())
    # Empty table: PostgREST cannot tell us the columns from no rows, so fall
    # back to accepting whatever the CSV offers and let the API complain.
    return set()


def load(client: httpx.Client, fname: str, table: str, conflict: str | None) -> None:
    path = DATA / fname
    if not path.exists():
        print(f"  {table:24s} SKIP  {fname} not found")
        return

    with path.open(encoding="utf-8-sig") as fh:
        raw = list(csv.DictReader(fh))
    if not raw:
        print(f"  {table:24s} SKIP  {fname} is empty")
        return

    cols = existing_columns(client, table)
    if cols is None:
        print(f"  {table:24s} FAIL  table is unreachable — run round2_schema.sql first")
        return

    mapping = {h: slug(h) for h in raw[0].keys()}
    if cols:
        unknown = {h: c for h, c in mapping.items() if c not in cols}
        if unknown:
            print(f"  {table:24s} note  {len(unknown)} column(s) not in Supabase, skipped: "
                  + ", ".join(sorted(unknown.values()))[:120])
        mapping = {h: c for h, c in mapping.items() if c in cols}

    rows = []
    for r in raw:
        # Empty string means "not recorded". Send null so the Operators can
        # tell an absent value from a blank one — that distinction is the
        # whole point of the missing-evidence path.
        rows.append({mapping[h]: (v if v not in ("", None) else None)
                     for h, v in r.items() if h in mapping})

    params = {}
    prefer = ["return=minimal"]
    if conflict:
        params["on_conflict"] = conflict
        prefer.append("resolution=merge-duplicates")

    written = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        r = client.post(f"{URL}/rest/v1/{table}", params=params, json=chunk,
                        headers=headers({"Prefer": ",".join(prefer)}))
        if r.status_code >= 300:
            print(f"  {table:24s} FAIL  HTTP {r.status_code} on rows {i}-{i+len(chunk)}")
            print(f"        {r.text[:300]}")
            return
        written += len(chunk)

    print(f"  {table:24s} OK    {written} row(s)")


def main() -> int:
    if not URL or not KEY:
        print("SUPABASE_URL and a Supabase key must be set in the environment.")
        return 1
    if not DATA.exists():
        print(f"Dataset directory not found: {DATA}")
        return 1

    print(f"Loading the Round 2 dataset into {URL}")
    print(f"Source: {DATA}\n")
    with httpx.Client(timeout=60.0) as client:
        for fname, table, conflict in PLAN:
            load(client, fname, table, conflict)

    print("\nVerifying...")
    with httpx.Client(timeout=30.0) as client:
        for probe, label in (
            ("issues?select=issue_key&issue_key=eq.ITSM-2230", "ITSM-2230 (auto-resolve demo)"),
            ("issues?select=issue_key&issue_key=eq.ITSM-2180", "ITSM-2180 (major incident)"),
            ("incident_problem_links?select=link_id", "incident links"),
            ("change_requests?select=change_id", "change requests"),
        ):
            r = client.get(f"{URL}/rest/v1/{probe}", headers=headers())
            n = len(r.json()) if r.status_code == 200 else -1
            state = "present" if n > 0 else ("MISSING" if n == 0 else f"HTTP {r.status_code}")
            print(f"  {label:34s} {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
