from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.repositories.sql_models import AgileDataORM, ForecastORM
from src.schemas.forecast import AgilePricePoint, ForecastSummary, ForecastWithPrices


class ForecastRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _list_latest_operational(self, limit: int) -> list[ForecastORM]:
        stmt = (
            select(ForecastORM)
            .where(~ForecastORM.name.like("bundle::history-%"))
            .order_by(ForecastORM.created_at.desc())
            .limit(limit)
        )
        return self.session.execute(stmt).scalars().all()

    def list_latest(self, limit: int = 1) -> list[ForecastSummary]:
        rows = self._list_latest_operational(limit=limit)
        return [ForecastSummary(id=row.id, name=row.name, created_at=row.created_at) for row in rows]

    def list_with_prices(
        self,
        region: str | None,
        days: int,
        forecast_count: int,
        include_high_low: bool,
    ) -> list[ForecastWithPrices]:
        forecasts = self._list_latest_operational(limit=forecast_count)
        if not forecasts:
            return []

        forecast_by_id = {f.id: f for f in forecasts}
        price_stmt = select(AgileDataORM).where(AgileDataORM.forecast_id.in_(forecast_by_id.keys()))
        if region is not None:
            price_stmt = price_stmt.where(AgileDataORM.region == region.upper())
        prices = self.session.execute(price_stmt).scalars().all()

        grouped: dict[int, list[AgileDataORM]] = defaultdict(list)
        for row in prices:
            grouped[row.forecast_id].append(row)

        results: list[ForecastWithPrices] = []
        for forecast in forecasts:
            forecast_prices = sorted(grouped.get(forecast.id, []), key=lambda p: p.date_time)
            if forecast_prices:
                max_date = forecast_prices[0].date_time + timedelta(days=days)
                forecast_prices = [p for p in forecast_prices if p.date_time <= max_date]

            forecast_name = forecast.name
            if region is not None:
                forecast_name = f"Region | {region.upper()} {forecast_name}"

            points = [
                AgilePricePoint(
                    date_time=price.date_time,
                    agile_pred=price.agile_pred,
                    agile_low=price.agile_low if include_high_low else None,
                    agile_high=price.agile_high if include_high_low else None,
                    region=price.region if region is None else None,
                )
                for price in forecast_prices
            ]
            results.append(
                ForecastWithPrices(id=forecast.id, name=forecast_name, created_at=forecast.created_at, prices=points)
            )

        return results
