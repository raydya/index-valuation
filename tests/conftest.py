import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import text

from index_valuation.config import IntegrationSettings
from index_valuation.db import build_engine_for_url


@pytest.fixture(scope="session")
def test_database_url():
    try:
        settings = IntegrationSettings()
    except ValidationError as error:
        if all(item["type"] == "missing" for item in error.errors()):
            pytest.skip("Set TEST_DATABASE_URL to a separate database ending in _test")
        raise
    return settings.database_url


@pytest.fixture(scope="session")
def test_database_engine(test_database_url):
    engine = build_engine_for_url(test_database_url)
    try:
        with engine.begin() as connection:
            database = connection.scalar(text("SELECT current_database()"))
            if database != test_database_url.database or not database.endswith("_test"):
                pytest.fail("Refusing integration tests against an unexpected database")
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
