"""Local, read-only web interface over the persisted direct index series."""

import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import false, select, text
from sqlalchemy.exc import SQLAlchemyError

from index_valuation.archive import json_text
from index_valuation.config import Settings
from index_valuation.db import build_engine
from index_valuation.domain import DataError
from index_valuation.index_series import build_dashboard, method_for, series_csv
from index_valuation.market_overview import build_market_overview
from index_valuation.models import (
    IndexRecord,
    IndexSeriesRecord,
    RunRecord,
    TotalReturnObservation,
)
from index_valuation.panic_reference import load_panic_reference, panic_reference_for
from index_valuation.registry import load_indices
from index_valuation.repository import Repository
from index_valuation.scheduled_tasks import ScheduledTasks

WEB = Path(__file__).with_name("web")
log = logging.getLogger(__name__)


def _database_ready(engine, expected_heads):
    """Verify read permissions and schema without reading historical series."""
    if engine is None or not expected_heads:
        return False
    with engine.connect() as connection:
        # The probe cannot alter application data and bounds lock/query waits.
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '3000ms'"))
        actual_heads = set(connection.scalars(text("SELECT version_num FROM alembic_version")))
        if actual_heads != expected_heads:
            return False
        for model in (IndexRecord, IndexSeriesRecord, RunRecord, TotalReturnObservation):
            connection.execute(select(model.__table__).where(false()))
    return True


def create_app(*, repository=None, specs=None, settings=None, scheduled_tasks=None):
    engine = None
    if repository is None:
        settings = settings or Settings()
        engine = build_engine(settings)
        repository = Repository(engine)
    specs = specs if specs is not None else load_indices((settings or Settings()).indices_file)
    panic_reference = load_panic_reference()
    scheduled_tasks = scheduled_tasks or ScheduledTasks(
        repository,
        specs,
        root=settings.scheduled_tasks_root if settings else None,
        snapshot_file=settings.scheduled_tasks_snapshot if settings else None,
    )
    readiness_engine = getattr(repository, "engine", None)
    expected_heads = None
    if readiness_engine is not None:
        try:
            expected_heads = set(
                ScriptDirectory(
                    str(settings.migrations_dir if settings else Path("migrations"))
                ).get_heads()
            )
        except (CommandError, OSError):
            log.warning("Web readiness unavailable: migration metadata is missing or invalid")

    @asynccontextmanager
    async def lifespan(app):
        yield
        if engine is not None:
            engine.dispose()

    app = FastAPI(title="指数估值", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        log.error("Dashboard database unavailable", exc_info=error)
        return Response(
            json_text({"detail": "数据库不可用，请检查 PostgreSQL 服务"}),
            status_code=503,
            media_type="application/json",
        )

    @app.get("/")
    def home():
        return FileResponse(WEB / "index.html")

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/readyz")
    def ready():
        try:
            is_ready = _database_ready(readiness_engine, expected_heads)
        except SQLAlchemyError:
            # SQLAlchemy errors can contain connection or statement values.
            log.warning("Web readiness unavailable: database check failed")
            is_ready = False
        if not is_ready:
            return Response(
                json_text({"status": "unavailable"}),
                status_code=503,
                media_type="application/json",
            )
        return {"status": "ready"}

    @app.get("/api/indices")
    def indices():
        return [
            {
                "key": s.key,
                "name": s.name,
                "code": s.quote_code,
                "tracking_etfs": s.tracking_etfs,
                "collection_enabled": s.collection_enabled,
                "collection_disabled_reason": s.collection_disabled_reason,
            }
            for s in specs.values()
        ]

    @app.get("/api/scheduled-tasks")
    def get_scheduled_tasks():
        return Response(json_text(scheduled_tasks.snapshot()), media_type="application/json")

    @app.get("/api/market-overview")
    def get_market_overview():
        return Response(
            json_text(build_market_overview(specs, repository.latest_index_points(specs))),
            media_type="application/json",
        )

    @app.get("/api/panic-reference")
    def get_panic_reference():
        return Response(
            json_text(panic_reference),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="panic-reference-{panic_reference["study_date"]}.json"'
                )
            },
        )

    def dashboard(key, as_of, chart_years, history_years):
        if key not in specs:
            raise HTTPException(404, "指数未配置")
        try:
            data = build_dashboard(
                specs[key],
                repository.index_series(key, method_for(specs[key])),
                as_of=as_of,
                chart_years=chart_years,
                history_years=history_years,
                total_return_points=repository.total_return_points(specs[key])
                if specs[key].total_return
                else [],
            )

            # Current lines come from the persisted current-method sequence. The
            # bundled research remains a dated archive with its own evidence.
            def visible_line(spec, line, day=None):
                if not spec.collection_enabled:
                    return {"status": "collection_paused", "as_of": day}
                return line or {"status": "not_calculated", "as_of": day}

            data["panic_line"] = visible_line(
                specs[key], data["panic_line"], data["selected"]["trade_date"]
            )
            lines = repository.panic_lines(
                specs, as_of=data["selected"]["trade_date"] if as_of else None
            )
            data["panic_overview"] = [
                {
                    "key": spec.key,
                    "name": spec.name,
                    "line": visible_line(spec, lines.get(spec.key)),
                }
                for spec in specs.values()
            ]
            data["greed_line"] = visible_line(
                specs[key], data["greed_line"], data["selected"]["trade_date"]
            )
            greed_lines = repository.greed_lines(
                specs, as_of=data["selected"]["trade_date"] if as_of else None
            )
            data["greed_overview"] = [
                {
                    "key": spec.key,
                    "name": spec.name,
                    "line": visible_line(spec, greed_lines.get(spec.key)),
                }
                for spec in specs.values()
            ]
            data["panic_reference"] = panic_reference_for(data, panic_reference, specs)
            return data
        except DataError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/indices/{key}/dashboard")
    def get_dashboard(
        key: str,
        as_of: date | None = None,
        chart_years: int = Query(5, ge=0, le=10),
        history_years: int = Query(5, ge=0, le=10),
    ):
        return Response(
            json_text(dashboard(key, as_of, chart_years, history_years)),
            media_type="application/json",
        )

    @app.get("/api/indices/{key}/export.csv")
    def export(
        key: str,
        as_of: date | None = None,
        chart_years: int = Query(0, ge=0, le=10),
        history_years: int = Query(5, ge=0, le=10),
    ):
        data = dashboard(key, as_of, chart_years, history_years)
        filename = f"{key}-bands-{data['selected']['trade_date']}.csv"
        return Response(
            series_csv(data),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
