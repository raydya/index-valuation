"""Backfill upstream index series and expose gaps before constituent recalculation."""

import csv
import logging
from bisect import bisect_right
from collections import Counter
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

from index_valuation.archive import json_text
from index_valuation.domain import DataError, IndexSpec, SourceError
from index_valuation.quality import compare_value

log = logging.getLogger(__name__)


def date_gaps(expected: set[date], available: set[date]) -> list[dict]:
    """Consecutive missing trading sessions, without counting weekends as gaps."""
    groups = []
    current = None
    for day in sorted(expected):
        if day in available:
            current = None
        elif current is None:
            current = {"start": day, "end": day, "sessions": 1}
            groups.append(current)
        else:
            current["end"] = day
            current["sessions"] += 1
    return groups


def coverage(expected, records) -> dict:
    days = {row.trade_date for row in records}
    return {
        "rows": len(records),
        "first_date": min(days) if days else None,
        "last_date": max(days) if days else None,
        "missing_sessions": len(expected - days),
        "gaps": date_gaps(expected, days),
    }


def admit_rows(records, spec, start, end, sessions) -> tuple[list, list]:
    """Quarantine off-calendar data without silently relabeling vendor dates."""
    admitted, quarantined, seen = [], [], set()
    for row in records:
        if row.symbol != spec.quote_code or not start <= row.trade_date <= end:
            raise DataError("Historical source returned a wrong symbol or date range")
        if row.trade_date in seen:
            raise DataError("Historical source returned duplicate dates")
        seen.add(row.trade_date)
        if row.trade_date in sessions:
            admitted.append(row)
        else:
            quarantined.append(
                {
                    "source": row.source,
                    "date": row.trade_date,
                    "metric": getattr(row, "metric", "price"),
                    "reason": "not_in_reference_trading_calendar",
                    "artifact_path": row.artifact_path,
                }
            )
    return admitted, quarantined


class HistoricalBackfill:
    def __init__(
        self,
        repository,
        primary_factory,
        secondary_factory,
        official_source,
        output_dir: Path,
        *,
        supplementary_metrics: tuple[str, ...] = (),
        price_supplement_factory=None,
    ):
        self.repository = repository
        self.primary_factory = primary_factory
        self.secondary_factory = secondary_factory
        self.official_source = official_source
        self.output_dir = output_dir
        self.supplementary_metrics = supplementary_metrics
        self.price_supplement_factory = price_supplement_factory

    def run(
        self,
        spec: IndexSpec,
        start: date | None = None,
        end: date | None = None,
        *,
        allow_today: bool = False,
    ) -> dict:
        now = datetime.now(ZoneInfo(spec.timezone))
        today = now.date()
        start, end = start or spec.inception_date, end or today - timedelta(days=1)
        if start is None:
            raise DataError("Set inception_date in indices.toml or pass --start")
        if (
            start > end
            or end > today
            or (
                end == today
                and (not allow_today or now.time() < clock_time.fromisoformat(spec.close_time))
            )
        ):
            raise DataError("Historical range must be ordered and end before today")
        self.repository.ensure_index(spec)
        with self.repository.lock(spec.key):
            run_id = self.repository.start_run(spec, end)
            report = {
                "mode": "historical_trial",
                "index": spec.key,
                "name": spec.name,
                "start": start,
                "end": end,
                "primary_source": "futu",
                "run_id": str(run_id),
                "sources": {},
                "quarantined_rows": [],
            }
            try:
                self._collect(spec, start, end, run_id, report)
            except Exception as error:
                log.exception("Historical run failed for %s", spec.key)
                report.update(status="failed", error=str(error), error_type=type(error).__name__)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"{spec.key}-history-{start}-{end}-{run_id}.json"
            report["report_path"] = str(path)
            path.write_text(json_text(report), encoding="utf-8")
            self.repository.finish_run(run_id, report["status"], report)
            # Full gaps/quarantine remain in the JSON; keep the CLI usable for 20-year runs.
            summary = {key: value for key, value in report.items() if key != "quarantined_rows"}
            summary["quarantined_count"] = len(report["quarantined_rows"])
            summary["sources"] = {
                key: {k: v for k, v in value.items() if k != "gaps"}
                for key, value in report["sources"].items()
            }
            return summary

    def _calendar_and_secondary(self, spec, start, end, report):
        if self.secondary_factory is None:
            with self.primary_factory() as primary:
                sessions, path = primary.trading_days(start, end, market=spec.market)
            report["trading_calendar"] = {
                "source": "futu",
                "market": spec.market,
                "sessions": len(sessions),
                "artifact_path": path,
                "cached": False,
            }
            return sessions, []
        sessions, secondary_prices = None, []
        secondary = self.secondary_factory()
        try:
            with secondary:
                sessions, calendar_path = secondary.trading_days(start, end)
                report["trading_calendar"] = {
                    "source": "baostock",
                    "sessions": len(sessions),
                    "artifact_path": calendar_path,
                    "cached": False,
                }
                if sessions:
                    secondary_prices = self._fetch(
                        report, "baostock_price", lambda: secondary.index_history(spec, start, end)
                    )
        except Exception as error:
            report["sources"].setdefault(
                "baostock_price", {"status": "failed", "error": str(error)}
            )
            if sessions is None:
                calendar_source, cached = "baostock", True
                try:
                    sessions, calendar_path = secondary.cached_trading_days(start, end)
                except SourceError:
                    # New daily dates may exceed the historical cache during an outage.
                    with self.primary_factory() as primary:
                        sessions, calendar_path = primary.trading_days(start, end)
                    calendar_source, cached = "futu", False
                report["trading_calendar"] = {
                    "source": calendar_source,
                    "sessions": len(sessions),
                    "artifact_path": calendar_path,
                    "cached": cached,
                    "fallback_error": str(error),
                }
                log.warning(
                    "Using %s calendar (cached=%s) after BaoStock failure: %s",
                    calendar_source,
                    cached,
                    error,
                )
        return sessions, secondary_prices

    def _collect(self, spec, start, end, run_id, report):
        sessions, secondary_prices = self._calendar_and_secondary(spec, start, end, report)
        if not sessions:
            report.update(status="skipped", reason="no_trading_sessions")
            return

        primary_prices, primary_pe, extra_series = [], [], {}
        try:
            with self.primary_factory() as primary:
                primary_prices = self._fetch(
                    report, "futu_price", lambda: primary.index_history(spec, start, end)
                )
                primary_pe = self._fetch(
                    report, "futu_pe", lambda: primary.valuation_history(spec, start, end)
                )
                for metric in self.supplementary_metrics:
                    key = f"futu_{metric.lower()}"
                    extra_series[key] = self._fetch(
                        report,
                        key,
                        lambda metric=metric: primary.valuation_history(spec, start, end, metric),
                    )
        except Exception as error:
            for key in ("futu_price", "futu_pe"):
                report["sources"].setdefault(key, {"status": "failed", "error": str(error)})

        official_prices, official_pe = [], []
        official_name = getattr(self.official_source, "source_name", "csindex")
        datasets = {
            "futu_price": primary_prices,
            "futu_pe": primary_pe,
            **extra_series,
        }
        if self.secondary_factory is not None:
            datasets["baostock_price"] = secondary_prices
        if self.official_source is not None:
            try:
                batch = self.official_source.index_history(spec, start, end)
                official_prices, official_pe = batch.observations, batch.valuations
                if batch.failures:
                    for key in (f"{official_name}_price", f"{official_name}_pe"):
                        report["sources"][key] = {
                            "status": "partial",
                            "failed_ranges": batch.failures,
                        }
            except Exception as error:
                for key in (f"{official_name}_price", f"{official_name}_pe"):
                    report["sources"][key] = {"status": "failed", "error": str(error)}
            datasets[f"{official_name}_price"] = official_prices
            datasets[f"{official_name}_pe"] = official_pe
        if self.price_supplement_factory:
            try:
                with self.price_supplement_factory() as supplement:
                    datasets["eastmoney_price"] = self._fetch(
                        report,
                        "eastmoney_price",
                        lambda: supplement.index_history(spec, start, end),
                    )
            except Exception as error:
                report["sources"]["eastmoney_price"] = {"status": "failed", "error": str(error)}
        for key, records in datasets.items():
            if any(row.source != key.split("_")[0] for row in records):
                raise DataError("Historical adapter returned the wrong source identity")
            admitted, quarantine = admit_rows(records, spec, start, end, sessions)
            report["quarantined_rows"].extend(quarantine)
            datasets[key] = admitted
            report["sources"].setdefault(key, {"status": "ok"})
            report["sources"][key].update(coverage(sessions, admitted))
            if key.endswith("_price"):
                self.repository.save_observations(run_id, admitted)
            else:
                self.repository.save_source_valuations(run_id, admitted)

        records = {key: {row.trade_date: row for row in rows} for key, rows in datasets.items()}
        checks, daily_rows = [], []
        weight_dates = self.repository.weight_dates(spec.key)
        weight_gaps, stale_weights = 0, 0
        for day in sorted(sessions):
            values = {}
            for key, lookup in records.items():
                row = lookup.get(day)
                values[key] = (row.price if key.endswith("_price") else row.value) if row else None
            comparisons = {}
            candidate_pairs = [
                ("futu_baostock_price", "futu_price", "baostock_price"),
                (f"futu_{official_name}_price", "futu_price", f"{official_name}_price"),
                (f"{official_name}_baostock_price", f"{official_name}_price", "baostock_price"),
                (f"{official_name}_eastmoney_price", f"{official_name}_price", "eastmoney_price"),
                ("baostock_eastmoney_price", "baostock_price", "eastmoney_price"),
                ("futu_eastmoney_price", "futu_price", "eastmoney_price"),
            ]
            pairs = [
                (name, left, right)
                for name, left, right in candidate_pairs
                if left in datasets and right in datasets
            ]
            pairs.append(("pe_numeric_difference", "futu_pe", f"{official_name}_pe"))
            for name, left, right in pairs:
                check = compare_value(spec.quote_code, name, values.get(left), values.get(right))
                checks.append(check)
                comparisons[name + "_status"] = check.status
                comparisons[name + "_relative_error"] = check.relative_error
            weight_index = bisect_right(weight_dates, day) - 1
            weight_date = weight_dates[weight_index] if weight_index >= 0 else None
            if weight_date is None:
                weight_gaps += 1
            elif (day - weight_date).days > spec.max_weight_age_days:
                stale_weights += 1
            daily_rows.append(
                {
                    "date": day,
                    **values,
                    **comparisons,
                    "pe_basis_status": "unconfirmed",
                    "stored_weight_date": weight_date,
                    "component_recalculation_status": "not_run_reference_series_only",
                }
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = self.output_dir / f"{spec.key}-history-{start}-{end}-{run_id}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=list(daily_rows[0]))
            writer.writeheader()
            writer.writerows(daily_rows)
        report.update(
            {
                "status": "partial",
                "csv": str(csv_path),
                "price_checks": {
                    name: dict(Counter(c.status for c in checks if c.metric == name))
                    for name, _, _ in pairs
                    if name != "pe_numeric_difference"
                },
                "pe_numeric_comparison": dict(
                    Counter(c.status for c in checks if c.metric == "pe_numeric_difference")
                ),
                "pe_basis_status": "unconfirmed",
                "pe_reference_source": official_name if self.official_source is not None else None,
                "pe_comparison_note": (
                    "Numeric agreement does not verify identical earnings/weight/loss treatment. "
                    "Futu vendor PE and any independent PE retain separate basis labels."
                ),
                "component_recalculation": {
                    "status": "blocked",
                    "calculated_days": 0,
                    "stored_weight_dates": weight_dates,
                    "sessions_without_prior_weights": weight_gaps,
                    "sessions_with_stale_weights": stale_weights,
                    "issues": [
                        "component_recalculation_not_attempted_by_reference_backfill",
                        "use_backfill_fundamentals_and_calculate_for_company_inputs",
                        "historical_membership_and_weight_verification_required",
                    ],
                },
                "bands": None,
                "first_session": daily_rows[0],
                "last_session": daily_rows[-1],
            }
        )

    @staticmethod
    def _fetch(report, key, fetch):
        try:
            log.info("Historical collection: %s", key)
            rows = fetch()
            report["sources"][key] = {"status": "ok"}
            return rows
        except Exception as error:
            log.warning("Historical source %s failed: %s", key, error)
            report["sources"][key] = {"status": "failed", "error": str(error)}
            return []
