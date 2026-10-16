"""Recent daily index candles embedded in the public Google Finance page.

Only explicit daily OHLC bars are eligible; live quotes and line-chart samples
are not closing records. No JavaScript is executed and no account is needed.
"""

import json
import re
from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, Observation, ReferenceHistory, decimal_or_none

MAPPINGS = {
    "CRSPTM1:INDEXNASDAQ": ("US.CRSPTM1", "Morningstar US Total Market PR USD"),
}
DATA_START = re.compile(r"AF_initDataCallback\(\{key:\s*'[^']*',\s*hash:\s*'[^']*',\s*data:\s*")


def parse_history(text, spec, start, end, observed_at, path):
    expected = MAPPINGS.get(spec.google_symbol)
    if (
        not expected
        or expected[0] != spec.quote_code
        or spec.market != "US"
        or spec.currency != "USD"
        or spec.timezone != "America/New_York"
    ):
        raise DataError("Google Finance requires a verified price-index mapping")
    identity = spec.google_symbol.split(":")
    decoder = json.JSONDecoder(parse_float=Decimal)
    candidates = []
    for match in DATA_START.finditer(text):
        try:
            payload, _ = decoder.raw_decode(text[match.end() :])
        except ValueError:
            continue
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], list):
            continue
        for record in payload[0]:
            if not isinstance(record, list) or len(record) < 10:
                continue
            if record[0] != identity or record[7] != expected[1] or record[8] != 86400:
                continue
            groups = record[3]
            if not isinstance(groups, list) or len(groups) != 1:
                continue
            chart = groups[0]
            if not isinstance(chart, list) or len(chart) != 3 or not isinstance(chart[2], list):
                continue
            bars = chart[2]
            # Line charts contain pairs of [date parts, values], not OHLC bars.
            if bars and isinstance(bars[0], list) and len(bars[0]) >= 5:
                candidates.append(bars)
    if len(candidates) != 1:
        raise DataError("Google page must contain one identifiable daily price-index OHLC series")
    zone, rows, seen = ZoneInfo(spec.timezone), [], set()
    for bar in candidates[0]:
        if not isinstance(bar, list) or len(bar) < 5 or not isinstance(bar[4], str):
            raise DataError("Google daily OHLC schema changed")
        try:
            stamp = datetime.fromisoformat(bar[4])
        except ValueError as error:
            raise DataError("Google daily bar has an invalid timestamp") from error
        if stamp.tzinfo is None or stamp.utcoffset() != stamp.astimezone(zone).utcoffset():
            raise DataError("Google daily bar must identify the New York market offset")
        day = stamp.date()
        if day in seen:
            raise DataError("Google returned duplicate daily bars")
        seen.add(day)
        if stamp.time() not in {time(13), time(16)}:
            raise DataError("Google daily bar is not a regular-session closing bar")
        # The page has a rolling recent window, independent of the requested range.
        close = datetime.combine(day, time.fromisoformat(spec.close_time), zone)
        if not start <= day <= end or stamp > observed_at or close > observed_at:
            continue
        price = decimal_or_none(bar[1])
        if price is None or price <= 0:
            raise DataError("Google returned an invalid daily closing price")
        rows.append(
            Observation(
                spec.quote_code,
                day,
                "google",
                price,
                None,
                None,
                observed_at,
                stamp,
                currency=spec.currency,
                eps_method="not_provided",
                artifact_path=path,
                raw={
                    "symbol": spec.google_symbol,
                    "name": expected[1],
                    "field": "daily_ohlc.close",
                    "frequency_seconds": 86400,
                    "timestamp": bar[4],
                    "ohlc": [
                        str(value) if isinstance(value, Decimal) else value for value in bar[:4]
                    ],
                    "coverage": "public_page_recent_window_only",
                },
            )
        )
    return sorted(rows, key=lambda row: row.trade_date)


class GoogleHistory:
    source_name = "google"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if MAPPINGS.get(spec.google_symbol, (None,))[0] != spec.quote_code:
            raise DataError("Google Finance requires a verified price-index mapping")
        url = f"https://www.google.com/finance/quote/{spec.google_symbol}?hl=en"
        response = httpx.get(
            url, timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        )
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "google_history",
            {
                "url": url,
                "status": response.status_code,
                "observed_at": observed_at,
                "text": response.text,
            },
        )
        response.raise_for_status()
        return ReferenceHistory(
            observations=parse_history(response.text, spec, start, end, observed_at, path)
        )
