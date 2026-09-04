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
.\.venv\Scripts\python.exe -m pytest -m db                 # the Postgres tests only
.\.venv\Scripts\python.exe -m pytest -m "not db"           # everything else
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

The `db`-marked tests need `TEST_DATABASE_URL` (in `engine/.env` or the environment) and skip
without it. See "The database tests" below before touching them.

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
whenever any rule changes. `engine/app/matching/*` follows the same rule for the same reason:
the occupation catalogue arrives as an argument, never through a query.

### O*NET 31.0 does not match what plan.md §6.4 describes

`plan.md` was written against an older release. Three differences, all of which read as bugs if
you don't know about them:

- **RIASEC moved.** There is no `interests.csv`; occupation interest profiles live in
  `career_interest_types.csv` as elements `1.B.1.a`–`1.B.1.f` on scale ID `OI` (1..7). The same
  file also carries `IH` high-point rows for `1.B.1.g`–`i` — a different measure, filtered out.
- **Work Values is gone.** No `work_values.csv`, no `1.B.2.*` element anywhere in the release. The
  six `occupations.value_*` columns are therefore permanently NULL. Nothing reads them, so they
  are left in place rather than dropped (migrations are append-only).
- **There is no Job Zone 1.** It was merged into a combined "Job Zone 1-2" band coded `2`, so §8's
  `matric → [1,2,3]` is implemented as `[2,3]`. Same behaviour, no dead value.

**Occupation match score is interest-only cosine**, not v1's `0.70 * interest + 0.30 * values`.
The values term measured the Work Importance Locator, which v2 retired (§19), and §7.3/§8 keep
GET2 deliberately out of the cosine — it measures tendency to act, not interest content. With no
student values vector and no occupation values data, that term has no operand on either side.
GET2 reaches matching only as `entrepreneurial_flag`, which adds up to 3 separately labelled
entrepreneurship-track occupations and never reweights the ranking.

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

Writes never use the RLS-bound client. `0002_rls.sql` grants authenticated users SELECT and
nothing else, so every insert and update runs through the service role in a server action
(`web/lib/supabase/admin.ts`). What replaces the RLS those writes bypass is one rule, and it is
the same shape as the taker-token rule above: **`organisation_id` comes from `verifySession()`,
never from a form field.** A server action is reachable by direct POST, so an `organisation_id`
in a `FormData` would let any authenticated counsellor write into any school's records.

Reads go the other way — through `web/lib/supabase/server.ts`, with RLS applied — so the
dashboard exercises the same policies the tests assert. A policy regression then shows up as an
empty dashboard rather than a silent cross-tenant leak.

Accounts are invite-only (`web/app/dash/settings/actions.ts`). There is no signup page and no
`handle_new_user` trigger on `auth.users`, because either would have to guess `organisation_id`
and `role` — and `role` is what gates `reviews.interview_notes`, the notes about a minor that §16
says to treat like health data. An institution's first admin is seeded by hand.

`db/migrations/0002_rls.sql` enables RLS on all sixteen v1 tables; `0003_reviews.sql` adds
three more (`reviews`, `review_events`, `career_directions`) plus a `psychologist` role.
`0004_r9_trigger_update.sql` widens the R9 trigger to `before insert or update` — 0003 created it
`before insert` only, which left `update reports set kind='student', session_id=<unreviewed>`
open. Appendix B states R9 as an invariant over rows, not over inserts.
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
- **`data/onet/**` is gitignored** — large, and redistributable only under O*NET's terms. Each
  developer downloads the CSV archive from onetcenter.org and extracts it there; the archive
  creates its own versioned subdirectory (`db_31_0_csv/`), which is why the ignore rules use
  `**`. `scripts/load_onet.py` globs for that directory rather than pinning a release.
- **`.gitattributes` forces LF.** Without it, `check-service-role.sh` authored on Windows
  fails on the Linux runner with an unreadable `\r: command not found`.
- **Charts are hand-written SVG** in `engine/app/report/charts.py`. No plotting library —
  the output is a hexagon and some bars, and WeasyPrint renders inline SVG well.
- **Statistical honesty (`plan.md` §9):** never print a percentage on a denominator below 10
  without the denominator beside it; never compare cohorts without both `n`s; the report says
  "classed misaligned by this instrument", never "in the wrong field".

## The database tests

`engine/tests/db/` applies the migrations to a real Postgres and asserts the R9 trigger and the
RLS policies. They skip unless `TEST_DATABASE_URL` is set; CI always sets it, so CI is where they
actually run. **The harness runs `drop schema public cascade`** — a throwaway database only.

`db/testing/` holds an `auth`-schema shim and the GRANTs that Supabase provides by default.
Nothing there is a migration: it is never applied to Supabase and is exempt from the append-only
rule. The grants are load-bearing for correctness, not convenience — without them a query returns
*permission denied* rather than *zero rows*, and a test asserting "this role sees nothing" passes
for the wrong reason.

Three things about this harness will look wrong and are not:

- **`set local role authenticated` in `as_user()`.** Postgres skips RLS for superusers, BYPASSRLS
  roles and the table owner, and the test connection is all three. Without that line every RLS
  assertion passes while testing nothing. `test_rls_is_enforced_at_all` is the canary that fails
  loudly if it breaks; do not delete it.
- **`insert_student_report()` deliberately does not use `as_user`.**
  `enforce_review_before_student_report()` is SECURITY INVOKER, so its `select 1 from reviews` is
  itself subject to `reviews` RLS. Wrap that insert in `as_user` and even a genuinely confirmed
  review gets filtered to zero rows, producing a spurious "R9 violation" that looks like the
  trigger working. In production the writer is the service role, which bypasses RLS.
- **`reports` is seeded after `reviews` in `demo_org.sql`.** Reorder it and the seed fails with
  "R9 violation" — that is the trigger, not a bug in the seed.

The R9 tests build their own rows; the RLS tests read the seed. That split is deliberate: the
"blocks" case needs a session with *no* review, and if it read seed state a later seed change that
added one would silently flip it to a false pass.

## Next.js version warning

`web/` runs Next.js 16, which has breaking changes against most training data. Read the
relevant guide in `web/node_modules/next/dist/docs/` before writing web code. `next dev`
regenerates `web/AGENTS.md` and `web/CLAUDE.md` — commit them with your work rather than
reverting them.

The one that bites hardest: **`middleware.ts` is deprecated and renamed `proxy.ts`**, exporting a
function called `proxy`. Every Supabase "Next.js middleware" snippet in circulation uses the old
name. Proxy also defaults to the Node.js runtime now, and setting `runtime` inside that file
throws.

`proxy.ts` refreshes the auth token and bounces unauthenticated traffic away from `/dash` and
`/review`, but it is **not** the security boundary — it runs on prefetches and must not query the
database. Authorisation lives in `web/lib/dal.ts`, next to the data, and ultimately in the RLS
policies. Auth checks do not belong in a layout either: Next's own guidance is that a layout
"does not control whether the rest of the route renders".

## WeasyPrint

WeasyPrint links against system libraries at import time. Both CI and the Dockerfile install
`libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 libcairo2 libgdk-pixbuf-2.0-0`.
Missing them is the number-one cause of "works locally, blank PDF in production". Report
fonts are bundled into the image — never fetched from Google Fonts at render time, or the PDF
silently falls back and looks wrong only in production.
