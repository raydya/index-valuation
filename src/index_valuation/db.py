"""Database primitives shared by future ingestion and valuation jobs."""

from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

from index_valuation.config import Settings


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": settings.postgres_connect_timeout,
            "options": "-c timezone=UTC",
        },
    )


def database_info(engine: Engine) -> dict[str, str]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT current_database() AS database, current_user AS username, "
                    "current_setting('server_version') AS server_version, "
                    "current_setting('server_encoding') AS encoding, "
                    "current_setting('TimeZone') AS timezone"
                )
            )
            .mappings()
            .one()
        )
        return dict(row)
