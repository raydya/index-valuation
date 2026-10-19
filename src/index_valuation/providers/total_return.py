"""Reuse price readers with explicit, separately stored total-return identities."""

from dataclasses import replace

from index_valuation.domain import DataError, ReferenceHistory, SourceError
from index_valuation.providers.baostock import BaoStockSource
from index_valuation.providers.cnindex_history import CNIndexHistory
from index_valuation.providers.eastmoney import EastmoneySource
from index_valuation.providers.futu import FutuSource
from index_valuation.providers.us_calendar import USCalendar


class TotalReturnSources:
    def __init__(self, settings, archive):
        self.settings, self.archive = settings, archive

    def futu(self):
        s = self.settings
        return FutuSource(self.archive, s.futu_host, s.futu_port, s.source_timeout)

    def trading_days(self, spec, start, end):
        if spec.market == "US":
            days, path = USCalendar(self.archive).trading_days(start, end, market="US")
            return days, {"source": "pandas_market_calendars", "artifact_path": path}
        if spec.market == "HK":
            with self.futu() as source:
                days, path = source.trading_days(start, end, market="HK")
            return days, {"source": "futu", "artifact_path": path}
        secondary = BaoStockSource(self.archive, self.settings.source_timeout)
        try:
            with secondary:
                days, path = secondary.trading_days(start, end)
            return days, {"source": "baostock", "artifact_path": path}
        except Exception as error:
            try:
                days, path = secondary.cached_trading_days(start, end)
                return days, {
                    "source": "baostock",
                    "artifact_path": path,
                    "cached": True,
                    "reason": str(error),
                }
            except (SourceError, DataError):
                with self.futu() as source:
                    days, path = source.trading_days(start, end, market="CN")
                return days, {"source": "futu", "artifact_path": path, "reason": str(error)}

    def history(self, spec, source, symbol, start, end):
        tr = spec.total_return
        if tr is None:
            raise DataError("No configured total-return index")
        # This temporary spec is only passed to a price reader; it is never
        # registered as another dashboard or fed to the PE calculation.
        quote_code = (
            symbol
            if source == "futu"
            else ("CSI." if spec.market == "CN" else "US." if spec.market == "US" else "HK.")
            + tr.code
        )
        query = replace(
            spec,
            name=tr.name,
            provider_code=tr.code,
            quote_code=quote_code,
            total_return=None,
            valuation_source="none",
            nasdaq_symbol=symbol,
            fred_series=symbol,
            eastmoney_secid=symbol if source == "eastmoney" else None,
        )
        if source == "futu":
            with self.futu() as reader:
                batch = ReferenceHistory(reader.index_history(query, start, end))
        elif source == "eastmoney":
            with EastmoneySource(self.archive, self.settings.source_timeout) as reader:
                batch = ReferenceHistory(reader.index_history(query, start, end))
        else:
            from index_valuation.providers.csindex_returns import CSITotalReturnHistory
            from index_valuation.providers.hsi_returns import HSITotalReturnHistory
            from index_valuation.providers.nasdaq_returns import NasdaqTotalReturnHistory

            readers = {
                "csindex": CSITotalReturnHistory,
                "cnindex": CNIndexHistory,
                "nasdaq": NasdaqTotalReturnHistory,
                "hsi": HSITotalReturnHistory,
            }
            if source not in readers:
                raise DataError("Unsupported total-return source")
            if source in {"csindex", "cnindex", "hsi"}:
                query = replace(query, provider_code=symbol)
            batch = readers[source](self.archive, self.settings.source_timeout).index_history(
                query, start, end
            )
        if any(row.symbol != query.quote_code for row in batch.observations):
            raise DataError("Total-return reader returned a different instrument")
        # Keep the provider response and canonical identity together. These
        # levels go exclusively to total_return_observations.
        batch.observations = [replace(row, price_basis=tr.basis) for row in batch.observations]
        return batch
