"""FRED's limited S&P licence and per-window failures must not erase valid data."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
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
