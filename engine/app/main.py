"""FastAPI application. The engine is private: every route except /health requires
the shared secret, and nothing here is ever called from a browser (plan §10)."""

import logging
import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status

from app.config import SCORING_ENGINE_VERSION, TEMPLATE_VERSION, Settings, get_settings

logger = logging.getLogger(__name__)

app = FastAPI(title="Percentile Engine", version=SCORING_ENGINE_VERSION)


def require_engine_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_engine_key: Annotated[str | None, Header()] = None,
) -> None:
    """Reject anything without the shared secret.

    Compared with `compare_digest` so a wrong key cannot be recovered by timing the
    response. An unset secret rejects everything rather than allowing everything.
    """
    expected = settings.engine_shared_secret
    if not expected or not x_engine_key or not secrets.compare_digest(x_engine_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid engine key"
        )


def check_database() -> None:
    """Read one row, to prove Postgres is reachable. Raises on any failure.

    `organisations` is the smallest tenant table and is never empty in a real
    deployment; `limit(1)` keeps this to a single row regardless.
    """
    from app.db import get_client

    get_client().table("organisations").select("id").limit(1).execute()


def get_database_check() -> Callable[[], None]:
    """Supply the health probe's database check.

    Indirection exists so tests can override the check via
    `app.dependency_overrides` — the same idiom `test_main.py` already uses for
    `get_settings` — rather than needing live Supabase credentials to assert
    that `/health` is open.
    """
    return check_database


@app.get("/health")
def health(
    response: Response,
    db_check: Annotated[Callable[[], None], Depends(get_database_check)],
) -> dict:
    """Liveness, and the keep-alive.

    The Postgres touch is the point, not a bonus: a Supabase free project pauses
    after 7 days idle, and between now and the job runner (M7) nothing else
    connects. `.github/workflows/keepalive.yml` calls this daily (plan §2, §11).

    A failure reports 503, not 200. A liveness probe that says "ok" while the
    database is unreachable is worse than no probe — it would keep the project
    alive while hiding that every job was failing.
    """
    try:
        db_check()
    except Exception:
        logger.exception("health check could not reach Postgres")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded"}
    return {"status": "ok"}


@app.get("/version", dependencies=[Depends(require_engine_key)])
def version() -> dict:
    return {
        "engine_version": SCORING_ENGINE_VERSION,
        "template_version": TEMPLATE_VERSION,
    }


@app.post("/tick", dependencies=[Depends(require_engine_key)])
def tick(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    """Claim and run pending jobs (plan §11, §12).

    The only thing that drives the engine. `.github/workflows/tick.yml` calls
    this every five minutes; nothing calls it from a user request, and a student
    submitting waits for the next tick rather than for a scoring run (plan §2).

    Returns counts, never payloads: a job payload carries a `participant_id`,
    and for a moment inside the invite handler a live token. This response is
    logged by curl, by Actions, and by whatever proxy sits in front.

    Runs synchronously rather than in a background task. The HTTP response is
    the only signal Actions gets — returning 202 immediately would make the
    workflow green whatever happened, which is the same fail-green shape that
    made `keepalive.yml` useless for six milestones.
    """
    from app.db import get_client
    from app.jobs.runner import run_tick

    result = run_tick(get_client(), settings)
    return result.as_dict()
