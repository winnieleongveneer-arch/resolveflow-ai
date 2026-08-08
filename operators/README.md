# Operators

The agents that do the work, exported from Supervity Auto. The Command Center in
this repository decides what they are allowed to do; these files are what they
actually run.

Import any of them into Auto with **My Operators → Import / Export → Import**.

| File | What it does | Asks the policy gate |
|---|---|---|
| `rf-01-sla-rescue-coordinator.json` | Ranks every unresolved ticket by SLA state, priority, VIP reporter and age, and writes a prioritised CSV. | no — batch worker |
| `rf-02-evidence-investigator.json` | Cross-references the knowledge base, user directory and asset register to build diagnostic evidence. Writing back is opt-in. | no — batch worker |
| `rf-03-resolution-specialist.json` | Takes one ticket, matches a cleared knowledge base article, scores the fix, **asks permission**, then applies or escalates. | **yes** |
| `rf-04-customer-liaison.json` | Tells the people who raised auto-resolved tickets. Sending is opt-in and capped. | no — batch worker |
| `rf-05-major-incident-commander.json` | Decides whether a ticket is the visible edge of one event rather than an isolated fault. | **yes** |
| `rf-06-change-recovery-controller.json` | Executes what a person allowed, reads the record back to confirm, and reports the outcome. Stops if nobody has decided. | reads the human decision |
| `resolveflow-supervisor.json` | Orchestrator: ranks the backlog, opens the most urgent case in the Command Center, hands it to RF-03. It has no route around the gate. | via RF-03 |

## Two things worth knowing before you run them

**Credentials come from the environment, never from these files.** Each Operator
reads `SUPABASE_TOKEN` and, where relevant, `MICROSOFT_OUTLOOK_TOKEN`, both
injected by Auto's connectors. Nothing here contains a key.

**`test_recipient_email` defaults to a placeholder.** Every message an Operator
sends is redirected to that address rather than to the real ticket reporter, so a
rehearsal can never reach a customer. Set it to your own address before running.

You will also need to point `supabase_url` and `command_center_url` at your own
instances — the defaults are from the build these were exported from.

## Round 1 and Round 2

RF-01, RF-02 and RF-04 began as Round 1 Operators and were rebuilt for the Round 2
schema: lowercase column names, no configuration table, and every Supabase read
performed over httpx with the connector-injected token rather than through the
`supabase` client, which failed in this platform's execution environment.

RF-01, RF-02, RF-03, RF-05, RF-06 and the Supervisor were all run against the
Round 2 dataset on build day and completed. RF-04's rebuild is included here but
was never imported, so the Operator running on Auto is still the Round 1 version
and does not work against Round 2 data. That is stated rather than hidden because
it is the sort of thing a reader would otherwise discover the hard way.
