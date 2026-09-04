# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read plan.md first

`plan.md` is the complete build spec — schema, scoring formulas, API contract, milestones,
and the tests that prove each one. It is authoritative; this file only summarises what is
hard to discover by reading code. When the two disagree, `plan.md` wins.

`plan.md` is v2, revised after two conversations with practising wellbeing counsellors and
against the "Clarity Compass" concept deck. If you see references to a Work Importance
Locator / card-sort values module anywhere (code, old commits, your own assumptions) — that
was v1. It is retired; module 3 is now **GET2** (`engine/app/scoring/get2.py`), entrepreneurial
tendency. See `plan.md` §19 for the full diff and why.

`plan.md` §0 lists ten non-negotiable rules. Six of them change what you are allowed to
write, and are repeated here because violating them silently produces working code that
destroys the product's credibility:

- **R1 — Raw responses are sacred.** Never store a computed score without the raw item
  responses that produced it. Every session must be rescoreable with one command.
  `scores` and `occupation_matches` are keyed by `(session_id, engine_version)` precisely so
  a rescore writes a new row beside the old one instead of overwriting history.
- **R2 — Never invent instrument items.** Interest Profiler items from onetcenter.org, IPIP
  items from ipip.ori.org, GET2 items from Sally Caird's published guide — downloaded or
  transcribed into `data/instruments/*.csv`, never generated, paraphrased, reordered or
  "improved". A paraphrased item is a different item with unknown properties. All code reads
  items from the database and must work with an empty `items` table.
- **R3 — Interpretation text is human-written, and a human reviews every result.**
  `engine/app/content/interpretations.yaml` is written by a person with psychology training.
  New in v2: a psychologist also reviews every individual session's machine output before a
  student report can render — that's R9 below. Draft product copy, error messages and docs
  freely; never the report's interpretive content, and never anything presented as the
  psychologist's own clinical judgement.
- **R4 — No percentiles until local norms exist.** Until a `norms` row for the population
  reaches `n >= 300`, reports show raw scores and provisional bands with
  `norms_status: "pending_local_norms"`. Never display US percentiles for Pakistani students.
- **R9 — No final report leaves the system without human sign-off.** A session cannot reach
  `reports` (student kind) until a `reviews` row exists with `status = 'confirmed'`
  (`db/migrations/0003_reviews.sql`, §9). Enforced by a database trigger
  (`trg_enforce_review_before_student_report`), not just application code — treat any code
  path that bypasses it as a severity-1 bug.
- **R10 — GET2 use is provisional until written permission is confirmed.** Label GET2 output
  `"provisional — pending permission"` internally and keep it out of sales material until
  then. If permission is refused, the module disables via the `module_not_administered`
  shape (`score_get2(responses, scoring=None)`) — every downstream template must already
  handle that shape without a KeyError, not add the branch later under deadline pressure.

Also load-bearing: **R6** puts O*NET/USDOL and GET2/Caird-OU attribution on every report page
and in the site footer (a licence term); **R7** forbids clinical language anywhere in report,
UI or marketing copy — the psychologist review step is never described as therapy or a
clinical service; **R8** means response-quality flags appear on the psychologist's review
screen and the counsellor's cohort copy, never the student's own report.

## Commands

Engine (`engine/`, Python 3.12 — the venv is not on PATH, invoke it explicitly):

```powershell
.\.venv\Scripts\python.exe -m pytest                      # all tests
.\.venv\Scripts\python.exe -m pytest tests/test_main.py    # one file
.\.venv\Scripts\python.exe -m pytest -k reverse_keyed      # one test by name
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Web (`web/`):

```powershell
pnpm dev
pnpm build
pnpm lint
```

Repo root:

```powershell
# The service-role guard. bash is not on PATH; use the Git install directly.
& "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe" scripts/check-service-role.sh
```

## Architecture

Three processes, one shared database, and one rule that ties them together.

```
Next.js (Vercel) ──writes job row──► Supabase Postgres ◄──claims jobs── FastAPI engine
                                                            ▲
                                          GitHub Actions cron │ POST /tick every 5 min
```

**The engine is never called synchronously from a user request.** When a student submits,
the web app writes a row to `jobs` and shows "your report is being prepared". A cron hits
`POST /tick`; the engine wakes, claims pending jobs with `for update skip locked`, scores,
renders PDFs, sends email, and sleeps. This makes a free-tier cold start harmless, keeps the
Supabase project from pausing, and turns a failed job into a retry rather than a lost
submission. Do not "optimise" it into a direct call — report generation taking a minute is a
feature of the design (`plan.md` §2, §11).

The only web-to-engine calls are `POST /render/cohort/{id}` and `GET /version`, both from
server-side code. Everything else flows through the `jobs` table.

All persistent state lives in Supabase; the engine host holds nothing. Moving the engine to
another provider must stay an afternoon's work.

**v2 adds a human checkpoint between scoring and reporting (R9).** Scoring still lands
automatically and enqueues `notify_psychologist`, not `render_student`. A session sits in
`pending_review` until a psychologist works the `/review` queue and confirms 1–3
`career_directions`; only then does `render_student` get enqueued, and the database trigger
in `0003_reviews.sql` refuses the insert otherwise. `POST /render/student/{session_id}`
returns 409 if `reviews.status != 'confirmed'`.

### Scoring is pure

`engine/app/scoring/*` takes raw responses in and returns score dicts — no database access
inside those modules. That is what makes them testable offline and rescoreable in bulk.
`engine.py` orchestrates and stamps `SCORING_ENGINE_VERSION` (in `app/config.py`); bump it
whenever any rule changes.

`app/config.py` also holds `TEMPLATE_VERSION`, stamped onto every `reports` row so old PDFs
stay reproducible after a template change.

### Security model

Two secrets, two different blast radii:

- `SUPABASE_SERVICE_ROLE_KEY` bypasses row-level security entirely. It is legitimate in the
  engine and in Next.js server components, server actions and route handlers. In a
  `"use client"` file or a `NEXT_PUBLIC_` variable it makes every student record public.
  `scripts/check-service-role.sh` fails CI on both cases — it has been verified to fire, so
  a green run is meaningful.
- `ENGINE_SHARED_SECRET` gates every engine route except `/health`. Compared with
  `secrets.compare_digest`; an unset secret rejects everything rather than allowing
  everything.

Students are never authenticated. The taker flow reaches the database only through server
actions that resolve `sha256(token) → participants.invite_token_hash` using the service
role. **Those actions must never accept a `participant_id` from the client — only a token.**

`db/migrations/0002_rls.sql` enables RLS on all sixteen v1 tables; `0003_reviews.sql` adds
three more (`reviews`, `review_events`, `career_directions`) plus a `psychologist` role.
`jobs` and `audit_log` deliberately have no policy at all: RLS denies by default, so the
service role keeps sole access. That silence is intentional — do not add a policy there
without writing down why.

`reviews.interview_notes` gets the same treatment by different means: a `counsellor` profile
must never read it, only `psychologist`/`org_admin`/`superadmin`. Since RLS is row-scoped, not
column-scoped, a counsellor gets **no** SELECT policy on `reviews` at all — only the narrow
`review_progress_for_session()` SECURITY DEFINER function, which returns `status` and
`confirmed_at` only. Do not "simplify" this into a second row-level policy on `reviews` — that
reopens the exact leak the split exists to prevent.

Most participants are under 18. `plan.md` §15 is not a compliance footnote; read it before
touching consent, retention, deletion or anything that sends data outward.

## Conventions

- **Migrations are append-only.** Never edit one that has run; add a new file.
- **`data/onet/*.csv` is gitignored** — large, and redistributable only under O*NET's terms.
  Each developer downloads it locally via `scripts/load_onet.py`.
- **`.gitattributes` forces LF.** Without it, `check-service-role.sh` authored on Windows
  fails on the Linux runner with an unreadable `\r: command not found`.
- **Charts are hand-written SVG** in `engine/app/report/charts.py`. No plotting library —
  the output is a hexagon and some bars, and WeasyPrint renders inline SVG well.
- **Statistical honesty (`plan.md` §9):** never print a percentage on a denominator below 10
  without the denominator beside it; never compare cohorts without both `n`s; the report says
  "classed misaligned by this instrument", never "in the wrong field".

## Next.js version warning

`web/` runs Next.js 16, which has breaking changes against most training data. Read the
relevant guide in `web/node_modules/next/dist/docs/` before writing web code. `next dev`
regenerates `web/AGENTS.md` and `web/CLAUDE.md` — commit them with your work rather than
reverting them.

## WeasyPrint

WeasyPrint links against system libraries at import time. Both CI and the Dockerfile install
`libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 libcairo2 libgdk-pixbuf-2.0-0`.
Missing them is the number-one cause of "works locally, blank PDF in production". Report
fonts are bundled into the image — never fetched from Google Fonts at render time, or the PDF
silently falls back and looks wrong only in production.
