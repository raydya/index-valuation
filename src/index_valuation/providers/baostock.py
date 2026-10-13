"""Independent end-of-day checks; BaoStock is never silently substituted for Futu."""

import contextlib
import hashlib
import io
import json
import logging
import socket
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from index_valuation.archive import Archive
from index_valuation.domain import DataError, IndexSpec, Observation, SourceError, decimal_or_none

log = logging.getLogger(__name__)


def parse_trading_calendar(rows, start, end):
    calendar = {date.fromisoformat(row["calendar_date"]): row["is_trading_day"] for row in rows}
    expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    if len(calendar) != len(rows) or set(calendar) != expected:
        raise DataError("BaoStock returned an incomplete or duplicate trading calendar")
    if set(calendar.values()) - {"0", "1"}:
        raise DataError("BaoStock returned invalid trading-day flags")
    return {day for day, trading in calendar.items() if trading == "1"}


def parse_daily(row: dict, spec: IndexSpec, observed_at: datetime, path: str) -> Observation:
    price = decimal_or_none(row.get("close"))
    if price is None or price <= 0:
        raise DataError(f"BaoStock {row.get('code')}: invalid price")
    day = date.fromisoformat(row["date"])
    pe = decimal_or_none(row.get("peTTM"))
    quote_at = datetime.combine(
        day, clock_time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
    )
    if quote_at > observed_at:
        raise DataError("BaoStock returned an unfinished daily bar")
    return Observation(
        symbol=row["code"].upper(),
        trade_date=day,
        source="baostock",
        price=price,
        eps_ttm=price / pe if pe is not None and pe != 0 else None,
        pe_ttm=pe,
        observed_at=observed_at,
        quote_at=quote_at,
        currency=spec.currency,
        artifact_path=path,
        raw=row,
    )


class BaoStockSource:
    def __init__(self, archive: Archive, timeout: int = 20):
        self.archive, self.timeout = archive, timeout
        self.api = None

    def __enter__(self):
        import baostock as bs

        self.api = bs
        # BaoStock constructs its own socket without a timeout parameter. The socket
        # retains this timeout; restore the process default immediately after login.
        previous_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(self.timeout)
            with contextlib.redirect_stdout(io.StringIO()):
                result = bs.login()
        finally:
            socket.setdefaulttimeout(previous_timeout)
        if result.error_code != "0":
            raise SourceError(f"BaoStock login: {result.error_msg}")
        return self

    def __exit__(self, *_):
        if self.api is not None:
            with contextlib.redirect_stdout(io.StringIO()):
                self.api.logout()

    def _history_rows(self, result, request: dict) -> tuple[list[dict], datetime, str]:
        rows = []
        while result.error_code == "0" and result.next():
            rows.append(dict(zip(result.fields, result.get_row_data(), strict=True)))
        if result.error_code != "0":
            raise SourceError(f"BaoStock history: {result.error_msg}")
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "baostock_history",
            {
                "observed_at": observed_at,
                "request": request,
                "rows": rows,
            },
        )
        return rows, observed_at, path

    def trading_days(self, start: date, end: date) -> tuple[set[date], str]:
        rows, _, path = self._history_rows(
            self.api.query_trade_dates(start_date=start.isoformat(), end_date=end.isoformat()),
            {"endpoint": "query_trade_dates", "start": start, "end": end},
        )
        return parse_trading_calendar(rows, start, end), path

    def cached_trading_days(self, start: date, end: date) -> tuple[set[date], str]:
        """Reuse only complete, intact, historical calendars; never infer missing days."""
        candidates = []
        for path in (self.archive.root / "baostock_history").glob("*.json"):
            # Company histories are large; their request metadata is at the start.
            with path.open("rb") as stream:
                if b'"endpoint": "query_trade_dates"' not in stream.read(1024):
                    continue
            try:
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != path.stem:
                    raise DataError("Corrupted archived calendar")
                payload = json.loads(content)
                request = payload["request"]
                first, last = (
                    date.fromisoformat(request["start"]),
                    date.fromisoformat(request["end"]),
                )
                observed = datetime.fromisoformat(payload["observed_at"])
                if observed.tzinfo is None:
                    raise DataError("Archived calendar has no observation timezone")
                if not first <= start <= end <= last:
                    continue
                if end > observed.astimezone(ZoneInfo("Asia/Shanghai")).date():
                    continue
                sessions = parse_trading_calendar(payload["rows"], first, last)
                candidates.append((observed, str(path), {d for d in sessions if start <= d <= end}))
            except (ValueError, KeyError, TypeError, DataError) as error:
                log.warning("Ignoring invalid calendar archive %s: %s", path, error)
        if not candidates:
            raise SourceError("No verified cached calendar covers the requested historical range")
        _, path, sessions = max(candidates, key=lambda candidate: candidate[0])
        return sessions, path

    def index_history(self, spec: IndexSpec, start: date, end: date) -> list[Observation]:
        rows, observed_at, path = self._history_rows(
            self.api.query_history_k_data_plus(
                spec.quote_code.lower(),
                "date,code,close",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="d",
                adjustflag="3",
            ),
            {
                "endpoint": "query_history_k_data_plus",
                "symbol": spec.quote_code,
                "start": start,
                "end": end,
                "adjustflag": "3",
            },
        )
        observations = [parse_daily(row, spec, observed_at, path) for row in rows]
        if len({row.trade_date for row in observations}) != len(observations):
            raise DataError("BaoStock returned duplicate historical dates")
        if any(
            row.symbol != spec.quote_code or not start <= row.trade_date <= end
            for row in observations
        ):
            raise DataError("BaoStock history contains wrong symbols or out-of-range dates")
        return observations

    def company_history(self, spec, symbol, start, end) -> list[Observation]:
        rows, observed_at, path = self._history_rows(
            self.api.query_history_k_data_plus(
                symbol.lower(),
                "date,code,close,peTTM,tradestatus",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="d",
                adjustflag="3",
            ),
            {
                "endpoint": "query_history_k_data_plus",
                "symbol": symbol,
                "start": start,
                "end": end,
                "adjustflag": "3",
                "earnings": "peTTM",
            },
        )
        observations = [parse_daily(row, spec, observed_at, path) for row in rows]
        if len({row.trade_date for row in observations}) != len(observations):
            raise DataError("BaoStock returned duplicate company dates")
        if any(row.symbol != symbol or not start <= row.trade_date <= end for row in observations):
            raise DataError("BaoStock returned wrong company or date range")
        return observations

    def security_dates(self, symbols: list[str]) -> tuple[dict, str]:
        rows, _, path = self._history_rows(
            self.api.query_stock_basic(),
            {"endpoint": "query_stock_basic"},
        )
        selected = set(symbols)
        result = {}
        for row in rows:
            symbol = row["code"].upper()
            if symbol not in selected:
                continue
            if symbol in result:
                raise DataError("BaoStock returned duplicate security profiles")
            result[symbol] = {
                "listed": date.fromisoformat(row["ipoDate"]) if row.get("ipoDate") else None,
                "delisted": date.fromisoformat(row["outDate"]) if row.get("outDate") else None,
            }
        return result, path

    def daily(
        self, spec: IndexSpec, symbols: list[str], day: date
    ) -> tuple[list[Observation], list[str]]:
        observations, errors = [], []
        for offset, symbol in enumerate(symbols):
            fields = (
                "date,code,close"
                if symbol == spec.quote_code
                else "date,code,close,peTTM,tradestatus"
            )
            result = self.api.query_history_k_data_plus(
                symbol.lower(),
                fields,
                start_date=day.isoformat(),
                end_date=day.isoformat(),
                frequency="d",
                adjustflag="3",
            )
            if result.error_code != "0":
                raise SourceError(f"BaoStock {symbol}: {result.error_msg}")
            rows = []
            while result.error_code == "0" and result.next():
                rows.append(dict(zip(result.fields, result.get_row_data(), strict=True)))
            if result.error_code != "0":
                raise SourceError(f"BaoStock {symbol}: incomplete response")
            observed_at = datetime.now(UTC)
            path = self.archive.save_json("baostock", {"observed_at": observed_at, "rows": rows})
            if len(rows) > 1:
                raise DataError(f"BaoStock {symbol}: expected one daily observation")
            if not rows:
                errors.append(f"BaoStock {symbol}: no data for {day}")
            for row in rows:
                try:
                    observation = parse_daily(row, spec, observed_at, path)
                    if observation.trade_date != day or observation.symbol != symbol:
                        raise DataError(f"BaoStock {symbol}: wrong date or symbol")
                    observations.append(observation)
                except (DataError, KeyError, ValueError) as error:
                    errors.append(str(error))
            if (offset + 1) % 30 == 0:
                log.info("Independent source: %s/%s securities", offset + 1, len(symbols))
        return observations, errors
