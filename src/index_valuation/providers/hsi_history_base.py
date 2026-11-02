"""Shared official Hang Seng daily-close transport with explicit index-version identity."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, Observation, ReferenceHistory, decimal_or_none

URL = (
    "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy"
    "/v1/product-data/pub/indexes/metadata/v2"
)


@dataclass(frozen=True)
class HSIProfile:
    mappings: dict[str, tuple[str, str]]
    code_field: str
    kind: str
    price_basis: str
    stage: str
    archive_name: str
    provider_codes: dict[str, str] | None = None

    def identity(self, spec):
        code = getattr(spec, self.code_field)
        identity = self.mappings.get(code)
        if (
            not identity
            or spec.quote_code != identity[0]
            or spec.market != "HK"
            or spec.currency != "HKD"
            or spec.timezone != "Asia/Hong_Kong"
            or (
                self.provider_codes is not None
                and self.provider_codes.get(code) != spec.provider_code
            )
        ):
            raise DataError(f"Hang Seng {self.kind} requires an explicitly mapped HKD index")
        return code, identity


def parse_index_history(payload, spec, start, end, observed_at, path, profile):
    code, identity = profile.identity(spec)
    kind = profile.kind
    if start > end:
        raise DataError("Hang Seng history requires an ordered date range")
    if not isinstance(payload, dict) or payload.get("code") not in {"0", 0}:
        raise DataError(f"Hang Seng did not return successful {kind} history")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise DataError(f"Hang Seng {kind} identity envelope changed")
    item = data[0]
    if item.get("indexCode") != code or item.get("indexName") != identity[1]:
        raise DataError(f"Hang Seng returned a different {kind} index")
    history = item.get("valueHistory")
    if not isinstance(history, list) or not history:
        raise DataError(f"Hang Seng returned no {kind} history")
    result, seen = ReferenceHistory(), set()
    for record in history:
        if not isinstance(record, dict):
            raise DataError(f"Hang Seng returned an invalid {kind} row")
        try:
            day = date.fromisoformat(record["date"])
        except (KeyError, TypeError, ValueError) as error:
            raise DataError(f"Hang Seng returned an invalid {kind} date") from error
        if day in seen:
            raise DataError(f"Hang Seng returned duplicate {kind} dates")
        seen.add(day)
        price = decimal_or_none(record.get("close"))
        if price is None or price <= 0:
            raise DataError(f"Hang Seng returned an invalid {kind} close")
        # The endpoint returns a rolling window, without date parameters.
        # Validate its rows, but admit only the requested closed sessions.
        if not start <= day <= end:
            continue
        quote_at = datetime.combine(
            day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if quote_at > observed_at:
            raise DataError(f"Hang Seng returned an unfinished {kind} close")
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
                price_basis=profile.price_basis,
                eps_method="not_applicable",
                artifact_path=path,
                raw={
                    **record,
                    "indexCode": item["indexCode"],
                    "indexName": item["indexName"],
                },
            )
        )
    first = min(seen)
    if start < first:
        result.failures.append(
            {
                "stage": profile.stage,
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


class HSIHistory:
    source_name = "hsi"
    profile: HSIProfile

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        code, _ = self.profile.identity(spec)
        if start > end:
            raise DataError("Hang Seng history requires an ordered date range")
        params = {"data": "valueHistoryPub", "indexCodes": code, "language": "en-hk"}
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
                self.profile.archive_name,
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
                raise DataError(
                    f"Hang Seng {self.profile.kind} response is not valid JSON"
                ) from error
            return parse_index_history(payload, spec, start, end, observed_at, path, self.profile)
        except (httpx.HTTPError, DataError) as error:
            return ReferenceHistory(
                failures=[
                    {
                        "stage": self.profile.stage,
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
