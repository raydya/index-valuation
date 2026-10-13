from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from index_valuation.domain import DataError, Observation, SourceValuation
from index_valuation.index_series import build_dashboard, derive_points, method_for
from index_valuation.providers.danjuan_history import BASIS, parse_history
from index_valuation.providers.fred_history import parse_history as parse_fred
from index_valuation.providers.gurufocus_valuation import parse_headline
from index_valuation.providers.multpl_valuation import parse_current
from index_valuation.registry import load_indices

SP = load_indices()["sp500"]
DAY = date(2024, 7, 5)
NOW = datetime(2024, 7, 6, tzinfo=UTC)
STAMP = 1720108800000  # Shanghai midnight July 5; New York is still July 4.


def test_danjuan_date_labels_are_not_shifted_to_previous_us_session():
    history = {"result_code": 0, "data": {"index_eva_pe_growths": [{"ts": STAMP, "pe": 20}]}}
    detail = {"result_code": 0, "data": {"index_code": "SP500", "ts": STAMP, "pe": 20}}
    rows = parse_history(history, detail, SP, DAY, DAY, NOW, "source")
    assert len(rows) == 1 and rows[0].trade_date == DAY
    assert rows[0].basis == BASIS and rows[0].metric == "pe_ttm"
    detail["data"]["pe"] = 21
    with pytest.raises(DataError, match="disagree"):
        parse_history(history, detail, SP, DAY, DAY, NOW, "source")
    detail["data"]["index_code"] = "NDX"
    with pytest.raises(DataError, match="identity"):
        parse_history(history, detail, SP, DAY, DAY, NOW, "source")


def test_danjuan_ignores_unclosed_snapshot_and_missing_pe_without_filling():
    history = {"result_code": 0, "data": {"index_eva_pe_growths": [{"ts": STAMP, "pe": 0}]}}
    detail = {"result_code": 0, "data": {"index_code": "SP500", "ts": STAMP, "pe": 20}}
    assert parse_history(history, detail, SP, DAY, DAY, NOW.replace(day=5, hour=18), "raw") == []
    with pytest.raises(DataError, match="successful"):
        parse_history({"result_code": 999}, detail, SP, DAY, DAY, NOW, "raw")


def test_multpl_keeps_only_current_snapshot_not_monthly_average_history():
    page = '<title>S&amp;P 500 PE Ratio</title><table id="datatable">'
    page += "<tr><th>Date</th><th>Value</th></tr>"
    page += "<tr><td>Jul 5, 2024</td><td><abbr>†</abbr>26.50</td></tr>"
    page += "<tr><td>Jun 1, 2024</td><td>26</td></tr></table>"
    rows = parse_current(page, SP, date(2020, 1, 1), DAY, NOW, "source")
    assert [(r.trade_date, r.value) for r in rows] == [(DAY, Decimal("26.50"))]
    with pytest.raises(DataError, match="different index"):
        parse_current(page.replace("PE Ratio", "Shiller PE Ratio"), SP, DAY, DAY, NOW, "raw")


def test_gurufocus_uses_dated_headline_not_stale_statistics():
    rows = parse_headline("S&P 500 PE Ratio : 26.5 (As of 2024-07-05)", SP, DAY, DAY, NOW, "raw")
    assert rows[0].value == Decimal("26.5") and rows[0].trade_date == DAY
    with pytest.raises(DataError, match="headline"):
        parse_headline("Nasdaq 100 PE Ratio : 26.5 (As of 2024-07-05)", SP, DAY, DAY, NOW, "raw")


def test_fred_csv_identity_missing_holiday_and_final_close():
    spec = load_indices()["nasdaq100"]
    text = "observation_date,NASDAQ100\n2024-07-04,.\n2024-07-05,20391.97\n"
    rows = parse_fred(text, spec, date(2024, 7, 4), DAY, NOW, "raw")
    assert len(rows) == 1 and rows[0].price == Decimal("20391.97")
    with pytest.raises(DataError, match="different series"):
        parse_fred(text.replace("NASDAQ100", "NASDAQCOM"), spec, DAY, DAY, NOW, "raw")
    with pytest.raises(DataError, match="unfinished"):
        parse_fred(text, spec, date(2024, 7, 4), DAY, NOW.replace(day=5, hour=18), "raw")


def inputs(day, value):
    prices = [
        Observation(
            SP.quote_code,
            day,
            source,
            Decimal(1000),
            None,
            None,
            NOW,
            NOW,
            currency="USD",
        )
        for source in ("yahoo", "eastmoney")
    ]
    valuation = SourceValuation(
        "danjuan", SP.quote_code, day, "pe_ttm", BASIS, Decimal(value), NOW, "raw"
    )
    return prices, valuation


def test_alternate_pe_series_cannot_be_filled_with_futu_or_another_basis():
    prices, pe = inputs(DAY, 20)
    point = derive_points(SP, prices, [pe])[0]
    assert point["pe_source"] == "danjuan" and point["implied_eps"] == 50
    for other in (replace(pe, source="futu"), replace(pe, basis="forward_pe")):
        assert derive_points(SP, prices, [other])[0]["pe"] is None
    assert method_for(SP) != method_for(load_indices()["csi300"])


def test_material_pe_discrepancy_stays_visible_but_blocks_eps_and_statistics():
    prices, pe = inputs(DAY, 20)
    reference = replace(
        pe, source="multpl", basis="multpl_reported_ttm_current_estimate", value=Decimal(30)
    )
    point = derive_points(SP, prices, [pe, reference])[0]
    assert point["pe"] == 20 and point["pe_comparison_status"] == "error"
    assert not point["pe_usable"] and point["implied_eps"] is None
    later = {**point, "trade_date": date(2024, 7, 15), "pe": Decimal(22), "pe_usable": True}
    selected = build_dashboard(SP, [point, later])["selected"]
    assert selected["history_count"] == 0


def test_weekly_statistics_ignore_current_week_and_never_overweight_daily_snapshots():
    days = [date(2024, 6, d) for d in (3, 4, 7, 10, 11, 14, 17, 18)]
    prices, valuations = [], []
    for day, value in zip(days, (100, 200, 10, 100, 200, 30, 40, 9000), strict=True):
        ps, pe = inputs(day, value)
        prices.extend(ps)
        valuations.append(pe)
    spec = replace(SP, band_min_observations=2)
    points = derive_points(spec, prices, valuations)
    selected = build_dashboard(spec, points, as_of=days[-2])["selected"]
    assert selected["history_count"] == 2 and selected["mean_pe"] == 20
    assert selected["history_end"] == date(2024, 6, 14)
    assert selected["percentile"] == 100
    assert build_dashboard(spec, points[:-1])["selected"] == selected
    assert build_dashboard(spec, points)["selected"]["mean_pe"] == 20


def test_any_material_reference_difference_blocks_new_pe_even_if_another_source_agrees():
    prices, pe = inputs(DAY, 20)
    close = replace(pe, source="multpl", basis="multpl_reported_ttm_current_estimate")
    far = replace(
        pe,
        source="gurufocus",
        metric="pe",
        basis="gurufocus_published_pe_unconfirmed",
        value=Decimal(30),
    )
    point = derive_points(SP, prices, [pe, close, far])[0]
    assert len(point["pe_references"]) == 2
    assert point["reference_pe_source"] == "gurufocus" and not point["pe_usable"]
    assert point["implied_eps"] is None
