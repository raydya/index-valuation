"""Browser-only projections; persisted records and full exports retain their evidence."""

import json

from index_valuation.archive import json_default

# Shared by all three chart modes, the tooltip, and paginated history table.
# The selected observation and top-level method/return/line metadata stay complete.
CHART_SERIES_FIELDS = (
    "trade_date",
    "price",
    "pe",
    "implied_eps",
    "pb",
    "ps",
    "reference_pe",
    "price_source",
    "price_status",
    "pe_comparison_status",
    "phase",
    "percentile",
    "mean_pe",
    "mean_points",
    "fixed",
    "quantile_pe",
    "quantile_points",
)


def chart_json(dashboard):
    """Keep every day, null, and Decimal; remove only unused per-day metadata."""
    payload = {
        **dashboard,
        "series": [
            {key: point[key] for key in CHART_SERIES_FIELDS} for point in dashboard["series"]
        ],
    }
    return json.dumps(
        payload,
        default=json_default,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
