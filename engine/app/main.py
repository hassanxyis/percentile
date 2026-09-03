"""FastAPI application. The engine is private: every route except /health requires
the shared secret, and nothing here is ever called from a browser (plan §10)."""

import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.config import SCORING_ENGINE_VERSION, TEMPLATE_VERSION, Settings, get_settings

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


@app.get("/health")
def health() -> dict:
    """Liveness. Will also touch Postgres once db.py lands, which is what keeps the
    Supabase free project from pausing after 7 days idle (plan §2)."""
    return {"status": "ok"}


@app.get("/version", dependencies=[Depends(require_engine_key)])
def version() -> dict:
    return {
        "engine_version": SCORING_ENGINE_VERSION,
        "template_version": TEMPLATE_VERSION,
    }
