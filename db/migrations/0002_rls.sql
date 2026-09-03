-- Row-level security (plan.md §5).
--
-- Getting this wrong leaks minors' data across institutions. Every table gets
-- RLS enabled; nothing is left open by omission.
--
-- Three access shapes:
--   1. Tenant-scoped   — readable only within the caller's organisation.
--   2. Reference data  — readable by any authenticated user, written by service role.
--   3. Service role only — jobs and audit_log; no anon, no authenticated.
--
-- Students are never authenticated. The taker flow reaches the database only
-- through Next.js server actions that resolve sha256(token) → participants
-- .invite_token_hash using the service role. Those actions must never accept a
-- participant_id from the client — only a token.
--
-- The service role bypasses RLS entirely, so all writes happen server-side.
-- These policies are read-only by design: no insert/update/delete policy exists
-- for authenticated users anywhere in this file.

alter table organisations      enable row level security;
alter table profiles           enable row level security;
alter table cohorts            enable row level security;
alter table participants       enable row level security;
alter table instruments        enable row level security;
alter table items              enable row level security;
alter table sessions           enable row level security;
alter table responses          enable row level security;
alter table value_sorts        enable row level security;
alter table scores             enable row level security;
alter table occupation_matches enable row level security;
alter table occupations        enable row level security;
alter table norms              enable row level security;
alter table reports            enable row level security;
alter table jobs               enable row level security;
alter table audit_log          enable row level security;

-- ── helper ───────────────────────────────────────────────────────────────
-- The caller's organisation. SECURITY DEFINER so it can read `profiles`
-- without recursing into that table's own policy, and STABLE so Postgres
-- evaluates it once per statement rather than once per row.
create or replace function auth_organisation_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select organisation_id from profiles where id = auth.id();
$$;

revoke all on function auth_organisation_id() from public;
grant execute on function auth_organisation_id() to authenticated;

-- ── 1. tenant-scoped ─────────────────────────────────────────────────────
-- A user reads only their own profile row. Not their colleagues' — nothing in
-- the counsellor UI needs that, and it keeps the helper above non-recursive.
create policy profiles_self_read on profiles
  for select to authenticated
  using (id = auth.id());

create policy organisations_read on organisations
  for select to authenticated
  using (id = auth_organisation_id());

create policy cohorts_read on cohorts
  for select to authenticated
  using (organisation_id = auth_organisation_id());

create policy participants_read on participants
  for select to authenticated
  using (exists (
    select 1 from cohorts c
    where c.id = participants.cohort_id
      and c.organisation_id = auth_organisation_id()
  ));

create policy sessions_read on sessions
  for select to authenticated
  using (exists (
    select 1 from participants p
    join cohorts c on c.id = p.cohort_id
    where p.id = sessions.participant_id
      and c.organisation_id = auth_organisation_id()
  ));

create policy responses_read on responses
  for select to authenticated
  using (exists (
    select 1 from sessions s
    join participants p on p.id = s.participant_id
    join cohorts c on c.id = p.cohort_id
    where s.id = responses.session_id
      and c.organisation_id = auth_organisation_id()
  ));

create policy value_sorts_read on value_sorts
  for select to authenticated
  using (exists (
    select 1 from sessions s
    join participants p on p.id = s.participant_id
    join cohorts c on c.id = p.cohort_id
    where s.id = value_sorts.session_id
      and c.organisation_id = auth_organisation_id()
  ));

create policy scores_read on scores
  for select to authenticated
  using (exists (
    select 1 from sessions s
    join participants p on p.id = s.participant_id
    join cohorts c on c.id = p.cohort_id
    where s.id = scores.session_id
      and c.organisation_id = auth_organisation_id()
  ));

create policy occupation_matches_read on occupation_matches
  for select to authenticated
  using (exists (
    select 1 from sessions s
    join participants p on p.id = s.participant_id
    join cohorts c on c.id = p.cohort_id
    where s.id = occupation_matches.session_id
      and c.organisation_id = auth_organisation_id()
  ));

-- Reports carry either a session_id or a cohort_id, never both (see the check
-- constraint in 0001). Both paths resolve to the same organisation test.
create policy reports_read on reports
  for select to authenticated
  using (
    exists (
      select 1 from sessions s
      join participants p on p.id = s.participant_id
      join cohorts c on c.id = p.cohort_id
      where s.id = reports.session_id
        and c.organisation_id = auth_organisation_id()
    )
    or exists (
      select 1 from cohorts c
      where c.id = reports.cohort_id
        and c.organisation_id = auth_organisation_id()
    )
  );

-- ── 2. reference data ────────────────────────────────────────────────────
-- Instrument item text is licensed but not secret; a counsellor may legitimately
-- read it. Written only by the service role via scripts/load_instruments.py.
create policy instruments_read  on instruments  for select to authenticated using (true);
create policy items_read        on items        for select to authenticated using (true);
create policy occupations_read  on occupations  for select to authenticated using (true);
create policy norms_read        on norms        for select to authenticated using (true);

-- ── 3. service role only ─────────────────────────────────────────────────
-- `jobs` and `audit_log` get RLS enabled and no policy at all. RLS denies by
-- default, so authenticated and anon see zero rows; the service role bypasses
-- RLS and retains full access. The silence is deliberate — do not add a policy
-- here without a reason written down.
