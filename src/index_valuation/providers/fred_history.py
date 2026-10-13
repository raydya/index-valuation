"""Daily index closes redistributed by FRED from S&P and Nasdaq; no PE estimates."""

import csv
from datetime import UTC, date, datetime, time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, Observation, ReferenceHistory, decimal_or_none

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def parse_history(text, spec, start, end, observed_at, path):
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames != ["observation_date", spec.fred_series]:
        raise DataError("FRED returned a different series or CSV schema")
    rows, seen = [], set()
    for item in reader:
        day = date.fromisoformat(item["observation_date"])
        if not start <= day <= end or day in seen:
            raise DataError("FRED returned duplicate or out-of-range dates")
        seen.add(day)
        raw = item[spec.fred_series]
        if raw in {"", "."}:  # FRED includes blank holiday/missing observations.
            continue
        price = decimal_or_none(raw)
        quote_at = datetime.combine(
            day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if price is None or price <= 0 or quote_at > observed_at:
            raise DataError("FRED returned invalid or unfinished index closes")
        rows.append(
            Observation(
                spec.quote_code,
                day,
                "fred",
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


class FredHistory:
    source_name = "fred"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if spec.market != "US" or spec.fred_series not in {"SP500", "NASDAQ100"}:
            raise DataError("FRED requires an explicitly mapped daily US index series")
        batch = ReferenceHistory()
        cursor = start
        with httpx.Client(timeout=self.timeout, transport=httpx.HTTPTransport(retries=2)) as client:
            while cursor <= end:
                last = min(date(cursor.year + 4, 12, 31), end)
                params = {
                    "id": spec.fred_series,
                    "cosd": cursor.isoformat(),
                    "coed": last.isoformat(),
                }
                try:
                    response = client.get(URL, params=params)
                    response.raise_for_status()
                except httpx.HTTPError as error:
                    batch.failures.append({"start": cursor, "end": last, "error": str(error)})
                else:
                    observed_at = datetime.now(UTC)
                    path = self.archive.save_json(
                        "fred_history",
                        {
                            "observed_at": observed_at,
                            "url": URL,
                            "params": params,
                            "text": response.text,
                        },
                    )
                    batch.observations.extend(
                        parse_history(
                            response.text,
                            spec,
                            cursor,
                            last,
                            observed_at,
                            path,
                        )
                    )
                cursor = last + timedelta(days=1)
        return batch
