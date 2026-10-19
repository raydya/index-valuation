"""Nasdaq's public daily gross total returns, isolated from valuation prices."""

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

from index_valuation.archive import json_text
from index_valuation.domain import DataError, Observation, ReferenceHistory, decimal_or_none

URL = "https://indexes.nasdaq.com/Index/HistoryChartData"
HISTORY_START = date(1999, 3, 4)
SYMBOL = "XNDX"
QUOTE_CODE = "US.XNDX"


def parse_history(payload, spec, start, end, observed_at, path, *, quarantined=None):
    """The chart's UTC-midnight x encodes a session date, not a NY instant."""
    if not isinstance(payload, list):
        raise DataError("Nasdaq total-return response is not a history list")
    if len(payload) > (end - start).days + 1:
        raise DataError("Nasdaq total-return history exceeds the bounded daily window")
    rows, seen, invalid_values = [], set(), []
    for item in payload:
        if not isinstance(item, dict) or item.get("FPSymbol") != SYMBOL:
            raise DataError("Nasdaq returned a different total-return index")
        stamp = item.get("x")
        if type(stamp) is not int or stamp % 86_400_000:
            raise DataError("Nasdaq total-return timestamp is not a UTC session date")
        try:
            day = datetime.fromtimestamp(stamp / 1000, UTC).date()
        except (ValueError, OverflowError, OSError) as error:
            raise DataError("Nasdaq returned an invalid total-return date") from error
        if day in seen or not start <= day <= end:
            raise DataError("Nasdaq returned duplicate or out-of-range total-return dates")
        seen.add(day)
        price = decimal_or_none(item.get("y"))
        quote_at = datetime.combine(
            day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if quote_at > observed_at:
            raise DataError("Nasdaq returned invalid or unfinished total-return closes")
        if price is None or price <= 0:
            if quarantined is None:
                raise DataError("Nasdaq returned invalid or unfinished total-return closes")
            invalid_values.append(
                {
                    "stage": "total_returns",
                    "category": "invalid_observation",
                    "start": day,
                    "end": day,
                    "trade_date": day,
                    "value": str(item.get("y")),
                    "error": "Nasdaq returned a missing or non-positive total-return close",
                    "artifact_path": path,
                }
            )
            continue
        rows.append(
            Observation(
                symbol=spec.quote_code,
                trade_date=day,
                source="nasdaq",
                price=price,
                eps_ttm=None,
                pe_ttm=None,
                observed_at=observed_at,
                quote_at=quote_at,
                currency=spec.currency,
                price_basis="gross_total_return",
                eps_method="not_applicable",
                artifact_path=path,
                # PostgreSQL JSONB uses the standard JSON encoder. Retain all
                # Decimal digits as strings there, while price stays Decimal.
                raw=json.loads(json_text(item)),
            )
        )
    if quarantined is not None:
        quarantined.extend(invalid_values)
    return sorted(rows, key=lambda row: row.trade_date)


class NasdaqTotalReturnHistory:
    source_name = "nasdaq"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if (
            spec.market != "US"
            or spec.nasdaq_symbol != SYMBOL
            or spec.quote_code != QUOTE_CODE
            or spec.currency != "USD"
            or spec.timezone != "America/New_York"
            or start > end
        ):
            raise DataError("Nasdaq total returns require the explicitly mapped USD XNDX index")
        result = ReferenceHistory()
        cursor = max(start, HISTORY_START)
        if start < HISTORY_START:
            result.failures.append(
                {
                    "stage": "total_returns",
                    "start": start,
                    "end": min(end, HISTORY_START - timedelta(days=1)),
                    "category": "unsupported_historical_coverage",
                    "error": "Nasdaq XNDX daily history starts on 1999-03-04",
                }
            )
        if cursor > end:
            return result
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            transport=httpx.HTTPTransport(retries=2),
        ) as client:
            while cursor <= end:
                last = min(date(cursor.year, 12, 31), end)
                params = {"id": SYMBOL, "startDate": str(cursor), "endDate": str(last)}
                path = ""
                try:
                    response = client.post(URL, data=params)
                    observed_at = datetime.now(UTC)
                    # Keep the exact JSON text as well as parsed Decimal values.
                    path = self.archive.save_json(
                        "nasdaq_total_returns",
                        {
                            "observed_at": observed_at,
                            "url": URL,
                            "parameters": params,
                            "http_status": response.status_code,
                            "text": response.text,
                        },
                    )
                    response.raise_for_status()
                    try:
                        payload = response.json(parse_float=Decimal)
                    except ValueError as error:
                        raise DataError("Nasdaq total-return response is not valid JSON") from error
                    result.observations.extend(
                        parse_history(
                            payload,
                            spec,
                            cursor,
                            last,
                            observed_at,
                            path,
                            quarantined=result.failures,
                        )
                    )
                except (httpx.HTTPError, DataError) as error:
                    result.failures.append(
                        {
                            "stage": "total_returns",
                            "start": cursor,
                            "end": last,
                            "category": "invalid_data"
                            if isinstance(error, DataError)
                            else "source_failure",
                            "error": str(error),
                            "artifact_path": path,
                        }
                    )
                # A calendar year fits below the 6,950 daily rows observed in a
                # real full-history response. Never rely on a long chart request
                # being unsampled or on this endpoint providing pagination.
                cursor = last + timedelta(days=1)
        return result
