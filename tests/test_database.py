from decimal import Decimal

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from index_valuation.db import database_info

pytestmark = pytest.mark.integration


@pytest.fixture
def engine(test_database_engine):
    return test_database_engine


def test_postgres_is_ready_and_migrated(engine):
    info = database_info(engine)
    assert info["encoding"] == "UTF8"
    assert info["timezone"] == "UTC"

    expected_heads = set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
    with engine.connect() as connection:
        assert set(MigrationContext.configure(connection).get_current_heads()) == expected_heads


def test_decimal_values_roundtrip_and_transaction_rolls_back(engine):
    # PostgreSQL temporary tables are session-local and never touch business tables.
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text("CREATE TEMP TABLE valuation_smoke (value NUMERIC(24, 10))"))
        transaction = connection.begin()
        try:
            expected = Decimal("123456789.1234567890")
            connection.execute(
                text("INSERT INTO valuation_smoke (value) VALUES (:value)"), {"value": expected}
            )
            assert connection.scalar(text("SELECT value FROM valuation_smoke")) == expected
        finally:
            transaction.rollback()
        assert connection.scalar(text("SELECT count(*) FROM valuation_smoke")) == 0
        connection.execute(text("DROP TABLE valuation_smoke"))
        connection.commit()
