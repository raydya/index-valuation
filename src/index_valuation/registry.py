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
            "fred",
            "danjuan",
            "multpl",
            "gurufocus",
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
        if spec.quote_code.startswith("CSI.") and (
            spec.futu_supported
            or spec.quote_code != f"CSI.{spec.provider_code}"
            or not spec.eastmoney_secid
        ):
            raise DataError("CSI namespace requires matching identity, explicit prices and no Futu")
        if spec.valuation_source not in {"futu", "csindex", "danjuan", "none"}:
            raise DataError("Unsupported valuation_source")
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
