# Percentile

Career interest, personality and work-values assessment for Pakistani institutions.
A counsellor uploads a class roster, students complete a ~25-minute assessment on their
phones, and two artifacts come out: a branded student PDF and a cohort report for the
institution.

**`plan.md` is the build spec.** Read `## 0. Rules` in it before writing any code — several
rules exist because breaking them destroys the product's credibility.

## Layout

```
.github/workflows/   ci.yml (lint + tests) · tick.yml (cron → engine) · keepalive.yml
db/migrations/       ordered SQL; never edit one that has run
db/testing/          auth-schema shim + grants for the test harness — NOT migrations
db/seed/             demo_org.sql, the two-organisation world the DB tests read
data/instruments/    downloaded item files — not generated (R2)
data/onet/           O*NET database export — gitignored, download locally
data/local/          pk_occupation_map.csv, the localisation layer
engine/              FastAPI: scoring · matching · WeasyPrint PDFs · email
web/                 Next.js: marketing, counsellor dashboard, student taker
```

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.12 |
| Node | 20+ |
| pnpm | via `corepack enable pnpm` |

## Setup

```powershell
# engine
cd engine
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env        # then fill it in
pytest -q

# web
cd web
pnpm install
copy .env.local.example .env.local   # then fill it in
pnpm dev
```

## Commands

```powershell
# engine
uvicorn app.main:app --reload
pytest -q
ruff check .
python scripts/load_instruments.py
python scripts/load_onet.py
python scripts/rescore_all.py --dry-run

# web
pnpm dev
pnpm build
pnpm lint

# db
supabase db push
```

## Database tests

The R9 trigger and the row-level security policies are enforced by Postgres, not by Python, so
they are tested against a real server. Those tests skip unless `TEST_DATABASE_URL` is set, which
keeps `pytest` fast and Docker-free by default.

```powershell
docker run -d --name pg-test -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16

# in engine/.env, or as an environment variable
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres

cd engine
.\.venv\Scripts\python.exe -m pytest -m db       # just these
.\.venv\Scripts\python.exe -m pytest -m "not db" # everything else
```

**The harness runs `drop schema public cascade`.** Point it at a throwaway database only.

It applies `db/testing/0000_supabase_shim.sql` (the `auth` schema, `auth.uid()`, and the
`anon`/`authenticated`/`service_role` roles that Supabase provides and a plain container does
not), then every migration in order, then `db/testing/9999_grants.sql`, then the seed. CI does the
same against a `postgres:16` service container — see `.github/workflows/ci.yml`.

Nothing in `db/testing/` is a migration; see its README.

## Architecture in one paragraph

The engine is never called synchronously from a user request. When a student submits, the
web app writes a row to `jobs` and shows "your report is being prepared". A GitHub Actions
cron hits `POST /tick` every five minutes; the engine wakes, claims pending jobs, scores
them, renders PDFs, sends email, and sleeps again. This makes a free-tier cold start
harmless, keeps the Supabase project from pausing, and turns a failed job into a retry
instead of a lost submission. Do not replace it with a direct call — see `plan.md` §2.

## Attribution

This product incorporates information from the O*NET Database by the U.S. Department of
Labor, Employment and Training Administration (USDOL/ETA). O*NET® is a trademark of
USDOL/ETA. Attribution appears on every report and in the site footer (`plan.md` R6).
