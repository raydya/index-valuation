"""Shared configuration for scripts, database connections, and migrations."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5433, ge=1, le=65535)
    postgres_db: str = Field(default="index_valuation", min_length=1)
    postgres_user: str = Field(default="index_valuation", min_length=1)
    postgres_password: SecretStr = Field(min_length=1)
    postgres_connect_timeout: int = Field(default=5, ge=1, le=60)
    futu_host: str = "127.0.0.1"
    futu_port: int = Field(default=11111, ge=1, le=65535)
    source_timeout: int = Field(default=20, ge=1, le=60)
    indices_file: Path = Path("indices.toml")
    data_dir: Path = Path("data")

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
