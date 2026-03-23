from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xg
from sklearn.model_selection import cross_val_score
from sklearn.neighbors import KernelDensity
from sqlalchemy import select

from src.repositories.sql_models import ForecastDataORM, ForecastORM, PriceHistoryORM
from src.repositories.unit_of_work import UnitOfWork

LEGACY_FEATURES: tuple[str, ...] = (
    "bm_wind",
    "solar",
    "demand",
    "peak",
    "days_ago",
    "wind_10m",
    "weekend",
)


@dataclass(frozen=True)
class MlParityForecastOutput:
    day_ahead_values: tuple[float, ...]
    day_ahead_low_values: tuple[float, ...]
    day_ahead_high_values: tuple[float, ...]
    cv_mean_rmse: float | None
    cv_stdev_rmse: float | None
    training_rows: int
    test_rows: int
    feature_columns: tuple[str, ...]
    range_mode: str


def _predict_with_dmatrix(model: xg.XGBRegressor, features: pd.DataFrame) -> np.ndarray:
    dmatrix = xg.DMatrix(features.to_numpy(), feature_names=list(features.columns))
    return model.get_booster().predict(dmatrix)


def _apply_legacy_scale_blend(
    preds: pd.Series,
    lows: pd.Series,
    highs: pd.Series,
    reference_day_ahead: pd.Series,
    bridge_day_ahead: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    if preds.empty:
        return preds, lows, highs

    ref = reference_day_ahead.sort_index().copy()
    ref.index = pd.to_datetime(ref.index, utc=True).floor("30min")
    ref = ref.groupby(ref.index).mean()
    if ref.empty:
        return preds, lows, highs

    agile_end = ref.index.max()
    if agile_end is None:
        return preds, lows, highs

    # Legacy behavior: first pin to known prices (+/-1), then optional bridge window (+/-5),
    # and finally let model output through.
    first_window = pd.date_range(preds.index[0], agile_end, freq="30min", tz="UTC")
    sfs: list[pd.DataFrame] = [
        pd.DataFrame(index=first_window, data={"mult": 0.0, "shift": 1.0}),
    ]

    if bridge_day_ahead is not None and not bridge_day_ahead.empty:
        bridge = bridge_day_ahead.sort_index().copy()
        bridge.index = pd.to_datetime(bridge.index, utc=True).floor("30min")
        bridge = bridge.groupby(bridge.index).mean()
        bridge = bridge[bridge.index > agile_end]
        if not bridge.empty:
            bridge_window = pd.date_range(bridge.index[0], bridge.index[-1], freq="30min", tz="UTC")
            sfs.append(pd.DataFrame(index=bridge_window, data={"mult": 0.0, "shift": 5.0}))
            remainder_index = preds.index.difference(first_window.union(bridge_window))
        else:
            bridge = pd.Series(dtype=float)
            remainder_index = preds.index.difference(first_window)
    else:
        bridge = pd.Series(dtype=float)
        remainder_index = preds.index.difference(first_window)

    sfs.append(pd.DataFrame(index=remainder_index, data={"mult": 1.0, "shift": 0.0}))

    scale_factors = pd.concat(sfs).sort_index()
    if bridge.empty:
        reference_full = ref
    else:
        reference_full = pd.concat([ref, bridge]).sort_index()
    scale_factors = pd.concat(
        [scale_factors, reference_full.reindex(scale_factors.index).rename("day_ahead")],
        axis=1,
    )

    # If reference data is missing for a slot, preserve model output instead of pinning to zero.
    missing_reference = scale_factors["day_ahead"].isna()
    if missing_reference.any():
        scale_factors.loc[missing_reference, "mult"] = 1.0
        scale_factors.loc[missing_reference, "shift"] = 0.0

    aligned_mult = scale_factors["mult"].reindex(preds.index).fillna(1.0)
    aligned_shift = scale_factors["shift"].reindex(preds.index).fillna(0.0)
    aligned_ref = scale_factors["day_ahead"].reindex(preds.index).fillna(0.0)

    blended_pred = preds * aligned_mult + aligned_ref * (1.0 - aligned_mult)
    blended_low = lows * aligned_mult + aligned_ref * (1.0 - aligned_mult) - aligned_shift
    blended_high = highs * aligned_mult + aligned_ref * (1.0 - aligned_mult) + aligned_shift
    return blended_pred, blended_low, blended_high


def _kde_quantiles(
    kde: KernelDensity,
    dt: list[float],
    pred: list[float],
    quantiles: dict[str, float],
    lim: tuple[float, float],
) -> dict[str, list[float]]:
    results = {q: [] for q in quantiles}
    lower, upper = int(lim[0]), int(lim[1])

    for dt1, pred1 in zip(dt, pred):
        x = np.array([[dt1, pred1, p] for p in range(lower, upper)])
        c = pd.Series(index=x[:, 2], data=np.exp(kde.score_samples(x)).cumsum())
        c /= c.iloc[-1]

        for key, quantile in quantiles.items():
            below = c[c < quantile]
            if len(below) == 0:
                results[key].append(float("nan"))
                continue

            idx = int(below.index[-1])
            if idx + 1 not in c.index:
                results[key].append(float(idx))
                continue

            span = c[idx + 1] - c[idx]
            if span == 0:
                results[key].append(float(idx))
                continue

            results[key].append(float((quantile - c[idx]) / span + idx))

    return results


def _to_dataframe(rows: list[object], cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame([{col: getattr(row, col) for col in cols} for row in rows])


def check_ml_training_readiness(
    uow: UnitOfWork,
    max_days: int = 180,
    min_joined_rows: int = 30,
) -> tuple[bool, str | None]:
    """Return whether ML training can run with the current legacy filters."""
    forecasts = uow.session.execute(select(ForecastORM).order_by(ForecastORM.created_at.asc())).scalars().all()
    if len(forecasts) < 2:
        return False, "insufficient forecast history for ML training"

    forecast_data = uow.session.execute(select(ForecastDataORM)).scalars().all()
    if len(forecast_data) < 50:
        return False, "insufficient forecast data rows for ML training"

    prices = uow.session.execute(select(PriceHistoryORM)).scalars().all()
    if len(prices) < 50:
        return False, "insufficient price history rows for ML training"

    ff = _to_dataframe(forecasts, ["id", "name", "created_at"]).set_index("id").sort_index()
    ff["created_at"] = pd.to_datetime(ff["created_at"], utc=True)
    ff["date"] = ff["created_at"].dt.tz_convert("GB").dt.normalize()
    ff["ag_start"] = ff["created_at"].dt.normalize() + pd.Timedelta(hours=22)
    ff["ag_end"] = ff["created_at"].dt.normalize() + pd.Timedelta(hours=334)  # 13 days + 22h start offset
    ff["dt1600"] = (
        (ff["date"] + pd.Timedelta(hours=16, minutes=15) - ff["created_at"].dt.tz_convert("GB"))
        .dt.total_seconds()
        .abs()
    )
    ff_train = ff.sort_values("dt1600").drop_duplicates("date").sort_index()

    fd = _to_dataframe(
        forecast_data,
        [
            "forecast_id",
            "date_time",
            "bm_wind",
            "solar",
            "emb_wind",
            "temp_2m",
            "wind_10m",
            "rad",
            "demand",
        ],
    )
    fd["date_time"] = pd.to_datetime(fd["date_time"], utc=True)

    prices_df = _to_dataframe(prices, ["date_time", "day_ahead"]).drop_duplicates(subset=["date_time"])
    prices_df["date_time"] = pd.to_datetime(prices_df["date_time"], utc=True)
    prices_df = prices_df.set_index("date_time").sort_index()

    df = fd.merge(ff[["created_at", "ag_start", "ag_end"]], right_index=True, left_on="forecast_id").set_index("date_time")
    df["weekend"] = (df.index.day_of_week >= 5).astype(int)
    df["time"] = df.index.tz_convert("GB").hour + df.index.minute / 60
    df["days_ago"] = (pd.Timestamp.now(tz="UTC") - df["created_at"]).dt.total_seconds() / 3600 / 24
    df["peak"] = ((df["time"] >= 16) & (df["time"] < 19)).astype(float)

    train_df = df[df["forecast_id"].isin(ff_train.index)]
    train_df = train_df[train_df["days_ago"] < max_days]
    train_df = train_df[(train_df.index >= train_df["ag_start"]) & (train_df.index < train_df["ag_end"])]
    train_df = train_df[list(LEGACY_FEATURES)].merge(prices_df[["day_ahead"]], left_index=True, right_index=True, how="inner")
    train_df = train_df.dropna()

    if len(train_df) < min_joined_rows:
        return False, "insufficient joined training rows after legacy filters"

    return True, None


def run_ml_day_ahead_forecast(
    uow: UnitOfWork,
    point_count: int,
    future_feature_frame: pd.DataFrame,
    bridge_day_ahead_values: tuple[float, ...] | None = None,
    max_days: int = 180,
    no_ranges: bool = False,
    use_gpu: bool = False,
) -> MlParityForecastOutput:
    forecasts = uow.session.execute(select(ForecastORM).order_by(ForecastORM.created_at.asc())).scalars().all()
    if len(forecasts) < 2:
        raise ValueError("insufficient forecast history for ML training")

    forecast_data = uow.session.execute(select(ForecastDataORM)).scalars().all()
    if len(forecast_data) < 50:
        raise ValueError("insufficient forecast data rows for ML training")

    prices = uow.session.execute(select(PriceHistoryORM)).scalars().all()
    if len(prices) < 50:
        raise ValueError("insufficient price history rows for ML training")

    ff = _to_dataframe(forecasts, ["id", "name", "created_at"]).set_index("id").sort_index()
    ff["created_at"] = pd.to_datetime(ff["created_at"], utc=True)
    ff["date"] = ff["created_at"].dt.tz_convert("GB").dt.normalize()
    ff["ag_start"] = ff["created_at"].dt.normalize() + pd.Timedelta(hours=22)
    ff["ag_end"] = ff["created_at"].dt.normalize() + pd.Timedelta(hours=334)  # 13 days + 22h start offset
    ff["dt1600"] = (
        (ff["date"] + pd.Timedelta(hours=16, minutes=15) - ff["created_at"].dt.tz_convert("GB"))
        .dt.total_seconds()
        .abs()
    )
    ff_train = ff.sort_values("dt1600").drop_duplicates("date").sort_index()

    fd = _to_dataframe(
        forecast_data,
        [
            "forecast_id",
            "date_time",
            "bm_wind",
            "solar",
            "emb_wind",
            "temp_2m",
            "wind_10m",
            "rad",
            "demand",
        ],
    )
    fd["date_time"] = pd.to_datetime(fd["date_time"], utc=True)

    prices_df = _to_dataframe(prices, ["date_time", "day_ahead"]).drop_duplicates(subset=["date_time"])
    prices_df["date_time"] = pd.to_datetime(prices_df["date_time"], utc=True)
    prices_df = prices_df.set_index("date_time").sort_index()

    df = fd.merge(ff[["created_at", "ag_start", "ag_end"]], right_index=True, left_on="forecast_id").set_index("date_time")
    df["weekend"] = (df.index.day_of_week >= 5).astype(int)
    df["time"] = df.index.tz_convert("GB").hour + df.index.minute / 60
    df["days_ago"] = (pd.Timestamp.now(tz="UTC") - df["created_at"]).dt.total_seconds() / 3600 / 24
    df["dt"] = (df.index - df["created_at"]).dt.total_seconds() / 3600 / 24
    df["peak"] = ((df["time"] >= 16) & (df["time"] < 19)).astype(float)

    train_df = df[df["forecast_id"].isin(ff_train.index)]
    train_df = train_df[train_df["days_ago"] < max_days]
    train_df = train_df[(train_df.index >= train_df["ag_start"]) & (train_df.index < train_df["ag_end"])]
    train_df = train_df[list(LEGACY_FEATURES)].merge(prices_df[["day_ahead"]], left_index=True, right_index=True, how="inner")
    # Allow NaN in features - XGBoost will learn to handle missing data
    train_df = train_df.dropna(subset=["day_ahead"])  # Only require target to be present
    if len(train_df) < 30:
        raise ValueError("insufficient joined training rows after legacy filters")

    train_y = train_df.pop("day_ahead").astype(float)
    train_x = train_df.astype(float)
    sample_weights = ((np.log10((train_y - train_y.mean()).abs() + 10) * 5) - 4).round(0)

    model_kwargs: dict[str, str] = {}
    if use_gpu:
        model_kwargs = {
            "tree_method": "hist",
            "device": "cuda",
        }

    common_model_kwargs = dict(
        objective="reg:squarederror",
        booster="dart",
        gamma=0.2,
        subsample=1.0,
        n_estimators=200,
        max_depth=10,
        colsample_bytree=1,
    )
    model = xg.XGBRegressor(
        **common_model_kwargs,
        **model_kwargs,
    )

    scores: np.ndarray | None = None
    if len(train_x) >= 5:
        cv_model_kwargs = dict(common_model_kwargs)
        if not use_gpu:
            cv_model_kwargs.update(model_kwargs)
        cv_model = xg.XGBRegressor(**cv_model_kwargs)
        scores = cross_val_score(cv_model, train_x, train_y, cv=5, scoring="neg_root_mean_squared_error")

    model.fit(train_x, train_y, sample_weight=sample_weights, verbose=False)

    test_df = df[~df["forecast_id"].isin(ff_train.index)]
    test_df = test_df[test_df.index > test_df["ag_start"]]
    test_df = test_df[test_df["days_ago"] < max_days]
    test_df = test_df.merge(prices_df[["day_ahead"]], left_index=True, right_index=True, how="inner")
    test_df = test_df.dropna(subset=list(LEGACY_FEATURES) + ["day_ahead", "dt"])

    fc = future_feature_frame.copy().sort_index().iloc[:point_count].copy()
    if fc.empty:
        raise ValueError("live future feature frame is empty")

    fc.index = pd.to_datetime(fc.index, utc=True)
    now_utc = pd.Timestamp.now(tz="UTC")
    fc["weekend"] = (fc.index.day_of_week >= 5).astype(int)
    fc["days_ago"] = 0
    fc["time"] = fc.index.tz_convert("GB").hour + fc.index.minute / 60
    fc["dt"] = (fc.index - now_utc).total_seconds() / 86400
    fc["peak"] = ((fc["time"] >= 16) & (fc["time"] < 19)).astype(float)

    feature_frame = fc.reindex(columns=list(LEGACY_FEATURES)).astype(float)
    # Do NOT ffill/bfill - pass NaN to XGBoost which learned to handle missing values
    preds = pd.Series(_predict_with_dmatrix(model, feature_frame), index=fc.index, name="day_ahead").astype(float)

    range_mode = "fallback"
    lows = preds * 0.9
    highs = preds * 1.1
    if (len(test_df) > 10) and (not no_ranges):
        results = test_df[["dt", "day_ahead"]].copy()
        test_features = test_df[list(LEGACY_FEATURES)].astype(float)
        results["pred"] = _predict_with_dmatrix(model, test_features)

        kde = KernelDensity()
        kde.fit(results[["dt", "pred", "day_ahead"]].to_numpy())

        lower = float(np.floor(results[["pred", "day_ahead"]].min(axis=1).min() / 11) * 10)
        upper = float(np.ceil(results[["pred", "day_ahead"]].max(axis=1).max() / 9) * 10)
        quantiles = _kde_quantiles(
            kde,
            dt=fc["dt"].tolist(),
            pred=preds.tolist(),
            quantiles={"day_ahead_low": 0.1, "day_ahead_high": 0.9},
            lim=(lower, upper),
        )

        lows = pd.Series(quantiles["day_ahead_low"], index=fc.index).rolling(3, center=True).mean().bfill().ffill()
        highs = pd.Series(quantiles["day_ahead_high"], index=fc.index).rolling(3, center=True).mean().bfill().ffill()

        lows = pd.concat([preds, lows], axis=1).min(axis=1)
        highs = pd.concat([preds, highs], axis=1).max(axis=1)
        range_mode = "kde"

    bridge_series: pd.Series | None = None
    if bridge_day_ahead_values:
        bridge_points = min(len(bridge_day_ahead_values), len(fc.index))
        bridge_series = pd.Series(
            data=[float(v) for v in bridge_day_ahead_values[:bridge_points]],
            index=fc.index[:bridge_points],
            dtype=float,
        )

    preds, lows, highs = _apply_legacy_scale_blend(
        preds=preds,
        lows=lows,
        highs=highs,
        reference_day_ahead=prices_df["day_ahead"].astype(float),
        bridge_day_ahead=bridge_series,
    )
    blend_mode = "blend+bridge" if bridge_series is not None and not bridge_series.empty else "blend"
    if range_mode == "kde":
        range_mode = f"kde+{blend_mode}"
    else:
        range_mode = f"fallback+{blend_mode}"

    cv_mean = float(-np.mean(scores)) if scores is not None else None
    cv_stdev = float(np.std(scores)) if scores is not None else None

    return MlParityForecastOutput(
        day_ahead_values=tuple(float(v) for v in preds.round(4).tolist()),
        day_ahead_low_values=tuple(float(v) for v in lows.round(4).tolist()),
        day_ahead_high_values=tuple(float(v) for v in highs.round(4).tolist()),
        cv_mean_rmse=cv_mean,
        cv_stdev_rmse=cv_stdev,
        training_rows=len(train_x),
        test_rows=len(test_df),
        feature_columns=LEGACY_FEATURES,
        range_mode=range_mode,
    )