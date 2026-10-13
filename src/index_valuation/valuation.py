"""Pure portfolio valuation; no network, SQL, or index-specific branching."""

from datetime import date
from decimal import Decimal, localcontext

from index_valuation.domain import Calculation, Check, IndexSpec, Observation, WeightSnapshot

METHOD = "published_weight_ttm_v1"


def quantile(values: list[Decimal], probability: Decimal) -> Decimal:
    if not values or not Decimal(0) <= probability <= Decimal(1):
        raise ValueError("Quantile requires values and a probability between zero and one")
    values = sorted(values)
    position = Decimal(len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def calculate(
    spec: IndexSpec,
    trade_date: date,
    snapshot: WeightSnapshot,
    primary: dict[str, Observation],
    index_price: Decimal | None,
    checks: list[Check],
    history: list[Decimal],
) -> Calculation:
    issues = []
    blocked = []
    if any(not member.weight_percent.is_finite() for member in snapshot.constituents):
        return Calculation("blocked", None, None, None, Decimal(0), ("invalid_weights",), {})
    age = (trade_date - snapshot.as_of).days
    if age < 0:
        blocked.append("future_weights")
    elif age > spec.max_weight_age_days:
        blocked.append("weights_too_old")
    elif age > 0:
        issues.append("dated_weights_estimate")
    symbols = {member.symbol for member in snapshot.constituents}
    if len(symbols) != spec.expected_constituents or len(symbols) != len(snapshot.constituents):
        blocked.append("invalid_constituent_count")
    if snapshot.total_percent <= 0 or any(
        item.weight_percent <= 0 for item in snapshot.constituents
    ):
        blocked.append("invalid_weights")
    if abs(snapshot.total_percent - 100) > Decimal("0.2"):
        blocked.append("invalid_weight_sum")
    weights = snapshot.weights() if snapshot.total_percent > 0 else {}
    coverage = Decimal(0)
    earnings_yield = Decimal(0)
    with localcontext() as ctx:
        ctx.prec = 40
        for symbol in symbols:
            row = primary.get(symbol)
            if row is None or row.eps_ttm is None or not row.eps_ttm.is_finite():
                blocked.append(f"missing_primary:{symbol}")
                continue
            if row.trade_date != trade_date or row.currency != spec.currency:
                blocked.append(f"incompatible_primary:{symbol}")
                continue
            if not row.price.is_finite() or row.price <= 0 or row.price_basis != "unadjusted_close":
                blocked.append(f"invalid_price:{symbol}")
                continue
            coverage += weights[symbol]
            # A loss is a negative earnings contribution, never silently dropped.
            earnings_yield += weights[symbol] * row.eps_ttm / row.price
    check_map = {(item.symbol, item.metric): item for item in checks}
    for symbol in symbols:
        for metric in ("price", "eps_ttm"):
            item = check_map.get((symbol, metric))
            if item is None or item.status not in {"pass", "warning"}:
                blocked.append(f"unverified:{symbol}:{metric}")
    if any(item.status in {"error", "missing"} for item in checks):
        blocked.append("cross_source_check_failed")
    if any(item.status == "warning" for item in checks):
        issues.append("cross_source_difference_1_to_5_percent")
    if index_price is None or not index_price.is_finite() or index_price <= 0:
        blocked.append("missing_index_price")
    if blocked:
        return Calculation("blocked", None, None, None, coverage, tuple(issues + blocked), {})
    index_eps = index_price * earnings_yield
    if earnings_yield <= 0:
        return Calculation(
            "non_positive_earnings",
            None,
            earnings_yield,
            index_eps,
            coverage,
            tuple(issues + ["aggregate_earnings_not_positive"]),
            {},
        )
    pe = Decimal(1) / earnings_yield
    positive_history = [value for value in history if value.is_finite() and value > 0]
    bands = {
        "fixed_multiples": {
            str(multiple): index_eps * multiple for multiple in spec.band_multiples
        },
        "history_observations": len(positive_history),
        "history_required": spec.band_min_observations,
        "historical_quantiles": None,
        "historical_band_points": None,
    }
    if len(positive_history) >= spec.band_min_observations:
        quantiles = {
            str(p): quantile(positive_history, p)
            for p in (
                Decimal("0.1"),
                Decimal("0.25"),
                Decimal("0.5"),
                Decimal("0.75"),
                Decimal("0.9"),
            )
        }
        bands["historical_quantiles"] = quantiles
        bands["historical_band_points"] = {
            key: value * index_eps for key, value in quantiles.items()
        }
    else:
        issues.append("insufficient_band_history")
    status = "estimated" if age > 0 else "verified"
    if any(item.status == "warning" for item in checks):
        status = "review"
    return Calculation(status, pe, earnings_yield, index_eps, coverage, tuple(issues), bands)
