"""Legacy-compatible ML parity forecasting modules."""

from src.ml.parity.day_ahead_xgb import MlParityForecastOutput, run_ml_day_ahead_forecast

__all__ = ["MlParityForecastOutput", "run_ml_day_ahead_forecast"]
