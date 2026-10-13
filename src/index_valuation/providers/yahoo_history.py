"""Explicitly mapped daily index closes; ETF and total-return substitutions are forbidden."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import (
    DataError,
    Observation,
    ReferenceHistory,
    SourceError,
    decimal_or_none,
)

URL = "https://query1.finance.yahoo.com/v8/finance/chart/"


def parse_history(payload, spec, start, end, observed_at, path):
    chart = payload.get("chart", {})
    results = chart.get("result") or []
    if chart.get("error") or len(results) != 1:
        raise SourceError("Yahoo did not return an index series")
    data = results[0]
    meta = data.get("meta", {})
    if (
        meta.get("symbol") != spec.yahoo_symbol
        or meta.get("instrumentType") != "INDEX"
        or meta.get("currency") != spec.currency
        or meta.get("exchangeTimezoneName") != spec.timezone
    ):
        raise DataError("Yahoo index identity, currency or timezone mismatch")
    # range=max silently changes 1d to monthly/quarterly bars. Reject that response.
    if meta.get("dataGranularity") != "1d":
        raise DataError("Yahoo returned non-daily index data")
    times = data.get("timestamp", [])
    quotes = data.get("indicators", {}).get("quote", [])
    closes = quotes[0].get("close", []) if len(quotes) == 1 else []
    if len(times) != len(closes):
        raise DataError("Yahoo timestamps and closing values have different lengths")
    zone, seen, rows = ZoneInfo(spec.timezone), set(), []
    for timestamp, value in zip(times, closes, strict=True):
        day = datetime.fromtimestamp(timestamp, zone).date()
        if not start <= day <= end or day in seen:
            raise DataError("Yahoo returned duplicate or out-of-range dates")
        seen.add(day)
        if value is None:  # A missing close is a gap, never a forward-filled price.
            continue
        price = decimal_or_none(value)
        quote_at = datetime.combine(day, time.fromisoformat(spec.close_time), zone)
        if price is None or price <= 0 or quote_at > observed_at:
            raise DataError("Yahoo returned invalid or unfinished index closes")
        rows.append(
            Observation(
                spec.quote_code,
                day,
                "yahoo",
                price,
                None,
                None,
                observed_at,
                quote_at,
                currency=spec.currency,
                eps_method="not_provided",
                artifact_path=path,
                raw={
                    "symbol": spec.yahoo_symbol,
                    "timestamp": timestamp,
                    "close": value,
                    "interval": "1d",
                    "field": "close",
                },
            )
        )
    return rows


class YahooHistory:
    source_name = "yahoo"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if spec.market != "US" or not spec.yahoo_symbol:
            raise DataError("Yahoo history requires a configured US index symbol")
        zone = ZoneInfo(spec.timezone)
        params = {
            "period1": int(datetime.combine(start, time(), zone).timestamp()),
            "period2": int(datetime.combine(end + timedelta(days=1), time(), zone).timestamp()),
            "interval": "1d",
        }
        url = URL + spec.yahoo_symbol
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0"},
            transport=httpx.HTTPTransport(retries=2),
        ) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "yahoo_history",
            {
                "observed_at": observed_at,
                "url": url,
                "params": params,
                "data": payload,
            },
        )
        return ReferenceHistory(parse_history(payload, spec, start, end, observed_at, path))
