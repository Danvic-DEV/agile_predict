from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ForecastORM(Base):
    __tablename__ = "prices_forecasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    stdev: Mapped[float | None] = mapped_column(Float, nullable=True)


class AgileDataORM(Base):
    __tablename__ = "prices_agiledata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    forecast_id: Mapped[int] = mapped_column(ForeignKey("prices_forecasts.id"), nullable=False)
    region: Mapped[str] = mapped_column(String(1), nullable=False)
    agile_pred: Mapped[float] = mapped_column(Float, nullable=False)
    agile_low: Mapped[float] = mapped_column(Float, nullable=False)
    agile_high: Mapped[float] = mapped_column(Float, nullable=False)
    date_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PriceHistoryORM(Base):
    __tablename__ = "prices_pricehistory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, unique=True)
    day_ahead: Mapped[float] = mapped_column(Float, nullable=False)
    agile: Mapped[float] = mapped_column(Float, nullable=False)


class HistoryORM(Base):
    __tablename__ = "prices_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, unique=True)
    total_wind: Mapped[float] = mapped_column(Float, nullable=False)
    bm_wind: Mapped[float] = mapped_column(Float, nullable=False)
    solar: Mapped[float] = mapped_column(Float, nullable=False)
    temp_2m: Mapped[float] = mapped_column(Float, nullable=False)
    wind_10m: Mapped[float] = mapped_column(Float, nullable=False)
    rad: Mapped[float] = mapped_column(Float, nullable=False)
    demand: Mapped[float] = mapped_column(Float, nullable=False)


class ForecastDataORM(Base):
    __tablename__ = "prices_forecastdata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    forecast_id: Mapped[int] = mapped_column(ForeignKey("prices_forecasts.id"), nullable=False)
    date_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    day_ahead: Mapped[float | None] = mapped_column(Float, nullable=True)
    bm_wind: Mapped[float] = mapped_column(Float, nullable=False)
    solar: Mapped[float] = mapped_column(Float, nullable=False)
    emb_wind: Mapped[float] = mapped_column(Float, nullable=False)
    temp_2m: Mapped[float] = mapped_column(Float, nullable=False)
    wind_10m: Mapped[float] = mapped_column(Float, nullable=False)
    rad: Mapped[float] = mapped_column(Float, nullable=False)
    demand: Mapped[float] = mapped_column(Float, nullable=False)
