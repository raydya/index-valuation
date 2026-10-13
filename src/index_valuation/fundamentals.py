"""Reproducible daily EPS TTM / PE backfill from company-level source data."""

import csv
import logging
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from index_valuation.archive import json_text
from index_valuation.domain import DataError, Fundamental
from index_valuation.history import date_gaps
from index_valuation.quality import compare_value

log = logging.getLogger(__name__)


def method_for(price_source: str) -> str:
    return f"futu_company_pe_ttm_{price_source}_v1"


def price_observation_source(price_provider: str) -> str:
    # Price-only K-lines must not overwrite Futu snapshots containing EPS.
    return "futu_history" if price_provider == "futu" else price_provider


def validated_map(rows, symbol, source, start, end, sessions):
    result, excluded = {}, []
    for row in rows:
        if row.symbol != symbol or row.source != source or not start <= row.trade_date <= end:
            raise DataError("Historical fundamentals have wrong source, security or date range")
        if row.trade_date in result:
            raise DataError("Historical fundamentals have duplicate dates")
        if row.trade_date not in sessions:
            excluded.append(row.trade_date)
        else:
            result[row.trade_date] = row
    return result, excluded


def combine(symbol, method, days, prices, pe, reference) -> list[Fundamental]:
    """Derive EPS only from same-day, unadjusted prices and documented TTM PE."""
    result = []
    for day in sorted(days):
        price_row, pe_row, ref = prices.get(day), pe.get(day), reference.get(day)
        if price_row and (
            price_row.trade_date != day
            or price_row.symbol != symbol
            or price_row.price_basis != "unadjusted_close"
            or price_row.currency != "CNY"
            or not price_row.price.is_finite()
            or price_row.price <= 0
        ):
            raise DataError("Historical EPS needs an aligned unadjusted CNY price")
        if pe_row and (
            pe_row.trade_date != day
            or pe_row.symbol != symbol
            or pe_row.source != "futu"
            or pe_row.metric != "pe_ttm"
            or pe_row.basis != "futu_company_pe_ttm_v1"
            or not pe_row.value.is_finite()
            or pe_row.value == 0
        ):
            raise DataError("Historical EPS needs a documented company TTM PE")
        if ref and (
            ref.source != "baostock"
            or ref.trade_date != day
            or ref.symbol != symbol
            or ref.currency != "CNY"
            or ref.price_basis != "unadjusted_close"
            or price_row
            and ref.source == price_row.source
        ):
            raise DataError("Historical fundamental checks require independent aligned inputs")
        price = price_row.price if price_row else None
        pe_value = pe_row.value if pe_row else None
        eps = price / pe_value if price is not None and pe_value is not None else None
        price_check = compare_value(symbol, "price", price, ref.price if ref else None)
        pe_check = compare_value(symbol, "pe_ttm", pe_value, ref.pe_ttm if ref else None)
        eps_check = compare_value(symbol, "eps_ttm", eps, ref.eps_ttm if ref else None)
        statuses = {check.status for check in (price_check, pe_check, eps_check)}
        if price is None:
            status = "missing_price"
        elif pe_value is None:
            status = "missing_pe"
        elif "error" in statuses:
            status = "blocked"
        elif "missing" in statuses:
            status = "unverified"
        elif "warning" in statuses:
            status = "review"
        else:
            status = "verified"
        result.append(
            Fundamental(
                symbol=symbol,
                trade_date=day,
                method=method,
                price=price,
                pe_ttm=pe_value,
                eps_ttm=eps,
                price_source=price_row.source if price_row else None,
                pe_source="futu",
                status=status,
                price_relative_error=price_check.relative_error,
                pe_relative_error=pe_check.relative_error,
                eps_relative_error=eps_check.relative_error,
                price_artifact=price_row.artifact_path if price_row else None,
                pe_artifact=pe_row.artifact_path if pe_row else None,
                reference_artifact=ref.artifact_path if ref else None,
            )
        )
    return result


class FundamentalBackfill:
    def __init__(
        self, repository, primary_factory, reference_factory, price_factory, output_dir: Path
    ):
        self.repository, self.primary_factory = repository, primary_factory
        self.reference_factory, self.price_factory = reference_factory, price_factory
        self.output_dir = output_dir

    def run(self, spec, start=None, end=None, symbols=None, refresh=False) -> dict:
        today = datetime.now(ZoneInfo(spec.timezone)).date()
        start, end = start or spec.inception_date, end or today - timedelta(days=1)
        if start is None or start > end or end >= today:
            raise DataError("Supply a valid historical range ending before today")
        self.repository.ensure_index(spec)
        universe = self.repository.constituent_universe(spec.key)
        if symbols:
            if set(symbols) - set(universe):
                raise DataError("Requested symbols must belong to the stored constituent universe")
            universe = {symbol: universe[symbol] for symbol in sorted(set(symbols))}
        if not universe:
            raise DataError("No stored constituents; run sync-constituents first")
        method = method_for(spec.historical_price_source)
        with self.repository.lock(spec.key):
            run_id = self.repository.start_run(spec, end)
            report = {
                "mode": "fundamental_backfill",
                "index": spec.key,
                "start": start,
                "end": end,
                "run_id": str(run_id),
                "method": method,
                "pe_source": "futu",
                "price_source": price_observation_source(spec.historical_price_source),
                "reference_source": "baostock",
                "eps_method": "price_div_pe_ttm",
                "eps_kind": "vendor_implied_not_reported",
                "universe_basis": "securities_in_stored_weight_snapshots_not_historical_membership",
                "universe_size": len(universe),
                "weight_dates": self.repository.weight_dates(spec.key),
                "symbols": [],
                "errors": [],
                "resumed_symbols": 0,
            }
            self.output_dir.mkdir(parents=True, exist_ok=True)
            stem = self.output_dir / f"{spec.key}-fundamentals-{start}-{end}-{run_id}"
            try:
                with ExitStack() as stack:
                    reference = stack.enter_context(self.reference_factory())
                    sessions, path = reference.trading_days(start, end)
                    profiles, profiles_path = reference.security_dates(list(universe))
                    report["calendar_artifact"], report["profiles_artifact"] = path, profiles_path
                    primary = stack.enter_context(self.primary_factory())
                    prices = (
                        primary
                        if spec.historical_price_source == "futu"
                        else stack.enter_context(self.price_factory())
                    )
                    for offset, (symbol, name) in enumerate(universe.items()):
                        previous = (
                            None
                            if refresh
                            else self.repository.fundamental_batch(
                                symbol,
                                start,
                                end,
                                method,
                            )
                        )
                        if previous:
                            report["symbols"].append(previous)
                            report["resumed_symbols"] += 1
                            continue
                        try:
                            item = self._symbol(
                                spec,
                                symbol,
                                name,
                                start,
                                end,
                                run_id,
                                method,
                                profiles.get(symbol),
                                sessions,
                                primary,
                                reference,
                                prices,
                            )
                            report["symbols"].append(item)
                        except Exception as error:
                            log.warning("Fundamentals %s failed: %s", symbol, error)
                            report["errors"].append({"symbol": symbol, "error": str(error)})
                        if (offset + 1) % 10 == 0 or offset + 1 == len(universe):
                            log.info(
                                "Fundamentals %s: %s/%s securities; %s source failures",
                                spec.key,
                                offset + 1,
                                len(universe),
                                len(report["errors"]),
                            )
                            self._progress(report, stem)
                counts = Counter()
                for item in report["symbols"]:
                    counts.update(item["statuses"])
                report["status_counts"] = dict(counts)
                report["eps_rows"] = sum(item["eps_rows"] for item in report["symbols"])
                report["pe_rows"] = sum(item["pe_rows"] for item in report["symbols"])
                report["status"] = (
                    "complete"
                    if not report["errors"] and not (set(counts) - {"verified"})
                    else "partial"
                )
            except Exception as error:
                log.exception("Fundamental backfill failed")
                report.update(status="failed", error=str(error))
            report["report_path"] = str(stem) + ".json"
            Path(report["report_path"]).write_text(json_text(report), encoding="utf-8")
            self.repository.finish_run(run_id, report["status"], report)
            return {key: value for key, value in report.items() if key != "symbols"}

    def _symbol(
        self,
        spec,
        symbol,
        name,
        start,
        end,
        run_id,
        method,
        profile,
        sessions,
        primary,
        reference,
        price_source,
    ):
        if not profile or not profile["listed"]:
            raise DataError("Listing date is missing; cannot claim full historical coverage")
        eligible = {
            day
            for day in sessions
            if day >= profile["listed"] and (not profile["delisted"] or day <= profile["delisted"])
        }
        pe = primary.company_pe_history(spec, symbol, start, end)
        prices = price_source.price_history(spec, symbol, start, end)
        ref = reference.company_history(spec, symbol, start, end)
        pe_map, bad_pe = validated_map(pe, symbol, "futu", start, end, eligible)
        price_map, bad_price = validated_map(
            prices,
            symbol,
            price_observation_source(spec.historical_price_source),
            start,
            end,
            eligible,
        )
        ref_map, bad_ref = validated_map(ref, symbol, "baostock", start, end, eligible)
        combined = combine(symbol, method, eligible, price_map, pe_map, ref_map)
        eps_days = {row.trade_date for row in combined if row.eps_ttm is not None}
        item = {
            "symbol": symbol,
            "name": name,
            "listed": profile["listed"],
            "expected_days": len(eligible),
            "price_rows": len(price_map),
            "pe_rows": len(pe_map),
            "eps_rows": len(eps_days),
            "reference_rows": len(ref_map),
            "statuses": dict(Counter(row.status for row in combined)),
            "eps_gaps": date_gaps(eligible, eps_days),
            "excluded_dates": {"pe": bad_pe, "price": bad_price, "reference": bad_ref},
        }
        # Each security has its own diagnostics file, so an interrupted multi-hour
        # backfill does not discard the completed securities or require all data in RAM.
        csv_path = self.output_dir / f"{symbol}-{start}-{end}-{method}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(
                [
                    "symbol",
                    "date",
                    "price",
                    "price_source",
                    "pe_ttm",
                    "eps_ttm",
                    "reference_pe_ttm",
                    "reference_eps_ttm",
                    "status",
                    "price_relative_error",
                    "pe_relative_error",
                    "eps_relative_error",
                ]
            )
            for row in combined:
                secondary = ref_map.get(row.trade_date)
                writer.writerow(
                    [
                        symbol,
                        row.trade_date,
                        row.price,
                        row.price_source,
                        row.pe_ttm,
                        row.eps_ttm,
                        secondary.pe_ttm if secondary else None,
                        secondary.eps_ttm if secondary else None,
                        row.status,
                        row.price_relative_error,
                        row.pe_relative_error,
                        row.eps_relative_error,
                    ]
                )
        # Commit the checkpoint only after its diagnostics file exists.
        self.repository.save_fundamental_batch(
            run_id,
            symbol,
            start,
            end,
            method,
            list(price_map.values()),
            list(ref_map.values()),
            list(pe_map.values()),
            combined,
            item,
        )
        return item

    def _progress(self, report, stem):
        Path(str(stem) + ".progress.json").write_text(json_text(report), encoding="utf-8")
        # The final run remains unfinished while symbol checkpoints are committed.
        log.info("Committed %s company checkpoints", len(report["symbols"]))
