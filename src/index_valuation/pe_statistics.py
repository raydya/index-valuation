"""Past-only PE sampling shared by charts and the current valuation summary."""

from bisect import bisect_left, bisect_right, insort
from collections import deque
from decimal import Decimal

from index_valuation.valuation import quantile_sorted


def years_before(day, years):
    if not years:
        return day.min
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


class PEHistory:
    """Advance in date order, read statistics, then observe the current day's PE.

    Daily samples enter after the day's statistics. Weekly samples enter only
    when the next ISO week begins, using the last usable PE of the prior week.
    """

    def __init__(self, *, years, minimum, weekly=False):
        self.years = years
        self.minimum = minimum
        self.weekly = weekly
        self.values = []
        self.observations = deque()
        self.total = Decimal(0)
        self.pending_week = None

    def _append(self, day, pe):
        insort(self.values, pe)
        self.observations.append((day, pe))
        self.total += pe

    def advance(self, day):
        """Prepare the inclusive lower boundary before reading this day's rank."""
        if (
            self.weekly
            and self.pending_week
            and self.pending_week[0].isocalendar()[:2] != day.isocalendar()[:2]
        ):
            self._append(*self.pending_week)
            self.pending_week = None
        lower = years_before(day, self.years)
        while self.observations and self.observations[0][0] < lower:
            _, old = self.observations.popleft()
            self.values.pop(bisect_left(self.values, old))
            self.total -= old
        return lower

    def observe(self, day, pe, usable=True):
        if pe is not None and pe > 0 and usable:
            if self.weekly:
                self.pending_week = (day, pe)
            else:
                self._append(day, pe)

    @property
    def count(self):
        return len(self.values)

    def percentile(self, pe, usable=True):
        if self.count < self.minimum or pe is None or pe <= 0 or not usable:
            return None
        return (
            Decimal(bisect_left(self.values, pe) + bisect_right(self.values, pe))
            / 2
            / self.count
            * 100
        )

    def summary(self, pe, usable=True, *, probabilities=()):
        enough = self.count >= self.minimum
        return {
            "mean_pe": self.total / self.count if enough else None,
            "quantile_pe": {str(q): quantile_sorted(self.values, q) for q in probabilities}
            if enough
            else {},
            "percentile": self.percentile(pe, usable),
            "history_count": self.count,
            "history_start": self.observations[0][0] if self.observations else None,
            "history_end": self.observations[-1][0] if self.observations else None,
        }
