"""Total-return identities, independent incremental collection, and source isolation."""

import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from index_valuation.domain import (
    DataError,
    IndexSpec,
    Observation,
    ReferenceHistory,
    SourceError,
    TotalReturnSpec,
)
from index_valuation.providers.total_return import TotalReturnSources
from index_valuation.registry import load_indices
from index_valuation.total_returns import TotalReturnCollector, select_return_points, source_start

DAY, NEXT = date(2024, 1, 2), date(2024, 1, 3)
TR = TotalReturnSpec(
    code="H00922",
    name="中证红利全收益指数",
    source="csindex",
    symbol="H00922",
    history_start_date=DAY,
    identity_url="https://www.csindex.com.cn/",
    verification_source="eastmoney",
    verification_symbol="2.H00922",
)
SPEC = IndexSpec(
    "dividend", "中证红利", "000922", "SH.000922", inception_date=NEXT, total_return=TR
)
OBSERVED_AT = datetime(2024, 2, 1, tzinfo=UTC)


def stored(day=DAY, value="100", source="csindex", **changes):
    return {
        "index_code": TR.code,
        "basis": TR.basis,
        "source": source,
        "symbol": TR.symbol if source == TR.source else TR.verification_symbol,
        "trade_date": day,
        "price": Decimal(value),
        "artifact_path": f"raw/{source}-{day}.json",
        **changes,
    }


def observation(day=DAY, value="100", source="csindex", **changes):
    return Observation(
        **{
            "symbol": "CSI.H00922",
            "trade_date": day,
            "source": source,
            "price": Decimal(value),
            "eps_ttm": None,
            "pe_ttm": None,
            "observed_at": OBSERVED_AT,
            "quote_at": OBSERVED_AT,
            "currency": "CNY",
            "price_basis": TR.basis,
            "artifact_path": f"raw/{source}-{day}.json",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "reference,status,usable",
    [("100.5", "pass", True), ("103", "warning", True), ("106", "error", False)],
)
def test_select_compares_only_same_day_same_return_identity(reference, status, usable):
    points = select_return_points(
        SPEC,
        [stored(), stored(value=reference, source="eastmoney"), stored(NEXT, "110")],
    )
    assert [point["trade_date"] for point in points] == [DAY, NEXT]
    assert points[0]["price_status"] == status and points[0]["price_usable"] is usable
    assert points[0]["reference_price"] == Decimal(reference)
    assert points[0]["reference_source"] == "eastmoney"
    assert points[0]["phase"] == "backcast" and points[1]["phase"] == "published"
    assert points[1]["price_status"] == "unverified"
    assert points[1]["reference_price"] is None


def test_select_ignores_other_codes_bases_sources_symbols_and_never_substitutes_reference():
    rows = [
        stored(),
        stored(NEXT, "120", source="eastmoney"),
        stored(NEXT, "500", index_code="OTHER"),
        stored(NEXT, "500", basis="net_total_return"),
        stored(NEXT, "500", source="old_source"),
        stored(NEXT, "500", symbol="old_symbol"),
        stored(value="999", source="eastmoney", symbol="2.WRONG"),
    ]
    points = select_return_points(SPEC, rows)
    assert len(points) == 1 and points[0]["trade_date"] == DAY
    assert points[0]["price"] == 100 and points[0]["price_source"] == "csindex"
    assert points[0]["price_status"] == "unverified" and points[0]["price_usable"]
    assert select_return_points(replace(SPEC, total_return=None), rows) == []
    assert select_return_points(replace(SPEC, collection_enabled=False), rows) == []


@pytest.mark.parametrize("source", ["csindex", "eastmoney"])
def test_duplicate_same_source_dates_are_rejected(source):
    with pytest.raises(DataError, match="Duplicate"):
        select_return_points(SPEC, [stored(source=source), stored(source=source)])


def test_incremental_start_uses_each_sources_last_stored_date_and_identity():
    rows = [
        stored(date(2024, 1, 20)),
        stored(date(2024, 1, 10), source="eastmoney"),
        stored(date(2025, 1, 1), index_code="WRONG"),
        stored(date(2025, 1, 1), basis="net_total_return"),
        stored(date(2025, 1, 1), symbol="WRONG"),
    ]
    assert source_start(TR, "csindex", "H00922", rows, incremental=True) == date(2024, 1, 6)
    assert source_start(TR, "eastmoney", "2.H00922", rows, incremental=True) == DAY
    assert source_start(TR, "csindex", "H00922", rows, incremental=False) == DAY
    assert source_start(TR, "unseen", "new", rows, incremental=True) == DAY
    assert source_start(TR, "csindex", "H00922", [], incremental=True) == DAY
    assert source_start(TR, "csindex", "H00922", rows, date(2023, 1, 1)) == DAY
    assert source_start(TR, "csindex", "H00922", rows, NEXT, incremental=True) == NEXT


class MemoryRepository:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.saved = []
        self.runs = []

    def ensure_index(self, spec):
        self.spec = spec

    def lock(self, key):
        return nullcontext()

    def start_run(self, spec, end):
        return len(self.runs) + 1

    def finish_run(self, run_id, status, report):
        self.runs.append((run_id, status, report))

    def total_return_observations(self, spec):
        return self.rows

    def total_return_points(self, spec):
        return select_return_points(spec, self.rows)

    def save_total_return_observations(self, spec, run_id, source, symbol, observations):
        self.saved.append((source, symbol, list(observations)))
        for item in observations:
            identity = (
                spec.total_return.code,
                spec.total_return.basis,
                source,
                symbol,
                item.trade_date,
            )
            self.rows = [
                row
                for row in self.rows
                if tuple(
                    row[key] for key in ("index_code", "basis", "source", "symbol", "trade_date")
                )
                != identity
            ]
            self.rows.append(
                stored(
                    item.trade_date,
                    str(item.price),
                    source,
                    symbol=symbol,
                    artifact_path=item.artifact_path,
                )
            )


class FakeSources:
    def __init__(self, batches, sessions=(DAY, NEXT)):
        self.batches = batches
        self.sessions = set(sessions)
        self.calls = []
        self.calendar_calls = []

    def trading_days(self, spec, start, end):
        self.calendar_calls.append((start, end))
        return {day for day in self.sessions if start <= day <= end}, {"source": "fixture"}

    def history(self, spec, source, symbol, start, end):
        self.calls.append((source, symbol, start, end))
        batch = self.batches[source]
        if isinstance(batch, Exception):
            raise batch
        return batch


def complete_batches():
    return {
        source: ReferenceHistory(
            [observation(DAY, source=source), observation(NEXT, "110", source)]
        )
        for source in ("csindex", "eastmoney")
    }


def test_collector_upserts_overlapping_dates_and_keeps_provenance(tmp_path):
    repo = MemoryRepository([stored(value="99")])
    sources = FakeSources(complete_batches())
    collector = TotalReturnCollector(repo, sources, tmp_path)
    report = collector.run(SPEC, end=NEXT, incremental=True)
    assert report["status"] == "collected" and report["stored_rows"] == 2
    assert len(repo.rows) == 4 and select_return_points(SPEC, repo.rows)[0]["price"] == 100
    assert all(point["price_status"] == "pass" for point in repo.total_return_points(SPEC))
    assert report["missing_primary_sessions"] == 0
    assert all(source["missing_sessions"] == 0 for source in report["sources"].values())
    collector.run(SPEC, end=NEXT, incremental=True)
    assert len(repo.rows) == 4
    assert len(repo.runs) == 2
    path = tmp_path / "dividend-total-return-1.json"
    saved_report = json.loads(path.read_text())
    assert saved_report["index_code"] == TR.code
    assert saved_report["basis"] == "gross_total_return"
    assert saved_report["identity_url"] == TR.identity_url
    assert repo.rows[0]["artifact_path"].startswith("raw/")


def test_collector_quarantines_off_calendar_and_out_of_range_rows(tmp_path):
    batches = complete_batches()
    batches["csindex"].observations.extend(
        [
            observation(date(2024, 1, 6)),
            observation(date(2024, 1, 1)),
            observation(date(2099, 1, 1)),
        ]
    )
    repo = MemoryRepository()
    report = TotalReturnCollector(repo, FakeSources(batches), tmp_path).run(
        SPEC, end=date(2024, 1, 6)
    )
    assert {row["date"] for row in report["quarantined_rows"]} == {
        date(2024, 1, 1),
        date(2024, 1, 6),
        date(2099, 1, 1),
    }
    assert {row["trade_date"] for row in repo.rows} == {DAY, NEXT}
    assert all(row["artifact_path"] for row in report["quarantined_rows"])
    assert report["status"] == "partial"


@pytest.mark.parametrize("failed", ["csindex", "eastmoney"])
def test_source_failure_preserves_old_rows_and_saves_the_other_source(tmp_path, failed):
    rows = [stored(), stored(source="eastmoney")]
    repo = MemoryRepository(rows)
    batches = complete_batches()
    batches[failed] = SourceError("read-only quote permission denied")
    report = TotalReturnCollector(repo, FakeSources(batches), tmp_path).run(SPEC, end=NEXT)
    assert report["status"] == "partial"
    assert report["sources"][failed]["status"] == "failed"
    assert "permission denied" in report["sources"][failed]["error"]
    assert len(repo.rows) == 3
    assert [row for row in repo.rows if row["source"] == failed] == [
        row for row in rows if row["source"] == failed
    ]
    assert len(repo.saved) == 1 and repo.saved[0][0] != failed


def test_collector_gap_is_partial_and_never_uses_reference_as_primary(tmp_path):
    batches = complete_batches()
    batches["csindex"] = ReferenceHistory([observation()])
    repo = MemoryRepository()
    report = TotalReturnCollector(repo, FakeSources(batches), tmp_path).run(SPEC, end=NEXT)
    assert report["sources"]["csindex"]["missing_sessions"] == 1
    assert report["missing_primary_sessions"] == 1
    assert report["status"] == "partial"
    assert [point["trade_date"] for point in repo.total_return_points(SPEC)] == [DAY]


@pytest.mark.parametrize(
    "bad", [observation(currency="USD"), observation(source="wrong"), observation()]
)
def test_collector_rejects_wrong_source_currency_or_duplicate_without_partial_batch_write(
    tmp_path, bad
):
    batches = complete_batches()
    batches["csindex"] = ReferenceHistory([observation(), bad])
    repo = MemoryRepository()
    report = TotalReturnCollector(repo, FakeSources(batches), tmp_path).run(SPEC, end=NEXT)
    assert report["sources"]["csindex"]["status"] == "failed"
    assert not any(row["source"] == "csindex" for row in repo.rows)
    assert len(repo.rows) == 2


@pytest.mark.parametrize(
    "spec,status",
    [
        (replace(SPEC, total_return=None), "unsupported"),
        (replace(SPEC, collection_enabled=False, collection_disabled_reason="paused"), "blocked"),
    ],
)
def test_collector_unconfigured_or_paused_never_calls_sources(tmp_path, spec, status):
    sources = FakeSources({})
    repo = MemoryRepository([stored()])
    report = TotalReturnCollector(repo, sources, tmp_path).run(spec, end=NEXT)
    assert report["status"] == status
    assert sources.calls == sources.calendar_calls == repo.saved == []
    assert len(repo.rows) == 1


def test_collector_future_date_is_rejected_before_repository_or_source_use(tmp_path):
    with pytest.raises(DataError, match="completed daily close"):
        TotalReturnCollector(None, None, tmp_path).run(SPEC, end=date(2099, 1, 1))


def test_collector_today_before_close_is_rejected_using_the_index_timezone(tmp_path, monkeypatch):
    class Morning(datetime):
        @classmethod
        def now(cls, tz):
            assert str(tz) == "Asia/Shanghai"
            return datetime(2024, 1, 3, 10, tzinfo=tz)

    monkeypatch.setattr("index_valuation.total_returns.datetime", Morning)
    with pytest.raises(DataError, match="completed daily close"):
        TotalReturnCollector(None, None, tmp_path).run(SPEC, end=NEXT)


def test_collector_partial_batch_saves_valid_rows_and_retains_source_failure_details(tmp_path):
    batches = complete_batches()
    failure = {"category": "unsupported_historical_coverage", "error": "rolling window only"}
    batches["csindex"].failures.append(failure)
    repo = MemoryRepository()
    report = TotalReturnCollector(repo, FakeSources(batches), tmp_path).run(SPEC, end=NEXT)
    assert report["status"] == "partial"
    assert report["sources"]["csindex"]["rows"] == 2
    assert report["sources"]["csindex"]["failures"] == [failure]
    assert len(repo.rows) == 4


def test_collector_calendar_failure_saves_no_rows_and_finishes_failed_run(tmp_path):
    class NoCalendar(FakeSources):
        def trading_days(self, *args):
            raise SourceError("calendar unavailable")

    repo = MemoryRepository([stored()])
    sources = NoCalendar({})
    report = TotalReturnCollector(repo, sources, tmp_path).run(SPEC, end=NEXT)
    assert report["status"] == "failed" and "calendar unavailable" in report["error"]
    assert sources.calls == repo.saved == []
    assert repo.rows == [stored()]
    assert repo.runs[0][1] == "failed"


@pytest.mark.parametrize("cached", [True, False])
def test_mainland_return_calendar_reuses_verified_cache_then_futu(monkeypatch, cached):
    calls = []

    class BaoStock:
        def __init__(self, *args):
            pass

        def __enter__(self):
            raise SourceError("temporary outage")

        def __exit__(self, *_):
            pass

        def cached_trading_days(self, start, end):
            calls.append("cache")
            if not cached:
                raise SourceError("no complete cache")
            return {DAY, NEXT}, "cached-calendar.json"

    class Futu:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def trading_days(self, start, end, market):
            calls.append(market)
            return {DAY, NEXT}, "futu-calendar.json"

    monkeypatch.setattr("index_valuation.providers.total_return.BaoStockSource", BaoStock)
    sources = TotalReturnSources(SimpleNamespace(source_timeout=5), None)
    monkeypatch.setattr(sources, "futu", Futu)
    days, report = sources.trading_days(SPEC, DAY, NEXT)
    assert days == {DAY, NEXT}
    assert calls == (["cache"] if cached else ["cache", "CN"])
    assert report["source"] == ("baostock" if cached else "futu")
    assert "temporary outage" in report["reason"]


def test_us_return_calendar_uses_market_sessions_without_open_d(monkeypatch):
    class Calendar:
        def __init__(self, archive):
            pass

        def trading_days(self, start, end, market):
            assert market == "US"
            return {NEXT}, "nyse-calendar.json"

    monkeypatch.setattr("index_valuation.providers.total_return.USCalendar", Calendar)
    sources = TotalReturnSources(None, None)
    days, report = sources.trading_days(replace(SPEC, quote_code="US..NDX"), DAY, NEXT)
    assert days == {NEXT} and report["source"] == "pandas_market_calendars"


def test_collector_incremental_fetches_new_source_from_its_own_start(tmp_path):
    repo = MemoryRepository([stored(date(2024, 1, 25))])
    sources = FakeSources({"csindex": ReferenceHistory(), "eastmoney": ReferenceHistory()})
    TotalReturnCollector(repo, sources, tmp_path).run(SPEC, end=date(2024, 1, 26), incremental=True)
    assert sources.calendar_calls == [(DAY, date(2024, 1, 26))]
    assert sources.calls == [
        ("csindex", "H00922", date(2024, 1, 11), date(2024, 1, 26)),
        ("eastmoney", "2.H00922", DAY, date(2024, 1, 26)),
    ]


@pytest.mark.parametrize(
    "source,symbol,module_name,class_name",
    [
        ("csindex", "H00922", "csindex_returns", "CSITotalReturnHistory"),
        ("cnindex", "H00922", "total_return", "CNIndexHistory"),
        ("eastmoney", "2.H00922", "total_return", "EastmoneySource"),
        ("futu", "SH.H00922", "total_return", "FutuSource"),
        ("nasdaq", "XNDX", "nasdaq_returns", "NasdaqTotalReturnHistory"),
        ("hsi", "12083.00", "hsi_returns", "HSITotalReturnHistory"),
    ],
)
def test_source_dispatch_uses_total_return_identity_without_mutating_valuation_spec(
    monkeypatch, source, symbol, module_name, class_name
):
    is_us, is_hk = source == "nasdaq", source == "hsi"
    tr = replace(
        TR, source=source, symbol=symbol, code="XNDX" if is_us else "HSTECHT" if is_hk else TR.code
    )
    spec = replace(
        SPEC,
        total_return=tr,
        quote_code="US..NDX" if is_us else "HK.800700" if is_hk else SPEC.quote_code,
        currency="USD" if is_us else "HKD" if is_hk else "CNY",
        timezone="America/New_York" if is_us else "Asia/Hong_Kong" if is_hk else "Asia/Shanghai",
    )
    queries = []

    class Reader:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def index_history(self, query, start, end):
            queries.append(query)
            rows = [observation(source=source, symbol=query.quote_code, currency=query.currency)]
            return rows if source in {"futu", "eastmoney"} else ReferenceHistory(rows)

    monkeypatch.setattr(f"index_valuation.providers.{module_name}.{class_name}", Reader)
    settings = SimpleNamespace(source_timeout=5, futu_host="localhost", futu_port=11111)
    result = TotalReturnSources(settings, None).history(spec, source, symbol, DAY, NEXT)
    assert len(queries) == 1
    query = queries[0]
    assert query.total_return is None and query.valuation_source == "none"
    assert query.provider_code == (symbol if source in {"csindex", "cnindex", "hsi"} else tr.code)
    assert query.name == tr.name and query.quote_code != spec.quote_code
    assert result.observations[0].price_basis == "gross_total_return"
    assert spec.total_return == tr and spec.valuation_source == "futu"


def test_source_adapter_rejects_an_incorrect_instrument(monkeypatch):
    class Reader:
        def __init__(self, *args):
            pass

        def index_history(self, *args):
            return ReferenceHistory([observation(symbol=SPEC.quote_code)])

    monkeypatch.setattr("index_valuation.providers.csindex_returns.CSITotalReturnHistory", Reader)
    sources = TotalReturnSources(SimpleNamespace(source_timeout=5), None)
    with pytest.raises(DataError, match="different instrument"):
        sources.history(SPEC, "csindex", "H00922", DAY, NEXT)
    with pytest.raises(DataError, match="No configured"):
        sources.history(replace(SPEC, total_return=None), "csindex", "H00922", DAY, NEXT)


def registry_config(tmp_path, **changes):
    values = {
        "code": "H00922",
        "name": "中证红利全收益指数",
        "source": "csindex",
        "symbol": "H00922",
        "history_start_date": "2004-12-31",
        "identity_url": "https://www.csindex.com.cn/",
        **changes,
    }
    content = '[indices.example]\nname="中证红利"\nprovider_code="000922"\nquote_code="SH.000922"\n'
    content += "[indices.example.total_return]\n"
    content += "\n".join(
        f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in values.items()
    )
    path = tmp_path / "indices.toml"
    path.write_text(content)
    return path


def test_registry_loads_a_documented_separate_gross_return_identity(tmp_path):
    spec = load_indices(registry_config(tmp_path))["example"]
    assert spec.total_return.code == "H00922"
    assert spec.total_return.history_start_date == date(2004, 12, 31)
    assert spec.total_return.basis == "gross_total_return"


@pytest.mark.parametrize(
    "changes",
    [
        {"basis": "net_total_return"},
        {"basis": "price_return"},
        {"code": "000922"},
        {"code": "bad.code"},
        {"name": " "},
        {"identity_url": "http://www.csindex.com.cn/"},
        {"source": "nasdaq"},
        {"source": "unknown"},
        {"verification_source": "eastmoney"},
        {"verification_symbol": "2.H00922"},
        {"verification_source": "csindex", "verification_symbol": "H00922"},
        {"verification_source": "fred", "verification_symbol": "NASDAQXNDX"},
        {"source": "futu", "symbol": "invalid"},
        {"source": "eastmoney", "symbol": "H00922"},
        {"source": "eastmoney", "symbol": "2.000922"},
        {"symbol": "WRONG"},
        {"source": "futu", "symbol": "US.XNDX"},
        {"source": "futu", "symbol": "SH.000922"},
    ],
)
def test_registry_rejects_wrong_total_return_basis_identity_or_market_source(tmp_path, changes):
    with pytest.raises(DataError):
        load_indices(registry_config(tmp_path, **changes))
