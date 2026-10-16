"""Rolling, past-only PE observation lines, independent of dated outcome research."""

from bisect import bisect_left, insort
from collections import deque
from decimal import Decimal

from index_valuation.domain import DataError, decimal_or_none
from index_valuation.valuation import quantile

VERSION = "rolling_pe_p10_v1"
HISTORY_YEARS = 5
PROBABILITY = Decimal("0.1")


def enrich_panic_lines(spec, points):
    """Return new dated points with same-method PE lines, never changing inputs.

    The caller supplies one index method's stored sequence. A complete window
    means five elapsed calendar years since its first usable post-launch PE and
    the configured minimum sample count, not proof of gap-free provider history.
    """
    # Imported here so the series builder can use this module during refresh.
    from index_valuation.index_series import method_for, valuation_profile, years_before

    ordered = sorted(points, key=lambda point: point["trade_date"])
    if len({point["trade_date"] for point in ordered}) != len(ordered):
        raise DataError("Duplicate dates cannot be used for rolling panic lines")
    profile = valuation_profile(spec)
    method = method_for(spec)
    source, basis = profile["source"], profile["basis"]
    weekly = profile["history_frequency"] == "weekly"
    history, queue, enriched = [], deque(), []
    total = Decimal(0)
    first_usable = pending_week = None

    for point in ordered:
        day = point["trade_date"]
        if weekly and pending_week and pending_week[0].isocalendar()[:2] != day.isocalendar()[:2]:
            old_day, old_pe = pending_week
            insort(history, old_pe)
            queue.append((old_day, old_pe))
            total += old_pe
            pending_week = None
        lower = years_before(day, HISTORY_YEARS)
        while queue and queue[0][0] < lower:
            _, old_pe = queue.popleft()
            history.pop(bisect_left(history, old_pe))
            total -= old_pe

        pe = decimal_or_none(point.get("pe"))
        same_basis = point.get("pe_source") == source and point.get("pe_basis") == basis
        same_method = point.get("method", method) == method
        published = point.get("phase") == "published" and (
            spec.inception_date is None or day >= spec.inception_date
        )
        usable = bool(
            pe is not None
            and pe > 0
            and point.get("pe_usable", True)
            and same_basis
            and same_method
            and published
        )
        complete = bool(
            first_usable is not None
            and first_usable <= lower
            and len(history) >= spec.band_min_observations
        )
        threshold = quantile(history, PROBABILITY) if complete and spec.collection_enabled else None
        mean_pe = total / len(history) if complete else None
        if not spec.collection_enabled:
            status = "collection_paused"
        elif pe is None or pe <= 0:
            status = "missing_pe"
        elif not same_basis:
            status = "different_basis"
        elif not same_method:
            status = "different_method"
        elif not usable:
            status = "unusable_pe"
        elif not complete:
            status = "insufficient_history"
        else:
            status = "reference"
        compare = threshold is not None and usable
        eps = decimal_or_none(point.get("implied_eps"))
        enriched.append(
            {
                **point,
                "panic_line": {
                    "version": VERSION,
                    "as_of": day,
                    "method": method,
                    "source": source,
                    "basis": basis,
                    "status": status,
                    "threshold_pe": threshold,
                    "threshold_to_mean": threshold / mean_pe if threshold is not None else None,
                    "history_start": queue[0][0] if queue else None,
                    "history_end": queue[-1][0] if queue else None,
                    "history_count": len(history),
                    "history_complete": complete,
                    "history_years": HISTORY_YEARS,
                    "minimum_history": spec.band_min_observations,
                    "quantile": PROBABILITY,
                    "history_frequency": profile["history_frequency"],
                    "reference_points": eps * threshold
                    if compare and eps is not None and eps > 0
                    else None,
                    "gap_pct": (pe / threshold - 1) * 100 if compare else None,
                    "pe": pe,
                },
            }
        )
        # Insert only after computing this day's line: today's observation must
        # never influence its own threshold, mean, comparison or sample count.
        if usable:
            if first_usable is None:
                first_usable = day
            if weekly:
                pending_week = (day, pe)
            else:
                insort(history, pe)
                queue.append((day, pe))
                total += pe
    return enriched
