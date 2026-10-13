"""Public headline only; no paid history downloads or access-challenge bypass."""

import re
from datetime import UTC, date, datetime, time
from html import unescape
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, ReferenceHistory, SourceValuation, decimal_or_none

PAGES = {
    "SP500": ("US..SPX", "S&P 500 PE Ratio", "57/sp-500-pe-ratio"),
    "NDX": ("US..NDX", "Nasdaq 100 PE Ratio", "6778/nasdaq-100-pe-ratio"),
}
BASIS = "gurufocus_published_pe_unconfirmed"


def parse_headline(headline, spec, start, end, observed_at, path):
    symbol, name, page = PAGES[spec.danjuan_symbol]
    if spec.quote_code != symbol:
        raise DataError("GuruFocus index identity mismatch")
    match = re.fullmatch(
        re.escape(name) + r"\s*:\s*([\d.]+)\s*\(As of (\d{4}-\d{2}-\d{2})\)",
        " ".join(headline.split()),
    )
    if match is None:
        raise DataError("GuruFocus dated headline schema changed")
    value, day = decimal_or_none(match[1]), date.fromisoformat(match[2])
    if value is None or value <= 0:
        raise DataError("Invalid GuruFocus PE")
    close = datetime.combine(day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone))
    if not start <= day <= end or close > observed_at:
        return []
    return [
        SourceValuation(
            "gurufocus",
            spec.quote_code,
            day,
            "pe",
            BASIS,
            value,
            observed_at,
            path,
            raw={
                "headline": headline,
                "frequency": "current_snapshot_only",
                "source_url": f"https://www.gurufocus.com/economic_indicators/{page}",
                "basis_status": "earnings_weight_and_loss_treatment_unconfirmed",
            },
        )
    ]


class GuruFocusValuation:
    source_name = "gurufocus"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if spec.danjuan_symbol not in PAGES:
            raise DataError("No GuruFocus index mapping")
        url = f"https://www.gurufocus.com/economic_indicators/{PAGES[spec.danjuan_symbol][2]}"
        response = httpx.get(url, timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"})
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "gurufocus_valuation",
            {
                "observed_at": observed_at,
                "url": url,
                "status": response.status_code,
                "text": response.text,
            },
        )
        response.raise_for_status()
        match = re.search(r"<h1\b[^>]*>(.*?)</h1>", response.text, flags=re.S | re.I)
        if match is None:
            raise DataError("GuruFocus has no dated public headline")
        headline = unescape(re.sub(r"<[^>]+>", " ", match[1]))
        return ReferenceHistory(
            valuations=parse_headline(
                headline,
                spec,
                start,
                end,
                observed_at,
                path,
            )
        )
