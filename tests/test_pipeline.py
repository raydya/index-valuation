from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text, update

from index_valuation.config import Settings
from index_valuation.db import Base, build_engine
from index_valuation.domain import (
    Constituent,
    DataError,
    IndexSpec,
    Observation,
    SourceError,
    SourceValuation,
    WeightSnapshot,
)
from index_valuation.models import (
    DailyFundamental,
    DailyObservation,
    DailyValuation,
    FundamentalBatch,
    RunRecord,
    SourceValuationRecord,
    WeightMember,
    WeightRecord,
)
from index_valuation.pipeline import Pipeline
from index_valuation.repository import Repository

pytestmark = pytest.mark.integration
DAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 12, 13, tzinfo=UTC)
SPEC = IndexSpec("test_index", "Test", "000300", "SH.000300", 2)
MEMBERS = (Constituent("SH.600001", "A", Decimal(60)), Constituent("SZ.000001", "B", Decimal(40)))


@pytest.fixture
def repository():
    settings = Settings()
    admin = build_engine(settings)
    schema = "test_valuation_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        settings.database_url,
        connect_args={
            "options": f"-c search_path={schema} -c timezone=UTC",
            "connect_timeout": settings.postgres_connect_timeout,
        },
    )
    try:
        Base.metadata.create_all(engine)
        yield Repository(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class Weights:
    def fetch(self, spec):
        return WeightSnapshot(
            DAY, MEMBERS, "csindex", "https://example.test/weights", "test", "test.xls"
        )


class Market:
    def __init__(self, source, *, missing=False, failure=False, trading=True):
        self.source, self.missing, self.failure, self.trading = source, missing, failure, trading

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def members(self, spec):
        return {item.symbol for item in MEMBERS}, "test-members.json"

    def is_trading_day(self, spec, day):
        return self.trading

    def daily(self, spec, symbols, day):
        if self.failure:
            raise SourceError("CN MarketStocks permission unavailable")
        rows = []
        for symbol in symbols:
            if self.missing and symbol == "SZ.000001":
                continue
            is_index = symbol == spec.quote_code
            price = Decimal(100 if is_index else (10 if symbol.startswith("SH") else 20))
            rows.append(
                Observation(
                    symbol,
                    day,
                    self.source,
                    price,
                    None if is_index else Decimal(1),
                    None if is_index else price,
                    NOW,
                    NOW,
                )
            )
        return rows, []


def pipeline(repository, tmp_path, monkeypatch, *, missing=False, failure=False, trading=True):
    monkeypatch.setattr("index_valuation.pipeline.datetime", FixedClock)
    return Pipeline(
        repository,
        Weights(),
        lambda: Market("futu", failure=failure, trading=trading),
        lambda: Market("baostock", missing=missing),
        tmp_path,
    )


def count(repository, model):
    with repository.engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(model))


def test_repeated_daily_run_updates_data_and_preserves_run_audit(repository, tmp_path, monkeypatch):
    job = pipeline(repository, tmp_path, monkeypatch)
    first, second = job.run_daily(SPEC), job.run_daily(SPEC)
    assert first["pe_ttm"] == second["pe_ttm"] == Decimal("12.5")
    assert second["status"] == "verified"
    assert first["run_id"] != second["run_id"]
    assert count(repository, WeightRecord) == 1
    assert count(repository, WeightMember) == 2
    assert count(repository, DailyObservation) == 6
    assert count(repository, DailyValuation) == 1
    assert count(repository, RunRecord) == 2
    replay = job.recalculate(SPEC, DAY)
    assert replay["pe_ttm"] == Decimal("12.5")


def test_a_second_index_uses_the_same_workflow_and_reuses_security_observations(
    repository, tmp_path, monkeypatch
):
    job = pipeline(repository, tmp_path, monkeypatch)
    other = replace(SPEC, key="another_index", name="Another", quote_code="SH.000905")
    assert job.run_daily(SPEC)["status"] == "verified"
    assert job.run_daily(other)["status"] == "verified"
    assert count(repository, WeightRecord) == 2
    assert count(repository, DailyObservation) == 8  # two stocks plus two indices, two sources
    assert count(repository, DailyValuation) == 2


def test_missing_independent_data_blocks_pe_and_remains_auditable(
    repository, tmp_path, monkeypatch
):
    report = pipeline(repository, tmp_path, monkeypatch, missing=True).run_daily(SPEC)
    assert report["status"] == "blocked"
    assert report["pe_ttm"] is None
    assert report["check_summary"]["missing"] == 2
    assert repository.latest_report(SPEC.key)["status"] == "blocked"


def test_permission_failure_never_falls_back_to_secondary_as_primary(
    repository, tmp_path, monkeypatch
):
    report = pipeline(repository, tmp_path, monkeypatch, failure=True).run_daily(SPEC)
    assert report["status"] == "failed"
    assert "permission" in report["error"]
    assert count(repository, WeightMember) == 2
    assert count(repository, DailyObservation) == 0


def test_holidays_do_not_create_fake_market_rows(repository, tmp_path, monkeypatch):
    report = pipeline(repository, tmp_path, monkeypatch, trading=False).run_daily(SPEC)
    assert report["status"] == "skipped"
    assert count(repository, DailyObservation) == 0


def test_job_lock_excludes_overlapping_runs_and_is_released(repository):
    with repository.lock(SPEC.key):
        with pytest.raises(DataError, match="already running"), repository.lock(SPEC.key):
            pass
    with repository.lock(SPEC.key):
        pass


def test_snapshot_selection_does_not_use_future_weights(repository):
    repository.ensure_index(SPEC)
    snapshot = Weights().fetch(SPEC)
    repository.save_snapshot(SPEC, snapshot, {"status": "pass"})
    future = replace(snapshot, as_of=date(2026, 11, 1), sha256="future")
    repository.save_snapshot(SPEC, future, {"status": "pass"})
    assert repository.snapshot(SPEC.key, DAY)[1].as_of == DAY


def test_band_history_excludes_current_day_and_future_results(repository, tmp_path, monkeypatch):
    pipeline(repository, tmp_path, monkeypatch).run_daily(SPEC)
    assert repository.history(SPEC, DAY) == []
    assert repository.history(SPEC, date(2026, 10, 13)) == [Decimal("12.5")]


class HistoryMarket:
    def __init__(self, source, failure=False):
        self.source, self.failure = source, failure

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def trading_days(self, start, end):
        return {date(2005, 4, 8), date(2005, 4, 11)}, "calendar.json"

    def index_history(self, spec, start, end):
        if self.failure:
            raise SourceError("historical quota unavailable")
        days = sorted(self.trading_days(start, end)[0])
        if self.source == "futu":
            days = days[1:]  # primary missing inception day
        return [
            Observation(
                spec.quote_code,
                day,
                self.source,
                Decimal(1000),
                None,
                None,
                NOW,
                NOW,
                eps_method="not_applicable",
            )
            for day in days
        ]

    def valuation_history(self, spec, start, end):
        return [
            SourceValuation(
                self.source, spec.quote_code, day, "pe", "vendor_pe", Decimal(15), NOW, "pe.json"
            )
            for day in sorted(self.trading_days(start, end)[0])
        ]


class OfficialHistory:
    source_name = "csindex"

    def index_history(self, spec, start, end):
        from index_valuation.domain import ReferenceHistory

        source = HistoryMarket("csindex")
        return ReferenceHistory(
            source.index_history(spec, start, end), source.valuation_history(spec, start, end)
        )


def test_historical_backfill_is_idempotent_and_does_not_invent_component_pe(repository, tmp_path):
    from index_valuation.history import HistoricalBackfill

    spec = replace(SPEC, inception_date=date(2005, 4, 8))
    repository.ensure_index(spec)
    repository.save_snapshot(spec, Weights().fetch(spec), {"status": "pass"})
    job = HistoricalBackfill(
        repository,
        lambda: HistoryMarket("futu"),
        lambda: HistoryMarket("baostock"),
        OfficialHistory(),
        tmp_path,
    )
    first = job.run(spec, end=date(2005, 4, 11))
    second = job.run(spec, end=date(2005, 4, 11))
    assert first["run_id"] != second["run_id"]
    assert second["status"] == "partial"
    assert second["sources"]["futu_price"]["missing_sessions"] == 1
    assert second["first_session"]["futu_price"] is None
    assert second["first_session"]["baostock_price"] == 1000
    assert second["component_recalculation"]["sessions_without_prior_weights"] == 2
    assert second["bands"] is None
    assert count(repository, DailyObservation) == 5
    assert count(repository, SourceValuationRecord) == 4
    assert count(repository, DailyValuation) == 0
    assert count(repository, RunRecord) == 2
    assert repository.history(spec, date(2005, 4, 12)) == []


def test_historical_price_failure_preserves_pe_and_secondary_without_promoting_them(
    repository, tmp_path
):
    from index_valuation.history import HistoricalBackfill

    job = HistoricalBackfill(
        repository,
        lambda: HistoryMarket("futu", failure=True),
        lambda: HistoryMarket("baostock"),
        OfficialHistory(),
        tmp_path,
    )
    report = job.run(SPEC, date(2005, 4, 8), date(2005, 4, 11))
    assert report["sources"]["futu_price"]["status"] == "failed"
    assert report["sources"]["futu_pe"]["rows"] == 2
    assert report["first_session"]["futu_price"] is None
    assert report["last_session"]["futu_price"] is None
    assert report["primary_source"] == "futu"
    assert count(repository, DailyValuation) == 0


@pytest.mark.parametrize("price_provider", ["eastmoney", "futu"])
def test_company_backfill_keeps_provenance_and_resumes_committed_symbols(
    repository, tmp_path, price_provider
):
    from index_valuation.fundamentals import (
        FundamentalBackfill,
        method_for,
        price_observation_source,
    )

    spec = replace(SPEC, inception_date=date(2005, 4, 8), historical_price_source=price_provider)
    price_source = price_observation_source(price_provider)
    repository.ensure_index(spec)
    repository.save_snapshot(spec, Weights().fetch(spec), {"status": "pass"})
    day, calls = date(2005, 4, 8), []
    prior_run = repository.start_run(spec, day)
    repository.save_observations(
        prior_run,
        [
            Observation(m.symbol, day, "futu", Decimal(100), Decimal(4), Decimal(25), NOW, NOW)
            for m in MEMBERS
        ],
    )

    class Source(HistoryMarket):
        def trading_days(self, start, end):
            return {day}, "calendar.json"

        def security_dates(self, symbols):
            return {s: {"listed": day, "delisted": None} for s in symbols}, "profiles.json"

        def price_history(self, spec, symbol, start, end):
            return [Observation(symbol, day, price_source, Decimal(100), None, None, NOW, NOW)]

        def company_pe_history(self, spec, symbol, start, end):
            calls.append(symbol)
            return [
                SourceValuation(
                    "futu",
                    symbol,
                    day,
                    "pe_ttm",
                    "futu_company_pe_ttm_v1",
                    Decimal(20),
                    NOW,
                    "pe.json",
                )
            ]

        def company_history(self, spec, symbol, start, end):
            return [
                Observation(
                    symbol, day, "baostock", Decimal(100), Decimal(5), Decimal(20), NOW, NOW
                )
            ]

    job = FundamentalBackfill(
        repository,
        lambda: Source("futu"),
        lambda: Source("baostock"),
        lambda: Source("eastmoney"),
        tmp_path,
    )
    first, second = job.run(spec, end=day), job.run(spec, end=day)
    assert first["status"] == second["status"] == "complete"
    assert len(calls) == 2
    assert second["resumed_symbols"] == 2
    assert count(repository, DailyFundamental) == 2
    assert count(repository, FundamentalBatch) == 2
    assert count(repository, DailyValuation) == 0
    rows = repository.fundamentals(
        list(repository.constituent_universe(spec.key)), day, method_for(price_provider)
    )
    assert all(
        row.price_source == price_source and row.pe_source == "futu" for row in rows.values()
    )
    assert all(row.eps_ttm == 5 for row in rows.values())
    original = repository.observations("futu", day, [m.symbol for m in MEMBERS])
    assert all(row.eps_ttm == 4 for row in original.values())

    calculator = Pipeline(repository, None, None, None, tmp_path)
    unavailable = calculator.recalculate(spec, day, historical_fundamentals=True)
    assert unavailable["status"] == "failed"
    assert "No weight snapshot" in unavailable["error"]
    assert count(repository, DailyValuation) == 0  # present-day membership is not backdated

    historical_weights = replace(Weights().fetch(spec), as_of=day, sha256="historical-weights")
    repository.save_snapshot(spec, historical_weights, {"status": "pass"})
    repository.save_observations(
        UUID(first["run_id"]),
        [
            Observation(spec.quote_code, day, source, Decimal(1000), None, None, NOW, NOW)
            for source in ("futu", "baostock")
        ],
    )
    replay = calculator.recalculate(spec, day, historical_fundamentals=True)
    assert replay["status"] == "verified"
    assert replay["pe_ttm"] == 20
    assert replay["index_eps_ttm"] == 50
    assert replay["primary_source"] == f"futu_pe+{price_provider}_price"
    assert repository.history(spec, DAY) == []
    assert repository.history(spec, date(2005, 4, 11), replay["method"]) == [Decimal(20)]

    # A later reference PE correction must block replay even if stored EPS still agrees.
    with repository.engine.begin() as connection:
        connection.execute(
            update(DailyObservation)
            .where(
                DailyObservation.source == "baostock", DailyObservation.symbol == MEMBERS[0].symbol
            )
            .values(pe_ttm=Decimal(40))
        )
    assert calculator.recalculate(spec, day, historical_fundamentals=True)["status"] == "blocked"


def test_direct_index_materialization_is_independent_and_idempotent(repository, tmp_path):
    from index_valuation.index_series import METHOD, IndexSeries
    from index_valuation.models import IndexSeriesRecord

    repository.ensure_index(SPEC)
    run = repository.start_run(SPEC, DAY)
    repository.save_observations(
        run,
        [
            Observation(SPEC.quote_code, DAY, source, Decimal(1000), None, None, NOW, NOW)
            for source in ("futu", "eastmoney")
        ],
    )
    repository.save_source_valuations(
        run,
        [
            SourceValuation(
                "futu",
                SPEC.quote_code,
                DAY,
                "pe",
                "futu_valuation_pe",
                Decimal(20),
                NOW,
                "test.json",
            )
        ],
    )
    job = IndexSeries(repository, tmp_path)
    first, second = job.refresh(SPEC), job.refresh(SPEC)
    assert first["run_id"] != second["run_id"]
    assert second["latest"]["implied_eps"] == 50
    assert second["latest"]["fixed"]["10"] == 500
    assert count(repository, IndexSeriesRecord) == 1
    assert count(repository, WeightRecord) == count(repository, DailyValuation) == 0
    assert repository.index_series(SPEC.key, METHOD)[0]["pe"] == 20
    assert repository.history(SPEC, DAY + timedelta(days=1)) == []


def test_official_priority_rebuild_preserves_legacy_method_and_exports(repository, tmp_path):
    import csv
    from io import StringIO

    from fastapi.testclient import TestClient

    from index_valuation.index_series import IndexSeries, derive_points, method_for
    from index_valuation.models import IndexSeriesRecord
    from index_valuation.webapp import create_app

    spec = replace(SPEC, valuation_source="csindex")
    repository.ensure_index(spec)
    run = repository.start_run(spec, DAY)
    prices = [
        Observation(spec.quote_code, DAY, source, Decimal(value), None, None, NOW, NOW)
        for source, value in (("csindex", 1000), ("eastmoney", 2000))
    ]
    values = [
        SourceValuation(source, spec.quote_code, DAY, "pe_ttm", basis, Decimal(value), NOW, "raw")
        for source, basis, value in (
            ("csindex", "csindex_published_rolling_pe", 8),
            ("danjuan", "danjuan_published_pe_ttm", 10),
        )
    ]
    repository.save_observations(run, prices)
    repository.save_source_valuations(run, values)
    legacy_method = "csindex_index_implied_daily_v1"
    legacy = {**derive_points(spec, prices, values)[0], "implied_eps": None, "pe_usable": False}
    repository.save_index_series(spec, legacy_method, run, [legacy])

    job = IndexSeries(repository, tmp_path)
    job.refresh(spec)
    result = job.refresh(spec)
    assert result["status"] == "collected"
    assert result["latest_complete_date"] == DAY
    assert count(repository, IndexSeriesRecord) == 2
    assert count(repository, DailyObservation) == count(repository, SourceValuationRecord) == 2
    assert repository.index_series(spec.key, legacy_method)[0]["implied_eps"] is None
    current = repository.index_series(spec.key, method_for(spec))[0]
    assert current["implied_eps"] == 125
    assert current["price_status"] == current["pe_comparison_status"] == "error"
    assert current["price_usable"] and current["pe_usable"]

    with TestClient(create_app(repository=repository, specs={spec.key: spec})) as client:
        data = client.get(f"/api/indices/{spec.key}/dashboard").json()
        assert data["method"] == method_for(spec) != legacy_method
        assert Decimal(data["selected"]["fixed"]["8"]) == 1000
        response = client.get(f"/api/indices/{spec.key}/export.csv")
    row = list(csv.DictReader(StringIO(response.text.lstrip("\ufeff"))))[0]
    assert row["price_usable"] == row["pe_usable"] == "True"
    assert Decimal(row["implied_eps"]) == 125
    assert Decimal(row["band_8x"]) == 1000
    assert row["price_status"] == row["pe_comparison_status"] == "error"


@pytest.mark.parametrize(
    "key,source_prices,status,legacy_source",
    [
        ("sp500", (("fred", 1000), ("yahoo", 2000)), "error", "danjuan"),
        ("ustotal", (("yahoo", 1000), ("google", 1000)), "pass", "none"),
    ],
)
def test_etf_proxy_rebuild_preserves_legacy_and_upserts_snapshot_metadata(
    repository, tmp_path, key, source_prices, status, legacy_source
):
    import csv
    from io import StringIO

    from fastapi.testclient import TestClient

    from index_valuation.index_series import IndexSeries, derive_points, method_for
    from index_valuation.models import IndexSeriesRecord
    from index_valuation.providers.yahoo_etf_valuation import DATE_BASIS, basis_for
    from index_valuation.registry import load_indices
    from index_valuation.webapp import create_app

    # Retired Yahoo methods remain reproducible after deployment source changes.
    spec = replace(
        load_indices()[key],
        key="test_us_proxy",
        valuation_source="yahooetf",
        yahoo_proxy_symbol="SPY" if key == "sp500" else "VTI",
        official_price_source="fred" if key == "sp500" else None,
        price_sources=tuple(source for source, _ in source_prices),
    )
    ticker = spec.yahoo_proxy_symbol
    repository.ensure_index(spec)
    run = repository.start_run(spec, DAY)
    prices = [
        Observation(
            spec.quote_code, DAY, source, Decimal(value), None, None, NOW, NOW, currency="USD"
        )
        for source, value in (*source_prices, ("eastmoney", 3000))
    ]
    old = SourceValuation(
        "danjuan",
        spec.quote_code,
        DAY,
        "pe_ttm",
        "danjuan_published_pe_ttm",
        Decimal(30),
        NOW,
        "old.json",
    )
    proxy = SourceValuation(
        "yahooetf",
        spec.quote_code,
        DAY,
        "pe_ttm_proxy",
        basis_for(ticker),
        Decimal(20),
        NOW,
        "etf.json",
        raw={"proxy_symbol": ticker, "date_basis": DATE_BASIS, "pe_effective_date": None},
    )
    repository.save_observations(run, prices)
    repository.save_source_valuations(run, [old, proxy])
    legacy_spec = replace(
        spec, valuation_source=legacy_source, official_price_source=None, price_sources=()
    )
    legacy_method = method_for(legacy_spec)
    repository.save_index_series(
        spec, legacy_method, run, derive_points(legacy_spec, prices, [old])
    )
    old_csv = tmp_path / f"{spec.key}-index-bands-{DAY}.csv"
    old_csv.write_text("legacy export")
    job = IndexSeries(repository, tmp_path)
    first = job.refresh(spec)
    repository.save_source_valuations(run, [replace(proxy, value=Decimal(25))])
    report = job.refresh(spec)
    assert report["csv"] == first["csv"] != str(old_csv)
    assert old_csv.read_text() == "legacy export"
    assert count(repository, IndexSeriesRecord) == 2
    assert count(repository, SourceValuationRecord) == 2
    assert repository.index_series(spec.key, legacy_method)[0]["pe"] == (
        30 if legacy_source == "danjuan" else None
    )
    with TestClient(create_app(repository=repository, specs={spec.key: spec})) as client:
        data = client.get(f"/api/indices/{spec.key}/dashboard").json()
        point = data["selected"]
        assert point["basis_status"] == "etf_proxy" and point["pe_proxy_symbol"] == ticker
        assert Decimal(point["pe"]) == 25 and Decimal(point["implied_eps"]) == 40
        assert point["price_status"] == status and point["price_usable"]
        assert point["pe_date_basis"] == DATE_BASIS
        assert set(point["source_prices"]) == set(spec.price_sources)
        response = client.get(f"/api/indices/{spec.key}/export.csv")
    row = list(csv.DictReader(StringIO(response.text.lstrip("\ufeff"))))[0]
    assert row["pe_proxy_symbol"] == ticker and row["pe_date_basis"] == DATE_BASIS
    assert row["pe_observed_at"] and Decimal(row["band_8x"]) == 320


def test_rest_rebuild_preserves_proxy_history_and_upserts_source_values(repository, tmp_path):
    import csv
    from io import StringIO

    from fastapi.testclient import TestClient

    from index_valuation.index_series import IndexSeries, derive_points, method_for
    from index_valuation.models import IndexSeriesRecord
    from index_valuation.registry import load_indices
    from index_valuation.webapp import create_app

    spec = replace(load_indices()["sp500"], key="test_us_rest")
    legacy_spec = replace(
        spec,
        valuation_source="yahooetf",
        yahoo_proxy_symbol="SPY",
        official_price_source="fred",
        price_sources=("fred", "yahoo"),
    )
    repository.ensure_index(spec)
    run = repository.start_run(spec, DAY)
    prices = [
        Observation(
            spec.quote_code, DAY, source, Decimal(1000), None, None, NOW, NOW, currency="USD"
        )
        for source in ("futorest", "fred", "yahoo")
    ]
    old = SourceValuation(
        "yahooetf",
        spec.quote_code,
        DAY,
        "pe_ttm_proxy",
        "yahoo_etf_spy_trailing_pe_proxy",
        Decimal(30),
        NOW,
        "legacy.json",
        raw={"proxy_symbol": "SPY"},
    )
    pe = SourceValuation(
        "futorest",
        spec.quote_code,
        DAY,
        "pe",
        "futorest_valuation_pe",
        Decimal(20),
        NOW,
        "rest-pe.json",
    )
    pb = replace(pe, metric="pb", basis="futorest_valuation_pb", value=Decimal(3))
    repository.save_observations(run, prices)
    repository.save_source_valuations(run, [old, pe, pb])
    legacy_method = method_for(legacy_spec)
    repository.save_index_series(
        spec, legacy_method, run, derive_points(legacy_spec, prices, [old])
    )
    legacy_csv = tmp_path / f"{spec.key}-index-bands-{DAY}-{legacy_method}.csv"
    legacy_csv.write_text("legacy proxy export")

    job = IndexSeries(repository, tmp_path)
    first = job.refresh(spec)
    repository.save_source_valuations(run, [replace(pe, value=Decimal(25))])
    report = job.refresh(spec)
    assert report["status"] == "collected" and report["latest"]["price_source"] == "futorest"
    assert report["csv"] == first["csv"] != str(legacy_csv)
    assert legacy_csv.read_text() == "legacy proxy export"
    assert count(repository, IndexSeriesRecord) == 2
    assert count(repository, SourceValuationRecord) == 3
    assert count(repository, DailyObservation) == 3
    assert repository.index_series(spec.key, legacy_method)[0]["pe"] == 30

    with TestClient(create_app(repository=repository, specs={spec.key: spec})) as client:
        data = client.get(f"/api/indices/{spec.key}/dashboard").json()
        point = data["selected"]
        assert data["method"] == "futorest_index_implied_daily_v1"
        assert Decimal(point["pe"]) == 25 and Decimal(point["implied_eps"]) == 40
        assert point["pe_source"] == point["price_source"] == "futorest"
        assert point["basis_status"] == "unconfirmed" and point["pe_proxy_symbol"] is None
        assert set(point["source_prices"]) == {"futorest", "fred"}
        assert Decimal(point["pb"]) == 3 and point["price_status"] == "pass"
        response = client.get(f"/api/indices/{spec.key}/export.csv")
    row = list(csv.DictReader(StringIO(response.text.lstrip("\ufeff"))))[0]
    assert row["pe_basis"] == "futorest_valuation_pe" and row["pe_proxy_symbol"] == ""
    assert Decimal(row["band_8x"]) == 320


def test_rest_numeric_json_responses_survive_real_collection_and_jsonb_storage(
    repository, tmp_path
):
    import json
    from datetime import time
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    import httpx

    from index_valuation.archive import Archive
    from index_valuation.domain import ReferenceHistory
    from index_valuation.history import HistoricalBackfill
    from index_valuation.index_series import IndexSeries
    from index_valuation.providers.futu_rest import FutuRestHistory
    from index_valuation.providers.us_calendar import USCalendar
    from index_valuation.registry import load_indices

    spec = replace(load_indices()["sp500"], key="test_rest_json")
    day = date(2024, 7, 5)
    midnight = datetime.combine(day, time(), ZoneInfo(spec.timezone))
    close_value = 3000.123456789
    metric_values = {1: 25.123456789, 2: 4.123456789, 3: 2.123456789}
    identity = {
        "code": spec.quote_code,
        "name": "S&P 500 Index",
        "stock_type": "IDX",
        "exchange": "US",
    }

    def respond(request):
        if request.url.path.endswith("stock-basicinfo"):
            data = {"basic_list": [identity]}
            extra = {}
        elif request.url.path.endswith("history-kline"):
            data = {
                "kline_list": [
                    {
                        "date": 20240705,
                        "time_key": int(midnight.timestamp() * 1000),
                        "time_zone": -4,
                        "name": identity["name"],
                        "close": close_value,
                        "open": 2999.123456789,
                        "high": 3001.987654321,
                        "pe_ratio": 0.0,
                    }
                ]
            }
            extra = {"pagination": {"has_more": False}}
        else:
            assert request.url.path.endswith("valuation/detail")
            metric = int(request.url.params["valuation_type"])
            data = {
                "valuation_type": metric,
                "trend": {
                    "historical_items": [
                        {
                            "time_str": day.isoformat(),
                            "time": int(midnight.timestamp()),
                            "value": metric_values[metric],
                        }
                    ]
                },
            }
            extra = {}
        # Real JSON numbers, not fixture strings: the adapter parses these as Decimal.
        return httpx.Response(200, json={"ret_code": 0, "data": data, **extra})

    reference = SimpleNamespace(
        source_name="fred",
        index_history=lambda *_: ReferenceHistory(
            [
                Observation(
                    spec.quote_code,
                    day,
                    "fred",
                    Decimal(str(close_value)),
                    None,
                    None,
                    NOW,
                    NOW,
                    currency="USD",
                )
            ]
        ),
    )
    archive = Archive(tmp_path / "raw")
    with httpx.Client(
        base_url="https://rest.example.test", transport=httpx.MockTransport(respond)
    ) as client:
        job = HistoricalBackfill(
            repository,
            None,
            None,
            FutuRestHistory(archive, client),
            tmp_path / "exports",
            calendar_source=USCalendar(archive),
            additional_sources=(reference,),
        )
        first = job.run(spec, day, day)
        second = job.run(spec, day, day)
    assert first["status"] != "failed" and second["status"] != "failed"
    for metric in ("price", "pe", "pb", "ps"):
        assert second["sources"][f"futorest_{metric}"]["rows"] == 1
        assert second["sources"][f"futorest_{metric}"]["status"] == "ok"
    assert count(repository, DailyObservation) == 2
    assert count(repository, SourceValuationRecord) == 3
    observations, valuations = repository.index_inputs(spec)
    saved_price = next(row for row in observations if row.source == "futorest")
    assert saved_price.price == Decimal(str(close_value))
    assert Decimal(saved_price.raw["close"]) == saved_price.price
    assert Decimal(saved_price.raw["high"]) == Decimal("3001.987654321")
    json.dumps(saved_price.raw)
    for row in valuations:
        assert Decimal(row.raw["value"]) == row.value
        json.dumps(row.raw)
    report = IndexSeries(repository, tmp_path / "exports").refresh(spec)
    assert report["status"] == "collected"
    assert report["latest"]["pe"] == Decimal(str(metric_values[1]))
    assert report["latest"]["implied_eps"] == Decimal(str(close_value)) / Decimal(
        str(metric_values[1])
    )
