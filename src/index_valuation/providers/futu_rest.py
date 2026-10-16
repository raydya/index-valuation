"""Read-only REST index prices and valuations, separate from Futu OpenD data."""

import json
import re
import time as clock
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx

from index_valuation.archive import json_text
from index_valuation.domain import (
    DataError,
    Observation,
    ReferenceHistory,
    SourceError,
    SourceValuation,
    decimal_or_none,
)

SOURCE = "futorest"
METRICS = {"pe": 1, "pb": 2, "ps": 3}
PAGE_SIZE = 370
EARLIEST_PRICE_REQUEST = date(1970, 1, 1)
INTERVAL_TYPE = 10  # The largest advertised window; actual coverage is reported separately.
MAX_REQUEST_ATTEMPTS = 4
MIN_REQUEST_INTERVAL = 1.0
MAX_RATE_LIMIT_WAIT = 60.0


class FutuRestSourceError(SourceError):
    def __init__(self, category, *, status=None, code=None, artifact_path=None, retry_after=None):
        self.category, self.status, self.code = category, status, code
        self.artifact_path = artifact_path
        self.retry_after = retry_after
        super().__init__(f"Futu REST {category} (HTTP {status}, code {code})")


def _failure_category(status, payload):
    message = str(payload.get("ret_msg", payload.get("message", ""))).lower()
    detail = payload.get("error") or {}
    if (
        status == 429
        or payload.get("ret_code") == -11
        or (isinstance(detail, dict) and detail.get("code") == "rate_limited")
    ):
        return "rate_limited"
    if status == 401:
        return "authentication_required"
    if status == 403 or any(
        word in message for word in ("permission", "无权限", "没有权限", "权限不足")
    ):
        return "permission_denied"
    if any(word in message for word in ("not support", "unsupported", "不支持")):
        return "unsupported_instrument"
    if status >= 500:
        return "temporary_failure"
    return "request_rejected"


def _rate_limit_delay(response, payload, attempt):
    """Honor the longest server hint; use bounded backoff only when no hint exists."""
    delays = []
    header = response.headers.get("Retry-After", "")
    seconds = decimal_or_none(header)
    if seconds is not None and seconds >= 0:
        delays.append(float(seconds))
    elif header:
        try:
            until = parsedate_to_datetime(header)
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            delays.append(max(0.0, (until - datetime.now(UTC)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            pass
    detail = payload.get("error") or {}
    messages = [payload.get("ret_msg", ""), payload.get("message", "")]
    if isinstance(detail, dict):
        messages.append(detail.get("message", ""))
    for message in messages:
        match = re.search(r"retry\s+after\s+(\d+(?:\.\d+)?)\s*s", str(message), re.I)
        if match:
            delays.append(float(match.group(1)))
    return max(delays) if delays else min(MAX_RATE_LIMIT_WAIT, 2 ** (attempt + 1))


def _timestamp(value, spec, *, milliseconds=False):
    if not isinstance(value, (int, Decimal)) or isinstance(value, bool):
        raise DataError("Futu REST returned an invalid date timestamp")
    try:
        return datetime.fromtimestamp(
            float(value) / (1000 if milliseconds else 1), ZoneInfo(spec.timezone)
        )
    except (OverflowError, OSError, ValueError) as error:
        raise DataError("Futu REST returned an invalid date timestamp") from error


def _close_at(day, spec):
    return datetime.combine(day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone))


def _raw_record(value):
    # JSONB uses the standard JSON encoder. Preserve Decimal precision as text in
    # provenance while keeping the actual price/valuation fields as Decimal.
    return json.loads(json_text(value))


def _check_identity_fields(row, symbol, identity):
    if not isinstance(row, dict):
        raise DataError("Futu REST returned an invalid price record")
    for field in ("symbol", "code"):
        if field in row and row[field] != symbol:
            raise DataError("Futu REST returned a different security")
    if row.get("name") and row["name"] != identity["name"]:
        raise DataError("Futu REST returned a different index name")


def parse_history_bar(row, spec, observed_at, path, *, identity, symbol):
    _check_identity_fields(row, symbol, identity)
    try:
        day = datetime.strptime(str(row["date"]), "%Y%m%d").date()
    except (KeyError, ValueError, TypeError) as error:
        raise DataError("Futu REST returned an invalid price date") from error
    label = _timestamp(row.get("time_key"), spec, milliseconds=True)
    if label.date() != day:
        raise DataError("Futu REST price timestamp and date disagree")
    # Real responses use hours (-4/-5), despite documentation describing minutes.
    # The IANA zone supplies historical DST; neither offset format shifts the label.
    offset = decimal_or_none(row.get("time_zone"))
    expected = Decimal(int(label.utcoffset().total_seconds())) / Decimal(3600)
    if offset not in (expected, expected * 60):
        raise DataError("Futu REST price timezone disagrees with the index timezone")
    price = decimal_or_none(row.get("close"))
    quote_at = _close_at(day, spec)
    if price is None or price <= 0 or quote_at > observed_at:
        raise DataError("Futu REST returned an invalid or unfinished daily close")
    return Observation(
        symbol=spec.quote_code,
        trade_date=day,
        source=SOURCE,
        price=price,
        eps_ttm=None,
        pe_ttm=None,
        observed_at=observed_at,
        quote_at=quote_at,
        currency=spec.currency,
        eps_method="not_provided",
        artifact_path=path,
        raw=_raw_record({**row, "rest_symbol": symbol, "identity": identity}),
    )


def parse_valuation_history(data, spec, start, end, observed_at, path, *, metric, symbol):
    if metric not in METRICS or data.get("valuation_type") != METRICS[metric]:
        raise DataError("Futu REST returned a different valuation metric")
    for field in ("symbol", "code"):
        if field in data and data[field] != symbol:
            raise DataError("Futu REST returned a different valuation security")
    trend = data.get("trend")
    items = trend.get("historical_items") if isinstance(trend, dict) else None
    if not isinstance(items, list):
        raise DataError("Futu REST valuation history schema changed")
    records, seen = [], set()
    for row in items:
        try:
            day = date.fromisoformat(row["time_str"])
        except (KeyError, ValueError, TypeError) as error:
            raise DataError("Futu REST returned an invalid valuation date") from error
        if not start <= day <= end:
            continue
        label = _timestamp(row.get("time"), spec)
        if label.date() != day:
            raise DataError("Futu REST valuation timestamp and date disagree")
        if day in seen:
            raise DataError("Futu REST returned duplicate valuation dates")
        seen.add(day)
        if _close_at(day, spec) > observed_at:
            raise DataError("Futu REST returned an unfinished valuation session")
        value = decimal_or_none(row.get("value"))
        if value is None or value == 0:
            continue
        records.append(
            SourceValuation(
                source=SOURCE,
                symbol=spec.quote_code,
                trade_date=day,
                metric=metric,
                basis=f"futorest_valuation_{metric}",
                value=value,
                observed_at=observed_at,
                artifact_path=path,
                raw=_raw_record(
                    {
                        **row,
                        "rest_symbol": symbol,
                        "valuation_type": METRICS[metric],
                        "interval_type": INTERVAL_TYPE,
                        "date_basis": "source_trade_date_verified_in_index_timezone",
                        "basis_status": "index_valuation_earnings_basis_not_disclosed",
                        "sampling": "source_observations_without_fill",
                    }
                ),
            )
        )
    return records


class FutuRestHistory:
    source_name = SOURCE
    _last_request_at = 0.0
    _not_before = 0.0

    def __init__(self, archive, client, *, timeout=20):
        self.archive, self.client, self.timeout = archive, client, timeout

    def _request(self, method, path, **kwargs):
        for attempt in range(MAX_REQUEST_ATTEMPTS):
            delay = (
                max(
                    FutuRestHistory._last_request_at + MIN_REQUEST_INTERVAL,
                    FutuRestHistory._not_before,
                )
                - clock.monotonic()
            )
            if delay > MAX_RATE_LIMIT_WAIT:
                raise FutuRestSourceError("rate_limited", retry_after=delay)
            if delay > 0:
                clock.sleep(delay)
            FutuRestHistory._last_request_at = clock.monotonic()
            payload, observed_at, artifact, response = self._request_once(method, path, **kwargs)
            if _failure_category(response.status_code, payload) != "rate_limited":
                return self._checked_response(payload, observed_at, artifact, response)
            retry_after = _rate_limit_delay(response, payload, attempt)
            FutuRestHistory._not_before = clock.monotonic() + retry_after
            if attempt == MAX_REQUEST_ATTEMPTS - 1 or retry_after > MAX_RATE_LIMIT_WAIT:
                raise FutuRestSourceError(
                    "rate_limited",
                    status=response.status_code,
                    code=payload.get("ret_code", payload.get("code")),
                    artifact_path=artifact,
                    retry_after=retry_after,
                )
        raise AssertionError("unreachable")

    def _request_once(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, timeout=self.timeout, **kwargs)
        except httpx.HTTPError as error:
            # Never include request headers or the authenticated client's exception text.
            raise FutuRestSourceError("temporary_failure") from error
        observed_at = datetime.now(UTC)
        try:
            payload = response.json(parse_float=Decimal)
        except ValueError:
            payload = {"invalid_json_body": response.text}
        artifact = self.archive.save_json(
            "futorest_history",
            {
                "observed_at": observed_at,
                "method": method,
                "path": path,
                "parameters": kwargs,
                "http_status": response.status_code,
                "retry_after": response.headers.get("Retry-After"),
                "response": payload,
            },
        )
        if not isinstance(payload, dict):
            raise DataError("Futu REST returned an invalid response envelope")
        return payload, observed_at, artifact, response

    @staticmethod
    def _checked_response(payload, observed_at, artifact, response):
        if response.status_code != 200 or payload.get("ret_code") != 0:
            raise FutuRestSourceError(
                _failure_category(response.status_code, payload),
                status=response.status_code,
                code=payload.get("ret_code", payload.get("code")),
                artifact_path=artifact,
            )
        if not isinstance(payload.get("data"), dict):
            raise DataError("Futu REST returned an invalid data envelope")
        return payload, observed_at, artifact

    @staticmethod
    def _failure(stage, start, end, error):
        return {
            "stage": stage,
            "start": start,
            "end": end,
            "category": getattr(
                error,
                "category",
                "invalid_data" if isinstance(error, DataError) else "source_failure",
            ),
            "error": str(error),
            "artifact_path": getattr(error, "artifact_path", None),
            "retry_after": getattr(error, "retry_after", None),
        }

    def index_history(self, spec, start, end):
        symbol = spec.futorest_symbol
        if spec.market != "US" or not symbol or not symbol.startswith("US.") or start > end:
            raise DataError("Futu REST requires a configured US index and a valid date range")
        payload, _, _ = self._request(
            "POST", "/api/v1.0/quote/stock-basicinfo", json={"code_list": [symbol]}
        )
        identities = payload["data"].get("basic_list")
        if not isinstance(identities, list):
            raise DataError("Futu REST security identity schema changed")
        if not identities:
            raise FutuRestSourceError("instrument_not_listed")
        if (
            len(identities) != 1
            or not isinstance(identities[0], dict)
            or identities[0].get("code") != symbol
            or identities[0].get("stock_type") != "IDX"
            or identities[0].get("exchange") != "US"
            or not identities[0].get("name")
        ):
            raise DataError("Futu REST did not confirm the configured index identity")
        identity = identities[0]
        result = ReferenceHistory()
        self._prices(spec, symbol, identity, start, end, result)
        for metric, valuation_type in METRICS.items():
            try:
                payload, observed_at, path = self._request(
                    "GET",
                    f"/api/v1.0/quote/{symbol}/valuation/detail",
                    params={"valuation_type": valuation_type, "interval_type": INTERVAL_TYPE},
                )
                result.valuations.extend(
                    parse_valuation_history(
                        payload["data"],
                        spec,
                        start,
                        end,
                        observed_at,
                        path,
                        metric=metric,
                        symbol=symbol,
                    )
                )
            except (SourceError, DataError) as error:
                result.failures.append(self._failure(metric, start, end, error))
        return result

    def _prices(self, spec, symbol, identity, start, end, result):
        # Live probes show that long requests silently stop after 1,000 rows,
        # ignore num=370 and report has_more=false. Never use that pagination as
        # evidence of complete coverage. A calendar-year request has at most 366
        # daily rows, which stays below both the requested and actual server cap.
        if start < EARLIEST_PRICE_REQUEST:
            result.failures.append(
                self._failure(
                    "prices",
                    start,
                    min(end, EARLIEST_PRICE_REQUEST - timedelta(days=1)),
                    FutuRestSourceError("unsupported_date_range_before_1970"),
                )
            )
        current = max(start, EARLIEST_PRICE_REQUEST)
        consecutive_failures = 0
        while current <= end:
            chunk_end = min(end, date(current.year, 12, 31))
            try:
                payload, observed_at, path = self._request(
                    "GET",
                    f"/api/v1.0/quote/{symbol}/history-kline",
                    params={
                        "start": current.isoformat(),
                        "end": chunk_end.isoformat(),
                        "ktype": 2,
                        "autype": 0,
                        "num": PAGE_SIZE,
                    },
                )
                items = payload["data"].get("kline_list")
                pagination = payload.get("pagination")
                if not isinstance(items, list) or not isinstance(pagination, dict):
                    raise DataError("Futu REST price history or pagination schema changed")
                if (
                    len(items) > (chunk_end - current).days + 1
                    or type(pagination.get("has_more")) is not bool
                ):
                    raise DataError("Futu REST price pagination is invalid")
                batch = [
                    parse_history_bar(
                        row, spec, observed_at, path, identity=identity, symbol=symbol
                    )
                    for row in items
                ]
                dates = [row.trade_date for row in batch]
                if len(set(dates)) != len(dates):
                    raise DataError("Futu REST returned duplicate prices within a page")
                if any(not current <= day <= chunk_end for day in dates):
                    raise DataError("Futu REST returned prices outside the bounded yearly window")
                result.observations.extend(batch)
                if pagination["has_more"]:
                    raise DataError("Futu REST claims a bounded yearly price window is truncated")
                consecutive_failures = 0
            except (SourceError, DataError) as error:
                result.failures.append(self._failure("prices", current, chunk_end, error))
                consecutive_failures += 1
                if consecutive_failures >= 3 or getattr(error, "category", "") in {
                    "authentication_required",
                    "permission_denied",
                    "unsupported_instrument",
                    "rate_limited",
                }:
                    if chunk_end < end:
                        result.failures.append(
                            self._failure(
                                "prices",
                                chunk_end + timedelta(days=1),
                                end,
                                FutuRestSourceError(
                                    "not_attempted_after_repeated_or_terminal_failure"
                                ),
                            )
                        )
                    return
            current = chunk_end + timedelta(days=1)
