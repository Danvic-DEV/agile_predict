# ML Parity Plan (Legacy Pipeline -> FastAPI)

## Goal
Rebuild the legacy working ML behavior from command `update.py` inside the new FastAPI pipeline, while keeping the new architecture (domain modules, repositories, single-container runtime, diagnostics).

## Status (2026-03-17)
- Completed: strict no-fallback defaults are enabled for ingest, ML update execution, and startup seeding.
- Completed: fallback paths are now opt-in only via env flags (`ALLOW_INGEST_FALLBACK`, `ALLOW_ML_FALLBACK`, `ALLOW_STARTUP_BOOTSTRAP_FALLBACK`).
- Completed: implemented XGBoost parity trainer and predictor in `backend/src/ml/parity/day_ahead_xgb.py`.
- Completed: update job now attempts ML-backed predictions first and falls back to deterministic pipeline on any ML failure.
- Completed: KDE/fallback day-ahead low/high ranges are computed and written through to agile low/high bounds.
- Completed: legacy scale/blend post-processing around known price window is ported (no-GB60 path).
- Completed: forecast-level CV metrics (`mean`, `stdev`) are now persisted in forecast rows when ML is available.
- Completed: diagnostics/state now include ML metadata (`ml_error`, training/test row counts, CV metrics, feature version, range mode).
- Completed: rollout switch added (`ML_WRITE_MODE`: `ml|deterministic|shadow`) with shadow-mode delta metrics (MAE, max abs, p95 abs).
- Completed: second legacy blend window is now implemented via deterministic day-ahead bridge (`shift=5`) in the migrated update path.
- Remaining: dedicated GB60 ingest parity is still open; current bridge window uses deterministic Nordpool-aligned values as a surrogate.

## Legacy behavior to match exactly

### 1. Training target and scope
- Target: day-ahead price (`day_ahead`) in `PriceHistory`.
- Prediction horizon cadence: 30-minute slots.
- Training subset: rows from historical forecasts nearest to 16:15 each day.
- Training time slice: only rows where target time is within `ag_start`..`ag_end` (22:00 to +24h from forecast day).
- Max age filter: `days_ago < max_days` (default 60).

### 2. Feature set
Legacy model uses these features:
- `bm_wind`
- `solar`
- `demand`
- `peak` (1 for 16:00-19:00 local, else 0)
- `days_ago`
- `wind_10m`
- `weekend`

Derived fields used in prep:
- `time` (hour + minute/60)
- `dt` (days between target time and forecast creation)
- `ag_start`, `ag_end` from forecast creation date

### 3. Model and training
- Model: `xgboost.XGBRegressor`
- Params:
  - `objective="reg:squarederror"`
  - `booster="dart"`
  - `gamma=0.2`
  - `subsample=1.0`
  - `n_estimators=200`
  - `max_depth=10`
  - `colsample_bytree=1`
- Sample weights:
  - `((log10(abs(y - y_mean) + 10) * 5) - 4).round(0)`
- Validation:
  - `cross_val_score(..., cv=5, scoring="neg_root_mean_squared_error")`

### 4. Confidence ranges
- Build KDE over `[dt, pred, day_ahead]` using `KernelDensity`.
- Compute 10% and 90% quantiles with interpolation (`day_ahead_low`, `day_ahead_high`).
- Smooth low/high with centered rolling window of 3.
- Clamp low/high around point prediction.
- Fallback when insufficient test points or ranges disabled: low/high = point * 0.9 / 1.1.

### 5. Post-processing and region conversion
- Scale/blend strategy around known price windows (actual agile and Nordpool bridge behavior).
- Convert day-ahead to regional agile using existing region factors and peak adders:
  - agile = day_ahead * factor + peak_adder (peak hours only)
- Emit per-region outputs (`agile_pred`, `agile_low`, `agile_high`).

### 6. Outputs persisted
- Save forecast metadata (`mean`, `stdev`) from CV scores.
- Save forecast feature frame to forecast data table.
- Save per-region agile predictions and range bounds.

## FastAPI implementation plan

### Phase A: Data parity layer
1. Build a parity data assembler in `backend/src/ml/` that reproduces:
- forecast row joins
- 16:15 nearest-forecast selection
- feature derivation and filters
2. Add deterministic fixture tests against known mini-datasets to verify exact row selection and feature columns.

### Phase B: Model parity layer
1. Implement `train_xgb_day_ahead_model(...)` with identical hyperparameters and sample weighting.
2. Implement CV score capture and output summary (`mean`, `stdev`).
3. Add unit tests asserting:
- model trains successfully on fixture data
- feature ordering is preserved
- CV score array exists and is numeric

### Phase C: Range parity layer
1. Implement KDE-based low/high quantile generator with same quantile math.
2. Implement fallback 0.9/1.1 path.
3. Add tests for:
- normal KDE path
- low sample fallback path
- monotonic bounds (`low <= pred <= high`)

### Phase D: Write-path parity
1. Integrate model output into `run_update_forecast_job` pipeline output contract.
2. Persist forecast metrics and per-region agile outputs exactly like legacy behavior.
3. Keep existing deterministic fallback as hard fail-safe.

### Phase E: Diagnostics and acceptance
1. Extend diagnostics payload with ML run metadata:
- training rows
- test rows
- cv_mean_rmse
- cv_stdev_rmse
- feature list hash/version
- range mode (`kde` or `fallback`)
2. Add integration tests covering:
- update job writes model-backed outputs
- diagnostics exposes ML metadata

## Acceptance criteria
- New pipeline reproduces legacy feature engineering and row filtering semantics.
- XGBoost model config and weighting match legacy values.
- Confidence intervals match legacy method (KDE + fallback).
- Regional output behavior matches existing region factor transformation.
- Startup/update flow remains resilient (deterministic fallback still available).

## Rollout strategy
1. Shadow mode: run ML path and deterministic path side-by-side; persist only deterministic for one cycle.
2. Compare outputs and RMSE deltas in diagnostics.
3. Enable ML-backed write path once parity thresholds are met.
4. Keep runtime switch to revert instantly to deterministic output.
