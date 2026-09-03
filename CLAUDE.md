# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read plan.md first

`plan.md` is the complete build spec — schema, scoring formulas, API contract, milestones,
and the tests that prove each one. It is authoritative; this file only summarises what is
hard to discover by reading code. When the two disagree, `plan.md` wins.

`plan.md` §0 lists eight non-negotiable rules. Four of them change what you are allowed to
write, and are repeated here because violating them silently produces working code that
destroys the product's credibility:

- **R1 — Raw responses are sacred.** Never store a computed score without the raw item
  responses that produced it. Every session must be rescoreable with one command.
  `scores` and `occupation_matches` are keyed by `(session_id, engine_version)` precisely so
  a rescore writes a new row beside the old one instead of overwriting history.
- **R2 — Never invent instrument items.** O*NET and IPIP item text is downloaded into
  `data/instruments/*.csv`, never generated, paraphrased, reordered or "improved". A
  paraphrased item is a different item with unknown properties. All code reads items from
  the database and must work with an empty `items` table.
- **R3 — Interpretation text is human-written.** `engine/app/content/interpretations.yaml`
  is written by a person with psychology training. Draft product copy, error messages and
  docs freely; never the report's interpretive content.
- **R4 — No percentiles until local norms exist.** Until a `norms` row for the population
  reaches `n >= 300`, reports show raw scores and provisional bands with
  `norms_status: "pending_local_norms"`. Never display US percentiles for Pakistani students.

Also load-bearing: **R6** puts O*NET/USDOL attribution on every report page and in the site
footer (a licence term); **R7** forbids clinical language anywhere in report, UI or marketing
copy; **R8** means response-quality flags appear only on the counsellor's copy, never the
student's.

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

`db/migrations/0002_rls.sql` enables RLS on all sixteen tables. `jobs` and `audit_log`
deliberately have no policy at all: RLS denies by default, so the service role keeps sole
access. That silence is intentional — do not add a policy there without writing down why.

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
