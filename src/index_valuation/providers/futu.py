"""Read-only Futu OpenD adapter. Current fundamentals must never be backdated."""

import json
import logging
import signal
import socket
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from index_valuation.archive import Archive
from index_valuation.domain import (
    DataError,
    IndexSpec,
    Observation,
    SourceError,
    SourceValuation,
    decimal_or_none,
)

# These exchange-confirmed closures are missing from OpenD's historical HK calendar.
# Apply them to all HK indices and preserve both the vendor dates and corrections.
HKEX_FULL_DAY_CLOSURES = {
    date(
        2016, 10, 21
    ): "https://www.hkex.com.hk/News/Market-Communications/2016/1610212news?sc_lang=en",
    date(
        2017, 8, 23
    ): "https://www.hkex.com.hk/News/Market-Communications/2017/1708232news?sc_lang=en",
}


def parse_history_bar(row: dict, spec: IndexSpec, observed_at: datetime, path: str) -> Observation:
    """Only closes: K-line pe_ratio has no documented TTM guarantee."""
    if row["code"] != spec.quote_code:
        raise DataError("Futu history returned an unexpected security")
    day = datetime.fromisoformat(row["time_key"]).date()
    quote_at = datetime.combine(
        day, clock_time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
    )
    price = decimal_or_none(row.get("close"))
    if price is None or price <= 0 or quote_at > observed_at:
        raise DataError("Futu history has an invalid or unfinished daily close")
    return Observation(
        symbol=spec.quote_code,
        trade_date=day,
        source="futu",
        price=price,
        eps_ttm=None,
        pe_ttm=None,
        observed_at=observed_at,
        quote_at=quote_at,
        currency=spec.currency,
        eps_method="not_applicable",
        artifact_path=path,
        raw=row,
    )


def parse_valuation_history(
    data, spec, start, end, observed_at, path, metric="PE"
) -> list[SourceValuation]:
    if metric not in {"PE", "PB", "PS"} or data.get("valuation_type") != metric:
        raise DataError("Futu returned a different valuation metric")
    records, seen = [], set()
    for row in data.get("trend", {}).get("historical_items", []):
        day = date.fromisoformat(row["time_str"])
        if not start <= day <= end:
            continue
        if day in seen:
            raise DataError("Futu returned duplicate historical PE dates")
        seen.add(day)
        value = decimal_or_none(row.get("value"))
        if value is None or value == 0:
            continue
        records.append(
            SourceValuation(
                source="futu",
                symbol=spec.quote_code,
                trade_date=day,
                metric=metric.lower(),
                basis=f"futu_valuation_{metric.lower()}",
                value=value,
                observed_at=observed_at,
                artifact_path=path,
                raw=row,
            )
        )
    return records


def parse_snapshot(
    row: dict,
    spec: IndexSpec,
    trade_date: date,
    observed_at: datetime,
    artifact_path: str,
) -> Observation:
    zone = ZoneInfo(spec.timezone)
    quote_at = datetime.fromisoformat(row["update_time"]).replace(tzinfo=zone)
    if observed_at.astimezone(zone).date() != trade_date:
        raise DataError("Current Futu fundamentals cannot be assigned to a historical date")
    if quote_at.date() != trade_date or quote_at.time() < clock_time.fromisoformat(spec.close_time):
        raise DataError(
            f"{row['code']}: no same-day closing snapshot (stale, suspended, or intraday)"
        )
    if quote_at > observed_at.astimezone(zone):
        raise DataError("Snapshot timestamp is in the future")
    price = decimal_or_none(row.get("last_price"))
    if price is None or price <= 0:
        raise DataError(f"{row['code']}: invalid closing price")
    is_index = row["code"] == spec.quote_code
    if not is_index and not row.get("equity_valid"):
        raise DataError(f"{row['code']}: equity fields are unavailable")
    pe = None if is_index else decimal_or_none(row.get("pe_ttm_ratio"))
    # A zero PE is a missing/sentinel value, not evidence of zero earnings.
    eps = price / pe if pe is not None and pe != 0 else None
    return Observation(
        symbol=row["code"],
        trade_date=trade_date,
        source="futu",
        price=price,
        eps_ttm=eps,
        pe_ttm=pe,
        observed_at=observed_at,
        quote_at=quote_at,
        currency=spec.currency,
        eps_method="price_div_pe_ttm" if not is_index else "not_applicable",
        reported_eps=None if is_index else decimal_or_none(row.get("earning_per_share")),
        artifact_path=artifact_path,
        raw=row,
    )


class FutuSource:
    def __init__(self, archive: Archive, host: str, port: int, timeout: int = 20):
        self.archive, self.host, self.port, self.timeout = archive, host, port, timeout
        self.ctx = None
        self._last_snapshot_request = 0.0
        self._last_history_request = 0.0
        self._last_valuation_request = 0.0

    def __enter__(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout):
                pass
        except OSError as error:
            raise SourceError(f"OpenD is unavailable at {self.host}:{self.port}") from error
        # The SDK changes SIGINT at import time; retain the application's handler.
        previous_handler = signal.getsignal(signal.SIGINT)
        from futu import OpenQuoteContext

        signal.signal(signal.SIGINT, previous_handler)
        logging.getLogger("FTConsoleLog").setLevel(logging.ERROR)
        self.ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self

    def __exit__(self, *_):
        if self.ctx is not None:
            self.ctx.close()

    def members(self, spec: IndexSpec) -> tuple[set[str], str]:
        result, data = self.ctx.get_plate_stock(spec.quote_code)
        if result != 0:
            raise SourceError(f"Futu constituents: {data}")
        rows = json.loads(data.to_json(orient="records", force_ascii=False))
        if not rows or len({row["code"] for row in rows}) != len(rows):
            raise DataError("Futu returned empty or duplicate constituents")
        path = self.archive.save_json(
            "futu_members", {"observed_at": datetime.now(UTC), "rows": rows}
        )
        return {row["code"] for row in rows}, path

    def is_trading_day(self, spec: IndexSpec, day: date) -> bool:
        result, data = self.ctx.request_trading_days(
            code=spec.quote_code,
            start=day.isoformat(),
            end=day.isoformat(),
        )
        if result != 0:
            raise SourceError(f"Futu trading calendar: {data}")
        return any(row["time"] == day.isoformat() for row in data)

    def trading_days(self, start: date, end: date, *, market="CN") -> tuple[set[date], str]:
        """Market sessions; older HK dates use actual HSI bars, not mainland holidays."""
        from futu import TradeDateMarket

        if market not in {"CN", "HK"}:
            raise DataError("Unsupported trading-calendar market")
        today = datetime.now(
            ZoneInfo("Asia/Hong_Kong" if market == "HK" else "Asia/Shanghai")
        ).date()
        try:
            earliest = today.replace(year=today.year - 10)
        except ValueError:
            earliest = today.replace(year=today.year - 10, day=28)
        if not start <= end <= today:
            raise DataError("Invalid historical calendar range")
        benchmark_rows, rows = [], []
        if start < earliest:
            if market != "HK":
                raise SourceError("Futu historical calendar only supports the past ten years")
            # The market calendar silently clips at ten years. An independent broad
            # index's actual sessions cover older HK history, including typhoon closures.
            try:
                oldest = today.replace(year=today.year - 20)
            except ValueError:
                oldest = today.replace(year=today.year - 20, day=28)
            if start < oldest:
                raise SourceError("Futu benchmark calendar only supports the past twenty years")
            benchmark = IndexSpec(
                "hk_calendar",
                "恒生指数交易日期",
                "HSI",
                "HK.800000",
                currency="HKD",
                timezone="Asia/Hong_Kong",
                close_time="16:10",
                reference_history_source="none",
            )
            benchmark_rows = self.index_history(
                benchmark, start, min(end, earliest - timedelta(days=1))
            )
            if not benchmark_rows:
                raise SourceError("No HSI history available for the older HK calendar")
        recent_start = max(start, earliest)
        if recent_start <= end:
            result, rows = self.ctx.request_trading_days(
                market=getattr(TradeDateMarket, market),
                start=recent_start.isoformat(),
                end=end.isoformat(),
            )
            if result != 0:
                raise SourceError(f"Futu trading calendar: {rows}")
        dates = [date.fromisoformat(row["time"]) for row in rows]
        dates.extend(row.trade_date for row in benchmark_rows)
        sessions = set(dates)
        if len(sessions) != len(dates) or any(
            not start <= day <= end or day.weekday() > 4 for day in sessions
        ):
            raise DataError("Futu returned invalid or duplicate calendar dates")
        corrections = [
            {"date": day, "reason": "exchange_announced_full_day_closure", "source_url": url}
            for day, url in HKEX_FULL_DAY_CLOSURES.items()
            if market == "HK" and day in sessions
        ]
        sessions.difference_update(item["date"] for item in corrections)
        path = self.archive.save_json(
            "futu_calendar",
            {
                "observed_at": datetime.now(UTC),
                "market": market,
                "start": start,
                "end": end,
                "rows": rows,
                "calendar_corrections": corrections,
                "market_calendar_start": recent_start if recent_start <= end else None,
                "historical_reference_symbol": "HK.800000" if benchmark_rows else None,
                "historical_reference_dates": [r.trade_date for r in benchmark_rows],
                "historical_reference_artifacts": sorted({r.artifact_path for r in benchmark_rows}),
            },
        )
        return sessions, path

    def snapshots(self, symbols: list[str]) -> tuple[list[dict], str, datetime]:
        rows = []
        for offset in range(0, len(symbols), 300):
            # Stay well below snapshot request-rate and per-request symbol limits.
            delay = 3.1 - (time.monotonic() - self._last_snapshot_request)
            if delay > 0:
                time.sleep(delay)
            self._last_snapshot_request = time.monotonic()
            result, data = self.ctx.get_market_snapshot(symbols[offset : offset + 300])
            if result != 0:
                raise SourceError(f"Futu snapshots: {data}")
            rows.extend(json.loads(data.to_json(orient="records", force_ascii=False)))
        observed_at = datetime.now(UTC)
        path = self.archive.save_json("futu_quotes", {"observed_at": observed_at, "rows": rows})
        if len({row["code"] for row in rows}) != len(rows):
            raise DataError("Futu returned duplicate quote rows")
        if set(row["code"] for row in rows) - set(symbols):
            raise DataError("Futu returned unexpected symbols")
        return rows, path, observed_at

    def daily(
        self, spec: IndexSpec, symbols: list[str], day: date
    ) -> tuple[list[Observation], list[str]]:
        rows, path, observed_at = self.snapshots(symbols)
        observations, errors = [], []
        for row in rows:
            try:
                observations.append(parse_snapshot(row, spec, day, observed_at, path))
            except (DataError, KeyError, ValueError) as error:
                errors.append(str(error))
        return observations, errors

    def index_history(self, spec: IndexSpec, start: date, end: date) -> list[Observation]:
        from futu import AuType, KLType

        observations, seen_dates, seen_keys = [], set(), set()
        page_key = None
        for page in range(100):
            delay = 3.1 - (time.monotonic() - self._last_history_request)
            if delay > 0:
                time.sleep(delay)
            self._last_history_request = time.monotonic()
            result, data, next_key = self.ctx.request_history_kline(
                spec.quote_code,
                start=start.isoformat(),
                end=end.isoformat(),
                ktype=KLType.K_DAY,
                autype=AuType.NONE,
                max_count=1000,
                page_req_key=page_key,
            )
            if result != 0:
                raise SourceError(f"Futu historical K-lines: {data}")
            rows = json.loads(data.to_json(orient="records", force_ascii=False))
            observed_at = datetime.now(UTC)
            path = self.archive.save_json(
                "futu_history",
                {
                    "observed_at": observed_at,
                    "symbol": spec.quote_code,
                    "start": start,
                    "end": end,
                    "page": page,
                    "autype": "NONE",
                    "rows": rows,
                },
            )
            for row in rows:
                record = parse_history_bar(row, spec, observed_at, path)
                if not start <= record.trade_date <= end or record.trade_date in seen_dates:
                    raise DataError("Futu history contains duplicate or out-of-range dates")
                seen_dates.add(record.trade_date)
                observations.append(record)
            if next_key is None:
                return observations
            if not rows or next_key in seen_keys:
                raise DataError("Futu historical pagination did not advance")
            seen_keys.add(next_key)
            page_key = next_key
        raise DataError("Futu historical pagination exceeded 100 pages")

    def valuation_history(
        self, spec: IndexSpec, start: date, end: date, metric: str = "PE"
    ) -> list[SourceValuation]:
        from futu import ValuationIntervalType, ValuationType

        if metric not in {"PE", "PB", "PS"}:
            raise DataError("Unsupported valuation metric")
        delay = 1.1 - (time.monotonic() - self._last_valuation_request)
        if delay > 0:
            time.sleep(delay)
        self._last_valuation_request = time.monotonic()
        result, data = self.ctx.get_valuation_detail(
            spec.quote_code,
            valuation_type=getattr(ValuationType, metric),
            interval_type=ValuationIntervalType.YEAR_30,
        )
        if result != 0:
            raise SourceError(f"Futu historical valuations: {data}")
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "futu_valuation_history",
            {
                "observed_at": observed_at,
                "symbol": spec.quote_code,
                "metric": metric,
                "interval": "YEAR_30",
                "requested_start": start,
                "requested_end": end,
                "data": data,
            },
        )
        return parse_valuation_history(data, spec, start, end, observed_at, path, metric)

    def company_pe_history(self, spec, symbol, start, end) -> list[SourceValuation]:
        if symbol == spec.quote_code:
            raise DataError("Company TTM definition must not be applied to an index")
        rows = self.valuation_history(replace(spec, quote_code=symbol), start, end)
        # https://support.futunn.com/topic497 defines company PE as price divided
        # by (TTM net profit attributable to owners / total shares).
        # This does not resolve index PE weighting.
        return [replace(row, metric="pe_ttm", basis="futu_company_pe_ttm_v1") for row in rows]

    def price_history(self, spec, symbol, start, end) -> list[Observation]:
        return [
            replace(row, source="futu_history", eps_method="not_provided")
            for row in self.index_history(replace(spec, quote_code=symbol), start, end)
        ]
