"""Local, read-only web interface over the persisted direct index series."""

import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from index_valuation.archive import json_text
from index_valuation.config import Settings
from index_valuation.db import build_engine
from index_valuation.domain import DataError
from index_valuation.index_series import build_dashboard, method_for, series_csv
from index_valuation.registry import load_indices
from index_valuation.repository import Repository

WEB = Path(__file__).with_name("web")
log = logging.getLogger(__name__)


def create_app(*, repository=None, specs=None, settings=None):
    engine = None
    if repository is None:
        settings = settings or Settings()
        engine = build_engine(settings)
        repository = Repository(engine)
    specs = specs if specs is not None else load_indices((settings or Settings()).indices_file)

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

    @app.get("/api/indices")
    def indices():
        return [
            {"key": s.key, "name": s.name, "code": s.quote_code, "tracking_etfs": s.tracking_etfs}
            for s in specs.values()
        ]

    def dashboard(key, as_of, chart_years, history_years):
        if key not in specs:
            raise HTTPException(404, "指数未配置")
        try:
            return build_dashboard(
                specs[key],
                repository.index_series(key, method_for(specs[key])),
                as_of=as_of,
                chart_years=chart_years,
                history_years=history_years,
            )
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
