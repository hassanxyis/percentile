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
    # Must be a PRIVATE bucket. A public one means a guessable URL is a minor's
    # full psychological profile, with no expiry and no audit trail. Delivery is
    # a signed URL minted at send time (§15, R8).
    report_storage_bucket: str = "reports"
    # How long a report link lives. plan §15: "a signed URL, expiring in 7 days".
    report_url_days: int = 7
    scoring_engine_version: str = SCORING_ENGINE_VERSION
    app_base_url: str = "http://localhost:3000"
    log_level: str = "INFO"

    # ── email ────────────────────────────────────────────────────────────
    # An unset resend_api_key selects LoggingEmailer, which logs instead of
    # sending (app/email.py). That is the only switch — there is deliberately no
    # separate enable flag, because two switches means a deployment can hold a
    # real key and still send nothing.
    email_from: str = "Percentile <noreply@example.invalid>"
    # Where job_failed_alert and review_backlog_alert go. Unset means those
    # mails are logged and dropped; the runner warns rather than failing the
    # job, since an alert that fails would alert about itself.
    operator_alert_email: str = ""

    # ── job runner (plan §12) ────────────────────────────────────────────
    job_batch_size: int = 10
    # Attempts are counted at CLAIM time, not on failure — see the header of
    # 0008_job_runner.sql for why, and why the stale timeout below is therefore
    # mandatory rather than an optimisation.
    job_max_attempts: int = 5
    # Must exceed the longest a legitimate job can run. Report renders (M9) are
    # the slow ones. Generous against a five-minute tick.
    job_stale_timeout_seconds: int = 1800
    # tick.yml calls curl with -m 120. The runner stops claiming new work before
    # this so it can answer with what it did; without it, a slow batch is killed
    # mid-flight and every job in it has to wait for the stale reaper.
    tick_deadline_seconds: int = 90
    review_backlog_days: int = 5

    # Tests only. A direct Postgres connection string for engine/tests/db/, which
    # assert the R9 trigger and the RLS policies against a real server — things
    # the Supabase REST client above cannot express. Empty in every deployment;
    # the application never reads it.
    test_database_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
