import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(os.getenv("APP_CONFIG_DIR", "/config"))
try:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    CONFIG_DIR = Path.cwd() / ".config"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = CONFIG_DIR / ".env"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_name: str = "agile-predict-api"
    app_version: str = "0.1.0"
    app_env: str = "development"

    db_mode: str = Field(default="local", alias="DB_MODE")
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/agile_predict",
        alias="DATABASE_URL",
    )
    postgres_db: str = Field(default="agile_predict", alias="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_password: str = Field(default="postgres", alias="POSTGRES_PASSWORD")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_data_dir: str = Field(default="/config/postgresql", alias="POSTGRES_DATA_DIR")
    cors_allowed_origins: str = Field(
        default="http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ALLOWED_ORIGINS",
    )
    auto_bootstrap_on_startup: bool = Field(default=True, alias="AUTO_BOOTSTRAP_ON_STARTUP")
    auto_bootstrap_mode: str = Field(default="update", alias="AUTO_BOOTSTRAP_MODE")
    auto_bootstrap_points: int = Field(default=96, alias="AUTO_BOOTSTRAP_POINTS")
    auto_bootstrap_regions: str = Field(default="X,G", alias="AUTO_BOOTSTRAP_REGIONS")
    auto_update_enabled: bool = Field(default=True, alias="AUTO_UPDATE_ENABLED")
    auto_update_interval_seconds: int = Field(default=1800, alias="AUTO_UPDATE_INTERVAL_SECONDS")
    auto_update_run_immediately: bool = Field(default=False, alias="AUTO_UPDATE_RUN_IMMEDIATELY")
    ml_write_mode: str = Field(default="ml", alias="ML_WRITE_MODE")
    allow_ingest_fallback: bool = Field(default=False, alias="ALLOW_INGEST_FALLBACK")
    allow_ml_fallback: bool = Field(default=False, alias="ALLOW_ML_FALLBACK")
    allow_startup_bootstrap_fallback: bool = Field(default=False, alias="ALLOW_STARTUP_BOOTSTRAP_FALLBACK")

    def validate_db_mode(self) -> None:
        if self.db_mode not in {"external", "local"}:
            raise ValueError("DB_MODE must be 'external' or 'local'.")

        if not self.database_url.startswith("postgresql"):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL.")

        if self.auto_bootstrap_mode not in {"update", "bootstrap"}:
            raise ValueError("AUTO_BOOTSTRAP_MODE must be 'update' or 'bootstrap'.")

        if self.ml_write_mode not in {"ml", "deterministic", "shadow"}:
            raise ValueError("ML_WRITE_MODE must be 'ml', 'deterministic', or 'shadow'.")

        if self.auto_update_interval_seconds < 60:
            raise ValueError("AUTO_UPDATE_INTERVAL_SECONDS must be >= 60.")

    @property
    def cors_origins_list(self) -> list[str]:
        return [item.strip() for item in self.cors_allowed_origins.split(",") if item.strip()]

    @property
    def bootstrap_regions_list(self) -> list[str]:
        return [item.strip().upper() for item in self.auto_bootstrap_regions.split(",") if item.strip()]


settings = Settings()
settings.validate_db_mode()
