"""Published index PE TTM: sparse historical samples plus a dated daily snapshot.

The endpoint encodes date labels as Shanghai midnight, not exchange timestamps.
Never forward-fill them or treat the weekly history as daily observations.
"""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, ReferenceHistory, SourceValuation, decimal_or_none

BASE = "https://danjuanfunds.com/djapi/index_eva"
BASIS = "danjuan_published_pe_ttm"
MAPPINGS = {"SP500": "US..SPX", "NDX": "US..NDX", "CSIH30269": "CSI.H30269"}
METHOD_URL = "https://danjuanfunds.com/article/337.html"


def payload_data(payload):
    if payload.get("result_code") != 0 or not isinstance(payload.get("data"), dict):
        raise DataError("Danjuan did not return a successful index valuation response")
    return payload["data"]


def parse_history(payload, detail, spec, start, end, observed_at, path):
    metadata = payload_data(detail)
    if (
        MAPPINGS.get(spec.danjuan_symbol) != spec.quote_code
        or metadata.get("index_code") != spec.danjuan_symbol
    ):
        raise DataError("Danjuan index identity mismatch")
    items = payload_data(payload).get("index_eva_pe_growths")
    if not isinstance(items, list):
        raise DataError("Danjuan history schema changed")
    by_day = {}
    for item in [*items, metadata]:
        stamp = datetime.fromtimestamp(int(item["ts"]) / 1000, ZoneInfo("Asia/Shanghai"))
        if stamp.time() != time():
            raise DataError("Danjuan date label is not Shanghai midnight")
        day = stamp.date()
        if not start <= day <= end:
            continue
        close = datetime.combine(day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone))
        if close > observed_at:
            continue  # Do not pair intraday/stale publications with today's closing index.
        value = decimal_or_none(item.get("pe"))
        if value is None or value == 0:
            continue
        if day in by_day and by_day[day].value != value:
            raise DataError("Danjuan history and daily snapshot disagree on the same date")
        by_day[day] = SourceValuation(
            "danjuan",
            spec.quote_code,
            day,
            "pe_ttm",
            BASIS,
            value,
            observed_at,
            path,
            raw={
                "item": item,
                "index_code": metadata["index_code"],
                "date_encoding": "Asia/Shanghai midnight date label, not a quote instant",
                "frequency": "weekly_history_and_daily_snapshots",
                "method_url": METHOD_URL,
                "basis_status": "provider_declared_ttm_weight_and_loss_treatment_unconfirmed",
            },
        )
    return sorted(by_day.values(), key=lambda row: row.trade_date)


class DanjuanHistory:
    source_name = "danjuan"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if MAPPINGS.get(spec.danjuan_symbol) != spec.quote_code:
            raise DataError("Danjuan requires an explicit supported index mapping")
        history_url = f"{BASE}/pe_history/{spec.danjuan_symbol}"
        detail_url = f"{BASE}/detail/{spec.danjuan_symbol}"
        params = {"day": "all", "type": "1"}
        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"}) as client:
            history = client.get(history_url, params=params)
            history.raise_for_status()
            detail = client.get(detail_url)
            detail.raise_for_status()
        observed_at = datetime.now(UTC)
        payload, metadata = history.json(), detail.json()
        path = self.archive.save_json(
            "danjuan_history",
            {
                "observed_at": observed_at,
                "url": history_url,
                "params": params,
                "detail_url": detail_url,
                "payload": payload,
                "detail": metadata,
            },
        )
        return ReferenceHistory(
            valuations=parse_history(
                payload,
                metadata,
                spec,
                start,
                end,
                observed_at,
                path,
            )
        )
