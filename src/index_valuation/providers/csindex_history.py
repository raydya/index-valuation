"""CSI published index history for independent checks, not constituent reconstruction."""

import logging
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import (
    DataError,
    IndexSpec,
    Observation,
    ReferenceHistory,
    SourceError,
    SourceValuation,
    decimal_or_none,
)

URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
log = logging.getLogger(__name__)


def parse_history_row(row, spec, observed_at, path) -> tuple[Observation, SourceValuation | None]:
    if row["indexCode"] != spec.provider_code:
        raise DataError("CSI history returned a different index")
    day = datetime.strptime(row["tradeDate"], "%Y%m%d").date()
    quote_at = datetime.combine(
        day, clock_time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
    )
    price, pe = decimal_or_none(row.get("close")), decimal_or_none(row.get("peg"))
    if price is None or price <= 0 or quote_at > observed_at:
        raise DataError("CSI history has an invalid or unfinished daily close")
    observation = Observation(
        symbol=spec.quote_code,
        trade_date=day,
        source="csindex",
        price=price,
        eps_ttm=None,
        pe_ttm=None,
        observed_at=observed_at,
        quote_at=quote_at,
        currency=spec.currency,
        eps_method="not_applicable",
        artifact_path=path,
        raw=row,
    )
    valuation = (
        None
        if pe is None or pe == 0
        else SourceValuation(
            source="csindex",
            symbol=spec.quote_code,
            trade_date=day,
            metric="pe_ttm",
            basis="csindex_published_rolling_pe",
            value=pe,
            observed_at=observed_at,
            artifact_path=path,
            raw=row,
        )
    )
    return observation, valuation


class CSIHistory:
    source_name = "csindex"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec: IndexSpec, start: date, end: date) -> ReferenceHistory:
        observations, valuations, seen = [], [], set()
        failures, consecutive_errors = [], 0
        transport = httpx.HTTPTransport(retries=2)
        try:
            with httpx.Client(transport=transport, timeout=self.timeout) as client:
                current = start
                while current <= end:
                    last = min(end, date(current.year, 12, 31))
                    # Query one extra day because endpoint boundary behavior varies.
                    # Only dates inside this chunk are admitted below.
                    params = {
                        "indexCode": spec.provider_code,
                        "startDate": current.strftime("%Y%m%d"),
                        "endDate": (last + timedelta(days=1)).strftime("%Y%m%d"),
                    }
                    try:
                        response = client.get(URL, params=params)
                        response.raise_for_status()
                    except httpx.HTTPError as error:
                        failures.append({"start": current, "end": last, "error": str(error)})
                        consecutive_errors += 1
                        log.warning("CSI history %s %s–%s: %s", spec.key, current, last, error)
                        current = last + timedelta(days=1)
                        if consecutive_errors >= 3:
                            if current <= end:
                                failures.append(
                                    {
                                        "start": current,
                                        "end": end,
                                        "error": "not_attempted_after_repeated_failures",
                                    }
                                )
                            break
                        continue
                    consecutive_errors = 0
                    observed_at = datetime.now(UTC)
                    data = response.json()
                    path = self.archive.save_json(
                        "csindex_history",
                        {
                            "observed_at": observed_at,
                            "url": URL,
                            "params": params,
                            "data": data,
                        },
                    )
                    if str(data.get("code")) != "200" or not isinstance(data.get("data"), list):
                        raise SourceError("CSI historical endpoint returned an invalid response")
                    for row in data["data"]:
                        # Filter inclusive/exclusive boundary overlap before parsing a
                        # potentially unfinished extra day's close.
                        day = datetime.strptime(row["tradeDate"], "%Y%m%d").date()
                        if not current <= day <= last:
                            continue
                        observation, valuation = parse_history_row(row, spec, observed_at, path)
                        if day in seen:
                            raise DataError("CSI returned duplicate historical dates")
                        seen.add(day)
                        observations.append(observation)
                        if valuation:
                            valuations.append(valuation)
                    log.info("CSI history %s: completed %s", spec.key, last)
                    current = last + timedelta(days=1)
        except httpx.HTTPError as error:
            raise SourceError(f"CSI historical download failed: {error}") from error
        return ReferenceHistory(observations, valuations, failures)
