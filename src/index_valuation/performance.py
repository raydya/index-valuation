"""Historical price returns, independent of PE coverage and valuation bands."""

from decimal import Decimal, localcontext


def price_return_summary(points, start, end):
    """Annualize the first/last observed closes inside the displayed date range.

    Dates are actual observations; do not fill missing prices, extrapolate a
    requested start, or skip a conflicting endpoint in search of a better value.
    """
    priced = sorted(
        (p for p in points if start <= p["trade_date"] <= end and p["price"] is not None),
        key=lambda p: p["trade_date"],
    )
    result = {
        "basis": "price_return_excluding_dividends",
        "annualized_return_pct": None,
        "period_return_pct": None,
        "start_date": None,
        "end_date": None,
        "start_price": None,
        "end_price": None,
        "start_source": None,
        "end_source": None,
        "elapsed_days": 0,
        "price_status": "missing",
        "includes_backcast": any(p.get("phase") == "backcast" for p in priced),
        "status": "insufficient_data",
    }
    if not priced:
        return result
    first, last = priced[0], priced[-1]
    days = (last["trade_date"] - first["trade_date"]).days
    result.update(
        start_date=first["trade_date"],
        end_date=last["trade_date"],
        start_price=first["price"],
        end_price=last["price"],
        start_source=first.get("price_source"),
        end_source=last.get("price_source"),
        elapsed_days=days,
    )
    if len(priced) < 2 or days <= 0:
        return result
    endpoints = (first, last)
    if any(not p["price"].is_finite() or p["price"] <= 0 for p in endpoints):
        result.update(status="invalid_price", price_status="error")
        return result
    if any(p.get("price_status") == "error" for p in endpoints):
        result.update(status="price_discrepancy", price_status="error")
        return result
    result["price_status"] = (
        "unverified"
        if any(p.get("price_status") not in {"pass", "warning"} for p in endpoints)
        else "warning"
        if any(p["price_status"] == "warning" for p in endpoints)
        else "pass"
    )
    with localcontext() as context:
        context.prec = 34
        ratio = last["price"] / first["price"]
        result.update(
            annualized_return_pct=(ratio ** (Decimal("365.25") / days) - 1) * 100,
            period_return_pct=(ratio - 1) * 100,
            status="ok",
        )
    return result
