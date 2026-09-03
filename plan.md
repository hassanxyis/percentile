# Percentile — Implementation Plan (v1)

> **What this file is.** The complete build spec for Percentile v1. It is written to be handed
> to a coding agent (Claude Code, Cursor) or followed by hand, milestone by milestone. Every
> milestone has files to create, a definition of done, and a test that proves it.
>
> **Read `## 0. Rules` before writing any code.** Several rules exist because breaking them
> destroys the product's credibility, not because they are stylistic preferences.
>
> Status: v1 spec, September 2026. Owner: Syed Hassan Raza.

---

## Table of contents

0. [Rules that are not negotiable](#0-rules-that-are-not-negotiable)
1. [What v1 is](#1-what-v1-is)
2. [Architecture](#2-architecture)
3. [Repository layout](#3-repository-layout)
4. [Accounts, secrets, environment](#4-accounts-secrets-environment)
5. [Database schema](#5-database-schema)
6. [Instrument data files](#6-instrument-data-files)
7. [Scoring specification](#7-scoring-specification)
8. [Occupation matching specification](#8-occupation-matching-specification)
9. [Cohort analytics specification](#9-cohort-analytics-specification)
10. [API contract](#10-api-contract)
11. [Job runner and keep-alive](#11-job-runner-and-keep-alive)
12. [Web application](#12-web-application)
13. [Report specification](#13-report-specification)
14. [Email](#14-email)
15. [Privacy, consent and minors](#15-privacy-consent-and-minors)
16. [Build order — milestones](#16-build-order--milestones)
17. [Testing](#17-testing)
18. [Deployment runbook](#18-deployment-runbook)
19. [Open items needing a human decision](#19-open-items-needing-a-human-decision)

---

## 0. Rules that are not negotiable

**R1 — Raw responses are sacred; everything else is derived.**
Never store a computed score without also storing every raw item response that produced it.
Scoring rules, interpretation text and norms will change many times in year one. It must always
be possible to rescore every historical session with one command. If that is not possible, every
improvement orphans the norming sample.

**R2 — Never invent instrument items.**
The item text for the O*NET Interest Profiler and the Work Importance Locator comes from
onetcenter.org. The IPIP items come from ipip.ori.org. Download them. Do not let any model
generate, paraphrase, "improve" or reorder them. A paraphrased item is a different item with
unknown properties, and every reliability figure you quote becomes a lie. All code in this repo
reads items from data files (§6) and must work with an empty item table on day one.

**R3 — Interpretation text is written by a human with psychology training.**
The differentiator of this product is that a trained person wrote the interpretations. Generated
interpretation copy is not defensible in a room with a PhD in it. Use a model to draft *product*
copy, error messages, and this documentation — never the report's interpretive content.

**R4 — No percentiles until local norms exist.**
Until the norm sample for a scale reaches n ≥ 300 for a defined population, reports show raw
scores, within-person rank and provisional bands, and state on the page that percentiles await
local norms. Never display US percentiles for Pakistani students.

**R5 — English only in v1.**
A translated instrument requires back-translation and a fresh reliability study. The site may say
an Urdu edition is in development. It may not ship one.

**R6 — Attribution is mandatory.**
Every report page and the site footer must credit O*NET and the U.S. Department of Labor,
Employment and Training Administration, per the licence chosen in §6. If items are modified in
any way, the O*NET developer licence applies: the modified version must be independently
validated and marked as not USDOL-endorsed. Do not modify items in v1 — it converts a licence
term into a research obligation.

**R7 — No clinical language.**
This is a career interest and values instrument. It does not diagnose, screen, or assess mental
health, ability or intelligence. Report copy, marketing copy and UI copy must never imply it does.

**R8 — Student results belong to the student.**
Individual reports go to the student. Institutions receive aggregates plus, where the institution
is the counsellor of record, the individual reports for their own cohort — disclosed to students
before they start. Never a third party. See §15.

---

## 1. What v1 is

A web application where an institution's counsellor uploads a class roster, each student
completes a ~25-minute three-module assessment on their phone, and two artifacts come out:

- **Student report** — branded PDF, ~12 pages, emailed to the student and stored for the counsellor.
- **Cohort report** — branded PDF for the institution: interest distributions, the intended-field
  congruence rate, value conflicts, and named lists of students to follow up.

The cohort report is the thing being sold. Build the student report first because it is the input,
but never treat the cohort report as a stretch goal.

### In scope

| | |
|---|---|
| Counsellor accounts, scoped to an organisation | Email + password auth |
| Roster upload, bulk invite, progress tracking | CSV |
| Three-module assessment, resumable, mobile-first | 130 items + a 20-card sort |
| Scoring engine with flags and provisional bands | §7 |
| Occupation matching with a Pakistan mapping layer | §8 |
| Student PDF, institution-branded | §13 |
| Cohort PDF and dashboard | §9, §13 |
| Rescore-everything command | R1 |

### Explicitly out of scope for v1

Student accounts · self-serve signup · card checkout · Urdu · the 120-item deep profile ·
aptitude/ability testing · a mobile app · custom domains per tenant · any AI-generated report content.

---

## 2. Architecture

```
                    ┌──────────────────────────┐
  Counsellor  ───►  │  Next.js (Vercel)        │
  Student     ───►  │  taker UI, dashboard,    │
                    │  server actions          │
                    └───────┬──────────┬───────┘
                            │          │
                     writes │          │ enqueue job row
                            ▼          ▼
                    ┌──────────────────────────┐
                    │  Supabase (Postgres)     │
                    │  auth · data · storage   │
                    └───────▲──────────┬───────┘
                            │          │
                     reads/ │          │ reads
                     writes │          ▼
                    ┌──────────────────────────┐
   GitHub Actions   │  FastAPI engine (Python) │
   cron every 5 min─►  scoring · matching ·    │
   POST /tick       │  WeasyPrint · email      │
                    └──────────────────────────┘
```

### The one design decision that makes the free tier work

The engine is **never called synchronously from a user request.** When a student submits, the web
app writes a row to `jobs` and immediately shows "your report is being prepared". A GitHub Actions
cron hits `POST /tick` on the engine every five minutes; the engine wakes, claims pending jobs,
scores them, renders PDFs, sends email, and goes back to sleep.

This single mechanism solves three problems at once:

1. A sleeping free-tier worker with a 60-second cold start is harmless — nobody is waiting.
2. The tick touches Postgres, which keeps the Supabase free project from pausing after 7 days idle.
3. Failed jobs retry naturally on the next tick instead of losing a student's submission.

Do not "optimise" this later by calling the engine directly from a request handler. Report
generation taking a minute is a feature of the architecture, not a defect.

### Stack

| Layer | Choice | Why |
|---|---|---|
| Web | Next.js (App Router) + TypeScript + Tailwind, on Vercel | Free tier, your comfort zone |
| Data/auth/storage | Supabase | Postgres + auth + object storage in one free tier |
| Engine | Python 3.12, FastAPI, numpy, pandas, Jinja2, WeasyPrint | Scoring and PDF are Python's home ground |
| Scheduler | GitHub Actions cron | Free, outside the sleeping host |
| Email | Resend (swap-able behind an interface) | Free tier covers a cohort many times over |
| Engine host | Any free container host; Railway Hobby ($5/mo) when it matters | Treat as disposable compute |

**Rule:** all persistent state lives in Supabase. The engine host holds nothing. Moving the engine
to a different provider must be an afternoon's work, never a migration.

---

## 3. Repository layout

```
percentile/
├── plan.md                       # this file
├── README.md
├── .github/workflows/tick.yml    # cron → POST /tick
├── db/
│   ├── migrations/
│   │   ├── 0001_init.sql
│   │   ├── 0002_rls.sql
│   │   └── ...
│   └── seed/
│       └── demo_org.sql
├── data/
│   ├── instruments/
│   │   ├── interests_items.csv       # O*NET IP-SF, 60 rows — YOU download
│   │   ├── personality_items.csv     # IPIP Big Five markers, 50 rows — YOU download
│   │   ├── values_statements.csv     # WIL, 20 rows — YOU download
│   │   └── values_scoring.json       # WIL worksheet encoding — YOU transcribe
│   ├── onet/
│   │   ├── occupation_data.csv       # O*NET db export
│   │   ├── interests.csv
│   │   ├── work_values.csv
│   │   └── job_zones.csv
│   └── local/
│       └── pk_occupation_map.csv     # your curated Pakistan layer
├── engine/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py               # FastAPI app, routes
│   │   ├── config.py             # env, settings
│   │   ├── db.py                 # supabase client
│   │   ├── jobs.py               # claim / run / retry
│   │   ├── scoring/
│   │   │   ├── interests.py
│   │   │   ├── personality.py
│   │   │   ├── values.py
│   │   │   ├── flags.py
│   │   │   └── engine.py         # orchestrates, stamps version
│   │   ├── matching/
│   │   │   ├── occupations.py
│   │   │   └── cohort.py
│   │   ├── report/
│   │   │   ├── student.py
│   │   │   ├── cohort.py
│   │   │   ├── charts.py         # hand-built SVG, no plotting lib
│   │   │   └── templates/
│   │   │       ├── student.html.j2
│   │   │       ├── cohort.html.j2
│   │   │       └── report.css
│   │   ├── content/
│   │   │   ├── interpretations.yaml   # HUMAN-WRITTEN (R3)
│   │   │   └── occupations_pk.yaml
│   │   └── email.py
│   ├── scripts/
│   │   ├── load_instruments.py
│   │   ├── load_onet.py
│   │   └── rescore_all.py        # R1
│   └── tests/
│       ├── fixtures/
│       └── test_*.py
└── web/
    ├── package.json
    ├── app/
    │   ├── (marketing)/
    │   ├── (dash)/
    │   └── a/[token]/            # the student taker flow
    ├── components/
    ├── lib/
    └── tests/
```

---

## 4. Accounts, secrets, environment

Create, in this order: GitHub repo · Supabase project · Vercel project (link repo) · Resend account
and verified sending domain · engine host account.

### `web/.env.local`

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=        # server-side only, never in a client component
ENGINE_BASE_URL=
ENGINE_SHARED_SECRET=
NEXT_PUBLIC_APP_URL=
```

### `engine/.env`

```
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
ENGINE_SHARED_SECRET=
RESEND_API_KEY=
REPORT_STORAGE_BUCKET=reports
SCORING_ENGINE_VERSION=1.0.0
APP_BASE_URL=
LOG_LEVEL=INFO
```

`SUPABASE_SERVICE_ROLE_KEY` bypasses row-level security. It appears only in server-side Next.js
code and in the engine. If it ever reaches a `NEXT_PUBLIC_` variable or a client component, every
student record in the database is public. Add a CI grep that fails the build on
`SERVICE_ROLE` appearing in `web/app/**` client components.

---

## 5. Database schema

`db/migrations/0001_init.sql`. Postgres via Supabase. `gen_random_uuid()` requires `pgcrypto`.

```sql
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
  intended_field     text,                    -- see §19 item 2
  education_level    text,
  invite_token_hash  text unique not null,    -- sha256 of the token; never store the token
  consent_at         timestamptz,
  status             text not null default 'invited',  -- invited|started|submitted|scored|failed
  created_at         timestamptz not null default now()
);
create index on participants(cohort_id);
create index on participants(status);

-- ── instrument definition (data, not code) ───────────────────────────────
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
  pk_title        text,     -- your localisation layer
  pk_pathway      text,     -- "FSc Pre-Engineering → BS Mechanical Engineering"
  pk_relevant     boolean not null default false
);
create index on occupations(pk_relevant);

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
```

### Row-level security — `0002_rls.sql`

Enable RLS on every table above. Policy shape:

- `profiles`: a user reads only their own row.
- `organisations`, `cohorts`, `participants`, `sessions`, `responses`, `value_sorts`, `scores`,
  `occupation_matches`, `reports`: readable only where the row's organisation matches the caller's
  `profiles.organisation_id`. Write via server-side code only.
- `items`, `instruments`, `occupations`, `norms`: readable by any authenticated user; written only
  by the service role.
- `jobs`, `audit_log`: service role only, no anon or authenticated access.

Students are not authenticated. The taker flow reaches the database only through Next.js server
actions that resolve `sha256(token) → participants.invite_token_hash` using the service role, and
those actions must never accept a `participant_id` from the client — only a token.

**Write a test that logs in as counsellor A and asserts a 0-row result for cohort B's participants.**
Getting this wrong leaks minors' data across institutions.

---

## 6. Instrument data files

**None of these files are generated. You download or transcribe them (R2).**

### `data/instruments/interests_items.csv`

Source: O*NET Resource Center → Interest Profiler Short Form. Choose the licence in §0/R6 and record
it in the `instruments` row.

```csv
code,ordinal,text,scale,response_min,response_max,reverse_keyed
IP_R_01,1,"<item text exactly as published>",R,0,4,false
...60 rows: 10 each for R,I,A,S,E,C
```

Response scale: 0 = strongly dislike … 4 = strongly like. Scale score range 0–40.

### `data/instruments/personality_items.csv`

Source: ipip.ori.org, the 50-item Big-Five factor markers (10 per domain). Public domain.

```csv
code,ordinal,text,scale,response_min,response_max,reverse_keyed
IPIP_E_01,1,"<item text exactly as published>",E,1,5,false
IPIP_E_02,2,"<item text exactly as published>",E,1,5,true
...50 rows
```

Scale letters: `O` Openness/Intellect, `C` Conscientiousness, `E` Extraversion, `A` Agreeableness,
`S` Emotional Stability. **Use Stability, not Neuroticism, as the reported pole** — a report handed
to a seventeen-year-old should not lead with a negatively framed trait name. Keep the item keying
exactly as published and flip the direction once, in the scoring layer, with a documented constant.

### `data/instruments/values_statements.csv` and `values_scoring.json`

Source: O*NET Work Importance Locator user's guide and score report. 20 need statements, sorted into
five columns of four (5 = most important … 1 = least). Transcribe the guide's scoring worksheet:

```json
{
  "values": {
    "ACH": { "items": ["WIL_03","WIL_11","WIL_17"], "multiplier": 2, "min": 6, "max": 30 },
    "IND": { "items": [...], "multiplier": 2, "min": 6, "max": 30 },
    "REC": { ... }, "REL": { ... }, "SUP": { ... },
    "WCN": { "items": [...], "multiplier": 1, "min": 6, "max": 30 }
  },
  "labels": {
    "ACH": "Achievement", "IND": "Independence", "REC": "Recognition",
    "REL": "Relationships", "SUP": "Support", "WCN": "Working Conditions"
  }
}
```

Transcribe the multipliers from the manual; they are not all the same, and Working Conditions is
handled differently. **Verify your implementation against the worked example in the published score
report before trusting a single result.**

### `data/onet/*.csv`

Download the O*NET database text files: `Occupation Data`, `Interests`, `Work Values`, `Job Zones`.
`scripts/load_onet.py` pivots the long-format Interests and Work Values files into the wide
`occupations` columns.

### `data/local/pk_occupation_map.csv`

Yours. Hand-curated, target ~120 rows, grown over time.

```csv
onet_soc_code,pk_title,pk_pathway,pk_relevant
29-1141.00,"Nurse (BSN)","FSc Pre-Medical → BS Nursing",true
17-2141.00,"Mechanical Engineer","FSc Pre-Engineering → BE/BS Mechanical",true
```

This file is the moat. Budget two weeks of evenings for the first pass and revise after every pilot.

---

## 7. Scoring specification

`engine/app/scoring/`. Pure functions: raw responses in, score dict out. No database access inside
the scoring modules — that makes them trivially testable and rescoreable.

### 7.1 Interests — `interests.py`

```python
def score_interests(responses: dict[str, int], items: list[Item]) -> dict:
    """responses: {item_code: 0..4}"""
```

1. `raw[scale] = sum(responses[i.code] for i in items if i.scale == scale)` → 0..40 each.
2. `differentiation = max(raw.values()) - min(raw.values())`
3. Differentiation band — **report copy changes on this, it is not decoration**:
   - `>= 20` → `"well_differentiated"`
   - `10..19` → `"moderate"`
   - `< 10` → `"undifferentiated"` — the report must say in words that no clear code emerged and
     that the counsellor should explore rather than recommend.
4. Holland code = top three scales by raw score, descending. Tie-break in fixed order
   `R,I,A,S,E,C` and record `tie_broken: true`.
5. If `raw[3rd] - raw[4th] < 2`, set `code_provisional: true` and the report says the third letter
   is unstable.
6. Percentiles only if a matching `norms` row exists with `n >= 300` (R4). Otherwise
   `percentiles: null, norms_status: "pending_local_norms"`.

Output:
```json
{"raw":{"R":18,"I":34,"A":29,"S":31,"E":20,"C":12},
 "code":"ISA","differentiation":22,"band":"well_differentiated",
 "code_provisional":false,"percentiles":null,
 "norms_status":"pending_local_norms"}
```

### 7.2 Personality — `personality.py`

1. For each item: `v = raw if not item.reverse_keyed else (item.response_max + item.response_min - raw)`
   → for a 1..5 scale that is `6 - raw`. Never hard-code the 6.
2. `domain[scale] = sum(v)` over its 10 items → 10..50.
3. Bands, **provisional until norms exist**, and labelled as such in the report:

   | Sum | Band |
   |---|---|
   | ≤ 19 | very low |
   | 20–27 | low |
   | 28–34 | average |
   | 35–42 | high |
   | ≥ 43 | very high |

4. When `norms` has a row for this population with `n >= 300`, bands switch to normative
   (`< -1.5 SD`, `-1.5..-0.5`, `-0.5..0.5`, `0.5..1.5`, `> 1.5 SD`) and `norms_status` becomes
   `"local"`. Both paths return the same shape so the template does not branch.

### 7.3 Work values — `values.py`

1. Validate the sort: exactly 20 placements, exactly 4 cards in each of 5 columns. Reject otherwise —
   a partial sort is not scoreable and must not be silently zero-filled.
2. For each value: `score = multiplier * sum(column_no for its items)`, per `values_scoring.json`.
3. Range-check against the published `min`/`max`; raise on violation rather than clamping. A score
   outside range means the transcription is wrong and you want to know immediately.
4. `top2` = two highest, ties broken alphabetically, `tie_broken` recorded.

### 7.4 Response-quality flags — `flags.py`

Flags appear **only on the counsellor's copy**, never on the student's (R8, and it is simply unkind).

| Flag | Rule |
|---|---|
| `straightlining` | longest run of identical values across modules A+B ≥ 12 |
| `too_fast` | total active time < 6 min, or median item time < 800 ms |
| `inconsistent_pairs` | for any Big Five domain, \|mean(forward) − reverse-adjusted mean(reverse)\| > 1.5 |
| `long_gap` | > 48 h between start and submit — context changed mid-assessment |
| `incomplete_sort` | card sort completed by fallback rather than drag |

Each flag: `{code, severity: "info"|"warn", detail}`. Two or more `warn` flags → the cohort report
excludes that participant from aggregate statistics **and says how many were excluded and why**.

### 7.5 Orchestration — `engine.py`

```python
def score_session(session_id) -> ScoreRecord:
    # 1. load items + responses + value_sorts
    # 2. run the three scorers + flags
    # 3. write scores row stamped with SCORING_ENGINE_VERSION
    # 4. enqueue: match_occupations, then render_student, then send_email
```

Bump `SCORING_ENGINE_VERSION` (semver) whenever any rule changes. `scripts/rescore_all.py` reruns
every session under the current version, writing new `scores` rows and leaving old ones intact —
that history is how you show a school that a rule change did or did not move their numbers.

---

## 8. Occupation matching specification

`engine/app/matching/occupations.py`.

**Ipsatize before comparing.** Interest profiles differ in elevation (some people like everything);
what matters is shape. Subtract each profile's own mean across its six scales, on both sides.

```python
def match(student_interests, student_values, education_level, k=15):
    s = ipsatize(student_interests)                     # 6-vector, mean 0
    pool = occupations_in_job_zones(job_zones_for(education_level))
    rows = []
    for occ in pool:
        o = ipsatize(occ.interests)
        interest_sim = cosine(s, o)                     # -1..1
        values_sim   = pearson(student_values, occ.values)  # -1..1, 0 if occ values missing
        score = 0.70 * norm01(interest_sim) + 0.30 * norm01(values_sim)
        rows.append((occ, score))
    rows.sort(key=lambda r: -r[1])
    return diversify(rows, k=k, max_per_group=2)
```

Details that matter:

- `norm01(x) = (x + 1) / 2`.
- **Job Zone mapping** — `matric → [1,2,3]`, `intermediate → [2,3,4]`, `undergraduate → [3,4,5]`.
  Job Zone is an education/experience band, so a matric student should not be matched to
  occupations requiring a doctorate.
- **`diversify`** caps results at 2 per 2-digit SOC major group. Without it the top 15 is fifteen
  varieties of the same job and the report looks unintelligent.
- **Localisation pass:** partition results into `pk_relevant = true` and the rest. The report shows
  up to 10 local matches first, then up to 5 international ones under a separate heading. If fewer
  than 5 local matches exist for a profile, that is a gap in `pk_occupation_map.csv` — log it, and
  review the log monthly. Those logs tell you exactly which occupations to map next.
- Write `occupation_matches` rows stamped with the engine version.

---

## 9. Cohort analytics specification

`engine/app/matching/cohort.py`. This produces the numbers that sell the product, so specify them
tightly enough that you can defend each one out loud.

### Field centroids

For each `intended_field` in your dropdown, define the set of O*NET occupations it leads to (from
`pk_occupation_map.csv`). The **field centroid** is the mean ipsatized interest vector of that set.
Store it; recompute when the map changes.

### Congruence

For each participant: `congruence = cosine(ipsatize(student_interests), centroid(intended_field))`.

| Congruence | Class |
|---|---|
| ≥ 0.60 | aligned |
| 0.35 – 0.60 | partial |
| < 0.35 | misaligned |

**These thresholds are provisional.** Print them on the cohort report's method page, and recalibrate
against real data once you have 300+ students. Never present a threshold as though it were a law.

### Cohort report contents

1. `n` invited, `n` completed, completion rate, `n` excluded for quality flags with the reason.
2. Holland code distribution — first-letter frequencies, and the top ten full three-letter codes.
3. **The headline: congruence rate.** "31% of students in Pre-Medical are classed misaligned with
   their stated field" plus the per-field breakdown.
4. Named follow-up list — misaligned students, sorted by how misaligned, for the counsellor only.
5. Value conflicts — students whose top work value is bottom-two in their field's centroid values.
6. Mean interest profile of the cohort vs each field's centroid, as a hexagon overlay.
7. Method page: instruments, versions, thresholds, exclusions, what the numbers do *not* mean (R7).

### Statistical honesty rules

- Never report a percentage on a denominator below 10 without printing the denominator beside it.
- Never compare two cohorts without stating both `n`s.
- The report says "classed misaligned by this instrument", never "in the wrong field".

That last distinction is the whole difference between a tool a counsellor trusts and a tool a
counsellor is embarrassed to hand to a parent.

---

## 10. API contract

Engine base URL is private. Every route requires `X-Engine-Key: $ENGINE_SHARED_SECRET`; reject with
401 otherwise. The engine is never called from a browser.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | liveness; also touches Postgres (keep-alive) |
| `POST` | `/tick` | claim and run up to N pending jobs; returns a summary |
| `POST` | `/score/{session_id}` | force-score one session (admin/debug) |
| `POST` | `/render/student/{session_id}` | force re-render |
| `POST` | `/render/cohort/{cohort_id}` | build the cohort report |
| `POST` | `/norms/recompute` | recompute norm tables for a population |
| `GET` | `/version` | engine + template + instrument versions |

`POST /tick` response:

```json
{"claimed": 4, "done": 3, "failed": 1,
 "by_kind": {"score_session": 2, "render_student": 2},
 "duration_ms": 8421}
```

### Web → engine

Only two calls, both from server-side code:
`POST /tick` is *not* one of them (that is the cron's job). The web app calls
`POST /render/cohort/{id}` when a counsellor clicks "Generate cohort report", and `/version` on the
admin page. Everything else flows through the `jobs` table.

---

## 11. Job runner and keep-alive

### Claiming (must be safe against overlapping ticks)

```sql
update jobs
set status = 'running', claimed_at = now(), attempts = attempts + 1
where id in (
  select id from jobs
  where status = 'pending' and run_after <= now()
  order by created_at
  limit 10
  for update skip locked
)
returning *;
```

`for update skip locked` is what makes two overlapping ticks safe. Do not replace it with a
select-then-update.

### Retry

On failure: `status = 'pending'`, `run_after = now() + interval '5 minutes' * attempts`,
`last_error` set. At `attempts >= 5`: `status = 'failed'` and alert (an email to you is enough).
A failed job means a student has no report — treat it as a real incident, not a log line.

### `.github/workflows/tick.yml`

```yaml
name: tick
on:
  schedule: [{ cron: "*/5 * * * *" }]
  workflow_dispatch:
jobs:
  tick:
    runs-on: ubuntu-latest
    steps:
      - name: Wake engine and run jobs
        run: |
          curl -sS -m 120 -X POST "$ENGINE_BASE_URL/tick" \
            -H "X-Engine-Key: $ENGINE_SHARED_SECRET" \
            --retry 3 --retry-connrefused --fail-with-body
        env:
          ENGINE_BASE_URL: ${{ secrets.ENGINE_BASE_URL }}
          ENGINE_SHARED_SECRET: ${{ secrets.ENGINE_SHARED_SECRET }}
```

`--retry-connrefused` matters: the first request wakes a sleeping host and may fail while it boots.

Scheduled GitHub Actions on free plans can be delayed at peak times and are disabled after 60 days
of repository inactivity. Both are fine for v1 — a five-minute job running eight minutes late is
invisible — but on pilot day, run `workflow_dispatch` manually rather than waiting.

---

## 12. Web application

### Routes

```
/                              marketing
/free-test                     public RIASEC-only taster → email capture   (M11)
/login  /logout
/dash                          cohort list
/dash/cohorts/new
/dash/cohorts/[id]             roster, progress, per-student reports
/dash/cohorts/[id]/upload      CSV
/dash/cohorts/[id]/report      cohort report: generate, view, download
/dash/settings                 org name, logo, brand colour
/a/[token]                     student taker: consent → modules → done
```

### Taker flow — the part that decides whether students finish

- **One item per screen on mobile**, `n of 130` progress, module name visible.
- Autosave every answer immediately (`POST` per response, optimistic UI). A student on a patchy
  campus connection must never lose progress. Queue failed writes in `localStorage` and flush.
- Resume from `sessions.progress` on reopen, with a "welcome back, continuing at item 47" screen.
- **Card sort:** drag-and-drop into five columns of four, with a **tap-to-place fallback** (tap
  card, tap column) that is always available, not only on touch failure. Drag-and-drop on a
  mid-range Android browser is unreliable; the fallback is the real interface for many students.
  Block submit until all five columns hold exactly four.
- Consent screen first: what is measured, who sees it, that it is voluntary, how to withdraw. Record
  `participants.consent_at`. No consent, no items.
- No back-navigation past a submitted module; free movement within one.
- Finish screen: "your report will reach you by email within a few minutes" — sets expectations that
  match the tick cadence.

### Roster CSV

```csv
full_name,email,external_ref,intended_field,education_level
Ayesha Khan,ayesha@example.edu.pk,2023-CS-014,Computer Science,undergraduate
```

Validate before insert: required columns present, emails well-formed, `intended_field` in the
controlled vocabulary, no duplicate `external_ref` within the cohort. Show a preview table with
per-row errors and import nothing until the file is clean. Generate a 32-byte urlsafe token per
participant, store only `sha256(token)`, and surface the links once for distribution.

---

## 13. Report specification

Jinja2 → HTML → WeasyPrint → PDF. A4, 18 mm margins. Charts are hand-written SVG in
`report/charts.py` — a RIASEC hexagon and horizontal bars. No plotting library; the output is two
shapes and WeasyPrint renders inline SVG well.

### Student report (~12 pages)

1. Cover — institution logo and colour, student name, date, instrument versions.
2. How to read this report — what it is and is not (R7), one page, plain language.
3. Interests — hexagon, six bars, the Holland code, and the differentiation statement.
4. What your code means — human-written interpretation from `interpretations.yaml`.
5. Personality — five bars with provisional bands and a clear note on what "provisional" means.
6. Trait interpretations — human-written, strengths-framed, no pathologising language.
7. Work values — six bars, top two explained.
8. Where interests and values agree, and where they pull apart.
9–10. Occupation matches — local first with the Pakistani pathway, international after.
11. Questions to bring to your counsellor — six prompts drawn from the profile.
12. Method and attribution — instruments, licences, USDOL credit (R6), limitations.

The counsellor's copy is the same PDF plus a flags appendix. Two renders, one template, a
`show_flags` boolean.

### Cohort report (~8 pages)

Cover · headline numbers · completion and exclusions · Holland distribution · congruence by field
(the headline table) · value conflicts · follow-up list · method page.

### Template versioning

`template_version` in `reports` and on the last page. When you change a template, old reports stay
reproducible because the version is recorded and the raw responses still exist (R1).

---

## 14. Email

`engine/app/email.py`, provider behind an interface so Resend can be swapped in an hour.

| Template | Trigger | Contains |
|---|---|---|
| `invite` | counsellor sends invites | what it is, ~25 min, the link, who sees results |
| `reminder` | 72 h, not started (counsellor-triggered) | one nudge only, ever |
| `student_report` | report rendered | 7-day signed link, not an attachment |
| `counsellor_digest` | cohort hits 100% or counsellor asks | completion summary, link to dashboard |
| `job_failed_alert` | attempts ≥ 5 | to you, with the job id |

Rules: never attach a student PDF to email (mail servers keep copies forever); always a signed URL
that expires. Every email carries the institution's name so a parent knows why it arrived. One
reminder maximum — a tool that nags students is a tool a school drops.

---

## 15. Privacy, consent and minors

**Most participants in a school cohort are under 18.** This is not a compliance footnote; it is the
thing that ends the business if handled carelessly.

- **Consent before items**, in plain language, on screen, recorded with a timestamp. A student may
  decline, and declining is visible to nobody.
- **Disclose the audience up front:** the student gets the full report; their counsellor at
  <institution> gets the full report and quality flags; the institution gets aggregates. Nothing
  goes anywhere else. Say this on the consent screen in those words.
- **School consent is the institution's job, not yours.** Require the counsellor to confirm in the
  dashboard that guardian consent is in place for participants under 18, before invites can be sent.
  Store that confirmation with the counsellor's identity and timestamp in `audit_log`.
- **Norming use is opt-in and separate.** A checkbox, unticked by default, for anonymised inclusion
  in norm tables. Norms use scale scores stripped of name, email and `external_ref`, keyed to a
  random id. If the student declines, they still get their report.
- **Retention:** raw responses kept while the institution's account is active plus 24 months, then
  anonymised for norms and stripped of identifiers. Publish this on the site before the first pilot.
- **Deletion:** a documented path for a student or guardian to request removal, reaching a real
  address you monitor. Removal deletes participant, session, responses, scores and report.
- **Never** send a student's individual results to a parent, employer or third party — even when
  the institution asks. Say so in the pilot agreement and hold the line.
- **Storage:** report PDFs in a private bucket, signed URLs only, 7-day expiry, no public bucket ever.

---

## 16. Build order — milestones

Roughly twenty hours per week for twelve weeks. Each milestone ships something testable. Do not
start a milestone before the previous one's test passes.

### M0 — Repo and skeleton *(week 1, ~4 h)*
Create the repo, both apps, `.env` templates, CI that runs lint + tests on push.
**Done when:** `pytest` and `next build` both pass on an empty project in CI.

### M1 — Instrument data loaded *(week 1, ~10 h)*
Download the three instruments (R2). Write `data/instruments/*.csv` and `values_scoring.json`.
Migration `0001_init.sql`. `scripts/load_instruments.py` is idempotent.
**Done when:** `select count(*) from items` returns 130 and re-running the loader changes nothing.

### M2 — Scoring engine, offline *(week 1–2, ~16 h)*
`scoring/*` as pure functions plus tests. No web, no database.
**Done when:** every §17.1 test passes, including the WIL worked example from the published manual.

### M3 — O*NET load and matching *(week 2, ~14 h)*
`load_onet.py`, `matching/occupations.py`, `diversify`, job-zone filter.
**Done when:** three hand-constructed profiles (a clear I-type, a clear S-type, a flat profile)
return sensible, non-duplicated top-15 lists, and the flat profile is flagged undifferentiated.

### M4 — Supabase, auth, orgs, RLS *(week 3, ~16 h)*
Full schema, RLS policies, Next.js shell, login, org and cohort CRUD, daily keep-alive.
**Done when:** the cross-tenant leak test (§17.3) passes and a deployed URL serves a login page.

### M5 — Roster upload and invites *(week 3–4, ~12 h)*
CSV validation with a preview table, participant creation, token generation, invite email.
**Done when:** a 40-row CSV imports, 40 unique links exist, and a deliberately broken row is
rejected with a message naming the row and the problem.

### M6 — Taker flow *(week 4, ~20 h)*
Consent, both Likert modules, the card sort with tap fallback, autosave, resume, submit.
**Done when:** you complete all 130 items plus the sort on a real mid-range Android phone, kill the
connection at item 60, reopen, and resume at 60 with nothing lost.

### M7 — Job runner *(week 5, ~8 h)*
`jobs` table, claim-with-skip-locked, retry/backoff, `/tick`, the GitHub Actions cron.
**Done when:** submitting a session produces a `scores` row within one tick without any manual step.

### M8 — Student report *(weeks 5–6, ~26 h)*
Jinja templates, SVG charts, WeasyPrint, branding, storage upload, signed-link email.
**Done when:** a submitted session yields a branded 12-page PDF in an inbox, and every band in
`interpretations.yaml` has human-written text with no placeholder left.
**This is the week 6 milestone — the artifact you take to GIFT's counselling office.**

### M9 — Counsellor dashboard *(week 7, ~16 h)*
Roster view with live status, resend invites, per-student report download, org branding settings.
**Done when:** a counsellor can run a cohort start to finish without you touching anything.

### M10 — Cohort report *(week 8, ~18 h)*
Field centroids, congruence, distributions, value conflicts, follow-up list, method page.
**Done when:** a 40-participant synthetic cohort produces a cohort PDF whose headline congruence
number you can recompute by hand from the raw data.

### M11 — Pilot hardening *(week 9, ~10 h + the pilot itself)*
Load-test 60 concurrent submissions. Error boundaries. An admin page showing job status. A
one-command "rerun everything for this cohort".
**Done when:** the GIFT pilot runs with no manual database intervention.

### M12 — Norms, free test, pricing *(weeks 10–12, ~24 h)*
`norms/recompute` with alpha coefficients per scale, the public RIASEC-only free test with email
capture, pricing page, invoice-based institution signup.
**Done when:** norms exist for your first population with `n` recorded, and you compare your alphas
against the published figures in writing.

**Reserve week 12 for the second school and the invoice. If M12 slips, ship the invoice anyway —
the paying customer is the milestone, the norm table is not.**

---

## 17. Testing

### 17.1 Scoring — `engine/tests/`

Golden fixtures in `tests/fixtures/`, one JSON per case: responses in, expected scores out.

| Test | Asserts |
|---|---|
| `test_interests_all_max` | every scale 40, differentiation 0, band `undifferentiated` |
| `test_interests_known_profile` | hand-computed sums, correct three-letter code |
| `test_interests_tiebreak` | fixed R,I,A,S,E,C order, `tie_broken: true` |
| `test_personality_reverse` | a reverse item at 1 scores 5 on a 1–5 scale |
| `test_personality_no_hardcoded_six` | passes for a hypothetical 0–6 item |
| `test_values_worked_example` | **matches the published WIL score-report example exactly** |
| `test_values_invalid_sort` | 3 cards in a column raises, does not zero-fill |
| `test_flags_straightline` | 12 identical answers flags, 11 does not |
| `test_matching_diversity` | no more than 2 results share a 2-digit SOC group |
| `test_matching_job_zone` | a matric student receives no Job Zone 5 occupation |
| `test_rescore_idempotent` | rescoring twice at one version yields identical values |

`test_values_worked_example` is the single most important test in the repo. It is the only
independent check that your transcription of the scoring worksheet is right.

### 17.2 Report

Render with a fixture session; assert the PDF has the expected page count, that no template
placeholder string survives, and that the attribution line is present. `pdftotext` plus a substring
check is sufficient and fast.

### 17.3 Security

- **Cross-tenant:** counsellor A queries cohort B's participants → 0 rows.
- **Token:** a random token 404s; a valid token resolves; a used-and-submitted token cannot rewrite
  responses.
- **Service role:** CI greps the client bundle for the service-role key and fails on a hit.
- **Engine auth:** every route without `X-Engine-Key` returns 401.

### 17.4 Manual, before the pilot

Run the whole flow on a real mid-range Android phone on mobile data, in a browser you have never
tested. Then hand the phone to someone who has not seen the product and say nothing while they use
it. Write down every place they hesitate. That list is worth more than the rest of this section.

---

## 18. Deployment runbook

**Web (Vercel):** link the repo, set env vars for preview and production, deploy on push to `main`.

**Engine:** Dockerfile on Python 3.12-slim. WeasyPrint needs system libraries — install
`libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 libcairo2 libgdk-pixbuf-2.0-0`.
Missing these is the number-one cause of "works locally, blank PDF in production".

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
    libcairo2 libgdk-pixbuf-2.0-0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Bundle the report's fonts into the image. Do not rely on a Google Fonts fetch at render time — the
PDF will silently fall back and look wrong, and only on production.

**Database:** apply migrations in order through the Supabase SQL editor or the CLI. Never edit a
migration that has run; add a new one.

**Pilot-day checklist**

1. Run `workflow_dispatch` on the tick workflow 30 minutes before, and confirm `/health` is 200.
2. Confirm the Supabase project is not paused.
3. Generate one test report end to end that morning.
4. Have the invite links on a printed sheet or a QR code — campus wifi will fail at the wrong moment.
5. Watch the admin job page during the session.

---

## 19. Open items needing a human decision

These block specific milestones. None can be answered from code.

1. **`intended_field` controlled vocabulary** — blocks M5 and all of M10. Needs the real list your
   students choose from: Pre-Medical, Pre-Engineering, ICS, Commerce, Humanities, and the degree
   programmes downstream. Wrong list, worthless cohort report.
2. **Which GIFT department and when** — blocks M11 scheduling and sets the whole calendar.
3. **Education-level → Job Zone mapping** — the §8 defaults are a guess. Confirm against what your
   students actually go on to do.
4. **Institution pricing for school two** — blocks M12. Decide before week 11, not during the call.
5. **Guardian-consent wording** — should be reviewed by someone at GIFT who knows what the
   institution requires. Do not draft this alone.
6. **The $5/month decision** — if yes, the engine moves to a paid hobby tier at M8 and cold-start
   handling stops mattering. If no, M11 must include a documented warm-up procedure.

---

## Appendix A — Commands

```bash
# engine
cd engine && pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest -q
python scripts/load_instruments.py
python scripts/load_onet.py
python scripts/rescore_all.py --dry-run

# web
cd web && pnpm install && pnpm dev
pnpm test

# db
supabase db push
```

## Appendix B — Definition of done for v1

- [ ] A counsellor uploads a 40-row roster and sends invites without help.
- [ ] 40 students complete on their own phones; ≥ 90% finish in one sitting.
- [ ] 40 branded student PDFs are delivered by email automatically.
- [ ] A cohort PDF states a congruence rate you can recompute by hand.
- [ ] `rescore_all.py` reruns every session and changes nothing at the same engine version.
- [ ] Cross-tenant and token security tests pass in CI.
- [ ] Every interpretation string is human-written; no placeholder survives.
- [ ] O*NET attribution appears on every report and on the site.
- [ ] A second school has been invoiced.
