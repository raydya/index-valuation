"""Nasdaq's public historical index closes, with identity and pagination checks."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import (
    DataError,
    Observation,
    ReferenceHistory,
    SourceError,
    decimal_or_none,
)

URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"


def parse_history(payload, spec, start, end, observed_at, path):
    data = payload.get("data")
    if not isinstance(data, dict) or payload.get("status", {}).get("rCode") != 200:
        raise SourceError("Nasdaq did not return index history")
    if data.get("symbol") != spec.nasdaq_symbol:
        raise DataError("Nasdaq returned a different index")
    rows, seen = [], set()
    for item in (data.get("tradesTable") or {}).get("rows") or []:
        day = datetime.strptime(item["date"], "%m/%d/%Y").date()
        if not start <= day <= end or day in seen:
            raise DataError("Nasdaq returned duplicate or out-of-range dates")
        seen.add(day)
        price = decimal_or_none(str(item.get("close", "")).replace(",", ""))
        quote_at = datetime.combine(
            day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if price is None or price <= 0 or quote_at > observed_at:
            raise DataError("Nasdaq returned invalid or unfinished index closes")
        rows.append(
            Observation(
                spec.quote_code,
                day,
                "nasdaq",
                price,
                None,
                None,
                observed_at,
                quote_at,
                currency=spec.currency,
                eps_method="not_provided",
                artifact_path=path,
                raw=item,
            )
        )
    return rows


class NasdaqHistory:
    source_name = "nasdaq"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if spec.market != "US" or not spec.nasdaq_symbol:
            raise DataError("Nasdaq history requires a configured US index symbol")
        rows, seen = [], set()
        with httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            transport=httpx.HTTPTransport(retries=2),
        ) as client:
            for _page in range(100):
                params = {
                    "assetclass": "index",
                    "fromdate": start.isoformat(),
                    "todate": end.isoformat(),
                    "limit": 5000,
                    "offset": len(rows),
                }
                url = URL.format(symbol=spec.nasdaq_symbol)
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                observed_at = datetime.now(UTC)
                path = self.archive.save_json(
                    "nasdaq_history",
                    {
                        "observed_at": observed_at,
                        "url": url,
                        "params": params,
                        "data": payload,
                    },
                )
                batch = parse_history(payload, spec, start, end, observed_at, path)
                total = int(payload["data"]["totalRecords"])
                dates = {row.trade_date for row in batch}
                if seen & dates or (not batch and len(rows) < total):
                    raise DataError("Nasdaq historical pagination did not advance")
                seen.update(dates)
                rows.extend(batch)
                if len(rows) == total:
                    return ReferenceHistory(rows)
                if len(rows) > total:
                    raise DataError("Nasdaq returned inconsistent historical row counts")
        raise DataError("Nasdaq historical pagination exceeded 100 pages")
