"""User-authorized historical unadjusted prices; validate against another source."""

import time
from datetime import UTC, date, datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import DataError, Observation, SourceError, decimal_or_none

URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def security_id(spec, symbol):
    if symbol == spec.quote_code and spec.eastmoney_secid:
        return spec.eastmoney_secid
    exchange, ticker = symbol.split(".", 1)
    if exchange not in {"SH", "SZ"}:
        raise DataError("An explicit Eastmoney mapping is required for this security")
    return f"{1 if exchange == 'SH' else 0}.{ticker}"


def parse_prices(data, spec, symbol, start, end, observed_at, path) -> list[Observation]:
    market, ticker = security_id(spec, symbol).split(".")
    if data.get("code") != ticker or data.get("market") != int(market):
        raise DataError("Eastmoney returned the wrong security")
    rows, seen = [], set()
    for line in data.get("klines", []):
        fields = line.split(",")
        day = date.fromisoformat(fields[0])
        if not start <= day <= end:
            raise DataError("Eastmoney returned out-of-range dates")
        if day in seen:
            raise DataError("Eastmoney returned duplicate dates")
        seen.add(day)
        price = decimal_or_none(fields[2])
        quote_at = datetime.combine(
            day,
            clock_time.fromisoformat(spec.close_time),
            ZoneInfo(spec.timezone),
        )
        if price is None or price <= 0 or quote_at > observed_at:
            raise DataError("Eastmoney returned invalid or unfinished prices")
        rows.append(
            Observation(
                symbol=symbol,
                trade_date=day,
                source="eastmoney",
                price=price,
                eps_ttm=None,
                pe_ttm=None,
                observed_at=observed_at,
                quote_at=quote_at,
                currency=spec.currency,
                eps_method="not_provided",
                artifact_path=path,
                raw={"kline": line, "fqt": "0"},
            )
        )
    return rows


class EastmoneySource:
    source_name = "eastmoney"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout
        self.client = None
        self.last_request = 0.0

    def __enter__(self):
        self.client = httpx.Client(timeout=self.timeout, transport=httpx.HTTPTransport(retries=2))
        return self

    def __exit__(self, *_):
        if self.client:
            self.client.close()

    def index_history(self, spec, start, end) -> list[Observation]:
        return self.price_history(spec, spec.quote_code, start, end)

    def price_history(self, spec, symbol, start, end) -> list[Observation]:
        params = {
            "secid": security_id(spec, symbol),
            "klt": "101",
            "fqt": "0",
            "beg": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "lmt": "1000000",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        delay = 0.3 - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        try:
            response = self.client.get(URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as error:
            raise SourceError(f"Eastmoney {symbol}: {error}") from error
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "eastmoney_history",
            {
                "url": URL,
                "params": params,
                "observed_at": observed_at,
                "data": payload,
            },
        )
        if payload.get("rc") != 0 or not isinstance(payload.get("data"), dict):
            raise SourceError(f"Eastmoney {symbol}: no valid history response")
        return parse_prices(payload["data"], spec, symbol, start, end, observed_at, path)
