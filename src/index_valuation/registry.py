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
        for field in ("inception_date", "history_start_date"):
            if isinstance(values.get(field), str):
                values[field] = date.fromisoformat(values[field])
        spec = IndexSpec(key=key, **values)
        if spec.historical_price_source not in {"futu", "eastmoney"}:
            raise DataError("Unsupported historical_price_source")
        if spec.inception_date is not None and type(spec.inception_date) is not date:
            raise DataError("inception_date must be a date")
        if spec.history_start_date is not None and type(spec.history_start_date) is not date:
            raise DataError("history_start_date must be a date")
        if not re.fullmatch(r"[a-z0-9_]+", key):
            raise DataError(f"Invalid index key: {key}")
        if not re.fullmatch(r"[A-Za-z0-9]+", spec.provider_code):
            raise DataError("Invalid provider index code")
        if not re.fullmatch(r"(?:SH|SZ|HK)\.[0-9]{6}", spec.quote_code):
            raise DataError("Unsupported index quote code")
        if spec.currency != {"CN": "CNY", "HK": "HKD"}[spec.market]:
            raise DataError("Index currency must match its market; no FX conversion is applied")
        if spec.reference_history_source not in {"csindex", "cnindex", "none"}:
            raise DataError("Unsupported reference_history_source")
        if spec.market == "HK" and spec.reference_history_source != "none":
            raise DataError("Mainland reference adapters cannot be used for HK indices")
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
