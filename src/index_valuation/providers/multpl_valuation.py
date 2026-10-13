"""Dated current S&P 500 trailing reported PE; monthly history is not daily PE."""

from datetime import UTC, datetime, time
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, ReferenceHistory, SourceValuation, decimal_or_none

URL = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
BASIS = "multpl_reported_ttm_current_estimate"


class FirstRow(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = self.in_cell = False
        self.cells = []
        self.current = ""

    def handle_starttag(self, tag, attrs):
        if tag == "table" and dict(attrs).get("id") == "datatable":
            self.in_table = True
        if tag == "td" and self.in_table and len(self.cells) < 2:
            self.in_cell = True
            self.current = ""

    def handle_data(self, value):
        if self.in_cell:
            self.current += value

    def handle_endtag(self, tag):
        if tag == "td" and self.in_cell:
            self.cells.append(self.current.strip())
            self.in_cell = False
        if tag == "table":
            self.in_table = False


def parse_current(text, spec, start, end, observed_at, path):
    if spec.quote_code != "US..SPX" or "S&amp;P 500 PE Ratio" not in text:
        raise DataError("Multpl returned a different index or metric")
    parser = FirstRow()
    parser.feed(text)
    if len(parser.cells) != 2:
        raise DataError("Multpl current estimate schema changed")
    day = datetime.strptime(parser.cells[0], "%b %d, %Y").date()
    value = decimal_or_none(parser.cells[1].replace("†", "").strip())
    if value is None or value <= 0:
        raise DataError("Invalid Multpl trailing PE estimate")
    close = datetime.combine(day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone))
    if not start <= day <= end or close > observed_at:
        return []
    return [
        SourceValuation(
            "multpl",
            spec.quote_code,
            day,
            "pe_ttm",
            BASIS,
            value,
            observed_at,
            path,
            raw={
                "source_url": URL,
                "frequency": "current_snapshot_only",
                "method": "trailing twelve month as-reported earnings; current PE estimated",
                "monthly_history": "not imported: monthly values are not same-day closing PE",
            },
        )
    ]


class MultplValuation:
    source_name = "multpl"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if spec.quote_code != "US..SPX":
            raise DataError("Multpl only supports S&P 500 trailing PE")
        response = httpx.get(URL, timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "multpl_valuation",
            {
                "observed_at": observed_at,
                "url": URL,
                "text": response.text,
            },
        )
        return ReferenceHistory(
            valuations=parse_current(
                response.text,
                spec,
                start,
                end,
                observed_at,
                path,
            )
        )
