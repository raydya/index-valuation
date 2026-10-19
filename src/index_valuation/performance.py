"""Historical price and total returns, independent of valuation bands."""

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
    if any(
        p.get("price_status") == "error" and not p.get("price_usable", False) for p in endpoints
    ):
        result.update(status="price_discrepancy", price_status="error")
        return result
    result["price_status"] = (
        "error"
        if any(p.get("price_status") == "error" for p in endpoints)
        else "unverified"
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


def total_return_summary(spec, points, start, end, price_summary):
    """Annualize the mapped gross total-return index, preserving its real range.

    A total-return index already includes reinvested dividends. Never infer it
    from a dividend-yield snapshot, a price index, or observations after ``end``.
    """
    mapping = spec.total_return
    result = price_return_summary([], start, end)
    result.update(
        basis="gross_total_return",
        index_code=mapping.code if mapping else None,
        index_name=mapping.name if mapping else None,
        source=mapping.source if mapping else None,
        identity_url=mapping.identity_url if mapping else None,
        reason="",
        requested_start=start,
        requested_end=end,
        latest_available_date=None,
        aligned_with_price_return=False,
        starts_late=False,
        ends_early=False,
        coverage_status="missing",
        observation_count=0,
    )
    if not spec.collection_enabled:
        result.update(
            status="collection_paused",
            reason=spec.collection_disabled_reason or "该指数已暂停采集，暂无分红再投资收益。",
        )
        return result
    if mapping is None:
        result.update(
            status="not_configured",
            reason=spec.total_return_unavailable_reason or "尚未接入已核实的全收益指数数据。",
        )
        return result
    available = [p for p in points if p["trade_date"] <= end and p["price"] is not None]
    result["latest_available_date"] = max((p["trade_date"] for p in available), default=None)
    in_range = [p for p in available if p["trade_date"] >= start]
    # Full-return endpoints with material disagreement are not silently accepted
    # or skipped, including when a price-only policy marked them authoritative.
    checked = [
        {**p, "price_usable": False} if p.get("price_status") == "error" else p for p in in_range
    ]
    calculated = price_return_summary(checked, start, end)
    result.update(calculated, basis=mapping.basis, observation_count=len(in_range))
    first, last = result["start_date"], result["end_date"]
    price_first, price_last = price_summary.get("start_date"), price_summary.get("end_date")
    result["aligned_with_price_return"] = bool(
        first is not None and last is not None and first == price_first and last == price_last
    )
    result["starts_late"] = bool(first is not None and first > (price_first or start))
    result["ends_early"] = bool(last is not None and last < end)
    if first is not None:
        result["coverage_status"] = (
            "partial" if result["starts_late"] or result["ends_early"] else "available"
        )
    if result["status"] != "ok":
        result["reason"] = {
            "insufficient_data": (
                "所选区间至少需要两个不同日期的全收益点位。"
                if in_range
                else "所选区间暂无全收益点位，不能计算分红再投资年化。"
            ),
            "invalid_price": "全收益端点点位无效，暂不计算年化。",
            "price_discrepancy": "全收益端点存在超过5%的来源差异，暂不计算年化。",
        }[result["status"]]
        return result
    notes = []
    if result["starts_late"]:
        notes.append("全收益历史较短，按实际起点计算")
    if result["ends_early"]:
        notes.append("全收益数据尚未覆盖所选结束日")
    if price_first and price_last and not result["aligned_with_price_return"]:
        notes.append("与价格年化起止日期不同，不能直接相减")
    if result["price_status"] == "unverified":
        notes.append("端点为单源数据，尚未交叉核验")
    elif result["price_status"] == "warning":
        notes.append("端点存在1–5%的来源差异")
    if result["elapsed_days"] < 365:
        notes.append("不足1年，按实际天数折算")
    if result["includes_backcast"]:
        notes.append("含发布前回溯数据")
    result["reason"] = "；".join(notes)
    return result
