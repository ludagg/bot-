from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sih:sih@localhost:5432/sih"
    redis_url: str = "redis://localhost:6379/0"
    github_token: str = ""
    log_level: str = "INFO"


def get_settings() -> Settings:
    return Settings()
