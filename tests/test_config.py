from pathlib import Path

import pytest
from pydantic import ValidationError

from index_valuation.config import IntegrationSettings, MigrationSettings, Settings


def test_database_url_preserves_special_characters_without_exposing_password(monkeypatch):
    password = "local-test@:/%?#password"
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    settings = Settings(_env_file=None)

    assert settings.database_url.password == password
    assert settings.database_url.drivername == "postgresql+psycopg"
    assert password not in str(settings.database_url)
    assert password not in repr(settings)


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("POSTGRES_PORT=5433\nPOSTGRES_PASSWORD=local-test\n")
    monkeypatch.setenv("POSTGRES_PORT", "55432")

    assert Settings(_env_file=dotenv).postgres_port == 55432


def test_container_runtime_paths_are_explicit_and_optional(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "runtime-password")
    host = Settings(_env_file=None)
    assert host.scheduled_tasks_root is None
    assert host.scheduled_tasks_snapshot is None
    assert host.migrations_dir == Path("migrations")

    monkeypatch.setenv("SCHEDULED_TASKS_ROOT", "/app")
    monkeypatch.setenv("SCHEDULED_TASKS_SNAPSHOT", "/app/data/scheduler/host-status.json")
    monkeypatch.setenv("MIGRATIONS_DIR", "/app/migrations")
    container = Settings(_env_file=None)
    assert container.scheduled_tasks_root == Path("/app")
    assert container.scheduled_tasks_snapshot == Path("/app/data/scheduler/host-status.json")
    assert container.migrations_dir == Path("/app/migrations")


@pytest.mark.parametrize("password", [None, ""])
def test_password_is_required(monkeypatch, password):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    if password is not None:
        monkeypatch.setenv("POSTGRES_PASSWORD", password)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_migration_requires_explicit_owner_url_even_if_runtime_is_configured(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "runtime-password")
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        MigrationSettings(_env_file=None)


def test_runtime_and_migration_credentials_are_separate(monkeypatch):
    password = "owner-password-secret"
    monkeypatch.setenv("POSTGRES_PASSWORD", "runtime-password")
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        f"postgresql://index_valuation_owner:{password}@localhost:15432/index_valuation",
    )
    runtime = Settings(_env_file=None)
    migration = MigrationSettings(_env_file=None)
    assert runtime.database_url.password == "runtime-password"
    assert migration.database_url.username == "index_valuation_owner"
    assert migration.database_url.password == password
    assert migration.database_url.drivername == "postgresql+psycopg"
    assert password not in repr(migration)
    assert password not in str(migration.database_url)
    assert not hasattr(runtime, "migration_database_url")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://test:secret@localhost/index_valuation",
        "postgresql://test:secret@localhost/octopus",
        "postgresql://test:secret@localhost/octopus_assistant",
        "postgresql://test:secret@localhost/index_valuation_test?dbname=index_valuation",
        "sqlite:///index_valuation_test",
    ],
)
def test_integration_rejects_production_or_overridden_targets(url):
    settings = IntegrationSettings(_env_file=None, test_database_url=url)
    with pytest.raises(ValueError) as error:
        _ = settings.database_url
    assert "secret" not in str(error.value)


def test_integration_does_not_fall_back_to_runtime_database(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "runtime-password")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        IntegrationSettings(_env_file=None)


def test_integration_accepts_explicit_isolated_database():
    settings = IntegrationSettings(
        _env_file=None,
        test_database_url="postgresql://test:secret@localhost:15433/index_valuation_test",
    )
    assert settings.database_url.database == "index_valuation_test"
    assert settings.database_url.port == 15433
