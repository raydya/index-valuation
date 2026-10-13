from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from index_valuation.domain import DataError, IndexSpec
from index_valuation.providers.baostock import parse_daily
from index_valuation.providers.futu import parse_snapshot

SPEC = IndexSpec("test", "Test", "000300", "SH.000300", 300)
DAY = date(2026, 10, 12)
OBSERVED_AT = datetime(2026, 10, 12, 13, tzinfo=UTC)


def futu_row(**changes):
    return {
        "code": "SH.600519",
        "update_time": "2026-10-12 15:00:00",
        "last_price": 100,
        "equity_valid": True,
        "pe_ttm_ratio": 20,
        "earning_per_share": 4,
        **changes,
    }


def test_futu_ttm_eps_is_explicitly_derived_and_raw_eps_preserved():
    observation = parse_snapshot(futu_row(), SPEC, DAY, OBSERVED_AT, "snapshot.json")
    assert observation.eps_ttm == Decimal(5)
    assert observation.reported_eps == Decimal(4)
    assert observation.eps_method == "price_div_pe_ttm"
    assert observation.quote_at.utcoffset().total_seconds() == 8 * 3600


@pytest.mark.parametrize("quote_time", ["2026-10-12 11:30:00", "2026-10-09 15:00:00"])
def test_intraday_and_stale_quotes_cannot_be_saved_as_daily_closes(quote_time):
    with pytest.raises(DataError):
        parse_snapshot(futu_row(update_time=quote_time), SPEC, DAY, OBSERVED_AT, "snapshot.json")


def test_current_fundamentals_cannot_be_used_to_backfill_a_past_day():
    with pytest.raises(DataError, match="historical date"):
        parse_snapshot(futu_row(), SPEC, DAY, datetime(2026, 10, 13, 13, tzinfo=UTC), "x")


def test_zero_pe_is_missing_and_negative_pe_preserves_loss():
    assert parse_snapshot(futu_row(pe_ttm_ratio=0), SPEC, DAY, OBSERVED_AT, "x").eps_ttm is None
    assert parse_snapshot(futu_row(pe_ttm_ratio=-20), SPEC, DAY, OBSERVED_AT, "x").eps_ttm == -5


def test_reference_source_uses_unadjusted_same_day_ttm_fields():
    observation = parse_daily(
        {"code": "sh.600519", "date": DAY.isoformat(), "close": "100.0000", "peTTM": "20.000000"},
        SPEC,
        OBSERVED_AT,
        "x",
    )
    assert observation.symbol == "SH.600519"
    assert observation.trade_date == DAY
    assert observation.price_basis == "unadjusted_close"
    assert observation.eps_ttm == Decimal(5)
