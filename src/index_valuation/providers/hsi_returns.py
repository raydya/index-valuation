"""Hang Seng official rolling gross-total-return history, distinct from valuation."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, Observation, ReferenceHistory, decimal_or_none

URL = (
    "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy"
    "/v1/product-data/pub/indexes/metadata/v2"
)
MAPPINGS = {
    "12083.00": ("HK.HSTECHT", "Hang Seng TECH Index (Gross Total Return Index)"),
}


def parse_history(payload, spec, start, end, observed_at, path):
    identity = MAPPINGS.get(spec.provider_code)
    if not identity or spec.quote_code != identity[0]:
        raise DataError("Hang Seng total returns require an explicitly mapped gross-return index")
    if not isinstance(payload, dict) or payload.get("code") not in {"0", 0}:
        raise DataError("Hang Seng did not return successful total-return history")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise DataError("Hang Seng total-return identity envelope changed")
    item = data[0]
    if item.get("indexCode") != spec.provider_code or item.get("indexName") != identity[1]:
        raise DataError("Hang Seng returned a different total-return index")
    history = item.get("valueHistory")
    if not isinstance(history, list) or not history:
        raise DataError("Hang Seng returned no total-return history")
    result, seen = ReferenceHistory(), set()
    for record in history:
        if not isinstance(record, dict):
            raise DataError("Hang Seng returned an invalid total-return row")
        try:
            day = date.fromisoformat(record["date"])
        except (KeyError, TypeError, ValueError) as error:
            raise DataError("Hang Seng returned an invalid total-return date") from error
        if day in seen:
            raise DataError("Hang Seng returned duplicate total-return dates")
        seen.add(day)
        price = decimal_or_none(record.get("close"))
        if price is None or price <= 0:
            raise DataError("Hang Seng returned an invalid total-return close")
        # The public endpoint always supplies its rolling window, with no date
        # parameters. Validate every row, then select the requested sessions.
        if not start <= day <= end:
            continue
        quote_at = datetime.combine(
            day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if quote_at > observed_at:
            raise DataError("Hang Seng returned an unfinished total-return close")
        result.observations.append(
            Observation(
                symbol=spec.quote_code,
                trade_date=day,
                source="hsi",
                price=price,
                eps_ttm=None,
                pe_ttm=None,
                observed_at=observed_at,
                quote_at=quote_at,
                currency=spec.currency,
                price_basis="gross_total_return",
                eps_method="not_applicable",
                artifact_path=path,
                raw={
                    "indexCode": item["indexCode"],
                    "indexName": item["indexName"],
                    **record,
                },
            )
        )
    first = min(seen)
    if start < first:
        result.failures.append(
            {
                "stage": "total_returns",
                "start": start,
                "end": min(end, first - timedelta(days=1)),
                "category": "unsupported_historical_coverage",
                "error": "Hang Seng public history supplies a rolling window of about five years",
                "available_start": first,
                "available_end": max(seen),
                "artifact_path": path,
            }
        )
    result.observations.sort(key=lambda row: row.trade_date)
    return result


class HSITotalReturnHistory:
    source_name = "hsi"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        identity = MAPPINGS.get(spec.provider_code)
        if (
            not identity
            or spec.quote_code != identity[0]
            or spec.market != "HK"
            or spec.currency != "HKD"
            or spec.timezone != "Asia/Hong_Kong"
            or start > end
        ):
            raise DataError("Hang Seng total returns require an explicitly mapped HKD gross index")
        params = {
            "data": "valueHistoryPub",
            "indexCodes": spec.provider_code,
            "language": "en-hk",
        }
        path = ""
        try:
            with httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                transport=httpx.HTTPTransport(retries=2),
            ) as client:
                response = client.get(URL, params=params)
            observed_at = datetime.now(UTC)
            path = self.archive.save_json(
                "hsi_total_returns",
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
                payload = response.json()
            except ValueError as error:
                raise DataError("Hang Seng total-return response is not valid JSON") from error
            return parse_history(payload, spec, start, end, observed_at, path)
        except (httpx.HTTPError, DataError) as error:
            return ReferenceHistory(
                failures=[
                    {
                        "stage": "total_returns",
                        "start": start,
                        "end": end,
                        "category": "invalid_data"
                        if isinstance(error, DataError)
                        else "source_failure",
                        "error": str(error),
                        "artifact_path": path,
                    }
                ]
            )
