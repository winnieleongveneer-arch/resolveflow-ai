-- ============================================================
-- ResolveFlow NEXUS — bring Supabase up to the Round 2 dataset
-- ============================================================
-- Paste this whole file into the Supabase SQL Editor and Run.
-- It is idempotent: safe to run more than once.
--
-- Every column is text on purpose. The Round 2 export carries three
-- date formats and deliberately blank numerics, and a typed column
-- would reject exactly the dirty rows the agent is supposed to notice.
-- Parsing stays in the Operators, where it can report what it could not
-- parse instead of failing an import.

-- ---- 1. new columns on the existing issues table -------------------
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "issue_key" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "issue_id" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "issue_type" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "summary" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "description" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "priority" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "status" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "resolution" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "assignee" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "reporter" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "created" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "updated" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "due_date" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "project_key" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "components" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "labels" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "request_type" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "organizations" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "customfield_10030_time_to_resolution" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "customfield_10101_assignment_group" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "x_channel" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "x_escalation_risk" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "x_reopened" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "first_response_time" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "linked_incident" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "x_confidence" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "resolved_by" text;
ALTER TABLE issues ADD COLUMN IF NOT EXISTS "resolution_notes" text;

-- ---- 2. the six missing Round 2 tables ------------------------------

CREATE TABLE IF NOT EXISTS incident_problem_links (
  "link_id" text PRIMARY KEY,
  "child_issue_key" text,
  "parent_incident_key" text,
  "relationship" text
);

CREATE TABLE IF NOT EXISTS change_requests (
  "change_id" text PRIMARY KEY,
  "issue_key" text,
  "risk" text,
  "status" text,
  "cab_approval_required" text,
  "approver" text
);

CREATE TABLE IF NOT EXISTS ticket_comments (
  "comment_id" text PRIMARY KEY,
  "issue_key" text,
  "author" text,
  "created" text,
  "body" text,
  "is_internal" text
);

CREATE TABLE IF NOT EXISTS team_roster (
  id bigserial PRIMARY KEY,
  "team" text,
  "member" text,
  "role" text,
  "on_call" text,
  "assignment_group" text,
  "region" text
);

CREATE TABLE IF NOT EXISTS sla_calendar (
  id bigserial PRIMARY KEY,
  "region" text,
  "business_hours" text,
  "timezone" text,
  "holiday_dates" text
);

CREATE TABLE IF NOT EXISTS csat_surveys (
  "survey_id" text PRIMARY KEY,
  "issue_key" text,
  "score" text,
  "comment" text,
  "submitted_at" text
);

-- ---- 3. make them readable through the REST API ---------------------
-- The service_role key bypasses RLS, which is what the Operators use.
-- RLS is enabled anyway so the tables are not world-readable if the
-- anon key is ever exposed.
ALTER TABLE incident_problem_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticket_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_roster ENABLE ROW LEVEL SECURITY;
ALTER TABLE sla_calendar ENABLE ROW LEVEL SECURITY;
ALTER TABLE csat_surveys ENABLE ROW LEVEL SECURITY;

-- ---- 4. indexes the Operators actually use --------------------------
CREATE INDEX IF NOT EXISTS idx_issues_linked_incident ON issues ("linked_incident");
CREATE INDEX IF NOT EXISTS idx_links_parent ON incident_problem_links ("parent_incident_key");
CREATE INDEX IF NOT EXISTS idx_links_child ON incident_problem_links ("child_issue_key");
CREATE INDEX IF NOT EXISTS idx_changes_issue ON change_requests ("issue_key");
CREATE INDEX IF NOT EXISTS idx_comments_issue ON ticket_comments ("issue_key");
