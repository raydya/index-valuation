from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from index_valuation.domain import DataError, IndexSpec, Observation, SourceValuation
from index_valuation.fundamentals import combine, method_for
from index_valuation.providers.eastmoney import parse_prices

DAY = date(2005, 4, 8)
NOW = datetime(2026, 10, 13, tzinfo=UTC)
SYMBOL = "SH.600519"
SPEC = IndexSpec("test", "Test", "000300", "SH.000300", 2)
METHOD = method_for("eastmoney")


def inputs(pe_value=Decimal(20)):
    price = Observation(
        SYMBOL, DAY, "eastmoney", Decimal(100), None, None, NOW, NOW, artifact_path="price.json"
    )
    pe = SourceValuation(
        "futu", SYMBOL, DAY, "pe_ttm", "futu_company_pe_ttm_v1", pe_value, NOW, "pe.json"
    )
    reference = replace(
        price,
        source="baostock",
        eps_ttm=Decimal(100) / pe_value,
        pe_ttm=pe_value,
        artifact_path="reference.json",
    )
    return price, pe, reference


@pytest.mark.parametrize("pe_value", [Decimal(20), Decimal(-20)])
def test_implicit_eps_retains_loss_and_separate_input_provenance(pe_value):
    price, pe, reference = inputs(pe_value)
    row = combine(SYMBOL, METHOD, {DAY}, {DAY: price}, {DAY: pe}, {DAY: reference})[0]
    assert row.eps_ttm == Decimal(100) / pe_value
    assert row.status == "verified"
    assert row.price_source == "eastmoney"
    assert row.pe_source == "futu"
    assert (row.price_artifact, row.pe_artifact, row.reference_artifact) == (
        "price.json",
        "pe.json",
        "reference.json",
    )


def test_missing_pe_is_not_replaced_by_reference_or_forward_filled():
    price, _, reference = inputs()
    row = combine(SYMBOL, METHOD, {DAY}, {DAY: price}, {}, {DAY: reference})[0]
    assert row.status == "missing_pe"
    assert row.eps_ttm is row.pe_ttm is None


def test_mismatch_is_retained_but_blocks_use_and_missing_check_stays_unverified():
    price, pe, reference = inputs()
    rows = combine(
        SYMBOL,
        METHOD,
        {DAY},
        {DAY: price},
        {DAY: pe},
        {DAY: replace(reference, pe_ttm=Decimal(30), eps_ttm=Decimal(100) / 30)},
    )
    assert rows[0].status == "blocked"
    assert rows[0].eps_ttm == 5  # candidate data is retained, never treated as verified
    assert combine(SYMBOL, METHOD, {DAY}, {DAY: price}, {DAY: pe}, {})[0].status == "unverified"


@pytest.mark.parametrize("bad", ["adjustment", "date", "currency", "zero_pe", "index_pe"])
def test_incompatible_inputs_cannot_produce_ttm_eps(bad):
    price, pe, reference = inputs()
    if bad == "adjustment":
        price = replace(price, price_basis="forward_adjusted")
    elif bad == "date":
        pe = replace(pe, trade_date=date(2005, 4, 11))
    elif bad == "currency":
        price = replace(price, currency="USD")
    elif bad == "zero_pe":
        pe = replace(pe, value=Decimal(0))
    else:
        pe = replace(pe, metric="pe", basis="futu_valuation_pe")
    with pytest.raises(DataError):
        combine(SYMBOL, METHOD, {DAY}, {DAY: price}, {DAY: pe}, {DAY: reference})


def test_eastmoney_uses_close_column_and_validates_symbol():
    data = {
        "code": "600519",
        "market": 1,
        "klines": ["2005-04-08,99,100,101,98,1000,100000,3,1,1,2"],
    }
    row = parse_prices(data, SPEC, SYMBOL, DAY, DAY, NOW, "em.json")[0]
    assert row.price == 100
    assert row.price_basis == "unadjusted_close"
    assert row.eps_ttm is None
    with pytest.raises(DataError, match="wrong security"):
        parse_prices({**data, "market": 0}, SPEC, SYMBOL, DAY, DAY, NOW, "em.json")
