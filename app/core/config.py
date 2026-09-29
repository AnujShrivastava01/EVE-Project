"""Application settings, loaded from environment variables (12-factor style)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_JWT_SECRETS = {"change-me", "changeme", "secret", ""}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- General -----------------------------------------------------------
    app_name: str = "EVE Healthcare Diagnostic Booking API"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- Infrastructure ----------------------------------------------------
    database_url: str = "postgresql+psycopg://eve:eve@localhost:5432/eve"
    redis_url: str = "redis://localhost:6379/0"

    # --- Auth --------------------------------------------------------------
    jwt_secret_key: str = Field(default="change-me")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # --- Booking rules -----------------------------------------------------
    booking_min_lead_minutes: int = 60
    booking_max_days_ahead: int = 90

    # --- Payments (simulated) ---------------------------------------------
    # What the simulator does when the client doesn't ask for a specific outcome.
    payment_simulation_mode: Literal["success", "failure", "pending"] = "success"
    # Lets clients pass `simulate_outcome` to /payments (handy for demos & tests).
    # MUST be false in production.
    payment_allow_simulation_override: bool = True
    # PENDING payments older than this are failed by the periodic cleanup task.
    payment_stale_after_minutes: int = 15

    # --- Webhooks ----------------------------------------------------------
    webhook_secret: str = "dev-webhook-secret"
    webhook_signature_required: bool = False
    webhook_max_retries: int = 5
    # Seconds to wait before retry N (1-indexed). Length must equal max_retries.
    webhook_retry_backoff_seconds: list[int] = [1, 5, 15, 30, 60]

    # --- Cache -------------------------------------------------------------
    cache_ttl_seconds: int = 60
    redis_socket_timeout_seconds: float = 0.5

    # --- Rate limiting (requests per window, per client IP) ----------------
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_auth_per_window: int = 10
    rate_limit_payment_per_window: int = 20
    rate_limit_webhook_per_window: int = 120

    @field_validator("cors_origins", "webhook_retry_backoff_seconds", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        # Allow "a,b,c" in env files in addition to JSON lists.
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if len(self.webhook_retry_backoff_seconds) != self.webhook_max_retries:
            raise ValueError("WEBHOOK_RETRY_BACKOFF_SECONDS must have WEBHOOK_MAX_RETRIES entries")
        if self.environment == "production":
            if self.jwt_secret_key in INSECURE_JWT_SECRETS or len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be a strong secret (>=32 chars) in production"
                )
            if self.payment_allow_simulation_override:
                raise ValueError("PAYMENT_ALLOW_SIMULATION_OVERRIDE must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
