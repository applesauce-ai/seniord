from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment (and an optional .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Defaults target host-run scripts/tests hitting the published Postgres port;
    # Docker Compose overrides DATABASE_URL to point at the "db" service.
    database_url: str = "postgresql://orders:orders@localhost:5432/orders"
    api_base_url: str = "http://localhost:8000"

    worker_poll_interval: float = 1.0
    worker_batch_size: int = 50


settings = Settings()
