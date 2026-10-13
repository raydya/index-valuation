"""Compare like-for-like observations from independent sources."""

from decimal import Decimal

from index_valuation.domain import Check, Observation


def compare_value(
    symbol: str, metric: str, primary: Decimal | None, secondary: Decimal | None
) -> Check:
    if primary is None or secondary is None or not primary.is_finite() or not secondary.is_finite():
        return Check(symbol, metric, "missing", primary, secondary, detail="缺少可比数据")
    if primary == secondary:
        difference = Decimal(0)
    elif primary == 0:
        return Check(symbol, metric, "error", primary, secondary, detail="主源为零，核验源非零")
    else:
        difference = abs(primary - secondary) / abs(primary)
    status = "pass" if difference <= Decimal("0.01") else "warning"
    if difference > Decimal("0.05"):
        status = "error"
    return Check(symbol, metric, status, primary, secondary, difference)


def compare_observations(
    symbols: list[str],
    primary: dict[str, Observation],
    secondary: dict[str, Observation],
) -> list[Check]:
    checks = []
    for symbol in symbols:
        left, right = primary.get(symbol), secondary.get(symbol)
        if left is None or right is None:
            checks.extend(Check(symbol, metric, "missing") for metric in ("price", "eps_ttm"))
            continue
        if left.source == right.source:
            checks.append(Check(symbol, "independence", "error", detail="不能用同一来源交叉核验"))
            continue
        if (left.trade_date, left.currency, left.price_basis) != (
            right.trade_date,
            right.currency,
            right.price_basis,
        ):
            checks.append(Check(symbol, "alignment", "error", detail="日期、币种或复权口径不一致"))
            continue
        checks.append(compare_value(symbol, "price", left.price, right.price))
        checks.append(compare_value(symbol, "eps_ttm", left.eps_ttm, right.eps_ttm))
    return checks
