"""Environment-backed settings. Every secret in the engine arrives through here."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Bumped (semver) whenever any scoring rule changes. Stamped onto every `scores` and
# `occupation_matches` row so a rescore can be compared against its predecessor (plan R1).
SCORING_ENGINE_VERSION = "1.0.0"

# Bumped whenever a report template changes. Stamped onto every `reports` row.
TEMPLATE_VERSION = "1.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    engine_shared_secret: str = ""
    resend_api_key: str = ""
    report_storage_bucket: str = "reports"
    scoring_engine_version: str = SCORING_ENGINE_VERSION
    app_base_url: str = "http://localhost:3000"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
