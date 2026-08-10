"""Shared settings for SignalWatch."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[1]
_ENV_FILE = _ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # Postgres + pgvector (Supabase)
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/postgres"

    slack_webhook_url: str = ""
    resend_api_key: str = ""
    alert_from_email: str = "alerts@signalwatch.local"
    alert_to_email: str = ""

    max_pages_per_run: int = 20
    scrape_timeout_seconds: int = 30
    scrape_max_retries: int = 2
    alert_score_threshold: float = 0.55
    review_band_low: float = 0.45
    review_band_high: float = 0.55
    request_delay_seconds: float = 1.0

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key != "sk-...")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
