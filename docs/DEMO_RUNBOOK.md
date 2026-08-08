# ResolveFlow AI — Demo Runbook

**Team Byte Me · Customer Support track · Finals 9 Aug 2026, Kuala Lumpur**
Slot: 10 minutes — target **8 minutes** of demo, leaving 2 for questions.

---

## T-60 minutes — pre-flight

Run every one of these. The failure modes below have all actually happened.

```powershell
# 1. Wake Supabase FIRST — free tier pauses after inactivity and a cold
#    start mid-demo is 30 seconds of dead air you cannot talk over.
Invoke-RestMethod "http://localhost:8001/api/outcomes/supabase-tables?table=issues_r2"

# 2. Stack up
cd C:\autopilot\AutoPilot-Template
docker compose up -d
docker compose ps            # three services, all healthy

# 3. Tunnel — leave this window open, minimised, never close it
ngrok http 8001

# 4. Integrations green
Invoke-RestMethod "http://localhost:8001/api/integrations/health-check" -Method POST |
  Select integration_name, status | Format-Table

# 5. Clean the data so nothing on screen contradicts itself
Invoke-RestMethod "http://localhost:8001/api/agent/maintenance?dry_run=false" -Method POST |
  Select issues_found

# 6. Policies at known values
Invoke-RestMethod "http://localhost:8001/api/ai/policies" |
  ForEach-Object { "$($_.policy_key) v$($_.active_version)" }
```

**Browser tabs, in this order, all pre-loaded:**

1. `localhost:3001` — Dashboard
2. `localhost:3001/data-manager`
3. `localhost:3001/workbench`
4. `localhost:3001/ai/policies`
5. Slack `#ticket-escalations`
6. Supervity Auto — Orchestrator open

Zoom the browser to **125%**. Judges watch a projector, not your laptop.

---

## ACT 1 — Live operation (~5 min)

### Open on the problem, not the product

> "A service desk gets a hundred tickets a day. Round 1 we automated 17.6% of
> them — and escalated 117 tickets with no explanation attached to any of them.
> Round 2 the question isn't how many we can automate. It's whether anyone can
> trust what the agent does when nobody is watching."

### 1. Data Manager (30s)

Show three integrations with real health, latency and last read/write times.

> "These aren't badges. Every status comes from a real call — that latency was
> measured just now. And when something is degraded, it says so."

*If Supervity Auto shows Degraded, do not skip past it — use it:*

> "That's honest reporting. Reads authenticate fine; the platform's execute
> endpoint returns a 500 after passing its own validation. We report the half
> that works and the half that doesn't, rather than a green badge."

### 2. Trigger a case (60s)

Trigger the Orchestrator. Show the run appear in **Recent agent activity**.

> "That's a real ticket from the system of record, not a fixture."

### 3. Policy gate (90s) — THE CENTRE OF THE DEMO

Open the run's **Decision Passport**. Read the reasons aloud:

> "6 correlated tickets were detected within 12 minutes, meeting the threshold
> of 5. Correlation confidence 0.70 is below the required 0.80."

> "The agent stopped itself. Not because a model felt uncertain — because a
> deterministic policy with a version number said no, and recorded why."

Expand **Configuration at the time**.

> "Months from now, someone asks why this decision was made. That's the answer,
> with the exact settings that were active."

### 4. Workbench + Slack (90s)

Show the queue item with full context: policy reasons, proposed action,
verification plan, rollback plan. Switch to Slack, show the escalation.

Click **Modify**, narrow the scope, submit.

> "I've just narrowed what the agent is allowed to do. The original
> recommendation is kept beside my amended one — that difference is the signal
> we learn from."

### 5. Dashboard moves (30s)

> "Awaiting human drops. Auto-resolution reads N/A, not 0% — nothing has been
> verified yet, and we won't show a number we can't stand behind."

---

## ACT 2 — Policy flip (~2 min)

Open **AI Policies -> Major Incident Declaration**.

> "This is a business user's screen. No code, no deploy."

Drag `minimum_correlated_ticket_count` from **5 to 10**. Type a change note.
Click **Save as v4**.

Re-run the same case. Show the different verdict.

Open **Evaluation history** — both evaluations, side by side, at different
versions.

> "Same ticket. One number changed. Different behaviour, both decisions logged
> permanently. That's what governed autonomy means — you can change what the
> agent is allowed to do without changing what it is."

**Reset to 5 afterwards** if you plan to re-run anything.

---

## ACT 3 — Proof (~1 min)

Press **Cmd/Ctrl+J**, ask:

> "Why was this action not executed automatically?"

Show the grounded answer with its evidence citations.

Then ask something unsupported — "What's the weather in KL?" — and show the
refusal.

> "It answers from stored records or it declines. It never invents an
> operational answer."

Close on the number:

> "Round 1: 17.6% automated, 117 escalations with no reason.
> Round 2: every refusal names the policy, the version and the failing
> condition. 64 of 150 cases stopped because a required field was missing — and
> we can tell you which field, on every one."

---

## Judge questions — prepared answers

**"What if I pick a ticket you haven't rehearsed?"**
> "Please do." Run it. The policy evaluator's signature is `(context, config)` —
> it takes no issue key, so branching on specific tickets is structurally
> impossible. There is a test asserting it.

**"Is that 17.6% real?"**
> Counted from Supabase: 25 auto-resolved of 142 the Round 1 agent decided. We
> report three denominators so you can challenge the definition rather than the
> arithmetic. Measuring against `resolved_by`-stamped rows alone would give
> 100%, which is why we do not quote it.

**"Why is your automation rate low?"**
> 43% of the backlog is missing a required field. We escalate those and name
> the field. A higher number would mean guessing. Round 1 escalated 117 tickets
> and told nobody why.

**"Show me the human loop actually working."**
> Workbench -> Modify -> the passport shows the agent's original proposal beside
> the approved scope. Only the narrowed scope executes.

**"What happens if Slack is down?"**
> The Workbench item is still created and the failure is recorded as Unhealthy
> in Data Manager. Losing the notification must not lose the exception.

**"How do I know the policy runs before the action?"**
> `POST /api/agent/gate` returns `may_execute: false` on anything but ALLOW, and
> the evaluation row is written before any external call. The Decision Passport
> shows the ordering.

---

## If something breaks

| Symptom | Do this |
|---|---|
| ngrok dropped | Keep going — the Command Center runs on localhost. Only the Auto callback needs the tunnel. |
| Supabase timeout | It paused. Re-run the call; it wakes in ~20s. Talk over it. |
| Auto execute 500 | Expected. Say so plainly and trigger from the Auto UI. |
| A page looks stale | `Ctrl+Shift+R`. Do not restart containers mid-demo. |
| Dashboard shows something odd | Move on. Never debug live. |

**Never** run `docker compose down` during the demo. **Never** edit code.

---

## The one line to land

> "ResolveFlow AI does not just resolve tickets. It refuses to act when it
> should not, tells you exactly why, and lets you change the rules without
> changing the code."
