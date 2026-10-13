"""One reusable workflow for every configured index."""

import csv
import logging
from collections import Counter
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

from index_valuation.archive import json_text
from index_valuation.domain import Check, DataError, IndexSpec, SourceError
from index_valuation.fundamentals import method_for, price_observation_source
from index_valuation.quality import compare_observations, compare_value
from index_valuation.valuation import METHOD, calculate

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(
        self, repository, weights_source, primary_factory, secondary_factory, output_dir: Path
    ):
        self.repository = repository
        self.weights_source = weights_source
        self.primary_factory = primary_factory
        self.secondary_factory = secondary_factory
        self.output_dir = output_dir

    def sync_constituents(self, spec: IndexSpec) -> dict:
        self.repository.ensure_index(spec)
        with self.repository.lock(spec.key):
            return self._sync_constituents(spec)

    def _sync_constituents(self, spec: IndexSpec, primary=None) -> dict:
        snapshot = self.weights_source.fetch(spec)
        if snapshot.as_of > datetime.now(ZoneInfo(spec.timezone)).date():
            raise DataError("Official weight snapshot is dated in the future")
        verification = {
            "status": "missing",
            "checked_at": datetime.now(UTC).isoformat(),
            "primary_source": "csindex",
            "secondary_source": "futu",
            "weight_verification": "official_single_source",
        }
        try:
            if primary is None:
                with self.primary_factory() as source:
                    members, artifact_path = source.members(spec)
            else:
                members, artifact_path = primary.members(spec)
            official = {item.symbol for item in snapshot.constituents}
            verification.update(
                {
                    "status": "pass" if members == official else "error",
                    "only_in_official": sorted(official - members),
                    "only_in_futu": sorted(members - official),
                    "artifact_path": artifact_path,
                }
            )
        except SourceError as error:
            verification["error"] = str(error)
        snapshot_id = self.repository.save_snapshot(spec, snapshot, verification)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        export = self.output_dir / f"{spec.key}-{snapshot.as_of}-constituents.csv"
        weights = snapshot.weights()
        with export.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(
                ["index", "weight_date", "symbol", "name", "weight_percent", "normalized_weight"]
            )
            for member in sorted(
                snapshot.constituents, key=lambda item: item.weight_percent, reverse=True
            ):
                writer.writerow(
                    [
                        spec.key,
                        snapshot.as_of,
                        member.symbol,
                        member.name,
                        member.weight_percent,
                        weights[member.symbol],
                    ]
                )
        return {
            "index": spec.key,
            "snapshot_id": snapshot_id,
            "weight_date": snapshot.as_of,
            "constituents": len(snapshot.constituents),
            "original_total_percent": snapshot.total_percent,
            "membership_verification": verification,
            "source_url": snapshot.source_url,
            "source_sha256": snapshot.sha256,
            "csv": str(export),
        }

    def _calculate(
        self,
        spec,
        day,
        run_id,
        primary,
        secondary,
        extra_issues=(),
        *,
        method=METHOD,
        primary_source="futu",
        extra_checks=(),
    ) -> dict:
        snapshot_id, snapshot, verification = self.repository.snapshot(spec.key, day)
        symbols = [member.symbol for member in snapshot.constituents]
        checks = compare_observations(symbols, primary, secondary)
        checks.extend(extra_checks)
        primary_index, secondary_index = (
            primary.get(spec.quote_code),
            secondary.get(spec.quote_code),
        )
        index_price = primary_index.price if primary_index else None
        checks.append(
            compare_value(
                spec.quote_code,
                "price",
                index_price,
                secondary_index.price if secondary_index else None,
            )
        )
        if primary_index and (
            primary_index.trade_date != day or primary_index.currency != spec.currency
        ):
            checks.append(Check(spec.quote_code, "alignment", "error"))
        if secondary_index and (
            secondary_index.trade_date != day or secondary_index.currency != spec.currency
        ):
            checks.append(Check(spec.quote_code, "alignment", "error"))
        if verification.get("status") != "pass":
            checks.append(Check(spec.key, "membership", "error", detail="成分名单未通过独立核验"))
        result = calculate(
            spec,
            day,
            snapshot,
            primary,
            index_price,
            checks,
            self.repository.history(spec, day, method),
        )
        self.repository.save_calculation(
            spec, day, snapshot_id, run_id, index_price, result, checks, method
        )
        return {
            "index": spec.key,
            "name": spec.name,
            "trade_date": day,
            "method": method,
            "primary_source": primary_source,
            "secondary_source": "baostock",
            "eps_method": "price_div_pe_ttm",
            "weight_date": snapshot.as_of,
            "weight_age_days": (day - snapshot.as_of).days,
            "weight_source": snapshot.source_url,
            "primary_stock_count": sum(symbol in primary for symbol in symbols),
            "secondary_stock_count": sum(symbol in secondary for symbol in symbols),
            "expected_stock_count": spec.expected_constituents,
            "index_price": index_price,
            **asdict(result),
            "check_summary": dict(Counter(item.status for item in checks)),
            "collection_issues": list(extra_issues),
            "checks": checks,
        }

    def _finish(self, run_id, spec, day, report):
        report["run_id"] = str(run_id)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{spec.key}-{day}-{run_id}.json"
        report["report_path"] = str(path)
        path.write_text(json_text(report), encoding="utf-8")
        self.repository.finish_run(run_id, report["status"], report)
        return {key: value for key, value in report.items() if key != "checks"}

    def run_daily(self, spec: IndexSpec, day: date | None = None) -> dict:
        now = datetime.now(ZoneInfo(spec.timezone))
        day = day or now.date()
        if day != now.date():
            raise DataError(
                "run-daily only collects today; use calculate for archived historical data"
            )
        self.repository.ensure_index(spec)
        with self.repository.lock(spec.key):
            run_id = self.repository.start_run(spec, day)
            try:
                with self.primary_factory() as primary_source:
                    weights = self._sync_constituents(spec, primary_source)
                    if not primary_source.is_trading_day(spec, day):
                        return self._finish(
                            run_id,
                            spec,
                            day,
                            {
                                "index": spec.key,
                                "trade_date": day,
                                "status": "skipped",
                                "reason": "non_trading_day",
                                "weights": weights,
                            },
                        )
                    if now.time() < clock_time.fromisoformat(spec.close_time):
                        return self._finish(
                            run_id,
                            spec,
                            day,
                            {
                                "index": spec.key,
                                "trade_date": day,
                                "status": "waiting_for_close",
                                "reason": "intraday_prices_are_not_daily_closes",
                                "weights": weights,
                            },
                        )
                    _, snapshot, _ = self.repository.snapshot(spec.key, day)
                    symbols = [member.symbol for member in snapshot.constituents] + [
                        spec.quote_code
                    ]
                    primary_rows, primary_errors = primary_source.daily(spec, symbols, day)
                self.repository.save_observations(run_id, primary_rows)
                with self.secondary_factory() as secondary_source:
                    secondary_rows, secondary_errors = secondary_source.daily(spec, symbols, day)
                self.repository.save_observations(run_id, secondary_rows)
                report = self._calculate(
                    spec,
                    day,
                    run_id,
                    {row.symbol: row for row in primary_rows},
                    {row.symbol: row for row in secondary_rows},
                    primary_errors + secondary_errors,
                )
            except Exception as error:
                log.exception("Daily run failed for %s", spec.key)
                report = {
                    "index": spec.key,
                    "trade_date": day,
                    "status": "failed",
                    "error": str(error),
                    "error_type": type(error).__name__,
                }
            return self._finish(run_id, spec, day, report)

    def _historical_inputs(self, spec, day, symbols, secondary):
        source = price_observation_source(spec.historical_price_source)
        method = method_for(spec.historical_price_source)
        fundamentals = self.repository.fundamentals(symbols, day, method)
        prices = self.repository.observations(source, day, symbols)
        primary, checks = {}, []
        for symbol in symbols:
            row, price = fundamentals.get(symbol), prices.get(symbol)
            if row is None or price is None:
                checks.append(Check(symbol, "historical_fundamental", "missing"))
                continue
            if (
                row.status not in {"verified", "review"}
                or row.price_source != source
                or row.pe_source != "futu"
                or row.price != price.price
                or row.price_artifact != price.artifact_path
            ):
                checks.append(Check(symbol, "historical_fundamental", "error", detail=row.status))
            ref = secondary.get(symbol)
            checks.append(compare_value(symbol, "pe_ttm", row.pe_ttm, ref.pe_ttm if ref else None))
            primary[symbol] = replace(
                price,
                source=f"futu_pe+{source}_price",
                eps_ttm=row.eps_ttm,
                pe_ttm=row.pe_ttm,
                eps_method="price_div_pe_ttm",
                raw={"price_artifact": row.price_artifact, "pe_artifact": row.pe_artifact},
            )
        # Keep historical derived inputs separate from contemporaneous snapshots in bands.
        return primary, checks, f"{METHOD}:{method}"

    def recalculate(self, spec: IndexSpec, day: date, historical_fundamentals=False) -> dict:
        if day > datetime.now(ZoneInfo(spec.timezone)).date():
            raise DataError("Cannot calculate a future date")
        self.repository.ensure_index(spec)
        with self.repository.lock(spec.key):
            run_id = self.repository.start_run(spec, day)
            try:
                _, snapshot, _ = self.repository.snapshot(spec.key, day)
                symbols = [item.symbol for item in snapshot.constituents] + [spec.quote_code]
                primary = self.repository.observations("futu", day, symbols)
                secondary = self.repository.observations("baostock", day, symbols)
                options = {}
                if historical_fundamentals:
                    stocks, checks, method = self._historical_inputs(
                        spec, day, symbols[:-1], secondary
                    )
                    primary = {s: row for s, row in primary.items() if s == spec.quote_code}
                    primary.update(stocks)
                    options = {
                        "method": method,
                        "primary_source": f"futu_pe+{spec.historical_price_source}_price",
                        "extra_checks": checks,
                        "extra_issues": ["historical_vendor_data_may_be_revised"],
                    }
                report = self._calculate(spec, day, run_id, primary, secondary, **options)
            except Exception as error:
                report = {
                    "index": spec.key,
                    "trade_date": day,
                    "status": "failed",
                    "error": str(error),
                }
            return self._finish(run_id, spec, day, report)
