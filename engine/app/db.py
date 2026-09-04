"""Supabase client construction. The engine always connects with the service
role key, which bypasses row-level security entirely (plan §4) — that is
correct here (scripts and the job runner act on behalf of the whole system,
not one tenant) and would be a severity-1 leak anywhere in `web/`.
"""

from functools import lru_cache

from supabase import Client, create_client

from app.config import get_settings


@lru_cache
def get_client() -> Client:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in engine/.env"
        )
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
