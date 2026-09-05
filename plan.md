# Percentile — Implementation Plan (v1)

> **What this file is.** The complete build spec for Percentile v1 — the Phase 1 ("destination
> identification") slice of the Clarity Compass vision: assessment, human-reviewed interpretation,
> and a report, for institutions. It is written to be handed to a coding agent (Claude Code,
> Cursor) or followed by hand, milestone by milestone. Every milestone has files to create, a
> definition of done, and a test that proves it.
>
> **Read `## 0. Rules` before writing any code.** Several rules exist because breaking them
> destroys the product's credibility, not because they are stylistic preferences.
>
> **v2 of this document.** Revised after two conversations with practising wellbeing counsellors
> and against the "Clarity Compass" concept deck. Three changes from v1: the third assessment
> module is GET2 (entrepreneurial tendency) instead of the retired Work Importance Locator; every
> student's results go through a human counsellor/psychologist before a final report is issued,
> not straight from engine to inbox; and the deck's Phase 2 — an AI-driven adaptive roadmap with
> live rerouting and an LMS connector — is deliberately deferred to v2 (§21) rather than built now.
> Read §19 for exactly what changed and why.
>
> Status: v2 spec, September 2026. Owner: Syed Hassan Raza.

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
9. [Psychologist review workflow](#9-psychologist-review-workflow)
10. [Cohort analytics specification](#10-cohort-analytics-specification)
11. [API contract](#11-api-contract)
12. [Job runner and keep-alive](#12-job-runner-and-keep-alive)
13. [Web application](#13-web-application)
14. [Report specification](#14-report-specification)
15. [Email](#15-email)
16. [Privacy, consent and minors](#16-privacy-consent-and-minors)
17. [Build order — milestones](#17-build-order--milestones)
18. [Testing](#18-testing)
19. [What changed in v2, and why](#19-what-changed-in-v2-and-why)
20. [Open items needing a human decision](#20-open-items-needing-a-human-decision)
21. [Deferred to v2 — Phase 2, the navigation engine](#21-deferred-to-v2--phase-2-the-navigation-engine)

---

## 0. Rules that are not negotiable

**R1 — Raw responses are sacred; everything else is derived.**
Never store a computed score without also storing every raw item response that produced it.
Scoring rules, interpretation text and norms will change many times in year one. It must always
be possible to rescore every historical session with one command. If that is not possible, every
improvement orphans the norming sample.

**R2 — Never invent instrument items.**
Interest Profiler items come from onetcenter.org, IPIP items from ipip.ori.org, GET2 items from
Sally Caird's published guide (§6.3). Download or transcribe them. Do not let any model generate,
paraphrase, "improve" or reorder them. A paraphrased item is a different item with unknown
properties, and every reliability figure you quote becomes a lie. All code in this repo reads
items from data files (§6) and must work with an empty item table on day one.

**R3 — Interpretation text is written by a human with psychology training.**
The differentiator of this product is that a trained person wrote the interpretations, and — new
in v2 — a trained person reviews every individual result before it becomes a final report. Generated
interpretation copy is not defensible in a room with a PhD in it. Use a model to draft *product*
copy, error messages, and this documentation — never the report's interpretive content, and never
anything presented as the psychologist's own clinical judgement.

**R4 — No percentiles until local norms exist.**
Until the norm sample for a scale reaches n ≥ 300 for a defined population, reports show raw
scores, within-person rank and provisional bands, and state on the page that percentiles await
local norms. Never display US percentiles for Pakistani students.

**R5 — English only in v1.**
A translated instrument requires back-translation and a fresh reliability study. The site may say
an Urdu edition is in development. It may not ship one.

**R6 — Attribution is mandatory.**
Every report page and the site footer must credit O*NET and the U.S. Department of Labor,
Employment and Training Administration (§6.1–6.2), and GET2's author, Dr. Sally Caird / The Open
University (§6.3), per the terms each instrument is used under. If O*NET items are modified in any
way, the O*NET developer licence applies: the modified version must be independently validated and
marked as not USDOL-endorsed. Do not modify O*NET items in v1.

**R7 — No clinical language.**
This is a career interest, personality and entrepreneurial-tendency instrument suite. It does not
diagnose, screen, or assess mental health, ability or intelligence. Report copy, marketing copy and
UI copy must never imply it does — and the psychologist review step (§9) must never be described to
students or parents as therapy, counselling for distress, or a clinical service. It is career
guidance with a qualified human checking the machine's output.

**R8 — Student results belong to the student.**
Individual reports go to the student. Institutions receive aggregates plus, where the institution
is the counsellor of record, the individual reports for their own cohort — disclosed to students
before they start. Never a third party. See §16.

**R9 — No final report leaves the system without human sign-off.**
This is the central change in v2. `scores` and `occupation_matches` are machine output and are
never emailed to a student directly. A session cannot reach `reports` (student kind) until a
`reviews` row exists with `status = 'confirmed'` (§9). This is enforced in the database, not just
in application code — see the trigger in §5. Treat any code path that bypasses this as a severity-1
bug, not a shortcut.

**R10 — GET2 use is provisional until permission is confirmed in writing.**
GET2 was published by Dr. Sally Caird through The Open University for research and educational use,
and a free public test exists at get2test.net, which reads as an invitation to use it — but no
written commercial-use terms were found during scoping. Email the Open University / Dr. Caird
before any paying institution sees a GET2-based report (M1, §20 item 1). Until that permission is
confirmed, label GET2 output internally as "provisional — pending permission" and do not use it in
sales material. If permission is refused or ignored past the pilot date, fall back to two modules
(interests + personality) — the scoring and report code must not assume GET2 is guaranteed to ship.

---

## 1. What v1 is

A web application implementing **Phase 1 of Clarity Compass** ("Assess → Identify") for
institutions: a counsellor uploads a class roster, each student completes a ~20-minute two- or
three-module assessment on their phone, a psychologist (or trained counsellor acting in that role)
reviews the machine output plus a short interview and confirms 2–3 career directions, and two
artifacts come out:

- **Student report** — branded PDF, ~13 pages, released only after psychologist confirmation, emailed
  to the student and stored for the counsellor.
- **Cohort report** — branded PDF for the institution: interest distributions, the intended-field
  congruence rate, and named lists of students to follow up.

Phase 2 of the deck — the AI-driven roadmap, milestone tracking, live rerouting and LMS connector —
is **v2, not v1**. §21 specifies what it is and why it waits. Building it now, before Phase 1 has a
single paying customer, is a six-month detour with no revenue at the end of it.

### Why the psychologist step, and what it costs you

Two wellbeing counsellors told you plainly: an automated report with nobody checking it is a
liability, and clients want a human in the loop. That is also, not coincidentally, the thing that
makes this defensible against a free internet quiz. The cost is real and you should go in with eyes
open: **one psychologist can review perhaps 15–25 students inside a single pilot week**, not the
400-student cohort the original plan assumed. §9 and §17 (M11) are sized to that reality.

### In scope

| | |
|---|---|
| Counsellor and psychologist accounts, scoped to an organisation | Email + password auth, two roles |
| Roster upload, bulk invite, progress tracking | CSV |
| Two-to-three module assessment, resumable, mobile-first, unhurried but not tedious | Interests + personality, GET2 if §20 item 1 clears in time |
| Scoring engine with flags and provisional bands | §7 |
| Occupation matching with a Pakistan mapping layer | §8 |
| **Psychologist review queue: scores, flags, interview notes, destination confirmation** | §9 |
| Student PDF, released only post-confirmation, institution-branded | §14 |
| Cohort PDF and dashboard | §10, §14 |
| Rescore-everything command | R1 |

### Explicitly out of scope for v1

Student accounts · self-serve signup · card checkout · Urdu · the 120-item deep personality profile ·
aptitude/ability testing · a mobile app · custom domains per tenant · any AI-generated report or
interview content · Phase 2 in full (§21): the O*NET roadmap engine, milestone tracker, adaptive
rerouting, LMS connector, and push notifications.

---

## 2. Architecture

```
                    ┌──────────────────────────┐
  Counsellor  ───►  │  Next.js (Vercel)        │
  Psychologist───►  │  taker UI, dashboards,   │
  Student     ───►  │  review queue,           │
                    │  server actions          │
                    └───────┬──────────┬───────┘
                            │          │
                     writes │          │ enqueue job row
                            ▼          ▼
                    ┌──────────────────────────┐
                    │  Supabase (Postgres)     │
                    │  auth · data · storage   │
                    │  DB trigger enforces R9  │
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

Unchanged from v1: the engine is never called synchronously from a user request; a job queue plus
a five-minute GitHub Actions cron does the work, which keeps a sleeping free-tier host harmless and
the Supabase free project from pausing on inactivity. See v1 rationale, preserved below in §12.

**What's new structurally:** a session's life cycle now has a human checkpoint between scoring and
reporting. `scores` land automatically; `reports` (student kind) require a `reviews` row confirmed
by a psychologist. The web app gains a psychologist-facing queue (§9, §13) alongside the
counsellor's roster dashboard.

### Stack — unchanged

| Layer | Choice | Why |
|---|---|---|
| Web | Next.js (App Router) + TypeScript + Tailwind, on Vercel | Free tier, your comfort zone |
| Data/auth/storage | Supabase | Postgres + auth + object storage in one free tier |
| Engine | Python 3.12, FastAPI, numpy, pandas, Jinja2, WeasyPrint | Scoring and PDF are Python's home ground |
| Scheduler | GitHub Actions cron | Free, outside the sleeping host |
| Email | Resend (swap-able behind an interface) | Free tier covers a pilot cohort many times over |
| Engine host | Any free container host; Railway Hobby ($5/mo) when it matters | Treat as disposable compute |

---

## 3. Repository layout

Unchanged from v1 except two additions, marked `NEW`:

```
percentile/
├── plan.md
├── README.md
├── .github/workflows/tick.yml
├── db/
│   ├── migrations/
│   │   ├── 0001_init.sql
│   │   ├── 0002_rls.sql
│   │   ├── 0003_reviews.sql        # NEW — §9 tables and the R9 trigger
│   │   └── ...
│   └── seed/demo_org.sql
├── data/
│   ├── instruments/
│   │   ├── interests_items.csv        # O*NET IP-SF, 60 rows
│   │   ├── personality_items.csv      # IPIP Big Five markers, 50 rows
│   │   ├── get2_items.csv             # NEW — GET2, ~54 rows, verify count in M1
│   │   └── get2_scoring.json          # NEW — subscale → item map, response weights
│   ├── onet/  (unchanged: occupation_data.csv, interests.csv, work_values.csv, job_zones.csv)
│   └── local/pk_occupation_map.csv
├── engine/
│   ├── app/
│   │   ├── scoring/
│   │   │   ├── interests.py
│   │   │   ├── personality.py
│   │   │   ├── get2.py             # NEW, replaces values.py
│   │   │   ├── flags.py
│   │   │   └── engine.py
│   │   ├── matching/{occupations.py, cohort.py}
│   │   ├── review/                 # NEW
│   │   │   └── workflow.py         # state transitions, §9
│   │   ├── report/{student.py, cohort.py, charts.py, templates/}
│   │   ├── content/{interpretations.yaml, occupations_pk.yaml}
│   │   └── email.py
│   ├── scripts/{load_instruments.py, load_onet.py, rescore_all.py}
│   └── tests/
└── web/
    ├── app/
    │   ├── (marketing)/
    │   ├── (dash)/                 # counsellor: roster, cohort report, org settings
    │   ├── (review)/               # NEW — psychologist queue and interview form
    │   └── a/[token]/              # student taker
    ├── components/
    ├── lib/
    └── tests/
```

---

## 4. Accounts, secrets, environment

Unchanged from v1. See Appendix A for the full `.env` templates (`web/.env.local`, `engine/.env`).
One addition: if GET2 permission (R10, §20 item 1) requires crediting a specific licence text on
the report, add it to `engine/app/content/occupations_pk.yaml`'s sibling `attributions.yaml` once
the wording is confirmed — do not hard-code attribution strings inside templates.

---

## 5. Database schema

Everything from v1 §5 stands. This section gives the v2 additions as a new migration,
`0003_reviews.sql`, plus the one changed table (`participants.status` gains new values) and the
GET2-shaped `scores.get2` column.

### Changed: `participants.status`

```
invited → started → submitted → scored → pending_review → reviewed → confirmed
                                                    ╲
                                                     → needs_more_info → pending_review (loop)
```

`failed` remains a terminal error state reachable from any point. `reviewed` means the
psychologist has entered notes but not yet locked in a destination; `confirmed` means the
student report can render (R9).

### `db/migrations/0003_reviews.sql`

```sql
-- ── psychologist role ────────────────────────────────────────────────────
alter table profiles
  drop constraint if exists profiles_role_check;
alter table profiles
  add constraint profiles_role_check
  check (role in ('counsellor', 'psychologist', 'org_admin', 'superadmin'));

-- ── occupation catalogue: mark entrepreneurship-track roles ─────────────
alter table occupations add column if not exists entrepreneurial_track boolean not null default false;

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
```

### GET2 in `scores`

No migration needed — `scores.values_scores` (v1's name for the third module) simply now holds the
GET2 shape instead of the WIL shape:

```json
{"instrument": "GET2",
 "subscales": {"achievement": 67, "autonomy": 17, "creativity": 42, "risk_taking": 33, "control": 50},
 "get2_total": 44,
 "entrepreneurial_band": "moderate"}
```

If R10 permission does not clear in time, `scores.values_scores` is written as
`{"instrument": null, "status": "module_not_administered"}` and every downstream template branches
on `instrument` rather than assuming the key exists. Write that branch into `engine.py` from day
one (M2) so dropping the module later is a config flag, not a rewrite.

### RLS additions

`reviews`, `review_events`, `career_directions`: readable/writable only by profiles with
`role in ('psychologist', 'org_admin', 'superadmin')` scoped to the session's organisation, plus a
narrow read-only policy so a `counsellor` can see `status` and `confirmed_at` (to track progress)
but not `interview_notes` unless they also hold the psychologist role. Write a test for exactly
that boundary — a counsellor reading interview notes is the kind of leak that ends a pilot.

---

## 6. Instrument data files

Everything from v1 §6.1 (Interest Profiler) and §6.2 (IPIP-50) is unchanged. This replaces v1 §6.3
(Work Importance Locator, now retired — see §19) with GET2, and keeps the O*NET occupation files
(§6.4 below, was §6.4) as-is.

### 6.3 `data/instruments/get2_items.csv` and `get2_scoring.json` — NEW

Source: Caird, S., *General Measure of Enterprising Tendency v2 (GET2)*, published through The
Open University (Open Research Online) and hosted at get2test.net. **R10 applies: confirm written
permission before this module reaches a paying customer.**

Five subscales, confirmed from the published test-results documentation:
`achievement` (need for achievement), `autonomy` (need for autonomy), `creativity` (creative
tendency), `risk_taking` (calculated risk-taking), `control` (internal locus of control).

**Item count and exact response scale need to be pinned from the primary source during M1** — the
figures circulating in secondary summaries (commonly cited as 54 items, a 3-point
agree/uncertain/disagree scale) were not independently confirmed while scoping this plan. Download
Caird's guide PDF directly from Open Research Online (`oro.open.ac.uk`, item id 5393) rather than
trusting a course-notes reproduction, and transcribe from that.

```csv
code,ordinal,text,scale,response_min,response_max,reverse_keyed
GET2_ACH_01,1,"<item text exactly as published>",achievement,0,2,false
...
```

```json
{
  "subscales": {
    "achievement":  { "items": ["GET2_ACH_01", "..."], "max_raw": 24 },
    "autonomy":     { "items": [...], "max_raw": 24 },
    "creativity":   { "items": [...], "max_raw": 24 },
    "risk_taking":  { "items": [...], "max_raw": 24 },
    "control":      { "items": [...], "max_raw": 24 }
  },
  "reporting": "percentage_of_max"
}
```

Report each subscale as a percentage of its own maximum (matching how GET2 results are
conventionally presented) rather than inventing a new scale — a counsellor who has seen a GET2
report before should recognise the shape.

### 6.4 `data/onet/*.csv` and `data/local/pk_occupation_map.csv`

Unchanged from v1. One addition: hand-flag `entrepreneurial_track = true` on a subset of your
Pakistan-mapped occupations (small business owner / trader, franchise operator, tech founder track,
agri-business) for the report section described in §7.3 and §14.

---

## 7. Scoring specification

`engine/app/scoring/`. Pure functions: raw responses in, score dict out. No database access inside
the scoring modules — that makes them trivially testable and rescoreable.

### 7.1 Interests, 7.2 Personality

Unchanged from v1 — see the full spec preserved below. Interests: O*NET Interest Profiler Short
Form, 60 items, six 0–40 scales, differentiation and Holland-code logic exactly as before.
Personality: IPIP-50, reverse-keying via `response_max + response_min - raw`, five domain sums
10–50 with provisional bands, switching to normative bands once `norms` has `n ≥ 300`.

### 7.3 Entrepreneurial tendency — `get2.py` (replaces v1's `values.py`)

```python
def score_get2(responses: dict[str, int], scoring: Get2Scoring) -> dict:
    """Returns None-shaped output if the module wasn't administered (R10 fallback)."""
```

1. If GET2 was not administered for this session (org config, or R10 permission not yet cleared):
   return `{"instrument": None, "status": "module_not_administered"}` and stop.
2. Per subscale: `raw = sum(responses[i.code] for i in scoring.subscales[name].items)`.
3. `pct = round(100 * raw / scoring.subscales[name].max_raw)`.
4. `get2_total = mean of the five subscale percentages`, rounded.
5. Band, provisional until norms exist:

   | `get2_total` | Band |
   |---|---|
   | < 35 | emerging |
   | 35–64 | moderate |
   | ≥ 65 | strong |

6. `entrepreneurial_flag = true` if `get2_total ≥ 65` **or** `risk_taking ≥ 70 and autonomy ≥ 70` —
   this drives whether the report surfaces the entrepreneurship-track occupations from
   `pk_occupation_map.csv` (§6.4) and whether the psychologist's review screen highlights it.

GET2 is deliberately **not** folded into the cosine-similarity occupation match in §8 — it measures
a different thing (tendency to act, not interest content) and O*NET occupation profiles have no
equivalent axis to compare it against. It surfaces as its own report section and as a boolean flag
that biases which occupations get shown, not as a fourth dimension bolted onto the interest vector.

### 7.4 Response-quality flags — `flags.py`

Unchanged from v1 (straightlining, too-fast, inconsistent reverse-keyed pairs, long gaps,
incomplete card sort — the last one drops out if the values/card-sort module is gone; add
`get2_uniform_response` — every subscale within 5 points of each other, which usually means the
respondent didn't engage). Flags appear only on the psychologist's review screen (§9), never on the
student's copy.

### 7.5 Orchestration — `engine.py`

```python
def score_session(session_id) -> ScoreRecord:
    # 1. load items + responses
    # 2. run interests, personality, get2 (or its null shape) + flags
    # 3. write scores row stamped with SCORING_ENGINE_VERSION
    # 4. enqueue: match_occupations
    # 5. set participants.status = 'pending_review'
    # 6. enqueue: notify_psychologist  (NOT render_student — R9)
```

The one change from v1: scoring no longer chains straight to rendering. It hands off to the review
queue (§9). `rescore_all.py` still works exactly as in v1 — it writes new `scores` rows under the
current engine version without touching `reviews`, so a rescore never silently re-releases a report
that hasn't been re-confirmed.

---

## 8. Occupation matching specification

Unchanged from v1: ipsatize, cosine similarity against the interest vector, Job Zone filter by
education level, `diversify` capped at 2 per SOC major group, Pakistan-relevant results shown
first. One addition: when `entrepreneurial_flag` is true (§7.3), include up to 3 results from
`entrepreneurial_track = true` occupations even if their raw cosine score would have placed them
outside the top 15, clearly labelled in the report as "worth exploring given your GET2 profile"
rather than blended anonymously into the ranked list.

---

## 9. Psychologist review workflow

This section is new in v2 and is the mechanical heart of R9. It exists because the counsellors you
spoke to were specific: they don't want an assessment that hands a seventeen-year-old a PDF with no
human between the algorithm and the kid.

### 9.1 What a psychologist sees

A queue at `/review` (organisation-scoped, `role = 'psychologist'` only), sorted oldest-first:

- Student name, cohort, intended field, time since submission.
- All engine output: interest hexagon, personality bars, GET2 subscales (or "not administered"),
  the top-15 occupation matches, and — prominently, not buried — every quality flag from §7.4 with
  its plain-language meaning. `undifferentiated` and `straightlining` should be visually distinct
  from `info`-level flags; they change what the review conversation needs to cover.
- A free-text interview notes field (R3 — this is the psychologist's clinical voice, never
  model-generated, never auto-filled from the scores).
- A destination picker: search/select up to 3 `career_directions`, each with a short rationale
  field. At least 1, at most 3 (matching "2–3 suitable career paths" from the deck).
- Two actions: **Save draft** (`reviews.status = 'in_progress'`, can return later) and **Confirm**
  (`status = 'confirmed'`, `confirmed_at = now()`, enqueues `render_student`).
- A **Send back** action available even after a first pass: sets `participants.status =
  'needs_more_info'` — used when the psychologist wants a second conversation before confirming.

### 9.2 Interview modes

`interview_mode` is recorded but the product does not schedule or host the interview in v1 — that
is deliberately out of scope, matching the "polished but conventional" answer on gamification and
the general instinct to not build tooling nobody asked for yet. In the pilot, interviews happen
however the counsellor already runs them (in person, a call) and the notes get typed into the
portal afterward. If a design partner specifically asks for scheduling, that is a v1.1 feature, not
a v1 blocker.

### 9.3 Load and pacing

One review — reading the profile, having or recalling the conversation, writing notes, picking
directions — realistically takes 15–30 minutes once a psychologist is fluent with the screen. Size
every pilot cohort against whoever is doing the reviewing: **15–25 students per reviewer per pilot
week**, not the 40–60 the v1 plan assumed for a purely automated flow. §17 (M11) reflects this.

### 9.4 What happens if nobody reviews it

`pending_review` sessions older than 5 days trigger a `job_failed_alert`-style email to you and the
assigned org's `org_admin`, not to the student. A student never sees "your report is late" — they
see a calm "your counsellor is preparing your results" state (§13). Silence on your end is an
operational problem to fix, not something to expose to a sixteen-year-old.

---

## 10. Cohort analytics specification

Unchanged from v1: field centroids, congruence classes (aligned ≥0.60, partial 0.35–0.60,
misaligned <0.35, provisional thresholds printed on the method page), the named follow-up list, and
the statistical honesty rules (never a percentage on n<10 without the denominator shown). One
addition: the cohort report's completion table now also shows `confirmed` vs `pending_review` counts
— a school comparing this term to next needs to see review-queue backlog as its own number, not
folded into "completion."

---

## 11. API contract

Unchanged from v1 except one route:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | liveness; also touches Postgres (keep-alive) |
| `POST` | `/tick` | claim and run up to N pending jobs |
| `POST` | `/score/{session_id}` | force-score one session (admin/debug) |
| `POST` | `/render/student/{session_id}` | **only succeeds if `reviews.status = 'confirmed'` (R9); returns 409 otherwise** |
| `POST` | `/render/cohort/{cohort_id}` | build the cohort report |
| `POST` | `/norms/recompute` | recompute norm tables for a population |
| `GET` | `/version` | engine + template + instrument versions |

Every route still requires `X-Engine-Key`; the engine is never called from a browser.

---

## 12. Job runner and keep-alive

Unchanged from v1: `for update skip locked` claiming, five-minute cron via GitHub Actions, retry
with backoff up to 5 attempts then `failed` + alert. New job kinds: `notify_psychologist` (fires
once when a session enters `pending_review`) and `review_reminder` (the 5-day nudge from §9.4).

**Constraint on the `invite` email, settled in M5.** The `send_email` job that M5 enqueues carries
`{template: 'invite', participant_id}` and deliberately **not** the token: `jobs` rows are retried,
logged, and copied into `last_error` on failure, which is no place for a live credential to a
minor's record. Only `sha256(token)` is ever stored, so the handler *cannot* reconstruct the
original link. It must therefore **mint a fresh token at send time**, update
`participants.invite_token_hash`, and email that. Any link produced by M5's one-time download
stops working at that moment — correct precedence, since the emailed invite is the one the student
actually receives. See `db/migrations/0005_import_roster.sql`.

---

## 13. Web application

### Routes — one new route group

```
/                              marketing
/free-test                     public RIASEC-only taster → email capture   (M13)
/login  /logout
/dash                          counsellor: cohort list
/dash/cohorts/[id]             roster, progress (invited/started/pending_review/confirmed), reports
/dash/cohorts/[id]/upload      CSV
/dash/cohorts/[id]/report      cohort report
/review                        NEW — psychologist queue (§9.1)
/review/[session_id]           NEW — one student's full review screen
/dash/settings                 org name, logo, brand colour, roles
/a/[token]                     student taker: consent → modules → done → "your counsellor is preparing your results"
```

### Taker flow — mostly unchanged, with the gamification decision folded in

You chose "polished but conventional" over full game mechanics — the right call for a 12-to-15-week
solo build. Concretely, on top of v1's spec (one item per screen, autosave, resumable, tap-fallback
for anything drag-based):

- Progress reads as a **path, not a bar** — a simple horizontal line of dots/segments per module
  with the current position highlighted, rather than a raw "47/130" counter. Cheap to build
  (a handful of styled divs), meaningfully less like a form.
- A short, warm transition screen between modules ("Nice work — next up: how you tend to think
  about things") rather than snapping straight into the next item. One screen, a few seconds,
  costs almost nothing and directly answers the fatigue complaint.
- No points, no badges, no unlockables in v1. If GET2's item count (§6.3) makes the total assessment
  run past ~25 minutes once confirmed, cut there — session length matters more than polish.
- End screen no longer promises a report "within a few minutes" (that was the v1, no-review-step
  copy). It now says results are being reviewed by the student's counsellor and a report will follow
  by email — set the expectation correctly given §9.

### Roster CSV, consent screen

Unchanged from v1 §12. The consent screen (§16) must now also disclose the review step in plain
language: "a counsellor at [institution] will review your results and may follow up for a short
conversation before your report is finalised."

---

## 14. Report specification

Jinja2 → HTML → WeasyPrint → PDF, A4, 18mm margins, hand-written SVG charts. Structure carries over
from v1 with GET2 replacing the work-values pages and a new destination-confirmation section.

### Student report (~13 pages)

1. Cover.
2. How to read this report (R7).
3–4. Interests — hexagon, bars, code, differentiation statement, interpretation text.
5–6. Personality — five bars, provisional bands, interpretation text.
7. **Entrepreneurial tendency (GET2)** — five subscale bars as % of max, the band, and interpretation
   text; page omitted entirely (not shown blank) if the module wasn't administered (R10 fallback).
8–9. Occupation matches — local first, entrepreneurial-track additions labelled per §8.
10. **Your confirmed direction(s)** — NEW. The 1–3 `career_directions` your psychologist selected
    with you, each with its short rationale in the psychologist's words. This page did not exist in
    v1 and is now the page a student is most likely to actually keep.
11. Where interests, personality and entrepreneurial tendency agree or pull against each other.
12. Questions to bring to your counsellor.
13. Method and attribution — instruments, licences, USDOL and Caird/OU credit (R6), review process
    disclosure, limitations.

The counsellor's copy adds a flags appendix (unchanged from v1) and, new, a one-page review summary
(who reviewed, when, interview mode) for the institution's own records.

### Cohort report (~8 pages)

Unchanged structure from v1, with the completion table addition noted in §10.

---

## 15. Email

Unchanged from v1 (`invite`, `reminder`, `job_failed_alert`) with two changes:

| Template | Trigger | Contains |
|---|---|---|
| `results_in_review` | NEW — `scores` written | to the student: results received, a counsellor will review, no report yet |
| `student_report` | report rendered (now only reachable post-confirmation) | 7-day signed link |
| `review_backlog_alert` | NEW — session `pending_review` > 5 days | to org_admin + you, per §9.4 |

Never a PDF attachment (R8/§16); a signed URL, expiring in 7 days.

---

## 16. Privacy, consent and minors

Unchanged from v1 in substance, with the consent screen wording addition from §13 and one new
retention point: `reviews.interview_notes` is the psychologist's clinical-adjacent working notes,
not exam-style feedback — treat it with the same access restriction as health data even though it
isn't health data (R7): visible to the reviewing psychologist and org_admin only, never to a
counsellor without the psychologist role, never in the student-facing report beyond the rationale
line the psychologist explicitly chooses to include on the confirmed-direction page.

---

## 17. Build order — milestones

Roughly twenty hours a week. Twelve milestones, now **~15 weeks / ~300 hours** — v2's added scope
(§19) is real and the honest move is to extend the calendar, not to quietly drop the psychologist
step or GET2 under schedule pressure. Do not start a milestone before the previous one's test
passes.

> **Progress: M0–M6 complete. M7 is next.**
>
> M0–M3 built the repo, the instrument loader, the pure scoring modules and O*NET matching.
> M4 added auth, RLS and the R9 trigger, with 18 database tests proving them against a real
> Postgres in CI. M5 added roster import: a counsellor signs in, creates a cohort, uploads a CSV,
> and gets 20 `invited` participants plus one queued `send_email` job each. M6 built the taker at
> `/a/[token]`, so those invite links now resolve: consent, both modules one item per screen,
> autosave, resume, and a submission that queues a `score_session` job.
>
> **Nothing drains the queue.** `engine/app/email.py` still does not exist and the job runner is
> M7, so both job kinds now sit unclaimed. That ordering was deliberate and has paid off — the
> taker flow existed before any invite was sent, so no student got a link to a 404.
>
> M7's `send_email` handler must mint a fresh token at send time; only `sha256(token)` was ever
> stored, so it cannot reconstruct the link the import screen handed out (§12).

### M0 — Repo and skeleton *(week 1, ~4 h)*
Unchanged from v1. **Done when:** `pytest` and `next build` pass in CI on an empty project.

### M1 — Instrument data loaded *(weeks 1–2, ~14 h — was 10h in v1)*
Download/transcribe all three instruments including GET2 (R2, §6.3). **This is also when you send
the GET2 permission email (R10, §20 item 1) — do it in week 1, not week 10, since the reply time is
outside your control.** Migrations `0001_init.sql` + `0003_reviews.sql`.
**Done when:** `select count(*) from items` matches your transcribed counts across all instruments
and the loader is idempotent.

### M2 — Scoring engine, offline *(week 2–3, ~18 h)*
`scoring/*` including `get2.py` with the `module_not_administered` fallback path exercised by a
test from day one (R10). No web, no database.
**Done when:** every §18.1 test passes, including a GET2 fixture checked by hand against the
published subscale-percentage convention.

### M3 — O*NET load and matching *(week 3, ~14 h)*
Unchanged from v1, plus the `entrepreneurial_track` flag and the 3-slot carve-out from §8.
**Done when:** a high-`entrepreneurial_flag` synthetic profile surfaces at least one
entrepreneurial-track occupation even when its raw cosine score is mediocre.

### M4 — Supabase, auth, orgs, RLS *(week 4, ~18 h — was 16h)*
Full schema including `reviews`/`review_events`/`career_directions`, the R9 trigger, RLS for the two
roles, org/cohort CRUD, daily keep-alive.
**Done when:** the R9 trigger test (insert a `reports` row with no confirmed review → expect the
transaction to fail) passes, and the cross-tenant leak test from v1 still passes.

### M5 — Roster upload and invites *(week 4–5, ~12 h)* — **DONE**
Unchanged from v1. **Done when:** a 20-row CSV imports cleanly with per-row validation errors shown.

Two things in this milestone were *decided*, not inherited — v2 says "unchanged from v1 §12" and
the v1 document is not in the repository:

- **The roster CSV format** now lives in `web/lib/roster-csv.ts`. `full_name` and `email` required;
  `external_ref` / `intended_field` / `education_level` optional; unknown columns ignored;
  `utf-8-sig` because schools export from Excel. Errors are collected per row, not raised on the
  first, and import is all-or-nothing.
- **The `intended_field` vocabulary** (§20 item 4, which named this as blocking M5 *and* M11) is a
  12-value starter list in `web/lib/intended-fields.ts`. **Revise it with a counsellor before the
  pilot** — changing a value after real imports orphans those rows.

Import goes through `import_roster()` (`db/migrations/0005_import_roster.sql`) so participants,
jobs and the audit row land in one transaction. See §12 for the constraint this places on M7.

### M6 — Taker flow *(weeks 5–6, ~22 h — was 20h)* — **DONE**
Consent (with the review disclosure), both Likert modules, the progress-path and
module-transition polish from §13, autosave, resume.

**Done when:** you complete the full assessment on a real mid-range Android phone, kill the
connection mid-module, reopen, and resume with nothing lost — and it does not feel like filling out
a government form. *Automated checks pass; the phone test is still outstanding and is the one that
actually closes this milestone.*

Four things in this milestone were decided rather than inherited:

- **Widgets are chosen by an item's response range, not by its instrument.** 0..1 renders two
  buttons, anything wider an N-point scale (`web/lib/taker.ts`). This is what makes R10's "adding
  GET2 is loading a file" literally true — GET2's 0..2 renders and validates with no change to the
  taker. A test asserts it, because the shortcut of switching on `instrument_code` would work today
  and break that promise silently.
- **The per-answer write is a route handler**, not a server action (`a/[token]/answer/route.ts`).
  Actions are queued sequentially per client and carry a re-render payload each — wrong shape for
  110 rapid taps on school wifi. Consent and submit remain actions.
- **Answers post fire-and-forget** with an in-memory retry queue and a `pagehide` flush. A write
  that never lands leaves the item unanswered, and resume re-asks it — which is why the queue can
  give up rather than block the student behind a spinner. Nothing is written to localStorage;
  school phones get shared (§16).
- **Three writes moved into SQL** (`0006_taker_flow.sql`), following `import_roster()`'s precedent.
  All are keyed on the token hash and none accepts a participant id, so `0002_rls.sql`'s rule holds
  at the database boundary too. 19 tests in `engine/tests/db/test_taker_flow.py` cover it.

One consequence worth knowing before M7: `submit_assessment` counts answers against **every**
loaded item, so loading a new instrument mid-cohort blocks students already in flight. That is the
loud failure and it is the right one — the quiet alternative scores a module nobody answered. Drain
in-flight sessions before loading GET2.

**The consent copy is a draft** and carries a `TODO(§20 item 8)`. It discloses the review step
(§13) and avoids clinical framing (R7), but §20 item 8 asks for a counsellor or someone at GIFT to
review the wording before it reaches a real cohort.

### M7 — Job runner *(week 7, ~8 h)*
Unchanged from v1, plus `notify_psychologist` and `review_reminder` job kinds.

### M8 — Psychologist review portal *(weeks 7–8, ~20 h) — NEW MILESTONE*
`/review` queue, `/review/[session_id]` detail screen, save-draft/confirm/send-back actions,
`review_events` audit trail.
**Done when:** you can walk through one full session as the reviewer — read the flags, write a
note, pick 2 directions, confirm — and a `reports` insert for that session then succeeds where it
would have failed (R9) before confirmation.

### M9 — Student report *(weeks 9–10, ~26 h)*
Templates including the new confirmed-direction page (§14) and the conditional GET2 page.
**Done when:** confirming a review in M8 produces a branded, ~13-page PDF in an inbox with no
placeholder text anywhere and the confirmed directions rendered in the psychologist's own wording.
**This is the milestone you take to your counsellor contacts to react to before the pilot.**

### M10 — Counsellor dashboard *(week 11, ~14 h)*
Roster view with the new status vocabulary (including `pending_review` backlog visibility),
resend invites, report downloads, branding settings.

### M11 — Cohort report *(week 12, ~16 h)*
Field centroids, congruence, distributions, the completion-with-review-backlog table (§10),
method page.

### M12 — Pilot, sized to the psychologist bottleneck *(week 13, ~10 h + the pilot itself)*
**Cap the pilot cohort at 15–25 students** (§9.3), matched to how many reviews you (or your
psychologist design partner) can realistically complete in the pilot week. Confirm which
counsellor/psychologist contact is playing that role before scheduling — this is now a scheduling
dependency, not just a technical one. Load-test the number of concurrent submissions the cohort
size implies (modest at this scale), verify the review queue under real data, and build the "rerun
everything for this cohort" admin command.
**Done when:** the pilot runs with every session reaching `confirmed` within a few days, not stuck
in the queue.

### M13 — Norms, free test, pricing *(weeks 14–15, ~24 h)*
`norms/recompute` with alpha coefficients, the public RIASEC-only free test, pricing page,
invoice-based institution signup.
**Done when:** norms exist for your first population with `n` recorded and compared in writing
against the published Interest Profiler and IPIP alphas.

**If GET2 permission (R10) hasn't cleared by M6, ship M6–M13 with the module disabled via the
`module_not_administered` path (§6.3, §7.3) rather than blocking the whole build on an email
reply.**

---

## 18. Testing

### 18.1 Scoring — additions to v1's table

| Test | Asserts |
|---|---|
| `test_get2_subscale_percentage` | hand-computed percentage matches, against a fixture built from the transcribed item set |
| `test_get2_not_administered_shape` | returns the `module_not_administered` shape, every downstream template branch handles it without a KeyError |
| `test_get2_entrepreneurial_flag` | flag fires at the documented thresholds, not off by one |
| `test_r9_trigger_blocks_unconfirmed` | inserting a student `reports` row with no `reviews.status='confirmed'` row raises |
| `test_r9_trigger_allows_confirmed` | same insert succeeds once a confirmed review exists |
| `test_review_rls_boundary` | a `counsellor`-only profile cannot read `reviews.interview_notes`; a `psychologist` profile can |

Everything from v1 §17.1–17.4 (interests, personality, matching, cross-tenant, token security,
manual phone test) stands unchanged.

---

## 19. What changed in v2, and why

For anyone picking this plan up mid-build: three inputs forced a revision from the original v1
spec — two conversations with practising wellbeing counsellors, and the "Clarity Compass" concept
deck. Here is the delta, so nothing gets silently reverted during implementation.

| | v1 | v2 | Why |
|---|---|---|---|
| Module 3 | O*NET Work Importance Locator | GET2 (entrepreneurial tendency) | WIL was retired by USDOL in June 2024 — "technical assistance is no longer available." GET2 is live, addresses a real construct, and was raised directly by your counsellor contacts. |
| Report release | Automatic on scoring | Gated on psychologist confirmation (R9) | Counsellors were explicit: an unreviewed automated report to a minor is a liability and not what clients want. |
| Roles | `counsellor`, `org_admin` | adds `psychologist` | The review step needs a distinct, more restricted role (§9, §16). |
| Pilot cohort size | 40–60 students | 15–25 students | Direct consequence of one human reviewing every result (§9.3). |
| Taker-flow polish | Functional, minimal | "Polished but conventional" — progress path, module transitions | Direct response to the fatigue concern raised by the counsellors. |
| Phase 2 (roadmap engine) | Not addressed | Explicitly deferred to v2, specified in §21 | The deck's full vision is a second product; sequencing it after Phase 1 has a paying customer is the difference between a 12-week and a 6-month first release. |
| Timeline | 12 weeks | ~15 weeks | Honest cost of the above, not absorbed by cutting corners elsewhere. |

Two items MBTI and the Gordon Occupational Checklist II, also raised in conversation, were
considered and rejected: MBTI requires written permission and certification fees from The
Myers-Briggs Company; the Gordon Occupational Checklist II is 1981 Pearson copyright (a library
holding a physical copy, as GIFT's does, is not a licence to reproduce it in software). Neither is
compatible with R2.

---

## 20. Open items needing a human decision

1. **Send the GET2 permission email this week.** R10 blocks M13 (pricing/sales) and should not
   block M1–M12 (build with the module active, labelled provisional). Contact The Open University's
   Open Research Online team or Dr. Sally Caird directly; ask specifically about use in a commercial,
   white-label student-assessment platform, and get the exact attribution wording they want.
2. **Pin the GET2 item count and response scale from the primary source** (§6.3) — the commonly
   cited "54 items, 3-point scale" was not independently verified while writing this plan.
3. **Who plays the psychologist role for the pilot** — you, one of the two counsellors you already
   spoke with, or someone at GIFT. This is now a scheduling dependency for M12, not just a technical
   one, and it caps the pilot at 15–25 students (§9.3, §17 M12).
4. **`intended_field` controlled vocabulary** — unchanged from v1, still blocks M5 and M11.
5. **Which GIFT department and when** — unchanged from v1, sets the M12 calendar.
6. **Education-level → Job Zone mapping** — unchanged from v1, confirm against real outcomes.
7. **Institution pricing for school two** — unchanged from v1, decide before week 14.
8. **Guardian-consent wording, updated for the review disclosure (§13, §16)** — should be reviewed by
   someone at GIFT or by your two counsellor contacts, not drafted solo.
9. **The $5/month decision** — unchanged from v1.

---

## 21. Deferred to v2 — Phase 2, the navigation engine

Specified here so the vision isn't lost, and explicitly **not** built in this plan. Revisit once
Phase 1 has a paying institution and real usage data — both because the sequencing makes financial
sense and because Phase 2's design should be informed by which occupation matches and directions
actually get confirmed in practice, which you don't have yet.

From the Clarity Compass deck, Phase 2 ("Navigate there") comprises:

- **O*NET-powered roadmap generation** — once a `career_directions` row is confirmed, pull the
  occupation's required skills, certifications and typical progression from O*NET and turn it into
  a personalised, timestamped milestone plan (courses, projects, certifications, internships).
- **Adaptive rerouting** — the "Google Maps for careers" mechanic: a missed milestone, a changed
  goal, a failed course, or a delayed semester triggers a recalculation from the student's current
  position, never from zero.
- **Progress tracking and notifications** — a student-facing dashboard of milestone status, plus
  deadline reminders and next-action nudges.
- **LMS connector and university-scale analytics** — institutional reporting on engagement and
  "career readiness," and a integration point for a school's existing learning-management system.

None of this is small. Each bullet above is roughly its own milestone-sized body of work, and the
adaptive-rerouting logic in particular deserves its own design pass once you've seen how often real
students actually change direction — a number you'll only have after Phase 1 ships. When you're
ready to scope it, treat this section as the brief and write it up the same way this document
treats Phase 1: rules, schema, milestones, tests — not before.

---

## Appendix A — Commands

Unchanged from v1.

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

- [ ] A counsellor uploads a 15–25 row roster and sends invites without help.
- [ ] Students complete on their own phones; ≥ 90% finish in one sitting.
- [ ] Every completed session reaches `pending_review` automatically.
- [ ] A psychologist works the queue end to end: reads flags, writes notes, confirms 1–3 directions.
- [ ] No `reports` row for a student ever exists without a confirmed `reviews` row (R9, enforced by
      the database trigger, not just application code).
- [ ] Branded student PDFs, including the confirmed-direction page, are delivered by email
      automatically post-confirmation.
- [ ] A cohort PDF states a congruence rate you can recompute by hand.
- [ ] `rescore_all.py` reruns every session and changes nothing at the same engine version, and does
      not re-trigger report delivery for already-confirmed sessions.
- [ ] Cross-tenant, token, and review-RLS-boundary security tests pass in CI.
- [ ] Every interpretation string is human-written; no placeholder survives.
- [ ] O*NET and GET2/Caird attribution appears on every report and on the site.
- [ ] GET2 permission status (R10) is resolved — confirmed, or the module is cleanly disabled.
- [ ] A second school has been invoiced.
