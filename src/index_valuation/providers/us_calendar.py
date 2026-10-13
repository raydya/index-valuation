"""US equity sessions, including exceptional closures; never mainland holidays."""

from datetime import UTC, datetime
from importlib.metadata import version

import pandas_market_calendars as mcal

from index_valuation.domain import DataError


class USCalendar:
    source_name = "pandas_market_calendars"

    def __init__(self, archive):
        self.archive = archive

    def trading_days(self, start, end, *, market):
        if market != "US" or start > end:
            raise DataError("Invalid US equity calendar request")
        schedule = mcal.get_calendar("NYSE").schedule(start_date=start, end_date=end)
        rows = [
            {
                "date": day.date(),
                "open": row.market_open.isoformat(),
                "close": row.market_close.isoformat(),
            }
            for day, row in schedule.iterrows()
        ]
        path = self.archive.save_json(
            "us_calendar",
            {
                "observed_at": datetime.now(UTC),
                "market": market,
                "calendar": "NYSE",
                "package": self.source_name,
                "version": version("pandas_market_calendars"),
                "source_url": "https://www.nyse.com/trade/hours-calendars",
                "start": start,
                "end": end,
                "rows": rows,
                "note": "Versioned calendar rules, not a live exchange feed; prices cross-checked.",
            },
        )
        return {row["date"] for row in rows}, path
