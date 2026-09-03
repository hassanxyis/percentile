-- Percentile v1 — initial schema (plan.md §5).
--
-- Never edit a migration that has run. Add a new one.
--
-- R1 governs this file: raw responses are sacred, everything else is derived.
-- `responses` and `value_sorts` are the source of truth; `scores` and
-- `occupation_matches` are stamped with an engine version and may be recomputed
-- at any time without loss.

create extension if not exists pgcrypto;

-- ── tenancy ──────────────────────────────────────────────────────────────
create table organisations (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  slug            text unique not null,
  logo_path       text,                       -- supabase storage path
  brand_hex       text default '#1C6A61',
  plan            text not null default 'pilot',   -- pilot | institution
  seats_purchased int  not null default 0,
  created_at      timestamptz not null default now()
);

create table profiles (                        -- counsellors & admins
  id              uuid primary key references auth.users(id) on delete cascade,
  organisation_id uuid not null references organisations(id) on delete cascade,
  full_name       text not null,
  role            text not null default 'counsellor',  -- counsellor | org_admin | superadmin
  created_at      timestamptz not null default now()
);
create index on profiles(organisation_id);

create table cohorts (
  id              uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references organisations(id) on delete cascade,
  name            text not null,              -- "Class of 2027 — Pre-Medical A"
  intake_year     int,
  education_level text,                       -- matric | intermediate | undergraduate
  created_by      uuid references profiles(id),
  created_at      timestamptz not null default now()
);
create index on cohorts(organisation_id);

-- ── participants ─────────────────────────────────────────────────────────
create table participants (
  id                 uuid primary key default gen_random_uuid(),
  cohort_id          uuid not null references cohorts(id) on delete cascade,
  full_name          text not null,
  email              text,
  external_ref       text,                    -- roll number
  intended_field     text,                    -- controlled vocabulary; plan §19 item 1
  education_level    text,
  invite_token_hash  text unique not null,    -- sha256 of the token; never store the token
  consent_at         timestamptz,
  status             text not null default 'invited',  -- invited|started|submitted|scored|failed
  created_at         timestamptz not null default now()
);
create index on participants(cohort_id);
create index on participants(status);

-- ── instrument definition (data, not code) ───────────────────────────────
-- Populated by scripts/load_instruments.py from data/instruments/*.csv.
-- Items are downloaded, never generated (R2). All code must work with an empty
-- item table on day one.
create table instruments (
  code        text primary key,               -- 'interests' | 'personality' | 'values'
  title       text not null,
  version     text not null,
  source      text not null,
  licence     text not null,
  item_count  int  not null
);

create table items (
  id              uuid primary key default gen_random_uuid(),
  instrument_code text not null references instruments(code),
  ordinal         int  not null,
  code            text not null,              -- stable id, e.g. 'IP_R_03'
  text            text not null,
  scale           text not null,              -- R I A S E C | O C E A N | ACH IND REC REL SUP WCN
  reverse_keyed   boolean not null default false,
  response_min    int not null,
  response_max    int not null,
  unique (instrument_code, code)
);
create index on items(instrument_code, ordinal);

-- ── sessions & responses ─────────────────────────────────────────────────
create table sessions (
  id             uuid primary key default gen_random_uuid(),
  participant_id uuid not null references participants(id) on delete cascade,
  started_at     timestamptz,
  submitted_at   timestamptz,
  last_seen_at   timestamptz,
  user_agent     text,
  progress       jsonb not null default '{}'::jsonb,  -- {module: last_ordinal}
  unique (participant_id)
);

create table responses (
  id           uuid primary key default gen_random_uuid(),
  session_id   uuid not null references sessions(id) on delete cascade,
  item_id      uuid not null references items(id),
  value        int  not null,
  ms_elapsed   int,                            -- time on this item; feeds flags
  answered_at  timestamptz not null default now(),
  unique (session_id, item_id)
);
create index on responses(session_id);

-- values card sort: 20 statements → 5 columns × 4 cards
create table value_sorts (
  id          uuid primary key default gen_random_uuid(),
  session_id  uuid not null references sessions(id) on delete cascade,
  item_id     uuid not null references items(id),
  column_no   int  not null check (column_no between 1 and 5),
  unique (session_id, item_id)
);

-- ── derived ──────────────────────────────────────────────────────────────
-- Rows here are disposable by design. `unique (session_id, engine_version)`
-- lets a rescore write a new row beside the old one instead of destroying the
-- history that shows whether a rule change moved a school's numbers (R1).
create table scores (
  id              uuid primary key default gen_random_uuid(),
  session_id      uuid not null references sessions(id) on delete cascade,
  engine_version  text not null,
  scored_at       timestamptz not null default now(),
  interests       jsonb not null,   -- {R:31,I:38,...,code:"ISA",differentiation:14,band:"moderate"}
  personality     jsonb not null,   -- {O:41,C:33,E:22,A:38,S:27, bands:{...}}
  values_scores   jsonb not null,   -- {ACH:24,IND:18,...,top2:["ACH","REL"]}
  flags           jsonb not null default '[]'::jsonb,
  unique (session_id, engine_version)
);
create index on scores(session_id);

create table occupation_matches (
  id             uuid primary key default gen_random_uuid(),
  session_id     uuid not null references sessions(id) on delete cascade,
  engine_version text not null,
  rank           int  not null,
  onet_soc_code  text not null,
  score          numeric(5,4) not null,
  local_title    text,
  local_pathway  text,
  unique (session_id, engine_version, rank)
);

-- ── reference data ───────────────────────────────────────────────────────
create table occupations (
  onet_soc_code   text primary key,
  title           text not null,
  job_zone        int,
  interest_r numeric, interest_i numeric, interest_a numeric,
  interest_s numeric, interest_e numeric, interest_c numeric,
  value_ach numeric, value_ind numeric, value_rec numeric,
  value_rel numeric, value_sup numeric, value_wcn numeric,
  pk_title        text,     -- localisation layer
  pk_pathway      text,     -- "FSc Pre-Engineering → BS Mechanical Engineering"
  pk_relevant     boolean not null default false
);
create index on occupations(pk_relevant);

-- No percentile is displayed until a matching row here reaches n >= 300 for a
-- defined population (R4). Never show US percentiles to Pakistani students.
create table norms (
  id           uuid primary key default gen_random_uuid(),
  population   text not null,    -- 'pk_intermediate_2026'
  instrument   text not null,
  scale        text not null,
  n            int  not null,
  mean         numeric not null,
  sd           numeric not null,
  percentiles  jsonb not null,   -- {"5":12,"10":15,...,"95":36}
  computed_at  timestamptz not null default now(),
  unique (population, instrument, scale)
);

-- ── reports & jobs ───────────────────────────────────────────────────────
create table reports (
  id               uuid primary key default gen_random_uuid(),
  kind             text not null,       -- 'student' | 'cohort'
  session_id       uuid references sessions(id) on delete cascade,
  cohort_id        uuid references cohorts(id) on delete cascade,
  storage_path     text not null,
  template_version text not null,
  engine_version   text not null,
  created_at       timestamptz not null default now(),
  check ((kind = 'student' and session_id is not null)
      or (kind = 'cohort'  and cohort_id  is not null))
);

create table jobs (
  id            uuid primary key default gen_random_uuid(),
  kind          text not null,        -- score_session | render_student | render_cohort | send_email | recompute_norms
  payload       jsonb not null,
  status        text not null default 'pending',  -- pending|running|done|failed
  attempts      int  not null default 0,
  last_error    text,
  run_after     timestamptz not null default now(),
  claimed_at    timestamptz,
  created_at    timestamptz not null default now()
);
create index on jobs(status, run_after);

create table audit_log (
  id          uuid primary key default gen_random_uuid(),
  actor       uuid,
  action      text not null,
  subject     text,
  meta        jsonb,
  created_at  timestamptz not null default now()
);
