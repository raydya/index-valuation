"""Shared configuration for scripts, database connections, and migrations."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=15432, ge=1, le=65535)
    postgres_db: str = Field(default="index_valuation", min_length=1)
    postgres_user: str = Field(default="index_valuation_runtime", min_length=1)
    postgres_password: SecretStr = Field(min_length=1)
    postgres_connect_timeout: int = Field(default=5, ge=1, le=60)
    futu_host: str = "127.0.0.1"
    futu_port: int = Field(default=11111, ge=1, le=65535)
    source_timeout: int = Field(default=20, ge=1, le=60)
    indices_file: Path = Path("indices.toml")
    data_dir: Path = Path("data")
    migrations_dir: Path = Path("migrations")
    scheduled_tasks_root: Path | None = None
    scheduled_tasks_snapshot: Path | None = None

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


def postgres_url(secret: SecretStr, setting_name: str) -> URL:
    """Parse an explicit PostgreSQL URL without including credentials in errors."""
    try:
        url = make_url(secret.get_secret_value())
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError
        if not url.database or not url.username or not url.host:
            raise ValueError
        if url.query:
            # A URL option such as dbname/service could override the checked target.
            raise ValueError
    except (ArgumentError, ValueError):
        raise ValueError(
            f"{setting_name} must be a PostgreSQL URL with host, user and database, "
            "without query parameters"
        ) from None
    return url.set(drivername="postgresql+psycopg")


class MigrationSettings(BaseSettings):
    """Only migration entry points load the database owner's credential."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    migration_database_url: SecretStr = Field(min_length=1)

    @property
    def database_url(self) -> URL:
        return postgres_url(self.migration_database_url, "MIGRATION_DATABASE_URL")


class IntegrationSettings(BaseSettings):
    """Integration tests must explicitly select an isolated test database."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    test_database_url: SecretStr = Field(min_length=1)

    @property
    def database_url(self) -> URL:
        url = postgres_url(self.test_database_url, "TEST_DATABASE_URL")
        if not url.database.endswith("_test"):
            raise ValueError("TEST_DATABASE_URL database name must end with _test")
        return url
