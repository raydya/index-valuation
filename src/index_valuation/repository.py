"""PostgreSQL storage, idempotency, and per-index job locking behind one interface."""

import json
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from index_valuation.archive import json_text
from index_valuation.domain import (
    Calculation,
    Check,
    Constituent,
    DataError,
    IndexSpec,
    WeightSnapshot,
)
from index_valuation.models import (
    DailyFundamental,
    DailyObservation,
    DailyValuation,
    FundamentalBatch,
    IndexRecord,
    IndexSeriesRecord,
    RunRecord,
    Security,
    SourceValuationRecord,
    TotalReturnObservation,
    WeightMember,
    WeightRecord,
)
from index_valuation.valuation import METHOD


def _upsert(connection, model, rows, key_columns):
    if not rows:
        return
    # Parameter batches let psycopg pipeline repeated upserts without compiling
    # one enormous SQL expression for every value in a historical batch.
    statement = insert(model.__table__)
    connection.execute(
        statement.on_conflict_do_update(
            index_elements=key_columns,
            set_={
                column.name: statement.excluded[column.name]
                for column in model.__table__.columns
                if column.name not in key_columns
            },
        ),
        rows,
    )


def _restore_valuation_detail(line):
    """Restore the typed line without changing other stored detail fields."""
    if line is None:
        return None
    result = dict(line)
    for key in ("as_of", "history_start", "history_end"):
        if result.get(key) is not None:
            result[key] = date.fromisoformat(result[key])
    for key in (
        "threshold_pe",
        "threshold_to_mean",
        "quantile",
        "reference_points",
        "gap_pct",
        "pe",
        "lower_pe",
        "upper_pe",
    ):
        if result.get(key) is not None:
            result[key] = Decimal(str(result[key]))
    return result


def _restore_index_point(row):
    details = row["details"]
    return {
        **details,
        **{
            field: _restore_valuation_detail(details[field])
            for field in ("panic_line", "greed_line", "valuation_signal")
            if field in details
        },
        **{key: row[key] for key in ("trade_date", "price", "pe", "implied_eps", "calculated_at")},
    }


class Repository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def ensure_index(self, spec: IndexSpec) -> None:
        with self.engine.begin() as connection:
            _upsert(
                connection,
                IndexRecord,
                [
                    {
                        "key": spec.key,
                        "name": spec.name,
                        "provider_code": spec.provider_code,
                        "quote_code": spec.quote_code,
                        "currency": spec.currency,
                        "expected_constituents": spec.expected_constituents,
                    }
                ],
                ["key"],
            )
            _upsert(
                connection,
                Security,
                [
                    {
                        "symbol": spec.quote_code,
                        "name": spec.name,
                        "currency": spec.currency,
                    }
                ],
                ["symbol"],
            )

    @contextmanager
    def lock(self, key: str):
        # The connection stays open for the session lock; writes use separate short transactions.
        with self.engine.connect() as connection:
            params = {"key": "index-valuation:" + key}
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), params
            )
            connection.commit()
            if not acquired:
                raise DataError(f"A job is already running for {key}")
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), params
                )
                connection.commit()

    def save_snapshot(self, spec: IndexSpec, snapshot: WeightSnapshot, verification: dict) -> int:
        with self.engine.begin() as connection:
            _upsert(
                connection,
                Security,
                [
                    {
                        "symbol": member.symbol,
                        "name": member.name,
                        "currency": spec.currency,
                    }
                    for member in snapshot.constituents
                ],
                ["symbol"],
            )
            statement = insert(WeightRecord).values(
                index_key=spec.key,
                as_of=snapshot.as_of,
                source=snapshot.source,
                source_url=snapshot.source_url,
                sha256=snapshot.sha256,
                artifact_path=snapshot.artifact_path,
                original_total_percent=snapshot.total_percent,
                observed_at=datetime.now(UTC),
                verification=verification,
            )
            snapshot_id = connection.scalar(
                statement.on_conflict_do_update(
                    index_elements=["index_key", "as_of", "sha256"],
                    set_={"verification": statement.excluded.verification},
                ).returning(WeightRecord.id)
            )
            _upsert(
                connection,
                WeightMember,
                [
                    {
                        "snapshot_id": snapshot_id,
                        "symbol": member.symbol,
                        "weight_percent": member.weight_percent,
                    }
                    for member in snapshot.constituents
                ],
                ["snapshot_id", "symbol"],
            )
            return snapshot_id

    def snapshot(self, key: str, day: date) -> tuple[int, WeightSnapshot, dict]:
        with self.engine.connect() as connection:
            record = (
                connection.execute(
                    select(WeightRecord.__table__)
                    .where(
                        WeightRecord.index_key == key,
                        WeightRecord.as_of <= day,
                    )
                    .order_by(WeightRecord.as_of.desc(), WeightRecord.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if record is None:
                raise DataError(f"No weight snapshot for {key} on or before {day}")
            rows = connection.execute(
                select(
                    WeightMember.symbol,
                    Security.name,
                    WeightMember.weight_percent,
                )
                .join(Security)
                .where(WeightMember.snapshot_id == record["id"])
            ).all()
            snapshot = WeightSnapshot(
                record["as_of"],
                tuple(Constituent(*row) for row in rows),
                record["source"],
                record["source_url"],
                record["sha256"],
                record["artifact_path"],
            )
            return record["id"], snapshot, record["verification"]

    def start_run(self, spec: IndexSpec, day: date) -> UUID:
        run_id = uuid4()
        with self.engine.begin() as connection:
            connection.execute(
                insert(RunRecord).values(
                    id=run_id,
                    index_key=spec.key,
                    trade_date=day,
                    started_at=datetime.now(UTC),
                    status="running",
                    report={},
                )
            )
        return run_id

    def finish_run(self, run_id: UUID, status: str, report: dict) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(RunRecord)
                .where(RunRecord.id == run_id)
                .values(
                    status=status,
                    finished_at=datetime.now(UTC),
                    report=json.loads(json_text(report)),
                )
            )

    def save_observations(self, run_id: UUID, rows) -> None:
        values = [{**asdict(row), "run_id": run_id} for row in rows]
        with self.engine.begin() as connection:
            for offset in range(0, len(values), 500):
                _upsert(
                    connection,
                    DailyObservation,
                    values[offset : offset + 500],
                    ["source", "symbol", "trade_date"],
                )

    def save_total_return_observations(self, spec, run_id, source, symbol, rows):
        tr = spec.total_return
        if tr is None or (source, symbol) not in {
            (tr.source, tr.symbol),
            (tr.verification_source, tr.verification_symbol),
        }:
            raise DataError("Unconfigured total-return identity")
        if any(row.source != source or row.currency != spec.currency for row in rows):
            raise DataError("Total-return observations have the wrong source or currency")
        values = [
            {
                "index_key": spec.key,
                "index_code": tr.code,
                "source": source,
                "symbol": symbol,
                "basis": tr.basis,
                "trade_date": row.trade_date,
                "price": row.price,
                "currency": row.currency,
                "quote_at": row.quote_at,
                "observed_at": row.observed_at,
                "artifact_path": row.artifact_path,
                "raw": {**row.raw, "identity_url": tr.identity_url},
                "run_id": run_id,
            }
            for row in rows
        ]
        with self.engine.begin() as connection:
            for offset in range(0, len(values), 500):
                _upsert(
                    connection,
                    TotalReturnObservation,
                    values[offset : offset + 500],
                    ["index_key", "index_code", "source", "symbol", "basis", "trade_date"],
                )

    def total_return_observations(self, spec):
        tr = spec.total_return
        if tr is None:
            return []
        pairs = [(tr.source, tr.symbol)]
        if tr.verification_source:
            pairs.append((tr.verification_source, tr.verification_symbol))
        model = TotalReturnObservation
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    select(model.__table__)
                    .where(
                        model.index_key == spec.key,
                        model.index_code == tr.code,
                        model.basis == tr.basis,
                        or_(*(and_(model.source == s, model.symbol == c) for s, c in pairs)),
                    )
                    .order_by(model.trade_date, model.source)
                ).mappings()
            )

    def total_return_points(self, spec):
        from index_valuation.total_returns import select_return_points

        return select_return_points(spec, self.total_return_observations(spec))

    def save_source_valuations(self, run_id: UUID, rows) -> None:
        values = [{**asdict(row), "run_id": run_id} for row in rows]
        with self.engine.begin() as connection:
            for offset in range(0, len(values), 500):
                _upsert(
                    connection,
                    SourceValuationRecord,
                    values[offset : offset + 500],
                    ["source", "symbol", "trade_date", "metric", "basis"],
                )

    def weight_dates(self, key: str) -> list[date]:
        with self.engine.connect() as connection:
            return list(
                connection.scalars(
                    select(WeightRecord.as_of)
                    .where(WeightRecord.index_key == key)
                    .distinct()
                    .order_by(WeightRecord.as_of)
                )
            )

    def constituent_universe(self, key: str) -> dict[str, str]:
        """Known securities to collect, not a claim of historical index membership."""
        with self.engine.connect() as connection:
            return dict(
                connection.execute(
                    select(WeightMember.symbol, Security.name)
                    .join(Security)
                    .join(WeightRecord, WeightMember.snapshot_id == WeightRecord.id)
                    .where(WeightRecord.index_key == key)
                    .distinct()
                    .order_by(WeightMember.symbol)
                ).all()
            )

    def fundamental_batch(self, symbol, start, end, method) -> dict | None:
        with self.engine.connect() as connection:
            return connection.scalar(
                select(FundamentalBatch.report).where(
                    FundamentalBatch.symbol == symbol,
                    FundamentalBatch.start_date == start,
                    FundamentalBatch.end_date == end,
                    FundamentalBatch.method == method,
                    FundamentalBatch.status == "collected",
                )
            )

    def save_fundamental_batch(
        self, run_id, symbol, start, end, method, prices, reference, pe, fundamentals, report
    ) -> None:
        # Source values, derived values and checkpoint commit together. A killed job
        # can rerun this symbol safely, and other indices can reuse the checkpoint.
        with self.engine.begin() as connection:
            for model, rows, keys in (
                (DailyObservation, prices + reference, ["source", "symbol", "trade_date"]),
                (SourceValuationRecord, pe, ["source", "symbol", "trade_date", "metric", "basis"]),
                (DailyFundamental, fundamentals, ["symbol", "trade_date", "method"]),
            ):
                for offset in range(0, len(rows), 500):
                    values = [
                        {**asdict(row), "run_id": run_id} for row in rows[offset : offset + 500]
                    ]
                    _upsert(connection, model, values, keys)
            _upsert(
                connection,
                FundamentalBatch,
                [
                    {
                        "symbol": symbol,
                        "start_date": start,
                        "end_date": end,
                        "method": method,
                        "status": "collected",
                        "report": json.loads(json_text(report)),
                        "run_id": run_id,
                        "finished_at": datetime.now(UTC),
                    }
                ],
                ["symbol", "start_date", "end_date", "method"],
            )

    def fundamentals(self, symbols, day, method) -> dict:
        from index_valuation.domain import Fundamental

        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(DailyFundamental.__table__).where(
                        DailyFundamental.symbol.in_(symbols),
                        DailyFundamental.trade_date == day,
                        DailyFundamental.method == method,
                    )
                )
                .mappings()
                .all()
            )
        return {
            row["symbol"]: Fundamental(**{k: v for k, v in row.items() if k != "run_id"})
            for row in rows
        }

    def observations(self, source: str, day: date, symbols: list[str]) -> dict:
        from index_valuation.domain import Observation

        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(DailyObservation.__table__).where(
                        DailyObservation.source == source,
                        DailyObservation.trade_date == day,
                        DailyObservation.symbol.in_(symbols),
                    )
                )
                .mappings()
                .all()
            )
        return {
            row["symbol"]: Observation(
                **{key: value for key, value in row.items() if key != "run_id"}
            )
            for row in rows
        }

    def history(self, spec: IndexSpec, day: date, method: str = METHOD) -> list:
        with self.engine.connect() as connection:
            return list(
                connection.scalars(
                    select(DailyValuation.pe_ttm)
                    .where(
                        DailyValuation.index_key == spec.key,
                        DailyValuation.method == method,
                        DailyValuation.trade_date < day,
                        DailyValuation.trade_date >= day - timedelta(days=spec.band_window_days),
                        DailyValuation.status.in_(["verified", "estimated"]),
                        DailyValuation.pe_ttm > 0,
                    )
                    .order_by(DailyValuation.trade_date)
                )
            )

    def save_calculation(
        self,
        spec: IndexSpec,
        day: date,
        snapshot_id: int,
        run_id: UUID,
        index_price,
        result: Calculation,
        checks: list[Check],
        method: str = METHOD,
    ) -> None:
        values = {
            "index_key": spec.key,
            "trade_date": day,
            "method": method,
            "snapshot_id": snapshot_id,
            "run_id": run_id,
            "calculated_at": datetime.now(UTC),
            "status": result.status,
            "index_price": index_price,
            "pe_ttm": result.pe_ttm,
            "index_eps_ttm": result.index_eps_ttm,
            "earnings_yield": result.earnings_yield,
            "coverage_weight": result.coverage_weight,
            "checks": json.loads(json_text(checks)),
            "issues": list(result.issues),
            "bands": json.loads(json_text(result.bands)),
        }
        with self.engine.begin() as connection:
            _upsert(connection, DailyValuation, [values], ["index_key", "trade_date", "method"])

    def latest_report(self, key: str) -> dict | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(RunRecord.__table__)
                    .where(
                        RunRecord.index_key == key,
                    )
                    .order_by(RunRecord.started_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return dict(row) if row is not None else None

    def scheduled_run_records(self, run_ids) -> list[dict]:
        """Resolve explicit log/receipt IDs; never guess the last scheduled run."""
        ids = [UUID(str(value)) for value in run_ids][:100]
        if not ids:
            return []
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(RunRecord.__table__).where(RunRecord.id.in_(ids))
            ).mappings()
            return [dict(row) for row in rows]

    def scheduled_index_summaries(self, specs) -> dict:
        """Current-method latest data only; independent of scheduled execution."""
        from index_valuation.index_series import method_for

        result = {}
        with self.engine.connect() as connection:
            for spec in specs.values():
                scope = (
                    IndexSeriesRecord.index_key == spec.key,
                    IndexSeriesRecord.method == method_for(spec),
                )
                latest = (
                    connection.execute(
                        select(
                            IndexSeriesRecord.trade_date,
                            IndexSeriesRecord.implied_eps,
                            IndexSeriesRecord.details,
                        )
                        .where(*scope)
                        .order_by(IndexSeriesRecord.trade_date.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                complete = connection.scalar(
                    select(func.max(IndexSeriesRecord.trade_date)).where(
                        *scope, IndexSeriesRecord.implied_eps.is_not(None)
                    )
                )
                # A latest manual build is useful data evidence, never run evidence.
                report = (
                    connection.execute(
                        select(RunRecord.status, RunRecord.started_at, RunRecord.report)
                        .where(RunRecord.index_key == spec.key)
                        .order_by(RunRecord.started_at.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                result[spec.key] = {
                    "latest": dict(latest) if latest else None,
                    "complete_date": complete,
                    "processing": dict(report) if report else None,
                }
        return result

    def index_inputs(self, spec):
        from index_valuation.domain import Observation, SourceValuation
        from index_valuation.index_series import PRICE_SOURCES

        with self.engine.connect() as connection:
            prices = (
                connection.execute(
                    select(DailyObservation.__table__).where(
                        DailyObservation.symbol == spec.quote_code,
                        DailyObservation.source.in_(PRICE_SOURCES),
                    )
                )
                .mappings()
                .all()
            )
            valuations = (
                connection.execute(
                    select(SourceValuationRecord.__table__).where(
                        SourceValuationRecord.symbol == spec.quote_code,
                    )
                )
                .mappings()
                .all()
            )
        return (
            [Observation(**{k: v for k, v in row.items() if k != "run_id"}) for row in prices],
            [
                SourceValuation(**{k: v for k, v in row.items() if k != "run_id"})
                for row in valuations
            ],
        )

    def save_index_series(self, spec, method, run_id, points):
        fields = {"trade_date", "price", "pe", "implied_eps"}
        now = datetime.now(UTC)
        with self.engine.begin() as connection:
            for offset in range(0, len(points), 500):
                rows = [
                    {
                        **{k: point[k] for k in fields},
                        "index_key": spec.key,
                        "method": method,
                        "run_id": run_id,
                        "calculated_at": now,
                        "details": json.loads(
                            json_text({k: v for k, v in point.items() if k not in fields})
                        ),
                    }
                    for point in points[offset : offset + 500]
                ]
                _upsert(connection, IndexSeriesRecord, rows, ["index_key", "trade_date", "method"])

    def index_series(self, key, method):
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(IndexSeriesRecord.__table__)
                    .where(
                        IndexSeriesRecord.index_key == key,
                        IndexSeriesRecord.method == method,
                    )
                    .order_by(IndexSeriesRecord.trade_date)
                )
                .mappings()
                .all()
            )
        return [_restore_index_point(row) for row in rows]

    def latest_index_points(self, specs):
        """Read the latest row of each current method, including explicit missing PE."""
        from index_valuation.index_series import method_for

        if isinstance(specs, Mapping):
            specs = specs.values()
        result = {}
        with self.engine.connect() as connection:
            for spec in specs:
                row = (
                    connection.execute(
                        select(IndexSeriesRecord.__table__)
                        .where(
                            IndexSeriesRecord.index_key == spec.key,
                            IndexSeriesRecord.method == method_for(spec),
                        )
                        .order_by(IndexSeriesRecord.trade_date.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                result[spec.key] = _restore_index_point(row) if row else None
        return result

    def panic_lines(self, specs, as_of: date | None = None):
        return self._valuation_lines(specs, "panic_line", as_of)

    def greed_lines(self, specs, as_of: date | None = None):
        return self._valuation_lines(specs, "greed_line", as_of)

    def _valuation_lines(self, specs, field, as_of):
        """Read each current method's latest stored line without loading its history."""
        from index_valuation.index_series import method_for

        if isinstance(specs, Mapping):
            specs = specs.values()
        result = {}
        with self.engine.connect() as connection:
            for spec in specs:
                statement = select(IndexSeriesRecord.details[field]).where(
                    IndexSeriesRecord.index_key == spec.key,
                    IndexSeriesRecord.method == method_for(spec),
                )
                if as_of is not None:
                    statement = statement.where(IndexSeriesRecord.trade_date <= as_of)
                result[spec.key] = _restore_valuation_detail(
                    connection.scalar(
                        statement.order_by(IndexSeriesRecord.trade_date.desc()).limit(1)
                    )
                )
        return result
