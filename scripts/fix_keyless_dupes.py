#!/usr/bin/env python3
"""
fix_keyless_dupes.py — repair the two tables the loader can duplicate.

    docker compose exec backend python /app/scripts/fix_keyless_dupes.py --check
    docker compose exec backend python /app/scripts/fix_keyless_dupes.py --repair

sla_calendar and team_roster have no natural primary key, so there is no column
for PostgREST to resolve a conflict against. Every run of the loader therefore
INSERTS rather than upserts, and a second run silently doubles them — which is
exactly what happened: 10 rows against 5 in the workbook, 24 against 12.

This is not a change to the dataset. It restores Supabase to what the workbook
says. --check tells you the truth and touches nothing; --repair deletes the
contents of those two tables and reloads them once from the CSVs.
"""
from __future__ import annotations

import argparse, csv, os, sys
from pathlib import Path

import httpx

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")
from load_round2 import load, headers, slug          # noqa: E402  reuse the real loader

DATA = Path(os.getenv("ROUND2_DATA_DIR", "/app/data/round2"))
SB = os.getenv("SUPABASE_URL", "").rstrip("/")
KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}

# (csv file, table, the column used only to select every row for deletion)
TARGETS = [("SLA_Calendar.csv", "sla_calendar"), ("Team_Roster.csv", "team_roster")]

G, Y, R, D, X = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"


def count(client, table):
    r = client.get(f"{SB}/rest/v1/{table}", params={"select": "*", "limit": "1"},
                   headers={**H, "Prefer": "count=exact"})
    if r.status_code not in (200, 206):
        return None
    cr = r.headers.get("content-range", "")
    return int(cr.split("/")[-1]) if "/" in cr else None


def csv_rows(fname):
    return list(csv.DictReader((DATA / fname).open(encoding="utf-8-sig")))


def check(client):
    ok = True
    for fname, table in TARGETS:
        want = len(csv_rows(fname))
        have = count(client, table)
        if have is None:
            print(f"  {table:16s} {R}unreachable{X}"); ok = False; continue
        mark = f"{G}match{X}" if have == want else f"{R}{have - want:+d}{X}"
        print(f"  {table:16s} workbook {want:3d}   supabase {have:3d}   {mark}")
        ok = ok and have == want
    return ok


def repair(client):
    for fname, table in TARGETS:
        rows = csv_rows(fname)
        first_col = slug(list(rows[0].keys())[0])
        # PostgREST refuses an unfiltered DELETE. "not equal to a value that
        # cannot occur" is the standard way to say every row, and it stays
        # explicit about what is being removed.
        r = client.delete(f"{SB}/rest/v1/{table}",
                          params={first_col: "not.is.null"}, headers=H)
        print(f"  cleared {table:16s} HTTP {r.status_code}")
        load(client, fname, table, None)
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--repair", action="store_true")
    a = ap.parse_args()
    client = httpx.Client(timeout=60.0)
    print()
    if a.repair:
        print("  before"); check(client); print()
        repair(client)
        print("  after"); ok = check(client)
        print(f"\n  {G}Supabase now matches the workbook.{X}" if ok
              else f"\n  {R}Still not matching — do not proceed, tell me the numbers.{X}")
    else:
        check(client)
    client.close()
    print()
