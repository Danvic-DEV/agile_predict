from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xgboost as xg
from sklearn.model_selection import cross_val_score
from sqlalchemy import select

from src.repositories.sql_models import AgileActualORM, ForecastDataORM, ForecastORM, PriceHistoryORM
from src.repositories.unit_of_work import UnitOfWork
from src.ml.transforms.agile_transform import agile_to_day_ahead

log = logging.getLogger(__name__)

LEGACY_FEATURES: tuple[str, ...] = (
    "bm_wind",
    "emb_wind",
    "solar",
    "demand",
    "peak",
    "days_ago",
    "wind_10m",
    "weekend",
    "temp_2m",
    "rad",
    "sin_hour",
    "cos_hour",
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




def _to_dataframe(rows: list[object], cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame([{col: getattr(row, col) for col in cols} for row in rows])


def check_ml_training_readiness(
    uow: UnitOfWork,
    max_days: int = 730,
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
    df["sin_hour"] = np.sin(2 * np.pi * df["time"] / 24)
    df["cos_hour"] = np.cos(2 * np.pi * df["time"] / 24)

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
    max_days: int = 730,
    no_ranges: bool = False,
    use_gpu: bool = False,
    training_region: str = "B",
) -> MlParityForecastOutput:
    # Load all forecasts but exclude seed/bootstrap forecasts from training
    all_forecasts = uow.session.execute(select(ForecastORM).order_by(ForecastORM.created_at.asc())).scalars().all()
    # Filter out seed forecasts (synthetic/deterministic training data)
    forecasts = [f for f in all_forecasts if "seed" not in f.name.lower() and "bootstrap" not in f.name.lower()]
    
    import logging
    log = logging.getLogger(__name__)
    excluded_count = len(all_forecasts) - len(forecasts)
    if excluded_count > 0:
        excluded_names = [f.name for f in all_forecasts if f not in forecasts]
        log.warning(f"Excluded {excluded_count} seed/bootstrap forecasts from training: {excluded_names}")
    
    if len(forecasts) < 2:
        raise ValueError("insufficient forecast history for ML training")

    # Only load forecast_data for valid (non-seed) forecasts
    valid_forecast_ids = {f.id for f in forecasts}
    all_forecast_data = uow.session.execute(select(ForecastDataORM)).scalars().all()
    forecast_data = [fd for fd in all_forecast_data if fd.forecast_id in valid_forecast_ids]
    if len(forecast_data) < 50:
        raise ValueError("insufficient forecast data rows for ML training")

    # Load actual Agile prices (primary training target - real-world outcomes)
    agile_actuals = uow.session.execute(
        select(AgileActualORM).where(AgileActualORM.region == training_region.upper())
    ).scalars().all()
    
    # Load Nordpool day-ahead prices (fallback for periods without Agile actuals)
    nordpool_prices = uow.session.execute(select(PriceHistoryORM)).scalars().all()
    
    # Build combined price dataframe - prefer Agile actuals, fall back to Nordpool
    agile_df = _to_dataframe(agile_actuals, ["date_time", "agile_actual"]) if agile_actuals else pd.DataFrame()
    nordpool_df = _to_dataframe(nordpool_prices, ["date_time", "day_ahead"]) if nordpool_prices else pd.DataFrame()
    
    if agile_df.empty and nordpool_df.empty:
        raise ValueError("insufficient price history rows for ML training")
    
    agile_count = 0
    nordpool_count = 0
    
    prices_df = pd.DataFrame()
    if not agile_df.empty:
        if "agile_actual" not in agile_df.columns:
            raise ValueError("agile_actual column missing from AgileActualORM training data")
        agile_df["date_time"] = pd.to_datetime(agile_df["date_time"], utc=True)
        agile_df = agile_df.set_index("date_time").sort_index()
        prices_df["day_ahead"] = agile_to_day_ahead(agile_df["agile_actual"], training_region)
        agile_count = len(agile_df)
    
    if not nordpool_df.empty:
        nordpool_df["date_time"] = pd.to_datetime(nordpool_df["date_time"], utc=True)  
        nordpool_df = nordpool_df.set_index("date_time").sort_index()
        if prices_df.empty:
            prices_df["day_ahead"] = nordpool_df["day_ahead"]
            nordpool_count = len(nordpool_df)
        else:
            # Fill gaps in Agile data with Nordpool
            before_fill = prices_df["day_ahead"].notna().sum()
            prices_df["day_ahead"] = prices_df["day_ahead"].fillna(nordpool_df["day_ahead"])
            nordpool_count = prices_df["day_ahead"].notna().sum() - before_fill
    
    prices_df = prices_df.dropna(subset=["day_ahead"]).drop_duplicates()
    
    log.info(
        f"ML training using {agile_count} Agile actual prices (region {training_region}) "
        f"+ {nordpool_count} Nordpool day-ahead prices = {len(prices_df)} total price points"
    )

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

    df = fd.merge(ff[["created_at", "ag_start", "ag_end"]], right_index=True, left_on="forecast_id").set_index("date_time")
    df["weekend"] = (df.index.day_of_week >= 5).astype(int)
    df["time"] = df.index.tz_convert("GB").hour + df.index.minute / 60
    df["days_ago"] = (pd.Timestamp.now(tz="UTC") - df["created_at"]).dt.total_seconds() / 3600 / 24
    df["dt"] = (df.index - df["created_at"]).dt.total_seconds() / 3600 / 24
    df["peak"] = ((df["time"] >= 16) & (df["time"] < 19)).astype(float)
    df["sin_hour"] = np.sin(2 * np.pi * df["time"] / 24)
    df["cos_hour"] = np.cos(2 * np.pi * df["time"] / 24)

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
    fc["sin_hour"] = np.sin(2 * np.pi * fc["time"] / 24)
    fc["cos_hour"] = np.cos(2 * np.pi * fc["time"] / 24)

    feature_frame = fc.reindex(columns=list(LEGACY_FEATURES)).astype(float)
    # Do NOT ffill/bfill - pass NaN to XGBoost which learned to handle missing values
    preds = pd.Series(_predict_with_dmatrix(model, feature_frame), index=fc.index, name="day_ahead").astype(float)

    range_mode = "fallback"
    lows = preds * 0.95
    highs = preds * 1.05
    if (len(test_df) > 10) and (not no_ranges):
        results = test_df[["dt", "day_ahead"]].copy()
        test_features = test_df[list(LEGACY_FEATURES)].astype(float)
        results["pred"] = _predict_with_dmatrix(model, test_features)
        results["residual"] = results["day_ahead"] - results["pred"]

        # Use empirical residual quantiles: bands are offsets from pred based on
        # the IQR of actual errors on the test set. This correctly centres the
        # band on pred and reflects true model uncertainty rather than the raw
        # price distribution (which the KDE joint approach collapsed into).
        p25_residual = float(results["residual"].quantile(0.25))
        p75_residual = float(results["residual"].quantile(0.75))

        lows = preds + p25_residual
        highs = preds + p75_residual
        # Ensure high >= pred; low is unclamped to show honest bias direction.
        highs = pd.concat([preds, highs], axis=1).max(axis=1)
        range_mode = "residual_iqr"

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