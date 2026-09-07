# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Where the build is

**M0–M9 are written and tested; M0–M7 are LIVE. The engine is on Render; the web app is on
Vercel. M10 (the counsellor dashboard) is next.** `plan.md` §17 carries the detail;
the short version:

> **M9 renders, but cannot yet produce a releasable report, and that is by design.**
> `engine/app/content/interpretations.yaml` ships with all 45 interpretive strings EMPTY —
> R3 says a person with psychology training writes them, and the loader raises
> `InterpretationMissing` rather than defaulting. A `render_student` job therefore fails
> until they are written. Run `python scripts/check_interpretations.py` for the worklist.
> Do not fill that file in. `test_report_student.py` has a test that fails if you do.

A counsellor can sign in, create a cohort and upload a roster CSV. A student can open their invite
link, consent, answer both modules on a phone, close the tab mid-way, come back and resume where
they left off, and submit — which queues a `score_session` job. M7 built the runner that drains
that queue: `POST /tick` claims jobs with `for update skip locked`, scores, matches occupations,
moves the participant to `pending_review` and emails the psychologist. Auth, RLS, the R9 trigger,
roster import, the taker-flow functions and the job functions are all covered by tests that run
against a real Postgres in CI.

**The pipeline is live end-to-end.** The engine is a Docker service on Render at
`https://percentile-hwlu.onrender.com`, built from `engine/Dockerfile` via `render.yaml`
(`runtime: docker`, `rootDir: engine`). The web app is on Vercel at
`https://percentile-lyart.vercel.app` (root dir `web`, Next.js 16, pnpm). GitHub Actions
secrets `ENGINE_BASE_URL` and `ENGINE_SHARED_SECRET` are set, so `tick.yml` no longer skips.
A manual tick has been verified against the live engine: `/health` → `{"status":"ok"}`.

**Live data state (Supabase project `rvfiqaurtardlirgfera`):**
- `items`: 110 loaded. `occupations`: 923, incl. 10 Pakistan-localised rows (3 on the
  entrepreneurship track), loaded with the fixed `scripts/load_onet.py`.
- 20 participants: 19 `invited`, 1 `pending_review` — **Fatima Khan**, who has 1 score and 5
  occupation matches. She is the seed case for M8.
- `jobs` table: **empty; every job done.** The first real tick ran 20 `send_email` jobs
  through `LoggingEmailer` (`NOT SENT`, no key), so every imported student's
  `invite_token_hash` has been re-minted and all original links are dead — reissuing by email
  (once `RESEND_API_KEY` is set and `APP_BASE_URL` points at the real web app) is the only
  path. 21 junk invite jobs (20 for participants deleted during a re-import experiment, 1
  refused for Fatima because she already submitted) were **deleted by hand** — do not expect
  them to return.

**Load-bearing quirks a new agent must know:**
- `scripts/load_onet.py` used to fail silently against Supabase: it wrote two partial upserts,
  and PostgREST assigns NULL to every omitted NOT NULL column on conflict. Fixed to one
  complete-row upsert (commit `6155e36`); re-running it is idempotent.
- **Render env `APP_BASE_URL` is still `http://localhost:3000`.** Change it to
  `https://percentile-lyart.vercel.app` *before* setting `RESEND_API_KEY` — invite and review
  links are built from it, and a link to localhost reaches nobody.
- **A private Supabase Storage bucket named `reports` must exist** before any render succeeds
  (`report_storage_bucket`). Private, not public: a public bucket makes a guessable URL a
  minor's full profile, with no expiry and no audit. Delivery is a 7-day signed URL minted at
  send time, never a PDF attachment (R8, §15).

**M8 is built (`web/app/review/`, `db/migrations/0009_review_actions.sql`).** The queue, the
detail screen and all three actions exist; `engine/tests/db/test_review_actions.py` asserts
M8's done-when — a `reports` insert fails before confirmation and succeeds after.

**M9 is built (`engine/app/report/`, `engine/app/content/`,
`db/migrations/0010_student_reports.sql`).** `render_student` now has a handler: it loads a
confirmed session, renders ~13 A4 pages through Jinja2 → WeasyPrint, uploads to the private
`reports` bucket, records the row and queues the delivery email — all except the upload in one
transaction (`record_student_report`). It fails, correctly, until the interpretation text is
written.

**Before M9 can run against live data — four things, none of them code:**

1. Apply `db/migrations/0010_student_reports.sql` to Supabase. CI applies every migration to a
   throwaway Postgres, so a green build says nothing about whether the live database has it.
   A missing `record_student_report` surfaces as every render job failing five times.
2. Create a **private** Storage bucket named `reports`.
3. Set Render's `APP_BASE_URL` to the Vercel URL.
4. Have a psychologist write `engine/app/content/interpretations.yaml` (45 strings;
   `python scripts/check_interpretations.py`). Until then every render fails by design.

Set `RESEND_API_KEY` *last*. It is the switch that turns logged mail into delivered mail, and
until step 4 is done the only thing to deliver is a failure.

**Start here: M10 — the counsellor dashboard.** Roster view with the `pending_review` backlog,
resend invites, report downloads, branding settings (`plan.md` §17 M10).

## Read plan.md first

`plan.md` is the complete build spec — schema, scoring formulas, API contract, milestones,
and the tests that prove each one. It is authoritative; this file only summarises what is
hard to discover by reading code. When the two disagree, `plan.md` wins.

`plan.md` is v2, revised after two conversations with practising wellbeing counsellors and
against the "Clarity Compass" concept deck. If you see references to a Work Importance
Locator / card-sort values module anywhere (code, old commits, your own assumptions) — that
was v1. It is retired; module 3 is now **GET2** (`engine/app/scoring/get2.py`), entrepreneurial
tendency. See `plan.md` §19 for the full diff and why.

**`plan_1.md` is not v1, despite its title.** It is a stale copy of v2 from before M5–M7 were
marked done — the diff against `plan.md` is five lines, all milestone headers. The real v1 is a
chat artefact, not a file in this repo. Everything from it that still matters has been folded in:
the reliability figures and the English-only rationale into §6.0, the Paddle lead time into M13,
and the correction that **v1 never specified the job runner** into §12. `plan.md` remains
authoritative; do not treat `plan_1.md` as a second source.

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
pnpm test                     # vitest, unit tests for the pure modules in lib/
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

Staff invites ride **Supabase Auth**, not the engine's Resend path. Two things about that flow
are counter-intuitive, and both were got wrong once already:

- **There is no hosted set-password page.** Supabase creates the auth user and mails a link;
  accepting it proves the address and establishes a session, and that is all. The account has
  no password, and `login/actions.ts` only calls `signInWithPassword` — so without
  `web/app/auth/set-password/` an invited colleague can open the app exactly once, from the
  emailed link, and never sign in again.
- **The link carries a token hash, not a `?code`.** `exchangeCodeForSession` is the OAuth/PKCE
  path and cannot work here: PKCE needs a `code_verifier` cookie from a flow the invitee's
  browser never started. Email links are verified with `verifyOtp({ type, token_hash })`, which
  is what `web/app/auth/confirm/route.ts` does. A default `{{ .ConfirmationURL }}` link returns
  its tokens in the **URL fragment**, which is never sent to the server, so a route handler
  cannot read it at all.

The chain is: `inviteMember` → `inviteUserByEmail(email, { redirectTo: .../auth/set-password })`
→ the invite email's link → `/auth/confirm` (verifyOtp) → `/auth/set-password` → `/dash`.

**This requires a dashboard change that no code can make.** The "Invite user" template
(Authentication → Email Templates) must point at `/auth/confirm` with `{{ .TokenHash }}` — the
exact markup is in `confirm/route.ts`'s header. `{{ .TokenHash }}` only appears if the template
asks for it, so until that is saved, invitations still land on `/login` with no session.
`redirectTo` must also be listed under Authentication → URL Configuration → Redirect URLs, or
Supabase silently falls back to Site URL. The engine's
`{organisation}: your careers questionnaire` invite is a separate system for students.

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

## The taker flow (`web/app/a/[token]/`)

The student side has no auth session and no DAL. `web/lib/dal.ts` is for counsellors; do not reach
for it here.

**The token is the whole of a student's authentication.** `queries.ts` resolves
`sha256(token) → participants.invite_token_hash` using the service role, and — quoting
`0002_rls.sql`'s header — the taker "must never accept a `participant_id` from the client, only a
token." `0006_taker_flow.sql` states the same rule in SQL: all three functions take
`p_token_hash` and none takes a participant or session id, so a caller that forgets cannot express
the mistake. Do not add an id argument to any of them as a shortcut.

Tokens are 256-bit base64url, minted in `web/app/dash/cohorts/[id]/upload/actions.ts`. Only the
sha256 is stored, so a token cannot be looked up or recovered — it can only be checked.

Three things here look like inconsistencies with the rest of the app and are deliberate:

- **The answer write is a route handler, not a server action.** Next queues actions sequentially
  per client and each response carries a re-render payload; across ~110 rapid taps that is a queue
  the student outruns. `answer/route.ts` is parallel-safe, answers in a few dozen bytes, and works
  with `sendBeacon` on `pagehide`. It re-adds the Origin check that actions get for free. Consent
  and submit stay actions — they are navigations.
- **Resume derives from `responses`, never from `sessions.progress`** (R1). `progress` is a
  counsellor-facing summary that can drift; the raw answers are the record.
- **`/a/` is excluded from `proxy.ts`'s matcher.** Students have no session to refresh, so
  including it bought a `supabase.auth.getUser()` round trip per answer.

**No widget decision reads `instrument_code`.** `widgetForItem` in `web/lib/taker.ts` picks from
the item's own `response_min`/`response_max`: 0..1 renders two buttons, anything wider renders an
N-point scale. That is what makes R10's promise real — GET2's 0..2 will render, and
`record_response` will validate it, with no change here. `taker.test.ts` asserts it; if a future
change starts switching on the instrument code, that test is the one that should stop you.

**GET2 still cannot be administered.** `data/instruments/get2_items.csv` and `get2_scoring.json`
are absent (plan §20 item 2), so `instruments` has no `get2` row, `MODULE_ORDER` filters it out,
and two modules is what ships.

One consequence of `submit_assessment` counting against *every* loaded item: loading a new
instrument mid-cohort blocks students already in flight, because the assessment they started is no
longer the one the system defines. That failure is loud on purpose — the quiet alternative is
scoring a module nobody answered. Drain in-flight sessions before loading a new instrument.

**The consent copy is a draft and is marked `TODO(plan.md §20 item 8)`.** §20 item 8 says the
wording needs a counsellor or someone at GIFT to review it, not to be drafted solo. It discloses
the psychologist review step (§13, §16) and avoids clinical framing (R7), but it should not go in
front of a real cohort unreviewed.

## The job runner (`engine/app/jobs/`)

`POST /tick` is the only thing that drives the engine. `runner.run_tick` reaps stale jobs, sweeps
the review backlog, then claims and dispatches until the batch is empty or the deadline is near.
The SQL is `0008_job_runner.sql`; the handlers are one file per kind.

Five decisions here look odd and are load-bearing:

- **`attempts` increments when a job is CLAIMED, not when it fails.** The engine host is disposable
  and will be killed mid-job; if only handled failures counted, a job that crashes the process
  retries forever and the cap never fires. The cost is that a job whose worker died stays `running`
  and is invisible to `claim_jobs` — which is why `requeue_stale_jobs()` is mandatory, not an
  optimisation. Removing it means one crashed process silently loses one student's scoring.
- **Scoring is still pure.** `plan.md` §7.5 sketches `score_session(session_id)` with the database
  inside; that would break `app/scoring/*`'s no-DB rule. The steps are split:
  `repository.py` loads, `scoring/engine.py` computes, `record_score()` writes. That split is what
  makes `rescore_all.py` (M12) possible, per R1.
- **`record_score` sets `pending_review`, never `scored`, and never enqueues `render_student`.**
  Nothing could observe `scored` (both writes are one transaction), and a participant stuck there
  after a `match_occupations` failure would vanish from the review queue with no error. The render
  job is R9's business and only M8's confirm action may queue it.
- **The follow-up jobs are guarded on `v_awaiting`.** `record_score` is idempotent and re-runs
  whenever a worker dies mid-job, and `rescore_all.py` will re-run it deliberately. Without the
  guard a rescore of an already-`confirmed` session would enqueue a *first* `notify_psychologist`
  and email a student about results they received weeks ago. `match_occupations` is deliberately
  *not* gated — matches are keyed by engine version, so a rescore must rematch.
- **`fail_job` will not alert about a failed alert.** The alert is itself a `send_email`; a mail
  outage would otherwise enqueue one new failing row per tick, forever.

`jobs.last_error` is truncated to 2000 chars in SQL and the runner records `TypeName: message`
rather than a traceback. That column is retried, logged and emailed, and a traceback can carry
locals — one of which is a live invite token inside the email handler.

**An unset `RESEND_API_KEY` is a supported mode.** `build_emailer` returns `LoggingEmailer`, which
logs `NOT SENT` at WARNING and reports success. Raising would make a local tick fail every job five
times; a silent no-op would let someone drain twenty invites and believe they were sent. There is
deliberately no second `EMAIL_ENABLED` flag — two switches means a deployment can hold a real key
and still send nothing.

## The review portal (`web/app/review/`)

M8. The human checkpoint R9 requires: a psychologist reads the engine's output, writes their own
notes, picks 1–3 career directions, and confirms — and only that confirm releases the student's
report.

**Every write goes through `save_review()` (`0009_review_actions.sql`), which is the only writer
for `reviews`, `career_directions` and `review_events`.** Confirming is five writes that must not
come apart, and supabase-js speaks REST — the same reasoning as 0005, 0006 and 0008.

Five decisions there are load-bearing:

- **`render_student` is enqueued in `save_review` and nowhere else**, and only on the transition
  *into* `confirmed`. 0008's `record_score` deliberately does not queue it. The guard reads the
  status the row held *before* the upsert, because afterwards every path looks confirmed — so a
  double-tapped Confirm cannot produce two reports and two emails.
- **The role and tenancy checks live inside the SQL function**, not only in the server action.
  The caller holds the service role and bypasses RLS, so this is the only enforcement in the
  database for this operation. A `counsellor` is refused: they have no read policy on `reviews`
  (§16), and a counsellor who could *write* `interview_notes` would walk around it.
- **Career directions are delete-then-insert, and rank comes from array position.** With
  `unique (review_id, rank)`, a review going from three directions to two would otherwise keep a
  stale rank 3 the psychologist deliberately removed. Trusting a client-supplied `rank` invites a
  duplicate that trips the constraint mid-transaction.
- **`confirmed_at` survives an edit but a send-back clears it.** Editing notes after sign-off is
  not a second sign-off; withdrawing sign-off is, and R9 then blocks the report again.
- **Reads go through the RLS-bound client**, unlike the taker's. This is the one screen holding
  `interview_notes`, so reading it through the policies `test_rls_reviews.py` asserts is what
  keeps the counsellor boundary honest — a regression shows up as an empty screen, not a leak.

`web/lib/review.ts` is pure (flag meanings, chart arithmetic, queue formatting) and tested in
`review.test.ts`. One test there pins **every flag code `flags.py` can emit to a plain-language
meaning** — §9.1 requires it, and the failure mode is silent: a new flag with no entry renders
beside a blank explanation, which reads as "nothing to worry about".

## The student report (`engine/app/report/`, `engine/app/content/`)

M9. Jinja2 → HTML → WeasyPrint → PDF, ~13 A4 pages, delivered as a 7-day signed URL.

The package splits the same way scoring does, and for the same reason: `student.py` (what
appears on the page) and `charts.py` (hand-written SVG) are **pure**, so the R8 and R6 tests can
assert against a rendered HTML string with no database and no font stack. `render.py` is the only
impure module, and it imports WeasyPrint *inside* the function so the rest stays importable on a
Windows machine.

Five things here are load-bearing:

- **`interpretations.yaml` ships empty and the loader raises.** R3 is a rule in a document;
  `InterpretationMissing` is that rule with teeth. A blank string a template asks for fails the
  render job — it does not become an empty paragraph a student reads as "the system had nothing
  to say about me". `scripts/check_interpretations.py` prints the worklist. There is a test that
  **fails if the interpretive strings get filled in**, because the likeliest thing to fill them
  is a model, and that is the one thing R3 forbids. Structural text (`report.*`) and licence text
  (`attribution.*`) are *not* interpretation and do ship written.
- **`StudentReportInput` has no `flags` field** (R8). Not filtered in the template, not hidden
  with CSS: absent, so a template edit cannot reintroduce it. Same for `interview_notes` (§16).
  `load_student_report` does not select either column.
- **R9 is checked three times** — in `load_student_report` before a PDF is built, in
  `record_student_report` (0010) before a row is written, and by the trigger. None is redundant:
  each covers a caller that skipped the one before. The first is the one that matters most,
  because failing at the trigger means the PDF already exists in storage.
- **The `reports` row and the delivery email are one transaction** (`record_student_report`,
  0010_student_reports.sql), not two supabase-py calls. The handler originally deduped the email
  with PostgREST's `payload->>template` filter syntax — used nowhere else in this codebase, and
  its failure mode is *silent*: a filter matching nothing sends a student a second link. In SQL
  it is an ordinary `where` that CI runs against real Postgres.
- **Object keys are `<org-slug>/<session-id>.pdf`, never the student's name.** The key appears in
  storage logs and inside the signed URL itself; `.../fatima-khan.pdf` tells anyone the link is
  forwarded to who the report is about before they open it. The URL is minted at send time and
  never stored — the same rule the invite token follows.

`test_report_pdf.py` is the only test that runs WeasyPrint, and it skips locally by catching
`Exception` (the import fails with `OSError` from cffi, which `pytest.importorskip` would not
catch — it would abort collection for the whole suite). CI installs the Pango libraries, so that
is where "works locally, blank PDF in production" gets caught.

Two rules constrain what this screen may say. **R3:** nothing pre-fills a note or drafts a
rationale — the psychologist's clinical voice is theirs. **R4:** no percentiles, so raw scores and
provisional bands only, with band names taken from the engine rather than re-derived here.

## The roster CSV and `intended_field`

plan.md says the roster format is "unchanged from v1 §12", and **the v1 document is not in this
repository** — so the format was decided in M5 and lives in `web/lib/roster-csv.ts`:
`full_name` and `email` required, `external_ref`/`intended_field`/`education_level` optional,
unknown columns ignored, `utf-8-sig` (Excel writes a BOM).

Two behaviours there are deliberate and easy to "fix" into something worse:

- **Every row error is collected, not just the first.** `load_instruments.py` raises on the first
  bad row because a malformed instrument file is a transcription bug; a roster is a human-typed
  document, and one error per upload cycle is the experience M5's done-when exists to prevent.
- **All-or-nothing.** A partial import leaves the counsellor working out which rows landed, and
  re-uploading the corrected file then trips the duplicate check on the ones that did.

`intended_field` is a controlled vocabulary in `web/lib/intended-fields.ts` (plan.md §20 item 4 —
also inherited from the missing v1 doc, so the list there was chosen, not carried over). It is a
starter list to revise with a counsellor before the pilot. It cannot be free text: M11's congruence
rate groups on this column, and "pre-med" / "MBBS" / "Medicine" would arrive as three fields with
an n of 1 each — which §9's statistical honesty rule then forbids reporting on.

**A student is unique per cohort by `lower(email)`** — `participants_cohort_email_unique`
(`0007_participant_email_unique.sql`). Scoped to the cohort, not the organisation, so a student
reassessed in a later intake year is a new row and §10's year-on-year comparison still works.

That index closes an M5 bug found during M6's end-to-end test: `parseRosterCsv` dedupes emails
within a single file, but the only unique column was `invite_token_hash`, which is freshly minted
per import — so uploading the same roster twice inserted a second full set and twenty students
became forty. It also made the `23505` branch in `upload/actions.ts` unreachable for a duplicated
student; that branch now names who collided.

If you ever need to clean duplicates again, `scripts/find-duplicate-participants.sql` is the
review-first tool. **Its keep-rule ranks by answer count, not by age**, and that is load-bearing:
on the M6 test cohort the student's 110 responses were on the *later* copy, so keeping "the oldest"
would have deleted the only real assessment in the database. Deleting a participant cascades to
sessions and responses, and raw responses are unrecoverable (R1).

Roster import goes through `import_roster()` (`db/migrations/0005_import_roster.sql`), not through
separate inserts. supabase-js speaks REST, so participants and jobs would otherwise be separate
requests with no transaction: a half-import leaves students whose invite tokens are unrecoverable,
because only `sha256(token)` is stored. The function is revoked from `authenticated` — it takes
`p_organisation_id` as an argument, so a browser session reaching it directly could pass another
school's id. `db/testing/9999_grants.sql` therefore grants functions **by name** rather than with a
blanket `grant execute on all functions`, which would silently re-grant what that migration revokes.

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
added one would silently flip it to a false pass. `test_taker_flow.py` does the same for the same
reason — every seeded participant has already consented and submitted, so its `invited` fixture is
built fresh.

The harness globs `db/migrations/[0-9][0-9][0-9][0-9]_*.sql`, so a new migration is picked up
without editing `harness.py`. It does mean a migration that fails to apply breaks every db test at
once rather than one — check the first failure's SQL error before assuming the tests are wrong.

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

WeasyPrint links against system libraries at import time. Both CI (`ci.yml`) and the engine's
container image (`engine/Dockerfile`) install `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b
libfribidi0 libcairo2 libgdk-pixbuf-2.0-0 fonts-dejavu-core` — the two hands on the same knob,
so "works in CI" and "works in the container" cannot drift apart. Missing them is the
number-one cause of "works locally, blank PDF in production". Report fonts ship inside the
image (bundled from M9; until then `fonts-dejavu-core` is the fallback family) — never fetched
from Google Fonts at render time, or the PDF silently falls back and looks wrong only in
production.
