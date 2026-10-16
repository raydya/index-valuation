"""User-authorized ETF PE proxies, read from Yahoo's public page snapshots."""

import json
from datetime import UTC, datetime, time
from decimal import Decimal
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, ReferenceHistory, SourceValuation, decimal_or_none

MAPPINGS = {"SPY": "US..SPX", "QQQ": "US..NDX", "VTI": "US.CRSPTM1"}
SOURCE = "yahooetf"
METRIC = "pe_ttm_proxy"
DATE_BASIS = "etf_regular_market_session;pe_effective_date_not_provided"


def basis_for(symbol):
    return f"yahoo_etf_{symbol.lower()}_trailing_pe_proxy"


class _JSONScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.parts = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/json":
            self.parts = []

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.parts is not None:
            self.scripts.append("".join(self.parts))
            self.parts = None


def parse_snapshot(text, spec, start, end, observed_at, path):
    symbol = spec.yahoo_proxy_symbol
    if spec.market != "US" or MAPPINGS.get(symbol) != spec.quote_code:
        raise DataError("Yahoo ETF proxy requires a verified ETF-to-index mapping")
    parser = _JSONScripts()
    parser.feed(text)
    candidates = []
    for script in parser.scripts:
        try:
            envelope = json.loads(script, parse_float=Decimal)
            if not isinstance(envelope, dict) or not isinstance(envelope.get("body"), str):
                continue
            payload = json.loads(envelope["body"], parse_float=Decimal)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        summary = payload.get("quoteSummary")
        if not isinstance(summary, dict) or summary.get("error"):
            continue
        for item in summary.get("result") or []:
            if isinstance(item, dict) and "price" in item and "summaryDetail" in item:
                candidates.append(item)
    if len(candidates) != 1:
        raise DataError("Yahoo page must contain one identifiable ETF valuation snapshot")
    item = candidates[0]
    quote, detail = item["price"], item["summaryDetail"]
    if not isinstance(quote, dict) or not isinstance(detail, dict):
        raise DataError("Yahoo ETF valuation snapshot schema changed")
    if (
        quote.get("symbol") != symbol
        or quote.get("quoteType") != "ETF"
        or quote.get("currency") != spec.currency
    ):
        raise DataError("Yahoo ETF identity, instrument type or currency mismatch")
    timestamp = quote.get("regularMarketTime")
    if type(timestamp) is not int or timestamp <= 0:
        raise DataError("Yahoo ETF snapshot has no valid regular-market timestamp")
    zone = ZoneInfo(spec.timezone)
    try:
        quote_at = datetime.fromtimestamp(timestamp, zone)
    except (OverflowError, OSError, ValueError) as error:
        raise DataError("Yahoo ETF snapshot has an invalid timestamp") from error
    day = quote_at.date()
    close = datetime.combine(day, time.fromisoformat(spec.close_time), zone)
    # Only the quote's own completed session is eligible. Never stamp the PE with
    # the request date or reuse today's snapshot for historical backfill dates.
    if quote_at > observed_at or close > observed_at or quote.get("marketState") == "REGULAR":
        return []
    if not start <= day <= end:
        return []
    trailing = detail.get("trailingPE") or {}
    if not isinstance(trailing, dict):
        raise DataError("Yahoo trailing PE schema changed")
    value = decimal_or_none(trailing.get("raw"))
    if value is None or value <= 0:
        return []
    return [
        SourceValuation(
            SOURCE,
            spec.quote_code,
            day,
            METRIC,
            basis_for(symbol),
            value,
            observed_at,
            path,
            raw={
                "proxy_symbol": symbol,
                "proxy_instrument_type": "ETF",
                "target_index": spec.quote_code,
                "field": "summaryDetail.trailingPE.raw",
                "frequency": "current_snapshot_only",
                "quote_at": quote_at.isoformat(),
                "date_basis": DATE_BASIS,
                "pe_effective_date": None,
                "basis_status": "etf_proxy_not_official_index_pe",
                "source_url": f"https://finance.yahoo.com/quote/{symbol}/",
            },
        )
    ]


class YahooETFValuation:
    source_name = SOURCE

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if MAPPINGS.get(spec.yahoo_proxy_symbol) != spec.quote_code:
            raise DataError("Yahoo ETF proxy requires a verified ETF-to-index mapping")
        url = f"https://finance.yahoo.com/quote/{spec.yahoo_proxy_symbol}/"
        response = httpx.get(
            url, timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        )
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "yahoo_etf_valuation",
            {
                "observed_at": observed_at,
                "url": url,
                "status": response.status_code,
                "text": response.text,
            },
        )
        response.raise_for_status()
        return ReferenceHistory(
            valuations=parse_snapshot(response.text, spec, start, end, observed_at, path)
        )
