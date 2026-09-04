-- Percentile v2 — psychologist review workflow (plan.md §5, §9).
--
-- Never edit a migration that has run. Add a new one.
--
-- This is the mechanical heart of R9: a session cannot reach `reports` (student
-- kind) until a `reviews` row exists with status = 'confirmed'. Enforced here at
-- the database level via trigger, not just in application code.

-- ── psychologist role ────────────────────────────────────────────────────
alter table profiles
  drop constraint if exists profiles_role_check;
alter table profiles
  add constraint profiles_role_check
  check (role in ('counsellor', 'psychologist', 'org_admin', 'superadmin'));

-- ── occupation catalogue: mark entrepreneurship-track roles ─────────────
alter table occupations add column if not exists entrepreneurial_track boolean not null default false;

-- ── participants.status: the review checkpoint joins the state machine ──
-- invited → started → submitted → scored → pending_review → reviewed → confirmed
--                                                    ╲
--                                                     → needs_more_info → pending_review (loop)
-- `failed` remains a terminal error state reachable from any point (plan §5).
alter table participants
  add constraint participants_status_check
  check (status in (
    'invited', 'started', 'submitted', 'scored',
    'pending_review', 'reviewed', 'needs_more_info', 'confirmed', 'failed'
  ));

-- `scores.values_scores` now holds the GET2 shape (plan §5, §7.3) instead of
-- the retired WIL shape. The column is not renamed — rescoreable history (R1)
-- would otherwise need a backfill — but the comment should not lie about what
-- v2 sessions actually contain.
comment on column scores.values_scores is
  'GET2 shape: {instrument:"GET2", subscales:{achievement,autonomy,creativity,risk_taking,control}, get2_total, entrepreneurial_band, entrepreneurial_flag} or {instrument:null, status:"module_not_administered"} per R10.';

-- `0001_init.sql`'s comments predate v2 and are append-only, so corrected here
-- rather than edited in place. `instruments.code` for module 3 is now 'get2',
-- not 'values'; `items.scale` for module 3 is now one of the five GET2
-- subscale codes, not the retired WIL ACH/IND/REC/REL/SUP/WCN set.
comment on column instruments.code is
  'interests | personality | get2. The v1 "values" instrument (WIL) is retired — see plan.md §19.';
comment on column items.scale is
  'R I A S E C | O C E A N | achievement autonomy creativity risk_taking control (GET2, plan §6.3). The v1 WIL set (ACH/IND/REC/REL/SUP/WCN) is retired.';

-- `value_sorts` (the 20-card, 5-column WIL sort) has no writer in v2 — GET2
-- is answered on a Likert scale like personality, not sorted. The table is
-- left in place rather than dropped: it is empty in every v2 install and
-- dropping a table in an already-applied migration set is exactly the kind
-- of edit R1's "migrations are append-only" rule exists to prevent. If a
-- future instrument needs a card sort again, reuse it; if not, a later
-- migration can drop it once you are certain no historical session used it.
comment on table value_sorts is
  'Unused since v2 (plan §19) — GET2 has no card sort. Left in place, not dropped; see 0003_reviews.sql.';

-- ── reviews: the human checkpoint required by R9 ─────────────────────────
create table reviews (
  id                uuid primary key default gen_random_uuid(),
  session_id        uuid not null references sessions(id) on delete cascade,
  reviewer_id       uuid not null references profiles(id),
  status            text not null default 'in_progress',  -- in_progress|confirmed|needs_more_info
  interview_mode    text,                 -- 'in_person' | 'video' | 'async_notes'
  interview_at      timestamptz,
  interview_notes   text,                 -- free text, the psychologist's own words (R3)
  flags_reviewed    jsonb not null default '[]'::jsonb,  -- which engine flags they looked at
  confirmed_at      timestamptz,
  created_at        timestamptz not null default now(),
  unique (session_id)
);
create index on reviews(reviewer_id);
create index on reviews(status);

-- a session can have exactly one *current* review; re-opening for "needs_more_info"
-- updates this row rather than creating a new one, so history stays on one thread
create table review_events (
  id          uuid primary key default gen_random_uuid(),
  review_id   uuid not null references reviews(id) on delete cascade,
  actor       uuid references profiles(id),
  event       text not null,   -- opened | note_added | direction_proposed | confirmed | reopened
  meta        jsonb,
  created_at  timestamptz not null default now()
);

-- ── confirmed career directions: the joint decision the deck describes ──
create table career_directions (
  id              uuid primary key default gen_random_uuid(),
  review_id       uuid not null references reviews(id) on delete cascade,
  rank            int  not null check (rank between 1 and 3),
  onet_soc_code   text references occupations(onet_soc_code),
  local_title     text not null,          -- shown to the student even if onet_soc_code is null
  rationale       text,                    -- psychologist's own words, short
  student_selected boolean not null default false,  -- did the student pick this as "the" one
  unique (review_id, rank)
);

-- ── enforce R9 at the database level, not just in application code ──────
create or replace function enforce_review_before_student_report()
returns trigger as $$
begin
  if NEW.kind = 'student' then
    if not exists (
      select 1 from reviews r
      where r.session_id = NEW.session_id and r.status = 'confirmed'
    ) then
      raise exception 'R9 violation: no confirmed review for session %', NEW.session_id;
    end if;
  end if;
  return NEW;
end;
$$ language plpgsql;

create trigger trg_enforce_review_before_student_report
  before insert on reports
  for each row execute function enforce_review_before_student_report();

-- ── RLS ───────────────────────────────────────────────────────────────────
alter table reviews           enable row level security;
alter table review_events     enable row level security;
alter table career_directions enable row level security;

-- Read-only by design, matching every policy in 0002_rls.sql: no insert/
-- update/delete policy exists for authenticated users anywhere in this file
-- either. All writes to reviews/review_events/career_directions happen
-- server-side (Next.js server actions) through the service role, which
-- bypasses RLS entirely.
--
-- Psychologists (and org_admin/superadmin) can read every column, including
-- interview_notes, within their organisation. A counsellor may read
-- status/confirmed_at to track progress but never interview_notes — that
-- boundary is load-bearing (plan §5, §16) and must have a test: a
-- counsellor-only profile reading interview_notes is the kind of leak that
-- ends a pilot.
create policy reviews_psychologist_read on reviews
  for select to authenticated
  using (
    exists (
      select 1 from profiles p
      where p.id = auth.id()
        and p.role in ('psychologist', 'org_admin', 'superadmin')
        and p.organisation_id = auth_organisation_id()
    )
    and exists (
      select 1 from sessions s
      join participants pt on pt.id = s.participant_id
      join cohorts c on c.id = pt.cohort_id
      where s.id = reviews.session_id
        and c.organisation_id = auth_organisation_id()
    )
  );

-- Counsellors see status/confirmed_at only, never interview_notes. RLS is
-- row-scoped, not column-scoped — a second SELECT policy on `reviews` itself
-- would grant a matching counsellor every column, including interview_notes.
-- That is the exact leak this table exists to prevent, so counsellors get NO
-- policy on `reviews` at all; only the narrow function below, following the
-- same SECURITY DEFINER shape as `auth_organisation_id()` in 0002_rls.sql.
create or replace function review_progress_for_session(p_session_id uuid)
returns table (status text, confirmed_at timestamptz)
language sql
stable
security definer
set search_path = public
as $$
  select r.status, r.confirmed_at
  from reviews r
  join sessions s on s.id = r.session_id
  join participants pt on pt.id = s.participant_id
  join cohorts c on c.id = pt.cohort_id
  where r.session_id = p_session_id
    and c.organisation_id = auth_organisation_id();
$$;

revoke all on function review_progress_for_session(uuid) from public;
grant execute on function review_progress_for_session(uuid) to authenticated;

create policy review_events_psychologist_read on review_events
  for select to authenticated
  using (
    exists (
      select 1 from profiles p
      where p.id = auth.id()
        and p.role in ('psychologist', 'org_admin', 'superadmin')
        and p.organisation_id = auth_organisation_id()
    )
    and exists (
      select 1 from reviews r
      join sessions s on s.id = r.session_id
      join participants pt on pt.id = s.participant_id
      join cohorts c on c.id = pt.cohort_id
      where r.id = review_events.review_id
        and c.organisation_id = auth_organisation_id()
    )
  );

create policy career_directions_psychologist_read on career_directions
  for select to authenticated
  using (
    exists (
      select 1 from profiles p
      where p.id = auth.id()
        and p.role in ('psychologist', 'org_admin', 'superadmin')
        and p.organisation_id = auth_organisation_id()
    )
    and exists (
      select 1 from reviews r
      join sessions s on s.id = r.session_id
      join participants pt on pt.id = s.participant_id
      join cohorts c on c.id = pt.cohort_id
      where r.id = career_directions.review_id
        and c.organisation_id = auth_organisation_id()
    )
  );

-- No student-facing policy exists on any of these three tables. Students are
-- never authenticated (0002_rls.sql's header note); the confirmed-direction
-- page in the student's PDF is rendered by the engine via the service role,
-- which bypasses RLS entirely, same as every other report field.
