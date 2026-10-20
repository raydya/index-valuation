from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text, update

from index_valuation.db import Base
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
def repository(test_database_url, test_database_engine):
    admin = test_database_engine
    schema = "test_valuation_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        test_database_url,
        connect_args={
            "options": f"-c search_path={schema} -c timezone=UTC",
            "connect_timeout": 5,
        },
    )
    try:
        Base.metadata.create_all(engine)
        yield Repository(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


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


def test_panic_line_jsonb_restores_precise_values_and_dates(repository):
    from index_valuation.index_series import method_for
    from index_valuation.panic_lines import enrich_panic_lines

    spec = replace(SPEC, band_min_observations=2)
    repository.ensure_index(spec)
    run = repository.start_run(spec, DAY)
    points = enrich_panic_lines(
        spec,
        [
            {
                "trade_date": day,
                "price": Decimal(1000),
                "pe": Decimal(value),
                "implied_eps": Decimal("50.123456789012345678901234567"),
                "pe_source": "futu",
                "pe_basis": "futu_valuation_pe",
                "pe_usable": True,
                "phase": "published",
                "legacy_detail": {"value": Decimal("1.25")},
            }
            for day, value in (
                (date(2019, 1, 1), "10.123456789012345678901234567"),
                (date(2020, 1, 1), "20.234567890123456789012345678"),
                (date(2024, 1, 1), "30.345678901234567890123456789"),
            )
        ],
    )
    repository.save_index_series(spec, method_for(spec), run, points)

    stored = repository.index_series(spec.key, method_for(spec))
    assert stored[-1]["panic_line"] == points[-1]["panic_line"]
    assert stored[0]["panic_line"] == points[0]["panic_line"]
    assert stored[-1]["legacy_detail"] == {"value": "1.25"}
    assert isinstance(stored[-1]["panic_line"]["threshold_pe"], Decimal)
    assert isinstance(stored[-1]["panic_line"]["history_end"], date)
    assert repository.panic_lines({spec.key: spec}) == {spec.key: points[-1]["panic_line"]}
    assert stored[-1]["greed_line"] == points[-1]["greed_line"]
    assert stored[-1]["valuation_signal"] == points[-1]["valuation_signal"]
    assert isinstance(stored[-1]["greed_line"]["threshold_pe"], Decimal)
    assert isinstance(stored[-1]["valuation_signal"]["lower_pe"], Decimal)
    assert repository.greed_lines({spec.key: spec}) == {spec.key: points[-1]["greed_line"]}
    assert repository.latest_index_points({spec.key: spec})[spec.key] == stored[-1]


def test_panic_line_lookup_uses_current_method_latest_row_and_as_of(repository):
    from index_valuation.index_series import method_for

    spec = replace(SPEC, valuation_source="futorest")
    missing = replace(spec, key="no_stored_series")
    repository.ensure_index(spec)
    run = repository.start_run(spec, DAY)
    start = date(2024, 1, 1)

    def point(day, threshold):
        return {
            "trade_date": day,
            "price": Decimal(1000),
            "pe": Decimal(20),
            "implied_eps": Decimal(50),
            "panic_line": {"as_of": day, "threshold_pe": Decimal(threshold)},
        }

    early, late = point(start, "10.1"), point(start + timedelta(days=2), "12.3")
    no_line = {**point(start + timedelta(days=1), "999"), "panic_line": None}
    repository.save_index_series(spec, method_for(spec), run, [early, no_line, late])
    repository.save_index_series(
        spec,
        "yahoo_spy_proxy_fred_daily_v1",
        run,
        [point(start + timedelta(days=3), "9999")],
    )

    assert repository.panic_lines([spec, missing]) == {
        spec.key: late["panic_line"],
        missing.key: None,
    }
    assert repository.panic_lines([spec], as_of=start) == {spec.key: early["panic_line"]}
    assert repository.panic_lines([spec], as_of=start - timedelta(days=1)) == {spec.key: None}
    # A missing latest line is explicit: do not silently reuse an earlier date.
    assert repository.panic_lines([spec], as_of=start + timedelta(days=1)) == {spec.key: None}


def test_index_refresh_recomputes_panic_lines_after_same_day_source_revision(repository, tmp_path):
    from index_valuation.index_series import IndexSeries, method_for
    from index_valuation.models import IndexSeriesRecord

    spec = replace(SPEC, band_min_observations=2)
    days = (date(2019, 1, 1), date(2020, 1, 1), date(2024, 1, 1))
    repository.ensure_index(spec)
    run = repository.start_run(spec, days[-1])
    repository.save_observations(
        run,
        [
            Observation(spec.quote_code, day, source, Decimal(1000), None, None, NOW, NOW)
            for day in days
            for source in ("futu", "eastmoney")
        ],
    )

    def valuation(day, value):
        return SourceValuation(
            "futu", spec.quote_code, day, "pe", "futu_valuation_pe", Decimal(value), NOW, "raw"
        )

    repository.save_source_valuations(
        run, [valuation(day, value) for day, value in zip(days, (10, 20, 30), strict=True)]
    )
    job = IndexSeries(repository, tmp_path)
    first = job.refresh(spec)
    assert first["latest"]["panic_line"]["threshold_pe"] == Decimal(11)
    assert first["latest"]["greed_line"]["threshold_pe"] == Decimal(19)
    assert first["latest"]["valuation_signal"]["signal"] == "greed"

    repository.save_source_valuations(run, [valuation(days[0], 40)])
    second = job.refresh(spec)
    assert second["latest"]["panic_line"]["threshold_pe"] == Decimal(22)
    assert second["latest"]["panic_line"]["as_of"] == days[-1]
    assert second["latest"]["greed_line"]["threshold_pe"] == Decimal(38)
    assert second["latest"]["valuation_signal"]["signal"] == "normal"
    assert first["run_id"] != second["run_id"]

    # Revising the current PE changes its horizontal point level, not its past-only P10.
    repository.save_source_valuations(run, [valuation(days[-1], 60)])
    third = job.refresh(spec)
    latest_line = third["latest"]["panic_line"]
    assert latest_line["threshold_pe"] == Decimal(22)
    assert latest_line["reference_points"] == Decimal(1000) / 60 * 22
    assert latest_line["reference_points"] != second["latest"]["panic_line"]["reference_points"]
    assert repository.panic_lines([spec])[spec.key] == latest_line
    assert repository.index_series(spec.key, method_for(spec))[-1]["panic_line"] == latest_line
    assert count(repository, IndexSeriesRecord) == 3
    assert third["latest"]["greed_line"]["threshold_pe"] == Decimal(38)
    assert third["latest"]["valuation_signal"]["signal"] == "greed"
    assert (
        repository.latest_index_points([spec])[spec.key]["valuation_signal"]
        == (third["latest"]["valuation_signal"])
    )


def test_market_overview_reads_current_latest_method_without_reusing_earlier_pe(repository):
    from fastapi.testclient import TestClient

    from index_valuation.index_series import method_for
    from index_valuation.panic_lines import enrich_panic_lines
    from index_valuation.webapp import create_app

    spec = replace(SPEC, band_min_observations=2)
    missing = replace(spec, key="no_stored_series")
    repository.ensure_index(spec)
    run = repository.start_run(spec, DAY)
    points = enrich_panic_lines(
        spec,
        [
            {
                "trade_date": day,
                "price": Decimal(1000),
                "pe": Decimal(value) if value is not None else None,
                "implied_eps": Decimal(50) if value is not None else None,
                "pe_source": "futu",
                "pe_basis": "futu_valuation_pe",
                "pe_usable": True,
                "phase": "published",
            }
            for day, value in (
                (date(2019, 1, 1), "10"),
                (date(2020, 1, 1), "20"),
                (date(2024, 1, 1), "30"),
                (date(2024, 1, 2), None),
            )
        ],
    )
    repository.save_index_series(spec, method_for(spec), run, points)
    repository.save_index_series(
        spec,
        "old_method",
        run,
        [{**points[-2], "trade_date": date(2024, 1, 3)}],
    )
    latest = repository.latest_index_points([spec, missing])
    assert latest[spec.key]["trade_date"] == date(2024, 1, 2)
    assert latest[spec.key]["pe"] is None
    assert latest[missing.key] is None
    with TestClient(create_app(repository=repository, specs={spec.key: spec})) as client:
        data = client.get("/api/market-overview").json()
        assert data["items"][0]["as_of"] == "2024-01-02"
        assert data["items"][0]["signal"] == "unknown"
        assert data["items"][0]["status"] == "missing_pe"
        assert data["items"][0]["pe"] is None
        assert data["items"][0]["upper_pe"] == "29.0"


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


def seed_total_return_valuation(repository):
    """Three real session dates over three years, with independent price/PE inputs."""
    from index_valuation.domain import TotalReturnSpec

    days = (date(2023, 10, 12), date(2024, 10, 11), DAY)
    mapping = TotalReturnSpec(
        code="H00922",
        name="Test gross dividend reinvestment index",
        source="csindex",
        symbol="H00922",
        history_start_date=days[0],
        identity_url="https://example.test/official-total-return",
    )
    spec = replace(SPEC, total_return=mapping, band_min_observations=2)
    repository.ensure_index(spec)
    run = repository.start_run(spec, DAY)
    prices = [
        Observation(spec.quote_code, day, source, Decimal(price), None, None, NOW, NOW)
        for day, price in zip(days, (1000, 1100, 1210), strict=True)
        for source in ("futu", "eastmoney")
    ]
    pe = [
        SourceValuation(
            "futu", spec.quote_code, day, "pe", "futu_valuation_pe", Decimal(20), NOW, "pe.json"
        )
        for day in days
    ]
    repository.save_observations(run, prices)
    repository.save_source_valuations(run, pe)
    returns = [
        Observation(
            "CSI.H00922",
            day,
            "csindex",
            Decimal(value),
            None,
            None,
            NOW,
            NOW,
            price_basis="gross_total_return",
            eps_method="not_applicable",
            artifact_path="total-return.json",
        )
        for day, value in zip(days, (1000, 1150, 1500), strict=True)
    ]
    return spec, run, days, returns


def test_total_return_upserts_do_not_change_price_pe_or_valuation_series(repository, tmp_path):
    from index_valuation.index_series import IndexSeries, method_for
    from index_valuation.models import IndexSeriesRecord, TotalReturnObservation

    spec, run, days, returns = seed_total_return_valuation(repository)
    job = IndexSeries(repository, tmp_path)
    job.refresh(spec)
    valuation_before = repository.index_series(spec.key, method_for(spec))
    inputs_before = repository.index_inputs(spec)
    repository.save_total_return_observations(spec, run, "csindex", "H00922", returns)
    repository.save_total_return_observations(spec, run, "csindex", "H00922", returns)
    assert count(repository, TotalReturnObservation) == 3

    # A supplier correction updates the same identity/date without adding rows.
    second_run = repository.start_run(spec, DAY)
    corrected = replace(returns[-1], price=Decimal("1550.12345678"), artifact_path="revised.json")
    repository.save_total_return_observations(spec, second_run, "csindex", "H00922", [corrected])
    stored = repository.total_return_observations(spec)
    assert len(stored) == 3 and stored[-1]["trade_date"] == days[-1]
    assert stored[-1]["price"] == Decimal("1550.12345678")
    assert stored[-1]["run_id"] == second_run and stored[-1]["artifact_path"] == "revised.json"
    assert stored[-1]["raw"]["identity_url"] == spec.total_return.identity_url
    report = job.refresh(spec)
    assert count(repository, TotalReturnObservation) == 3
    assert count(repository, DailyObservation) == 6
    assert count(repository, SourceValuationRecord) == 3
    assert count(repository, IndexSeriesRecord) == 3
    assert repository.index_inputs(spec) == inputs_before
    valuation_after = repository.index_series(spec.key, method_for(spec))
    for before, after in zip(valuation_before, valuation_after, strict=True):
        assert {k: v for k, v in before.items() if k != "calculated_at"} == {
            k: v for k, v in after.items() if k != "calculated_at"
        }
    assert report["latest"]["price"] == 1210
    assert report["latest"]["pe"] == 20
    assert report["latest"]["implied_eps"] == Decimal("60.5")
    assert report["latest"]["fixed"]["8"] == 484
    assert report["total_return"]["end_price"] == Decimal("1550.12345678")


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "other_official"},
        {"symbol": "DIFFERENT_VENDOR_ID"},
        {"code": "DIFFERENT_INDEX"},
        {"basis": "net_total_return"},
    ],
)
def test_total_return_identity_changes_do_not_mix_old_records(repository, changes):
    from index_valuation.models import TotalReturnObservation

    spec, run, _, returns = seed_total_return_valuation(repository)
    repository.save_total_return_observations(spec, run, "csindex", "H00922", returns)
    changed = replace(spec, total_return=replace(spec.total_return, **changes))
    assert repository.total_return_observations(changed) == []
    assert repository.total_return_points(changed) == []
    new_row = replace(returns[-1], source=changed.total_return.source, price=Decimal(2000))
    repository.save_total_return_observations(
        changed, run, changed.total_return.source, changed.total_return.symbol, [new_row]
    )
    assert count(repository, TotalReturnObservation) == 4
    assert [row["price"] for row in repository.total_return_points(changed)] == [Decimal(2000)]
    assert [row["price"] for row in repository.total_return_points(spec)] == [
        Decimal(1000),
        Decimal(1150),
        Decimal(1500),
    ]


def test_total_return_refresh_api_csv_and_historical_asof_agree(repository, tmp_path):
    import csv
    from io import StringIO
    from pathlib import Path

    from fastapi.testclient import TestClient

    from index_valuation.index_series import IndexSeries
    from index_valuation.webapp import create_app

    spec, run, days, returns = seed_total_return_valuation(repository)
    repository.save_total_return_observations(spec, run, "csindex", "H00922", returns)
    report = IndexSeries(repository, tmp_path).refresh(spec)
    saved_rows = list(csv.DictReader(StringIO(Path(report["csv"]).read_text().lstrip("\ufeff"))))

    with TestClient(create_app(repository=repository, specs={spec.key: spec})) as client:
        for cutoff, expected_rows, last_value in ((DAY, 3, 1500), (days[1], 2, 1150)):
            params = {"as_of": str(cutoff), "chart_years": 0}
            response = client.get(f"/api/indices/{spec.key}/dashboard", params=params)
            assert response.status_code == 200
            data = response.json()
            performance = data["total_return"]
            assert performance["status"] == "ok"
            assert performance["basis"] == "gross_total_return"
            assert performance["start_date"] == str(days[0])
            assert performance["end_date"] == str(cutoff)
            assert performance["latest_available_date"] == str(cutoff)
            assert performance["observation_count"] == expected_rows
            assert performance["aligned_with_price_return"]
            assert Decimal(performance["start_price"]) == 1000
            assert Decimal(performance["end_price"]) == last_value
            assert Decimal(performance["period_return_pct"]) == Decimal(last_value) / 10 - 100
            assert performance["price_status"] == "unverified"
            assert Decimal(performance["annualized_return_pct"]) > Decimal(
                data["price_return"]["annualized_return_pct"]
            )
            if cutoff == DAY:
                assert (
                    Decimal(performance["annualized_return_pct"])
                    == report["total_return"]["annualized_return_pct"]
                )
            csv_response = client.get(f"/api/indices/{spec.key}/export.csv", params=params)
            assert csv_response.status_code == 200
            csv_rows = list(csv.DictReader(StringIO(csv_response.text.lstrip("\ufeff"))))
            assert len(csv_rows) == len(data["series"]) == expected_rows
            for row, point in zip(csv_rows, data["series"], strict=True):
                assert row["trade_date"] == point["trade_date"] <= str(cutoff)
                assert Decimal(row["total_return_level"]) == Decimal(point["total_return_level"])
                assert row["total_return_source"] == point["total_return_source"] == "csindex"
                assert row["total_return_code"] == point["total_return_code"] == "H00922"
                assert row["total_return_basis"] == "gross_total_return"
                assert row["total_return_status"] == "unverified"
                assert Decimal(row["pe"]) == 20
                assert Decimal(row["price"]) == Decimal(point["price"])
                assert Decimal(row["implied_eps"]) == Decimal(row["price"]) / 20
                saved = next(x for x in saved_rows if x["trade_date"] == row["trade_date"])
                for field in ("price", "pe", "implied_eps", "band_8x", "total_return_level"):
                    assert Decimal(row[field]) == Decimal(saved[field])
                for field in (
                    "trade_date",
                    "total_return_source",
                    "total_return_code",
                    "total_return_basis",
                    "total_return_status",
                ):
                    assert row[field] == saved[field]
