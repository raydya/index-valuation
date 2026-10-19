"""Collect and compare dividend-reinvestment levels independently of valuation."""

import logging
from datetime import datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from index_valuation.archive import json_text
from index_valuation.domain import DataError
from index_valuation.quality import compare_value

log = logging.getLogger(__name__)


def select_return_points(spec, rows):
    tr = spec.total_return
    if tr is None or not spec.collection_enabled:
        return []
    primary, reference = {}, {}
    for row in rows:
        if row["index_code"] != tr.code or row["basis"] != tr.basis:
            continue
        pair = (row["source"], row["symbol"])
        target = (
            primary
            if pair == (tr.source, tr.symbol)
            else reference
            if pair == (tr.verification_source, tr.verification_symbol)
            else None
        )
        if target is not None:
            if row["trade_date"] in target:
                raise DataError("Duplicate total-return date in one source")
            target[row["trade_date"]] = row
    points = []
    for day, row in sorted(primary.items()):
        other = reference.get(day)
        check = compare_value(
            tr.code, "total_return", row["price"], other["price"] if other else None
        )
        status = "unverified" if check.status == "missing" else check.status
        points.append(
            {
                "trade_date": day,
                "price": row["price"],
                "price_source": tr.source,
                "price_status": status,
                "price_usable": status != "error",
                "phase": "backcast"
                if spec.inception_date and day < spec.inception_date
                else "published",
                "index_code": tr.code,
                "basis": tr.basis,
                "artifact_path": row["artifact_path"],
                "reference_price": other["price"] if other else None,
                "reference_source": tr.verification_source if other else None,
                "relative_error": check.relative_error,
            }
        )
    return points


def source_start(tr, source, symbol, rows, requested_start=None, *, incremental=False):
    if requested_start is not None:
        return max(tr.history_start_date, requested_start)
    matching = [
        row["trade_date"]
        for row in rows
        if (row["source"], row["symbol"], row["index_code"], row["basis"])
        == (source, symbol, tr.code, tr.basis)
    ]
    if incremental and matching:
        return max(tr.history_start_date, max(matching) - timedelta(days=14))
    return tr.history_start_date


class TotalReturnCollector:
    def __init__(self, repository, sources, output_dir):
        self.repository, self.sources, self.output_dir = repository, sources, output_dir

    def run(self, spec, *, start=None, end=None, incremental=False):
        now = datetime.now(ZoneInfo(spec.timezone))
        end = end or (
            now.date()
            if now.time() >= clock_time.fromisoformat(spec.close_time)
            else now.date() - timedelta(days=1)
        )
        if end > now.date() or (
            end == now.date() and now.time() < clock_time.fromisoformat(spec.close_time)
        ):
            raise DataError("Total-return collection requires a completed daily close")
        self.repository.ensure_index(spec)
        with self.repository.lock(spec.key):
            run_id = self.repository.start_run(spec, end)
            report = {
                "mode": "total_return",
                "index": spec.key,
                "run_id": str(run_id),
                "end": end,
                "sources": {},
                "quarantined_rows": [],
            }
            tr = spec.total_return
            if not spec.collection_enabled:
                report.update(status="blocked", reason=spec.collection_disabled_reason)
            elif tr is None:
                report.update(status="unsupported", reason=spec.total_return_unavailable_reason)
            else:
                report.update(index_code=tr.code, basis=tr.basis, identity_url=tr.identity_url)
                try:
                    self._collect(spec, start, end, incremental, run_id, report)
                except Exception as error:
                    log.exception("Total-return collection failed for %s", spec.key)
                    report.update(status="failed", error=str(error))
            self.output_dir.mkdir(parents=True, exist_ok=True)
            path = self.output_dir / f"{spec.key}-total-return-{run_id}.json"
            report["report_path"] = str(path)
            path.write_text(json_text(report), encoding="utf-8")
            self.repository.finish_run(run_id, report["status"], report)
            return report

    def _collect(self, spec, start, end, incremental, run_id, report):
        tr = spec.total_return
        existing = self.repository.total_return_observations(spec)
        pairs = [(tr.source, tr.symbol)]
        if tr.verification_source:
            pairs.append((tr.verification_source, tr.verification_symbol))
        starts = {
            source: source_start(tr, source, symbol, existing, start, incremental=incremental)
            for source, symbol in pairs
        }
        earliest = min(starts.values())
        if earliest > end:
            report.update(status="skipped", reason="no_completed_dates_in_requested_range")
            return
        sessions, calendar = self.sources.trading_days(spec, earliest, end)
        report["trading_calendar"] = calendar
        for source, symbol in pairs:
            source_report = {"symbol": symbol, "start": starts[source], "end": end}
            report["sources"][source] = source_report
            if starts[source] > end:
                source_report.update(status="skipped", rows=0)
                continue
            try:
                batch = self.sources.history(spec, source, symbol, starts[source], end)
                admitted, seen = [], set()
                for row in batch.observations:
                    if row.source != source or row.currency != spec.currency:
                        raise DataError("Total-return source/currency mismatch")
                    if row.trade_date in seen:
                        raise DataError("Duplicate total-return date")
                    seen.add(row.trade_date)
                    if (
                        not starts[source] <= row.trade_date <= end
                        or row.trade_date not in sessions
                    ):
                        report["quarantined_rows"].append(
                            {
                                "source": source,
                                "date": row.trade_date,
                                "artifact_path": row.artifact_path,
                                "reason": "outside_range_or_market_calendar",
                            }
                        )
                        continue
                    admitted.append(row)
                self.repository.save_total_return_observations(
                    spec, run_id, source, symbol, admitted
                )
                admitted.sort(key=lambda row: row.trade_date)
                expected = {day for day in sessions if starts[source] <= day <= end}
                missing = len(expected - {row.trade_date for row in admitted})
                source_report.update(
                    status="partial"
                    if batch.failures
                    or not admitted
                    or missing
                    or len(admitted) != len(batch.observations)
                    else "ok",
                    rows=len(admitted),
                    first_date=admitted[0].trade_date if admitted else None,
                    last_date=admitted[-1].trade_date if admitted else None,
                    missing_sessions=missing,
                    failures=batch.failures,
                )
            except Exception as error:
                source_report.update(status="failed", rows=0, error=str(error))
                log.warning("Total-return source %s %s failed: %s", spec.key, source, error)
        points = self.repository.total_return_points(spec)
        primary_dates = {p["trade_date"] for p in points}
        report.update(
            status="partial"
            if any(s["status"] in {"partial", "failed"} for s in report["sources"].values())
            else "collected",
            stored_rows=len(points),
            stored_first_date=points[0]["trade_date"] if points else None,
            stored_last_date=points[-1]["trade_date"] if points else None,
            missing_primary_sessions=len(
                {d for d in sessions if starts[tr.source] <= d <= end} - primary_dates
            ),
        )
