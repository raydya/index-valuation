"""Persistent source observations and independently reproducible valuation results."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from index_valuation.db import Base

AMOUNT = Numeric(30, 16)


class IndexRecord(Base):
    __tablename__ = "indices"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    provider_code: Mapped[str] = mapped_column(String(32))
    quote_code: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    expected_constituents: Mapped[int | None]


class Security(Base):
    __tablename__ = "securities"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3))


class WeightRecord(Base):
    __tablename__ = "weight_snapshots"
    __table_args__ = (UniqueConstraint("index_key", "as_of", "sha256"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    index_key: Mapped[str] = mapped_column(ForeignKey("indices.key"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str]
    sha256: Mapped[str] = mapped_column(String(64))
    artifact_path: Mapped[str]
    original_total_percent: Mapped[Decimal] = mapped_column(AMOUNT)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB)


class WeightMember(Base):
    __tablename__ = "constituent_weights"
    __table_args__ = (CheckConstraint("weight_percent > 0", name="positive_weight"),)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("weight_snapshots.id"), primary_key=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("securities.symbol"), primary_key=True)
    weight_percent: Mapped[Decimal] = mapped_column(AMOUNT)


class RunRecord(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    index_key: Mapped[str] = mapped_column(ForeignKey("indices.key"), index=True)
    trade_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)


class DailyObservation(Base):
    __tablename__ = "daily_observations"
    __table_args__ = (CheckConstraint("price > 0", name="positive_price"),)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("securities.symbol"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    price: Mapped[Decimal] = mapped_column(AMOUNT)
    eps_ttm: Mapped[Decimal | None] = mapped_column(AMOUNT)
    pe_ttm: Mapped[Decimal | None] = mapped_column(AMOUNT)
    reported_eps: Mapped[Decimal | None] = mapped_column(AMOUNT)
    eps_method: Mapped[str] = mapped_column(String(64))
    price_basis: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    quote_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    artifact_path: Mapped[str]
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"))


class DailyValuation(Base):
    __tablename__ = "daily_valuations"
    index_key: Mapped[str] = mapped_column(ForeignKey("indices.key"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    method: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("weight_snapshots.id"))
    run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    index_price: Mapped[Decimal | None] = mapped_column(AMOUNT)
    pe_ttm: Mapped[Decimal | None] = mapped_column(AMOUNT)
    index_eps_ttm: Mapped[Decimal | None] = mapped_column(AMOUNT)
    earnings_yield: Mapped[Decimal | None] = mapped_column(AMOUNT)
    coverage_weight: Mapped[Decimal] = mapped_column(AMOUNT)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    issues: Mapped[list[str]] = mapped_column(JSONB)
    bands: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SourceValuationRecord(Base):
    __tablename__ = "source_valuations"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("securities.symbol"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    basis: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Decimal] = mapped_column(AMOUNT)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    artifact_path: Mapped[str]
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"))


class DailyFundamental(Base):
    __tablename__ = "daily_fundamentals"
    symbol: Mapped[str] = mapped_column(ForeignKey("securities.symbol"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    method: Mapped[str] = mapped_column(String(64), primary_key=True)
    price: Mapped[Decimal | None] = mapped_column(AMOUNT)
    pe_ttm: Mapped[Decimal | None] = mapped_column(AMOUNT)
    eps_ttm: Mapped[Decimal | None] = mapped_column(AMOUNT)
    price_source: Mapped[str | None] = mapped_column(String(32))
    pe_source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    price_relative_error: Mapped[Decimal | None] = mapped_column(AMOUNT)
    pe_relative_error: Mapped[Decimal | None] = mapped_column(AMOUNT)
    eps_relative_error: Mapped[Decimal | None] = mapped_column(AMOUNT)
    price_artifact: Mapped[str | None]
    pe_artifact: Mapped[str | None]
    reference_artifact: Mapped[str | None]
    run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"))


class FundamentalBatch(Base):
    __tablename__ = "fundamental_batches"
    symbol: Mapped[str] = mapped_column(ForeignKey("securities.symbol"), primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, primary_key=True)
    end_date: Mapped[date] = mapped_column(Date, primary_key=True)
    method: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IndexSeriesRecord(Base):
    """Vendor index valuation, independent of constituent weights and TTM claims."""

    __tablename__ = "index_series"
    index_key: Mapped[str] = mapped_column(ForeignKey("indices.key"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    method: Mapped[str] = mapped_column(String(64), primary_key=True)
    price: Mapped[Decimal | None] = mapped_column(AMOUNT)
    pe: Mapped[Decimal | None] = mapped_column(AMOUNT)
    implied_eps: Mapped[Decimal | None] = mapped_column(AMOUNT)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_runs.id"))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
