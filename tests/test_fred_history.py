"""FRED's limited S&P licence and per-window failures must not erase valid data."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from index_valuation.archive import Archive
from index_valuation.domain import DataError
from index_valuation.providers.fred_history import FredHistory, parse_history
from index_valuation.registry import load_indices

SPECS = load_indices()
SP = SPECS["sp500"]
NDX = replace(SPECS["nasdaq100"], fred_series="NASDAQ100")
NOW = datetime(2026, 10, 16, 7, tzinfo=UTC)


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def client_mock(monkeypatch, handler):
    original_client = httpx.Client
    monkeypatch.setattr("index_valuation.providers.fred_history.datetime", FixedClock)
    monkeypatch.setattr(
        "index_valuation.providers.fred_history.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler)),
    )


def test_sp500_clips_to_documented_ten_year_window_and_reports_earlier_gap(tmp_path, monkeypatch):
    requested = []

    def handler(request):
        start, end = request.url.params["cosd"], request.url.params["coed"]
        requested.append((start, end))
        day = {"2016": "2016-10-17", "2021": "2021-01-04", "2026": "2026-10-15"}[start[:4]]
        return httpx.Response(200, text=f"observation_date,SP500\n{day},3000.12\n")

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(SP, date(1957, 3, 4), date(2026, 10, 15))
    assert requested == [
        ("2016-10-16", "2020-12-31"),
        ("2021-01-01", "2025-12-31"),
        ("2026-01-01", "2026-10-15"),
    ]
    assert [row.trade_date for row in batch.observations] == [
        date(2016, 10, 17),
        date(2021, 1, 4),
        date(2026, 10, 15),
    ]
    assert len(batch.failures) == 1
    gap = batch.failures[0]
    assert gap["category"] == "unsupported_historical_coverage" and gap["stage"] == "prices"
    assert gap["start"] == date(1957, 3, 4) and gap["end"] == date(2016, 10, 15)
    assert gap["policy_url"] == "https://fred.stlouisfed.org/series/SP500"
    for row in batch.observations:
        payload = json.loads(Path(row.artifact_path).read_text())
        assert payload["params"]["cosd"] <= row.trade_date.isoformat() <= payload["params"]["coed"]


def test_wholly_unavailable_old_sp500_request_never_downloads_present_prices(tmp_path, monkeypatch):
    def handler(request):
        raise AssertionError(
            "A pre-coverage request could silently return today's full FRED series"
        )

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(SP, date(1957, 3, 4), date(1961, 12, 31))
    assert batch.observations == [] and batch.failures[0]["end"] == date(1961, 12, 31)


def test_nasdaq_history_is_not_limited_by_the_sp500_licence(tmp_path, monkeypatch):
    def handler(request):
        assert request.url.params["cosd"] == "1986-01-02"
        return httpx.Response(200, text="observation_date,NASDAQ100\n1986-01-02,131.25\n")

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(NDX, date(1986, 1, 2), date(1986, 1, 2))
    assert batch.failures == [] and batch.observations[0].trade_date == date(1986, 1, 2)


def test_bad_windows_do_not_discard_successful_prices_or_skip_later_windows(tmp_path, monkeypatch):
    requested = []

    def handler(request):
        year = request.url.params["cosd"][:4]
        requested.append(year)
        payloads = {
            "2000": "observation_date,NASDAQ100\n2000-01-03,3000.25\n",
            "2005": "observation_date,NASDAQ100\n2005-01-03,3000\n2005-01-03,3000\n",
            "2010": "observation_date,NASDAQCOM\n2010-01-04,3000\n",
            "2015": "observation_date,NASDAQ100\n2026-10-15,3000\n",
            "2020": "observation_date,NASDAQ100\n2020-01-02,5000.75\n",
        }
        return httpx.Response(200, text=payloads[year])

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(NDX, date(2000, 1, 1), date(2024, 12, 31))
    assert requested == ["2000", "2005", "2010", "2015", "2020"]
    assert [row.trade_date for row in batch.observations] == [date(2000, 1, 3), date(2020, 1, 2)]
    assert len(batch.failures) == 3
    assert all(f["category"] == "invalid_data" and f["stage"] == "prices" for f in batch.failures)
    assert all(Path(f["artifact_path"]).exists() for f in batch.failures)


@pytest.mark.parametrize(
    "body,match",
    [
        ("observation_date,NASDAQ100\n2020-01-02,5000\n", "different series"),
        ("observation_date,SP500\n2020-01-02,5000\n2020-01-02,5000\n", "duplicate"),
        ("observation_date,SP500\n2026-10-15,5000\n", "out-of-range"),
        ("observation_date,SP500\ninvalid,5000\n", "invalid observation date"),
    ],
)
def test_parser_keeps_strict_identity_duplicate_and_date_checks(body, match):
    with pytest.raises(DataError, match=match):
        parse_history(body, SP, date(2020, 1, 1), date(2020, 12, 31), NOW, "raw")


def test_wrong_index_mapping_is_rejected_before_request(tmp_path):
    with pytest.raises(DataError, match="explicitly mapped"):
        FredHistory(Archive(tmp_path)).index_history(
            replace(SP, fred_series="NASDAQ100"), date(2020, 1, 1), date(2020, 12, 31)
        )


def recent_page(rows, *, series="SP500", frequency="Daily,<br />Close"):
    observations = "".join(
        f'<tr><td align="right">{day}:&nbsp;</td>'
        f'<td class="series-obs value">{value}</td><td>&nbsp;</td></tr>'
        for day, value in rows
    )
    return (
        f'<html><head><link rel="canonical" href="https://fred.stlouisfed.org/series/{series}">'
        f'</head><body><span class="series-meta-value-frequency">{frequency}</span>'
        '<span class="series-meta-updated-date">Oct 30, 2026</span>'
        f'<table id="recent-obs" class="table table-condensed">{observations}'
        f'<tr><td colspan=2><a id="viewall-button" href="/data/{series}">'
        "View All</a></td></tr></table></body></html>"
    )


def test_recent_page_fills_only_actual_missing_dates_and_keeps_csv_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{__name__}.NOW", datetime(2026, 11, 2, 3, tzinfo=UTC))
    requested = []

    def handler(request):
        requested.append(request.url.path)
        if request.url.path.endswith(".csv"):
            return httpx.Response(200, text="observation_date,SP500\n2026-10-28,7743.98\n")
        return httpx.Response(
            200,
            text=recent_page(
                [
                    ("2026-10-29", "7,720.39"),
                    ("2026-10-28", "7,743.98"),
                    ("2026-10-27", "7,722.60"),
                ]
            ),
        )

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(SP, date(2026, 10, 28), date(2026, 10, 30))
    assert requested == ["/graph/fredgraph.csv", "/series/SP500"]
    assert batch.failures == []
    assert [(row.trade_date, row.price, row.source) for row in batch.observations] == [
        (date(2026, 10, 28), Decimal("7743.98"), "fred"),
        (date(2026, 10, 29), Decimal("7720.39"), "fred"),
    ]
    original, supplemented = batch.observations
    assert original.raw == {"observation_date": "2026-10-28", "SP500": "7743.98"}
    assert supplemented.quote_at.isoformat() == "2026-10-29T16:15:00-04:00"
    assert supplemented.raw["retrieval_method"] == "series_recent_observations"
    payload = json.loads(Path(supplemented.artifact_path).read_text())
    assert payload["url"] == "https://fred.stlouisfed.org/series/SP500"
    assert payload["target_date"] == "2026-10-30"
    assert payload["start"] == "2026-10-28" and payload["end"] == "2026-10-30"
    assert 'id="recent-obs"' in payload["text"]


@pytest.mark.parametrize(
    "page,error",
    [
        (recent_page([("2026-10-29", "7720.39")], series="NASDAQ100"), "identity"),
        (recent_page([("2026-10-29", "7720.39")], frequency="Weekly, Close"), "frequency"),
        (recent_page([("2026-10-29", "7720.39")], frequency="Daily, Open"), "frequency"),
        (recent_page([("2026-10-29", "7720.39")], frequency=""), "frequency"),
        (recent_page([("2026-02-30", "7720.39")]), "invalid observation date"),
        (recent_page([("10/29/2026", "7720.39")]), "invalid observation date"),
        (recent_page([("2026-10-29", "7720.39")] * 2), "duplicate"),
        (recent_page([("2026-11-02", "7720.39")]), "unfinished"),
        (recent_page([("2026-10-29", "0")]), "invalid"),
        (recent_page([("2026-10-29", "-1")]), "invalid"),
        (recent_page([("2026-10-29", "NaN")]), "invalid"),
        (recent_page([("2026-10-29", "Infinity")]), "invalid"),
        (recent_page([("2026-10-29", "77,20.39")]), "invalid"),
        (recent_page([]), "missing or incomplete"),
        (recent_page([("2026-10-29", "7720.39")]).replace("</table>", ""), "incomplete"),
        (
            recent_page([("2026-10-29", "7720.39")]).replace("fred.stlouisfed.org", "example.org"),
            "identity",
        ),
        (recent_page([("2026-10-29", "7720.39")]).replace('rel="canonical"', "rel"), "identity"),
        (
            recent_page([("2026-10-29", "7720.39")]).replace(
                'class="series-meta-value-frequency"', "class"
            ),
            "frequency",
        ),
    ],
)
def test_invalid_recent_page_preserves_csv(tmp_path, monkeypatch, page, error):
    monkeypatch.setattr(f"{__name__}.NOW", datetime(2026, 11, 2, 3, tzinfo=UTC))

    def handler(request):
        return httpx.Response(
            200,
            text="observation_date,SP500\n2026-10-28,7743.98\n"
            if request.url.path.endswith(".csv")
            else page,
        )

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(SP, date(2026, 10, 28), date(2026, 10, 30))
    assert [(row.trade_date, row.price) for row in batch.observations] == [
        (date(2026, 10, 28), Decimal("7743.98"))
    ]
    assert len(batch.failures) == 1
    failure = batch.failures[0]
    assert failure["category"] == "invalid_data" and error in failure["error"]
    assert json.loads(Path(failure["artifact_path"]).read_text())["text"] == page


def test_recent_page_conflict_reports_both_values_and_keeps_other_missing_dates(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(f"{__name__}.NOW", datetime(2026, 11, 2, 3, tzinfo=UTC))

    def handler(request):
        return httpx.Response(
            200,
            text="observation_date,SP500\n2026-10-28,7743.98\n"
            if request.url.path.endswith(".csv")
            else recent_page([("2026-10-29", "7720.39"), ("2026-10-28", "7740.01")]),
        )

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(SP, date(2026, 10, 28), date(2026, 10, 30))
    assert [(row.trade_date, row.price) for row in batch.observations] == [
        (date(2026, 10, 28), Decimal("7743.98")),
        (date(2026, 10, 29), Decimal("7720.39")),
    ]
    assert len(batch.failures) == 1
    failure = batch.failures[0]
    assert failure["category"] == "same_source_conflict"
    assert failure["trade_date"] == date(2026, 10, 28)
    assert failure["csv_value"] == Decimal("7743.98")
    assert failure["page_value"] == Decimal("7740.01")
    assert Path(failure["csv_artifact_path"]).exists() and Path(failure["artifact_path"]).exists()


@pytest.mark.parametrize("mode", ["http", "connection"])
def test_failed_recent_request_preserves_csv_and_archives_retrieval(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(f"{__name__}.NOW", datetime(2026, 11, 2, 3, tzinfo=UTC))

    def handler(request):
        if request.url.path.endswith(".csv"):
            return httpx.Response(200, text="observation_date,SP500\n2026-10-28,7743.98\n")
        if mode == "http":
            return httpx.Response(503, text="Service unavailable")
        raise httpx.ConnectError("Connection refused", request=request)

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(SP, date(2026, 10, 28), date(2026, 10, 30))
    assert [row.trade_date for row in batch.observations] == [date(2026, 10, 28)]
    assert len(batch.failures) == 1 and batch.failures[0]["category"] == "request_failed"
    payload = json.loads(Path(batch.failures[0]["artifact_path"]).read_text())
    assert payload["url"] == "https://fred.stlouisfed.org/series/SP500"
    assert payload["retrieval_method"] == "series_recent_observations"
    if mode == "http":
        assert payload["status_code"] == 503 and payload["text"] == "Service unavailable"
    else:
        assert "Connection refused" in payload["error"]


@pytest.mark.parametrize(
    "now,start,end,csv",
    [
        # Weekend boundaries do not turn Saturday or Sunday into a missing session.
        ("2026-11-01T12:00:00+00:00", "2026-10-29", "2026-11-01", "2026-10-30,7720.39"),
        ("2026-11-02T12:00:00+00:00", "2026-10-31", "2026-11-01", ""),
        # A historical interval outside the recent 14 days never triggers the page.
        ("2026-11-02T12:00:00+00:00", "2026-10-01", "2026-10-18", "2026-10-01,7720.39"),
        # Today is not closed; Friday's present close is sufficient on Monday morning.
        ("2026-11-02T12:00:00+00:00", "2026-10-30", "2026-11-02", "2026-10-30,7720.39"),
        # Thanksgiving is closed; Wednesday's observation is the target.
        ("2026-11-26T23:00:00+00:00", "2026-11-25", "2026-11-26", "2026-11-25,7720.39"),
    ],
)
def test_page_not_requested_for_closed_sessions_already_present_or_old_window(
    tmp_path, monkeypatch, now, start, end, csv
):
    monkeypatch.setattr(f"{__name__}.NOW", datetime.fromisoformat(now))
    requested = []

    def handler(request):
        requested.append(request.url.path)
        assert request.url.path.endswith(".csv"), "No missing recent closed session"
        return httpx.Response(200, text=f"observation_date,SP500\n{csv}\n")

    client_mock(monkeypatch, handler)
    batch = FredHistory(Archive(tmp_path)).index_history(
        SP, date.fromisoformat(start), date.fromisoformat(end)
    )
    assert requested == ["/graph/fredgraph.csv"] and batch.failures == []
