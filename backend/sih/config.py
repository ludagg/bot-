from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sih:sih@localhost:5432/sih"
    redis_url: str = "redis://localhost:6379/0"  # réservé (verrous/files) ; v0 : verrous en base
    github_token: str = ""
    log_level: str = "INFO"

    # Moteur de détection
    baseline_days: int = 14
    publish_min_history_days: int = 7

    # LLM (explication uniquement)
    anthropic_api_key: str = ""
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_daily_cap: int = 50

    # Publication
    site_base_url: str = "http://localhost:3000"
    x_mode: str = "review"  # off | review | auto  (interrupteur manuel)
    x_daily_cap: int = 3
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_secret: str = ""

    # Alertes
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    cors_origins: str = "*"


def get_settings() -> Settings:
    return Settings()
