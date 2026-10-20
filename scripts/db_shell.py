"""Run psql with the application's actual connection settings, without URL arguments."""

import os
import shutil
import subprocess

from index_valuation.config import Settings


def psql_environment(settings: Settings) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PGSERVICE", None)
    environment.pop("PGSERVICEFILE", None)
    environment.update(
        PGHOST=settings.postgres_host,
        PGPORT=str(settings.postgres_port),
        PGDATABASE=settings.postgres_db,
        PGUSER=settings.postgres_user,
        PGPASSWORD=settings.postgres_password.get_secret_value(),
        PGCONNECT_TIMEOUT=str(settings.postgres_connect_timeout),
        PGOPTIONS="-c timezone=UTC",
    )
    return environment


def main() -> int:
    executable = shutil.which("psql")
    if executable is None:
        print("未找到 psql；请安装 PostgreSQL 客户端后重试。")
        return 1
    return subprocess.call(
        [executable, "-X", "--set", "ON_ERROR_STOP=1"],
        env=psql_environment(Settings()),
    )


if __name__ == "__main__":
    raise SystemExit(main())
