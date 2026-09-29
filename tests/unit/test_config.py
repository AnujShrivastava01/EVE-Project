import pytest
from pydantic import ValidationError

from app.core.config import Settings

STRONG_SECRET = "x" * 40


def make(**env: str) -> Settings:
    return Settings(_env_file=None, **env)


def test_list_settings_accept_csv_and_json_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, http://b.example")
    monkeypatch.setenv("WEBHOOK_RETRY_BACKOFF_SECONDS", "2,4,8,16,32")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://a.example", "http://b.example"]
    assert settings.webhook_retry_backoff_seconds == [2, 4, 8, 16, 32]

    monkeypatch.setenv("CORS_ORIGINS", '["http://c.example"]')
    assert Settings(_env_file=None).cors_origins == ["http://c.example"]


def test_backoff_length_must_match_max_retries() -> None:
    with pytest.raises(ValidationError):
        make(webhook_max_retries=3)  # default schedule has 5 entries


def test_production_refuses_insecure_configuration() -> None:
    with pytest.raises(ValidationError):  # default/placeholder secret
        make(
            environment="production",
            jwt_secret_key="change-me",
            payment_allow_simulation_override=False,
        )
    with pytest.raises(ValidationError):  # simulation override still on
        make(environment="production", jwt_secret_key=STRONG_SECRET)
    ok = make(
        environment="production",
        jwt_secret_key=STRONG_SECRET,
        payment_allow_simulation_override=False,
    )
    assert ok.environment == "production"
