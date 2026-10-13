"""CN Index's published closing levels; valuation fields are not inferred."""

from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import (
    DataError,
    Observation,
    ReferenceHistory,
    SourceError,
    decimal_or_none,
)

URL = "https://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat"


def parse_history(data, spec, start, end, observed_at, path):
    if data.get("indexCode") != spec.provider_code:
        raise DataError("CN Index history returned a different index")
    fields = data.get("item", [])
    if len(set(fields)) != len(fields) or not {"timestamp", "close"} <= set(fields):
        raise DataError("CN Index history schema changed")
    result, seen = [], set()
    for values in data.get("data", []):
        row = dict(zip(fields, values, strict=True))
        day = date.fromisoformat(row["timestamp"])
        if not start <= day <= end:
            continue
        if day in seen:
            raise DataError("CN Index returned duplicate dates")
        seen.add(day)
        price = decimal_or_none(row["close"])
        quote_at = datetime.combine(
            day, clock_time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if price is None or price <= 0 or quote_at > observed_at:
            raise DataError("CN Index returned an invalid or unfinished daily close")
        result.append(
            Observation(
                symbol=spec.quote_code,
                trade_date=day,
                source="cnindex",
                price=price,
                eps_ttm=None,
                pe_ttm=None,
                observed_at=observed_at,
                quote_at=quote_at,
                currency=spec.currency,
                eps_method="not_provided",
                artifact_path=path,
                raw=row,
            )
        )
    return result


class CNIndexHistory:
    source_name = "cnindex"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        observations = []
        try:
            with httpx.Client(timeout=self.timeout, transport=httpx.HTTPTransport(retries=2)) as c:
                current = start
                while current <= end:
                    last = min(end, date(current.year, 12, 31))
                    params = {
                        "indexCode": spec.provider_code,
                        "startDate": current.isoformat(),
                        "endDate": (last + timedelta(days=1)).isoformat(),
                        "frequency": "day",
                    }
                    response = c.get(URL, params=params)
                    response.raise_for_status()
                    data, observed_at = response.json(), datetime.now(UTC)
                    path = self.archive.save_json(
                        "cnindex_history",
                        {"url": URL, "params": params, "observed_at": observed_at, "data": data},
                    )
                    if data.get("code") != 200 or not isinstance(data.get("data"), dict):
                        raise SourceError("CN Index returned an invalid history response")
                    observations.extend(
                        parse_history(data["data"], spec, current, last, observed_at, path)
                    )
                    current = last + timedelta(days=1)
        except httpx.HTTPError as error:
            raise SourceError(f"CN Index historical download failed: {error}") from error
        return ReferenceHistory(observations)
