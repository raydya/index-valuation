from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from index_valuation.cli import main
from index_valuation.config import Settings
from index_valuation.webapp import _database_ready, create_app


def fake_engine(*, heads=("0007",), error=None):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.scalars.return_value = heads
    if error:
        connection.execute.side_effect = error
    return engine, connection


def test_liveness_does_not_connect_and_database_errors_are_redacted(caplog):
    secret = "database-password-must-not-appear"
    engine, connection = fake_engine(error=OperationalError("SELECT", {}, Exception(secret)))
    with TestClient(create_app(repository=SimpleNamespace(engine=engine), specs={})) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        engine.connect.assert_not_called()

        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
        assert secret not in response.text
        assert secret not in caplog.text
        assert connection.execute.called


def test_readiness_rejects_old_schema_even_when_database_is_reachable():
    engine, _ = fake_engine(heads=("0006",))
    with TestClient(create_app(repository=SimpleNamespace(engine=engine), specs={})) as client:
        assert client.get("/readyz").status_code == 503


def test_readiness_requires_bundled_migration_metadata(tmp_path):
    engine, _ = fake_engine()
    settings = Settings(
        _env_file=None,
        postgres_password="runtime-test",
        migrations_dir=tmp_path / "missing-migrations",
    )
    app = create_app(repository=SimpleNamespace(engine=engine), specs={}, settings=settings)
    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 503
        engine.connect.assert_not_called()


def test_scheduler_container_paths_are_forwarded(monkeypatch):
    called = {}

    def scheduled_tasks(repository, specs, **kwargs):
        called.update(kwargs)
        return SimpleNamespace(snapshot=lambda: {})

    monkeypatch.setattr("index_valuation.webapp.ScheduledTasks", scheduled_tasks)
    settings = Settings(
        _env_file=None,
        postgres_password="runtime-test",
        scheduled_tasks_root=Path("/app"),
        scheduled_tasks_snapshot=Path("/app/data/scheduler/host-status.json"),
    )
    create_app(repository=SimpleNamespace(), specs={}, settings=settings)
    assert called == {
        "root": Path("/app"),
        "snapshot_file": Path("/app/data/scheduler/host-status.json"),
    }


@pytest.mark.parametrize(
    ("arguments", "host"),
    [([], "127.0.0.1"), (["--host", "0.0.0.0"], "0.0.0.0")],
)
def test_serve_listener_is_configurable_without_changing_host_default(monkeypatch, arguments, host):
    settings = SimpleNamespace()
    app = object()
    calls = []
    monkeypatch.setattr("index_valuation.cli.Settings", lambda: settings)
    monkeypatch.setattr("index_valuation.webapp.create_app", lambda **kwargs: app)
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert main(["serve", "--port", "18000", *arguments]) == 0
    assert calls == [((app,), {"host": host, "port": 18000})]


@pytest.mark.integration
def test_readiness_checks_current_schema_with_actual_postgres(test_database_engine):
    expected_heads = set(ScriptDirectory("migrations").get_heads())
    assert _database_ready(test_database_engine, expected_heads) is True
    assert _database_ready(test_database_engine, {"unavailable-revision"}) is False
