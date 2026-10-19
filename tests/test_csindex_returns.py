import json
from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError, IndexSpec, SourceError
from index_valuation.providers import csindex_returns
from index_valuation.providers.csindex_returns import (
    CSITotalReturnHistory,
    parse_total_returns,
)

SPEC = IndexSpec("dividend_return", "中红收益", "H00922", "CSI.H00922")
DAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 17, tzinfo=UTC)
XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


@pytest.fixture(autouse=True)
def no_real_throttle_sleep(monkeypatch):
    clock = {"now": 100.0, "sleeps": []}

    def advance(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(csindex_returns, "_last_request", None)
    monkeypatch.setattr(csindex_returns, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(csindex_returns, "sleep", advance)
    return clock


def workbook(rows, *, shared=False, extra_columns=False):
    headers = [
        "日期Date",
        "指数代码Index Code",
        "指数中文全称Index Chinese Name(Full)",
        "收盘Close",
    ]
    if extra_columns:
        # Reordered columns and empty intervening cells occur in genuine XLSX;
        # cell addresses, not list offsets, must determine identity and price.
        headers = [
            "收盘Close",
            "空列",
            "指数代码Index Code",
            "日期Date",
            "指数英文全称Index English Name(Full)",
        ]
        rows = [[row[3], "", row[1], row[0], row[2]] for row in rows]
    sheet = ElementTree.Element("worksheet", xmlns=XML_NS)
    data = ElementTree.SubElement(sheet, "sheetData")
    strings = []
    for number, values in enumerate([headers, *rows], 1):
        row = ElementTree.SubElement(data, "row", r=str(number))
        for index, value in enumerate(values):
            if value == "":
                continue
            cell = ElementTree.SubElement(
                row, "c", r=f"{chr(65 + index)}{number}", t="s" if shared else "inlineStr"
            )
            if shared:
                strings.append(str(value))
                ElementTree.SubElement(cell, "v").text = str(len(strings) - 1)
            else:
                ElementTree.SubElement(ElementTree.SubElement(cell, "is"), "t").text = str(value)
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", ElementTree.tostring(sheet))
        if shared:
            elements = ElementTree.Element("sst", xmlns=XML_NS)
            for value in strings:
                ElementTree.SubElement(ElementTree.SubElement(elements, "si"), "t").text = value
            archive.writestr("xl/sharedStrings.xml", ElementTree.tostring(elements))
    return buffer.getvalue()


def parse(content, *, observed_at=NOW, start=DAY, end=DAY):
    return parse_total_returns(content, SPEC, start, end, observed_at, "archive.json")


@pytest.mark.parametrize("shared", [False, True])
def test_real_export_shapes_preserve_identity_and_decimal_without_pe(shared):
    rows = parse(
        workbook(
            [["20261016", "H00922", "CSI Dividend Total Return Index", "12137.96"]],
            shared=shared,
            extra_columns=True,
        )
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.symbol == "CSI.H00922" and row.source == "csindex"
    assert row.price == Decimal("12137.96")
    assert row.price_basis == "gross_total_return"
    assert row.eps_ttm is row.pe_ttm is None
    assert row.quote_at.isoformat() == "2026-10-16T15:00:00+08:00"


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("000922", "中证红利指数"),
        ("H00922", "中证红利指数"),
        ("H00922", "CSI Dividend Net Return Index"),
        ("H00922", "中证红利净收益指数"),
        ("H00922", "全收益与净收益指数"),
    ],
)
def test_rejects_wrong_identity_or_return_variant(code, name):
    with pytest.raises(DataError, match="different index|gross total-return"):
        parse(workbook([["20261016", code, name, "1000"]]))


def test_batch_workbook_requires_explicit_original_request_allowlist():
    content = workbook(
        [
            ["20261016", "H00922", "中证红利全收益指数", "12137.96"],
            ["20261016", "H20269", "中证红利低波动全收益指数", "24615.16"],
        ]
    )
    with pytest.raises(DataError, match="different index"):
        parse(content)
    rows = parse_total_returns(
        content,
        SPEC,
        DAY,
        DAY,
        NOW,
        "original-batch-request.json",
        requested_codes=["H00922", "H20269"],
    )
    assert len(rows) == 1
    assert rows[0].price == Decimal("12137.96")
    assert rows[0].artifact_path == "original-batch-request.json"
    with pytest.raises(DataError, match="different index"):
        parse_total_returns(
            content, SPEC, DAY, DAY, NOW, "batch.json", requested_codes=["H00922", "H00300"]
        )
    with pytest.raises(DataError, match="include the requested target"):
        parse_total_returns(content, SPEC, DAY, DAY, NOW, "batch.json", requested_codes=["H20269"])


@pytest.mark.parametrize("price", ["", "0", "-1", "NaN", "Infinity", "bad"])
def test_rejects_missing_nonpositive_or_nonfinite_close(price):
    with pytest.raises(DataError, match="invalid or unfinished"):
        parse(workbook([["20261016", "H00922", "中证红利全收益指数", price]]))


def test_rejects_duplicate_dates_and_intraday_observation():
    row = ["20261016", "H00922", "中证红利全收益指数", "12137.96"]
    with pytest.raises(DataError, match="duplicate"):
        parse(workbook([row, row]))
    with pytest.raises(DataError, match="unfinished"):
        parse(workbook([row]), observed_at=datetime(2026, 10, 16, 6, 59, tzinfo=UTC))


def test_filters_prebase_and_extra_boundary_without_shifting_dates():
    rows = [
        ["20041230", "H00922", "中证红利全收益指数", "1000"],
        ["20041231", "H00922", "中证红利全收益指数", "1000"],
        ["20050104", "H00922", "中证红利全收益指数", "981.56"],
    ]
    parsed = parse(workbook(rows), start=date(2004, 12, 31), end=date(2004, 12, 31))
    assert [row.trade_date for row in parsed] == [date(2004, 12, 31)]


def test_rejects_invalid_workbook_or_changed_schema():
    with pytest.raises(SourceError, match="valid.*XLSX"):
        parse(b"<html>temporarily unavailable</html>")
    content = workbook([])
    buffer = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(buffer, "w") as target:
        xml = source.read("xl/worksheets/sheet1.xml").replace(b"Close", b"Price")
        target.writestr("xl/worksheets/sheet1.xml", xml)
    with pytest.raises(DataError, match="schema changed"):
        parse(buffer.getvalue())


def test_collects_year_chunks_with_binary_and_request_provenance(tmp_path, monkeypatch):
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        stamp = "20241231" if len(calls) == 1 else "20250102"
        return httpx.Response(
            200,
            content=workbook([[stamp, "H00922", "中证红利全收益指数", "1000"]]),
        )

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(respond))
    result = CSITotalReturnHistory(Archive(tmp_path)).index_history(
        SPEC, date(2024, 12, 31), date(2025, 1, 2)
    )
    assert len(result.observations) == 2 and not result.valuations and not result.failures
    assert calls == [
        [{"indexCode": "H00922", "startDate": "20241231", "endDate": "20250101"}],
        [{"indexCode": "H00922", "startDate": "20250101", "endDate": "20250103"}],
    ]
    for observation in result.observations:
        metadata = json.loads(open(observation.artifact_path).read())
        assert metadata["status_code"] == 200
        assert metadata["workbook_path"].endswith(".xlsx")
        assert metadata["json"][0]["indexCode"] == "H00922"


@pytest.mark.parametrize(("status", "calls_expected"), [(403, 1), (429, 1), (503, 3)])
def test_denials_stop_immediately_and_transient_failures_stop_after_three(
    tmp_path, monkeypatch, status, calls_expected
):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, content=b"unavailable")

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(respond))
    result = CSITotalReturnHistory(Archive(tmp_path)).index_history(
        SPEC, date(2010, 1, 1), date(2026, 10, 16)
    )
    assert len(calls) == calls_expected
    assert not result.observations
    assert result.failures[-1]["end"] == DAY
    assert result.failures[-1]["error"] == "not_attempted_after_source_failure"
    assert all(failure.get("artifact") for failure in result.failures[:-1])


def test_preserves_successful_chunks_and_reports_empty_ranges(tmp_path, monkeypatch):
    def respond(request):
        body = json.loads(request.content)
        rows = (
            []
            if body[0]["startDate"] == "20041231"
            else [["20050104", "H00922", "中证红利全收益指数", "981.56"]]
        )
        return httpx.Response(200, content=workbook(rows))

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(respond))
    result = CSITotalReturnHistory(Archive(tmp_path)).index_history(
        SPEC, date(2004, 12, 31), date(2005, 1, 4)
    )
    assert len(result.observations) == 1
    assert result.failures[0]["error"] == "no_total_return_data_in_requested_range"


def test_request_pacing_is_shared_across_provider_instances(
    tmp_path, monkeypatch, no_real_throttle_sleep
):
    clock = no_real_throttle_sleep
    requested_at = []

    def respond(request):
        requested_at.append(clock["now"])
        return httpx.Response(
            200,
            content=workbook([["20261016", "H00922", "中证红利全收益指数", "12137.96"]]),
        )

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_: httpx.MockTransport(respond))
    for _ in range(2):
        result = CSITotalReturnHistory(Archive(tmp_path)).index_history(SPEC, DAY, DAY)
        assert len(result.observations) == 1 and not result.failures
    assert clock["sleeps"] == [2.0]
    assert requested_at == [100.0, 102.0]
    clock["now"] += 3
    CSITotalReturnHistory(Archive(tmp_path)).index_history(SPEC, DAY, DAY)
    assert clock["sleeps"] == [2.0]
