"""Index-specific configuration; algorithms never branch on an index name."""

import re
import tomllib
from datetime import date
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from index_valuation.domain import DataError, IndexSpec


def load_indices(path: Path = Path("indices.toml")) -> dict[str, IndexSpec]:
    with path.open("rb") as source:
        configs = tomllib.load(source).get("indices", {})
    result = {}
    for key, values in configs.items():
        values = dict(values)
        if "band_multiples" in values:
            values["band_multiples"] = tuple(Decimal(str(x)) for x in values["band_multiples"])
        if "tracking_etfs" in values:
            values["tracking_etfs"] = tuple(values["tracking_etfs"])
        if "additional_history_sources" in values:
            values["additional_history_sources"] = tuple(values["additional_history_sources"])
        if "price_sources" in values:
            values["price_sources"] = tuple(values["price_sources"])
        for field in ("inception_date", "history_start_date"):
            if isinstance(values.get(field), str):
                values[field] = date.fromisoformat(values[field])
        spec = IndexSpec(key=key, **values)
        if spec.historical_price_source not in {"futu", "eastmoney", "none"}:
            raise DataError("Unsupported historical_price_source")
        if spec.inception_date is not None and type(spec.inception_date) is not date:
            raise DataError("inception_date must be a date")
        if spec.history_start_date is not None and type(spec.history_start_date) is not date:
            raise DataError("history_start_date must be a date")
        if not re.fullmatch(r"[a-z0-9_]+", key):
            raise DataError(f"Invalid index key: {key}")
        if not re.fullmatch(r"[A-Za-z0-9]+", spec.provider_code):
            raise DataError("Invalid provider index code")
        if not re.fullmatch(
            r"(?:(?:SH|SZ|HK)\.[0-9]{6}|CSI\.[A-Z0-9]+|US\.\.?[A-Z0-9]+)", spec.quote_code
        ):
            raise DataError("Unsupported index quote code")
        if spec.currency != {"CN": "CNY", "HK": "HKD", "US": "USD"}[spec.market]:
            raise DataError("Index currency must match its market; no FX conversion is applied")
        supported_sources = {
            "csindex",
            "cnindex",
            "nasdaq",
            "yahoo",
            "google",
            "fred",
            "danjuan",
            "multpl",
            "gurufocus",
            "yahooetf",
            "futorest",
        }
        if spec.reference_history_source not in supported_sources | {"none"}:
            raise DataError("Unsupported reference_history_source")
        sources = [spec.reference_history_source, *spec.additional_history_sources]
        if len(set(sources)) != len(sources) or any(
            source not in supported_sources for source in spec.additional_history_sources
        ):
            raise DataError("Invalid or duplicate additional history sources")
        if spec.market != "CN" and {"csindex", "cnindex"}.intersection(sources):
            raise DataError("Mainland reference adapters cannot be used for overseas indices")
        if spec.market == "US" and spec.timezone != "America/New_York":
            raise DataError("US indices require America/New_York for DST-aware trading dates")
        if type(spec.futu_supported) is not bool:
            raise DataError("futu_supported must be a boolean")
        if type(spec.collection_enabled) is not bool or not isinstance(
            spec.collection_disabled_reason, str
        ):
            raise DataError("Collection policy must have a boolean flag and text reason")
        if not spec.collection_enabled and not spec.collection_disabled_reason.strip():
            raise DataError("A paused index collection requires an explicit reason")
        if spec.quote_code.startswith("CSI.") and (
            spec.futu_supported
            or spec.quote_code != f"CSI.{spec.provider_code}"
            or not spec.eastmoney_secid
        ):
            raise DataError("CSI namespace requires matching identity, explicit prices and no Futu")
        if spec.valuation_source not in {
            "futu",
            "futorest",
            "csindex",
            "danjuan",
            "yahooetf",
            "none",
        }:
            raise DataError("Unsupported valuation_source")
        if spec.price_sources or spec.official_price_source:
            if (
                not spec.price_sources
                or len(set(spec.price_sources)) != len(spec.price_sources)
                or not set(spec.price_sources) <= {"fred", "nasdaq", "yahoo", "google", "futorest"}
                or not set(spec.price_sources) <= set(sources)
                or spec.market != "US"
                or spec.official_price_source not in {None, "fred", "nasdaq"}
                or (
                    spec.official_price_source
                    and spec.price_sources[0] != spec.official_price_source
                )
            ):
                raise DataError("US price policy requires collected sources and a valid primary")
            if spec.official_price_source == "fred" and (spec.fred_series, spec.quote_code) not in {
                ("SP500", "US..SPX"),
                ("NASDAQ100", "US..NDX"),
            }:
                raise DataError("Official FRED series must match the index identity")
            if spec.official_price_source == "nasdaq" and (spec.nasdaq_symbol, spec.quote_code) != (
                "NDX",
                "US..NDX",
            ):
                raise DataError("Official Nasdaq series must match the index identity")
        if "futorest" in sources or spec.valuation_source == "futorest":
            reference_mapping = {
                "US..SPX": ("fred", "SP500"),
                "US..NDX": ("nasdaq", "NDX"),
            }
            mapping = reference_mapping.get(spec.futorest_symbol)
            if (
                spec.market != "US"
                or not mapping
                or spec.futorest_symbol != spec.quote_code
                or spec.futu_supported
                or spec.valuation_source != "futorest"
                or spec.reference_history_source != "futorest"
                or spec.price_sources != ("futorest", mapping[0])
                or set(sources) != set(spec.price_sources)
                or spec.official_price_source is not None
                or (spec.fred_series if mapping[0] == "fred" else spec.nasdaq_symbol) != mapping[1]
                or spec.eastmoney_secid is not None
                or spec.historical_price_source != "none"
            ):
                raise DataError(
                    "Futu REST requires a verified US index and matching price reference"
                )
        if "yahooetf" in sources or spec.valuation_source == "yahooetf":
            from index_valuation.providers.yahoo_etf_valuation import MAPPINGS

            if spec.market != "US" or MAPPINGS.get(spec.yahoo_proxy_symbol) != spec.quote_code:
                raise DataError("Yahoo ETF proxy must match the configured index")
            if (
                spec.valuation_source != "yahooetf"
                or "yahooetf" not in sources
                or not spec.price_sources
                or set(sources) != {*spec.price_sources, "yahooetf"}
                or (
                    spec.official_price_source
                    and spec.price_sources != (spec.official_price_source, "yahoo")
                )
                or (not spec.official_price_source and spec.price_sources != ("yahoo", "google"))
            ):
                raise DataError(
                    "ETF proxy requires official prices or cross-checked Yahoo/Google index prices"
                )
        if "google" in sources:
            from index_valuation.providers.google_history import MAPPINGS

            if (
                spec.market != "US"
                or MAPPINGS.get(spec.google_symbol, (None,))[0] != spec.quote_code
            ):
                raise DataError("Google prices require a verified price-index mapping")
        if spec.valuation_source == "csindex" and (spec.market != "CN" or "csindex" not in sources):
            raise DataError("CSI primary PE requires its mainland history source")
        if "danjuan" in sources or spec.valuation_source == "danjuan":
            from index_valuation.providers.danjuan_history import MAPPINGS

            if MAPPINGS.get(spec.danjuan_symbol) != spec.quote_code:
                raise DataError("Danjuan requires an explicit supported index mapping")
            if spec.valuation_source == "danjuan" and "danjuan" not in sources:
                raise DataError("The primary PE source must be collected")
        if "multpl" in sources and spec.danjuan_symbol != "SP500":
            raise DataError("Multpl currently supports only S&P 500 trailing PE")
        if "gurufocus" in sources and spec.danjuan_symbol not in {"SP500", "NDX"}:
            raise DataError("GuruFocus requires an explicit index mapping")
        if any(not re.fullmatch(r"[A-Z][A-Z0-9.]*", code) for code in spec.tracking_etfs):
            raise DataError("Invalid tracking ETF symbol")
        if spec.yahoo_symbol is not None and not re.fullmatch(r"\^[A-Z0-9]+", spec.yahoo_symbol):
            raise DataError("Yahoo mapping must identify an index, not an ETF")
        if "yahoo" in sources and (spec.market != "US" or not spec.yahoo_symbol):
            raise DataError("Yahoo prices require an explicit index mapping")
        if "nasdaq" in sources and (
            spec.market != "US"
            or not spec.nasdaq_symbol
            or not re.fullmatch(r"[A-Z0-9]+", spec.nasdaq_symbol)
        ):
            raise DataError("Nasdaq history requires an explicit US index mapping")
        if "fred" in sources and (
            spec.market != "US" or spec.fred_series not in {"SP500", "NASDAQ100"}
        ):
            raise DataError("FRED requires a supported daily closing index series")
        if spec.eastmoney_secid is not None and not re.fullmatch(
            r"[0-9]+\.[A-Za-z0-9]+", spec.eastmoney_secid
        ):
            raise DataError("Invalid Eastmoney security mapping")
        if spec.market == "HK" and spec.eastmoney_secid is None:
            raise DataError("HK indices require an explicit Eastmoney security mapping")
        if (
            spec.expected_constituents is not None and spec.expected_constituents < 1
        ) or spec.band_min_observations < 2:
            raise DataError("Invalid constituent or history count")
        if spec.max_weight_age_days < 0 or spec.band_window_days < 1:
            raise DataError("Invalid date window")
        if not spec.band_multiples or any(x <= 0 or not x.is_finite() for x in spec.band_multiples):
            raise DataError("Band multiples must be positive and finite")
        from datetime import time

        time.fromisoformat(spec.close_time)
        ZoneInfo(spec.timezone)
        result[key] = spec
    if not result:
        raise DataError("No indices configured")
    return result
