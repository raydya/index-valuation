"""Command-line entry point for development checks and future batch jobs."""

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from index_valuation.archive import Archive, json_text
from index_valuation.config import Settings
from index_valuation.db import build_engine, database_info
from index_valuation.domain import DataError, SourceError
from index_valuation.fundamentals import FundamentalBackfill
from index_valuation.history import HistoricalBackfill
from index_valuation.index_series import IndexSeries, method_for
from index_valuation.pipeline import Pipeline
from index_valuation.providers.baostock import BaoStockSource
from index_valuation.providers.cnindex_history import CNIndexHistory
from index_valuation.providers.csindex import CSIWeights
from index_valuation.providers.csindex_history import CSIHistory
from index_valuation.providers.danjuan_history import DanjuanHistory
from index_valuation.providers.eastmoney import EastmoneySource
from index_valuation.providers.fred_history import FredHistory
from index_valuation.providers.futu import FutuSource
from index_valuation.providers.futu_rest import FutuRestHistory
from index_valuation.providers.futu_rest_auth import FutuRestClient
from index_valuation.providers.google_history import GoogleHistory
from index_valuation.providers.gurufocus_valuation import GuruFocusValuation
from index_valuation.providers.multpl_valuation import MultplValuation
from index_valuation.providers.nasdaq_history import NasdaqHistory
from index_valuation.providers.total_return import TotalReturnSources
from index_valuation.providers.us_calendar import USCalendar
from index_valuation.providers.yahoo_etf_valuation import YahooETFValuation
from index_valuation.providers.yahoo_history import YahooHistory
from index_valuation.registry import load_indices
from index_valuation.repository import Repository
from index_valuation.total_returns import TotalReturnCollector


def incremental_start(spec, existing):
    """Overlap the latest stored date; a new method starts at its full history."""
    start = spec.history_start_date or spec.inception_date
    needs_first_pe = spec.valuation_source in {"futu", "futorest"} and not any(
        row["pe"] is not None for row in existing
    )
    if existing and not needs_first_pe:
        start = max(start, existing[-1]["trade_date"] - timedelta(days=14))
    return start


def record_paused_collection(repository, spec, output_dir, end=None):
    """Audit a configured pause without contacting providers or rewriting history."""
    repository.ensure_index(spec)
    with repository.lock(spec.key):
        run_id = repository.start_run(spec, end or datetime.now(ZoneInfo(spec.timezone)).date())
        report = {
            "mode": "direct_index_collection_paused",
            "index": spec.key,
            "run_id": str(run_id),
            "status": "blocked",
            "collection_enabled": False,
            "reason": spec.collection_disabled_reason,
            "archived_method": method_for(spec),
            "sources": {},
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{spec.key}-collection-paused-{run_id}.json"
        report["report_path"] = str(path)
        path.write_text(json_text(report), encoding="utf-8")
        repository.finish_run(run_id, "blocked", report)
        return report


def check_database(settings: Settings, wait: int) -> int:
    engine = build_engine(settings)
    deadline = time.monotonic() + wait
    try:
        while True:
            try:
                info = database_info(engine)
            except SQLAlchemyError:
                if time.monotonic() >= deadline:
                    print(
                        "数据库连接失败。请检查 .env 和 PostgreSQL 状态；"
                        "本地容器日志可用 make db-logs 查看。",
                        file=sys.stderr,
                    )
                    return 1
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            else:
                print(json_text({"status": "ok", **info}))
                return 0
    finally:
        engine.dispose()


def check_sources(spec, weights_source, primary_factory, secondary_factory) -> dict:
    report = {"index": spec.key}
    snapshot = weights_source.fetch(spec)
    report["csindex"] = {
        "status": "ok",
        "weight_date": snapshot.as_of,
        "constituents": len(snapshot.constituents),
    }
    sample = snapshot.constituents[0].symbol
    try:
        with primary_factory() as source:
            members, _ = source.members(spec)
            report["membership_matches"] = members == {row.symbol for row in snapshot.constituents}
            rows, path, _ = source.snapshots([sample, spec.quote_code])
            report["futu"] = {
                "status": "ok",
                "rows": len(rows),
                "artifact_path": path,
                "quote_times": {row["code"]: row["update_time"] for row in rows},
            }
    except (SourceError, OSError) as error:
        report["futu"] = {"status": "unavailable", "error": str(error)}
    try:
        with secondary_factory() as source:
            rows, errors = source.daily(spec, [sample], snapshot.as_of)
            report["baostock"] = {
                "status": "ok" if rows and not errors else "unavailable",
                "sample_date": snapshot.as_of,
                "rows": len(rows),
                "errors": errors,
            }
    except (SourceError, OSError) as error:
        report["baostock"] = {"status": "unavailable", "error": str(error)}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="指数估值系统：成分、行情核验与每日估值")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-db", help="检查 PostgreSQL 连接")
    check.add_argument("--wait", type=int, default=0, metavar="SECONDS")
    commands.add_parser("list-indices", help="列出配置的指数")
    serve = commands.add_parser("serve", help="启动本机指数估值网页")
    serve.add_argument("--port", type=int, default=8000)
    for command, help_text in (
        ("sync-constituents", "保存官方权重并核对富途成分列表"),
        ("check-sources", "只读检查主源行情权限及独立核验源"),
        ("run-daily", "收盘后采集、核验、计算；支持同日重跑"),
        ("calculate", "使用已保存数据重新计算指定日期"),
        ("backfill-history", "从发布日期回填指数历史参考序列并检查成分重算缺口"),
        ("backfill-fundamentals", "补齐已知成分公司的历史 PE TTM 和隐含 EPS TTM"),
        ("backfill-index", "回填全部指数点位、PE/PB/PS并生成独立的指数估值带"),
        ("backfill-returns", "单独回填已配置的分红再投资全收益历史，不重新请求价格和PE"),
        ("update-index", "增量更新指数直接估值，无需成分数据"),
        ("build-index", "只用已入库指数数据生成隐含 EPS 和估值带"),
        ("status", "查看最近一次任务报告"),
    ):
        command_parser = commands.add_parser(command, help=help_text)
        selection = command_parser.add_mutually_exclusive_group(required=True)
        selection.add_argument("--index", help="indices.toml 中的指数标识，例如 csi300")
        selection.add_argument("--all", action="store_true", help="处理所有配置的指数")
        if command == "update-index":
            selection.add_argument(
                "--market", nargs="+", choices=("CN", "HK", "US"), help="更新指定市场的所有指数"
            )
        if command == "calculate":
            command_parser.add_argument("--date", type=date.fromisoformat, required=True)
            command_parser.add_argument(
                "--historical-fundamentals",
                action="store_true",
                help="使用已核验的历史个股 EPS/PE，单独保存方法版本；仍需当时权重",
            )
        if command in {
            "backfill-history",
            "backfill-fundamentals",
            "backfill-index",
            "backfill-returns",
            "update-index",
        }:
            command_parser.add_argument(
                "--start", type=date.fromisoformat, help="默认使用配置中的正式发布日期"
            )
            command_parser.add_argument(
                "--end", type=date.fromisoformat, help="截至日期；指数直接流程支持当天收盘后的数据"
            )
        if command == "backfill-fundamentals":
            command_parser.add_argument("--symbols", nargs="+", help="只处理指定的已知成分证券")
            command_parser.add_argument("--refresh", action="store_true", help="重新采集已完成证券")
    args = parser.parse_args(argv)
    if args.command == "check-db" and args.wait < 0:
        parser.error("--wait 必须大于或等于 0")
    if args.command == "serve" and not 1024 <= args.port <= 65535:
        parser.error("--port 必须在 1024 到 65535 之间")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = Settings()
    except ValidationError as error:
        fields = ", ".join(".".join(map(str, item["loc"])) for item in error.errors())
        print(f"配置无效：{fields}。请运行 make env 并检查 .env。", file=sys.stderr)
        return 1
    if args.command == "check-db":
        return check_database(settings, args.wait)
    if args.command == "serve":
        import uvicorn

        from index_valuation.webapp import create_app

        uvicorn.run(create_app(settings=settings), host="127.0.0.1", port=args.port)
        return 0
    engine, rest_client = None, None
    try:
        specs = load_indices(settings.indices_file)
        if args.command == "list-indices":
            print(json_text(list(specs.values())))
            return 0
        if getattr(args, "market", None):
            selected = [spec for spec in specs.values() if spec.market in args.market]
            if not selected:
                raise DataError("所选市场没有配置指数")
        else:
            selected = list(specs.values()) if args.all else [specs[args.index]]
        archive = Archive(settings.data_dir / "raw")
        weights_source = CSIWeights(archive)

        def primary_factory():
            return FutuSource(
                archive, settings.futu_host, settings.futu_port, settings.source_timeout
            )

        def secondary_factory():
            return BaoStockSource(archive, settings.source_timeout)

        if args.command == "check-sources":
            results = [
                check_sources(spec, weights_source, primary_factory, secondary_factory)
                for spec in selected
            ]
            print(json_text(results))
            return (
                0
                if all(
                    result["futu"]["status"] == "ok"
                    and result["baostock"]["status"] == "ok"
                    and result.get("membership_matches")
                    for result in results
                )
                else 2
            )
        engine = build_engine(settings)
        repository = Repository(engine)
        return_collector = TotalReturnCollector(
            repository, TotalReturnSources(settings, archive), settings.data_dir / "exports"
        )
        pipeline = Pipeline(
            repository,
            weights_source,
            primary_factory,
            secondary_factory,
            settings.data_dir / "exports",
        )
        results, exit_code = [], 0
        rest_client = FutuRestClient(timeout=settings.source_timeout)
        for spec in selected:
            if not spec.collection_enabled and args.command in {
                "backfill-index",
                "update-index",
                "backfill-history",
                "backfill-returns",
            }:
                results.append(
                    record_paused_collection(
                        repository, spec, settings.data_dir / "exports", args.end
                    )
                )
                exit_code = 2
                continue
            reference_sources = {
                "csindex": CSIHistory,
                "cnindex": CNIndexHistory,
                "nasdaq": NasdaqHistory,
                "yahoo": YahooHistory,
                "google": GoogleHistory,
                "yahooetf": YahooETFValuation,
                "fred": FredHistory,
                "danjuan": DanjuanHistory,
                "multpl": MultplValuation,
                "gurufocus": GuruFocusValuation,
                "futorest": lambda archive, timeout: FutuRestHistory(
                    archive, rest_client, timeout=timeout
                ),
            }
            reference_source = (
                reference_sources[spec.reference_history_source](archive, settings.source_timeout)
                if spec.reference_history_source in reference_sources
                else None
            )
            history_secondary = secondary_factory if spec.market == "CN" else None
            history_primary = primary_factory if spec.futu_supported else None
            calendar_source = USCalendar(archive) if spec.market == "US" else None
            additional_sources = tuple(
                reference_sources[name](archive, settings.source_timeout)
                for name in spec.additional_history_sources
            )
            if args.command == "backfill-returns":
                collection = return_collector.run(spec, start=args.start, end=args.end)
                if spec.total_return:
                    result = IndexSeries(repository, settings.data_dir / "exports").refresh(spec)
                    result["total_return_collection"] = collection
                    if collection["status"] in {"failed", "partial", "blocked"}:
                        result["status"] = "partial"
                    repository.finish_run(UUID(result["run_id"]), result["status"], result)
                    Path(result["report_path"]).write_text(json_text(result), encoding="utf-8")
                else:
                    result = collection
            elif args.command in {"backfill-index", "update-index", "build-index"}:
                collection = None
                return_collection = None
                if args.command != "build-index":
                    now = datetime.now(ZoneInfo(spec.timezone))
                    end = args.end or (
                        now.date()
                        if now.time() >= clock_time.fromisoformat(spec.close_time)
                        else now.date() - timedelta(days=1)
                    )
                    start = args.start or spec.history_start_date or spec.inception_date
                    if args.command == "update-index" and args.start is None:
                        existing = repository.index_series(spec.key, method_for(spec))
                        start = incremental_start(spec, existing)
                    collection = HistoricalBackfill(
                        repository,
                        history_primary,
                        history_secondary,
                        reference_source,
                        settings.data_dir / "exports",
                        supplementary_metrics=("PB", "PS"),
                        price_supplement_factory=(
                            lambda: EastmoneySource(archive, settings.source_timeout)
                        )
                        if spec.market == "CN" or spec.eastmoney_secid
                        else None,
                        calendar_source=calendar_source,
                        calendar_fallback_factory=primary_factory if spec.market == "CN" else None,
                        additional_sources=additional_sources,
                    ).run(spec, start, end, allow_today=True)
                    if spec.total_return:
                        return_collection = return_collector.run(
                            spec,
                            start=args.start,
                            end=end,
                            incremental=args.command == "update-index",
                        )
                result = IndexSeries(repository, settings.data_dir / "exports").refresh(spec)
                if return_collection:
                    result["total_return_collection"] = return_collection
                    if return_collection["status"] in {"partial", "failed", "blocked"}:
                        result["status"] = "partial"
                if collection:
                    result["collection"] = collection
                    if collection.get("status") == "failed" or any(
                        s.get("status") in {"failed", "partial"}
                        for s in collection.get("sources", {}).values()
                    ):
                        result["status"] = "partial"
                    repository.finish_run(UUID(result["run_id"]), result["status"], result)
                    Path(result["report_path"]).write_text(json_text(result), encoding="utf-8")
            elif args.command == "sync-constituents":
                result = pipeline.sync_constituents(spec)
                if result["membership_verification"]["status"] != "pass":
                    exit_code = 2
            elif args.command == "run-daily":
                result = pipeline.run_daily(spec)
            elif args.command == "calculate":
                result = pipeline.recalculate(spec, args.date, args.historical_fundamentals)
            elif args.command == "backfill-history":
                result = HistoricalBackfill(
                    repository,
                    history_primary,
                    history_secondary,
                    reference_source,
                    settings.data_dir / "exports",
                    calendar_source=calendar_source,
                    calendar_fallback_factory=primary_factory if spec.market == "CN" else None,
                    additional_sources=additional_sources,
                ).run(spec, args.start, args.end)
            elif args.command == "backfill-fundamentals":
                result = FundamentalBackfill(
                    repository,
                    primary_factory,
                    secondary_factory,
                    lambda: EastmoneySource(archive, settings.source_timeout),
                    settings.data_dir / "exports" / "fundamentals",
                ).run(spec, args.start, args.end, args.symbols, args.refresh)
            else:
                result = repository.latest_report(spec.key) or {
                    "index": spec.key,
                    "status": "no_runs",
                }
                if "report" in result:
                    result["report"].pop("checks", None)
            if result.get("status") in {
                "blocked",
                "failed",
                "waiting_for_close",
                "partial",
                "unsupported",
            }:
                exit_code = 2
            results.append(result)
        print(json_text(results))
        return exit_code
    except KeyError as error:
        print(f"未知指数或数据字段：{error}", file=sys.stderr)
        return 1
    except (DataError, SourceError, OSError, SQLAlchemyError) as error:
        print(f"任务失败：{error}", file=sys.stderr)
        return 1
    finally:
        if rest_client is not None:
            rest_client.close()
        if engine is not None:
            engine.dispose()
