"""Provider-independent records. Monetary values and weights always use Decimal."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


class DataError(ValueError):
    """Data cannot be used without changing its meaning or hiding a gap."""


class SourceError(RuntimeError):
    """An upstream source is unavailable or has denied a read request."""


def decimal_or_none(value: object) -> Decimal | None:
    if value is None or str(value).strip() in {"", "N/A", "--", "None"}:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


@dataclass(frozen=True)
class IndexSpec:
    key: str
    name: str
    provider_code: str
    quote_code: str
    expected_constituents: int | None = None
    currency: str = "CNY"
    timezone: str = "Asia/Shanghai"
    close_time: str = "15:00"
    max_weight_age_days: int = 45
    band_min_observations: int = 252
    band_window_days: int = 1826
    band_multiples: tuple[Decimal, ...] = (
        Decimal(8),
        Decimal(10),
        Decimal(12),
        Decimal(15),
        Decimal(18),
        Decimal(20),
        Decimal(25),
    )
    inception_date: date | None = None
    historical_price_source: str = "futu"
    history_start_date: date | None = None
    reference_history_source: str = "csindex"
    additional_history_sources: tuple[str, ...] = ()
    price_sources: tuple[str, ...] = ()
    official_price_source: str | None = None
    eastmoney_secid: str | None = None
    yahoo_symbol: str | None = None
    yahoo_proxy_symbol: str | None = None
    google_symbol: str | None = None
    nasdaq_symbol: str | None = None
    fred_series: str | None = None
    tracking_etfs: tuple[str, ...] = ()
    description: str = ""
    futu_supported: bool = True
    valuation_source: str = "futu"
    danjuan_symbol: str | None = None

    @property
    def market(self) -> str:
        exchange = self.quote_code.split(".", 1)[0]
        return {"SH": "CN", "SZ": "CN", "CSI": "CN", "HK": "HK", "US": "US"}[exchange]


@dataclass(frozen=True)
class Constituent:
    symbol: str
    name: str
    weight_percent: Decimal


@dataclass(frozen=True)
class WeightSnapshot:
    as_of: date
    constituents: tuple[Constituent, ...]
    source: str
    source_url: str
    sha256: str
    artifact_path: str

    @property
    def total_percent(self) -> Decimal:
        return sum((item.weight_percent for item in self.constituents), Decimal(0))

    def weights(self) -> dict[str, Decimal]:
        return {item.symbol: item.weight_percent / self.total_percent for item in self.constituents}


@dataclass(frozen=True)
class Observation:
    symbol: str
    trade_date: date
    source: str
    price: Decimal
    eps_ttm: Decimal | None
    pe_ttm: Decimal | None
    observed_at: datetime
    quote_at: datetime
    currency: str = "CNY"
    price_basis: str = "unadjusted_close"
    eps_method: str = "price_div_pe_ttm"
    reported_eps: Decimal | None = None
    artifact_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceValuation:
    """An upstream valuation, distinct from our constituent-based calculation."""

    source: str
    symbol: str
    trade_date: date
    metric: str
    basis: str
    value: Decimal
    observed_at: datetime
    artifact_path: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReferenceHistory:
    observations: list[Observation] = field(default_factory=list)
    valuations: list[SourceValuation] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Fundamental:
    """Daily earnings implied by documented company PE, with input provenance."""

    symbol: str
    trade_date: date
    method: str
    price: Decimal | None
    pe_ttm: Decimal | None
    eps_ttm: Decimal | None
    price_source: str | None
    pe_source: str
    status: str
    price_relative_error: Decimal | None
    pe_relative_error: Decimal | None
    eps_relative_error: Decimal | None
    price_artifact: str | None
    pe_artifact: str | None
    reference_artifact: str | None


@dataclass(frozen=True)
class Check:
    symbol: str
    metric: str
    status: str
    primary: Decimal | None = None
    secondary: Decimal | None = None
    relative_error: Decimal | None = None
    detail: str = ""


@dataclass(frozen=True)
class Calculation:
    status: str
    pe_ttm: Decimal | None
    earnings_yield: Decimal | None
    index_eps_ttm: Decimal | None
    coverage_weight: Decimal
    issues: tuple[str, ...]
    bands: dict[str, Any]
