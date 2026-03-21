# External Data Ingestion Feeds Inventory
*Generated: 2026-03-20*

Comprehensive mapping of all external API data sources feeding the agile_predict system.

---

## 1. AGILE UK TARIFF PRICES (Octopus Energy)

### API Endpoints
- **Base URL**: `https://api.octopus.energy/v1/products/`
- **Full endpoint**: `https://api.octopus.energy/v1/products/E-1R-{PRODUCT_CODE}-{REGION}/standard-unit-rates/`
- **Product Code**: `AGILE-24-10-01` (current tariff)
- **Example**: `https://api.octopus.energy/v1/products/E-1R-AGILE-24-10-01-G/standard-unit-rates/`

### Regional Coverage
**15 regions** (A-N, P, X):
- X: National Average (0.2136, 12.21 factors)
- A: Eastern England (0.21, 13.0)
- B: East Midlands (0.20, 14.0)
- C: London (0.20, 12.0)
- D: Merseyside and Northern Wales (0.22, 13.0)
- E: West Midlands (0.21, 11.0)
- F: North Eastern England (0.21, 12.0)
- G: North Western England (0.21, 12.0)
- H: Southern England (0.21, 12.0)
- J: South Eastern England (0.22, 12.0)
- K: Southern Wales (0.22, 12.0)
- L: South Western England (0.23, 11.0)
- M: Yorkshire (0.20, 13.0)
- N: Southern Scotland (0.21, 13.0)
- P: Northern Scotland (0.24, 12.0)

### Pull Frequency
- **Historical pull**: On-demand via management command (`latest_agile.py`)
- **Real-time frequency**: 30-minute update intervals per period window
- **Data granularity**: 30-minute periods (48 periods/day)
- **Lookback window**: Typically since July 2023 (`2023-07-01`)

### Error Handling
- **Strategy**: Basic try/except with external exception logging
- **Logger**: Configured in `config/settings.py` logging section
- **Failure behavior**: Returns silently on `RequestException` — no exception raised
- **Log destination**: `logs/utils.log`

### Retry Logic
- **Retries**: 3 attempts maximum
- **Backoff**: None implemented (immediate retry)
- **Retry triggers**:
  ```
  HTTPStatus.TOO_MANY_REQUESTS (429)
  HTTPStatus.INTERNAL_SERVER_ERROR (500)
  HTTPStatus.BAD_GATEWAY (502)
  HTTPStatus.SERVICE_UNAVAILABLE (503)
  HTTPStatus.GATEWAY_TIMEOUT (504)
  ```

### Code Location
- **Primary function**: [`config/utils.py`](config/utils.py#L495) — `get_agile(start, tz="GB", region="G")`
- **Usage command**: [`prices/management/commands/latest_agile.py`](prices/management/commands/latest_agile.py)
- **National converter**: [`config/utils.py`](config/utils.py#L528) — `day_ahead_to_agile(df, reverse, region)`
- **Regional factors**: [`config/settings.py`](config/settings.py#L242-L296) — `GLOBAL_SETTINGS["REGIONS"]`

### Storage Model
- **Table**: `prices_pricehistory` (Django ORM)
- **Fields**: `date_time`, `day_ahead`, `agile`, `id`
- **Transformation**: Raw Agile prices stored; Day-Ahead prices derived via regional multiplier + offset

---

## 2. DAY-AHEAD PRICES (Nordpool / N2EX)

### API Endpoints
- **URL**: `https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices`
- **Market**: N2EX_DayAhead (UK's day-ahead electricity market)
- **Delivery area**: UK (single geographic zone)
- **Currency**: GBP

### Query Parameters
```
date              = {target_date} [YYYY-MM-DD, typically +13 hours from now]
market            = N2EX_DayAhead
deliveryArea      = UK
currency          = GBP
```

### Pull Frequency
- **Schedule**: Daily, requested ~13 hours before delivery
- **Time window**: 24 hourly prices per day (or 48 half-hourly theoretical)
- **Payload type**: `multiAreaEntries` with hourly deliveryStart timestamps and `entryPerArea.UK` prices

### Error Handling
- **Strategy**: Exception caught in `fetch_day_ahead_prices()`, re-raised if final attempt fails
- **Fallback**: When unavailable, pipeline uses deterministic values or skips Nordpool upsert
- **Log destination**: Implicit via logger; no explicit logging in nordpool.py

### Retry Logic
- **Retries**: 3 attempts
- **Backoff**: Exponential (2.0x multiplier, delays: 0s, 2s, 4s)
- **Timeout**: 20 seconds per request
- **Implementation**: `_retry(fn, retries=3, backoff=2.0)`

### Code Locations
- **Newer (preferred) implementation**: [`backend/src/ml/ingest/nordpool.py`](backend/src/ml/ingest/nordpool.py)
  - `fetch_day_ahead_prices(now=None, timeout=20)` — main fetch
  - `build_nordpool_params(target_date)` — param builder
  - `parse_day_ahead_payload(payload)` — JSON parser
- **Legacy implementation**: [`config/utils.py`](config/utils.py#L34) — `get_gb60()` (deprecated but still in codebase)
- **Usage in pipeline**: [`backend/src/jobs/pipelines/update_forecast.py`](backend/src/jobs/pipelines/update_forecast.py#L27) — imports and calls `fetch_day_ahead_prices`

### Storage Model
- **Table**: `prices_pricehistory` (same as Agile)
- **Model in legacy**: `prices_nordpool` model (being phased out per migration 0026)
- **Join strategy**: Price history join on forecast data via `day_ahead` column for ML training

---

## 3. WEATHER DATA (Open-Meteo)

### API Endpoints
- **Archive (historical)**: `https://archive-api.open-meteo.com/v1/archive`
- **Forecast (current+future)**: `https://api.open-meteo.com/v1/forecast`
- **Fixed location**: Latitude 54.0°N, Longitude 2.3°E (central UK)

### Metrics
```
temperature_2m      → temp_2m           (°C)
wind_speed_10m      → wind_10m          (m/s)
direct_radiation    → rad               (W/m²)
```

### Pull Frequency & Lookback
| Source | Lookback | Frequency | Coverage |
|--------|----------|-----------|----------|
| Archive | 62 days | Historical complete | Full historical if available |
| Forecast | Last 5D + next 14D | Real-time | Current + near-term lookback |

### Error Handling
- **Strategy**: Individual source try/except; failures logged as warnings, not fatal
- **Fallback chain**: Archive → Forecast → Uses forecast data to fill archive gaps
- **Graceful degradation**: All sources optional; if all fail, raises `RuntimeError` for calling pipeline
- **Logger**: Module-level logger, warnings on individual failures

### Retry Logic
- **Retries**: 3 attempts per source
- **Backoff**: Exponential (2.0x multiplier)
- **Timeout**: 30 seconds per request
- **Implementation**: `_retry(fn, retries=3, backoff=2.0)`

### Code Location
- **Main module**: [`backend/src/ml/ingest/grid_weather.py`](backend/src/ml/ingest/grid_weather.py#L170) — `_fetch_open_meteo(start_date, end_date)`
- **Called by**: [`backend/src/ml/ingest/grid_weather.py`](backend/src/ml/ingest/grid_weather.py#L233) — `fetch_grid_weather_features(lookback_days=62, now=None)`
- **Resample**: 30-minute intervals via `resample("30min")` with interpolation

### Data Columns Output
After processing: `temp_2m`, `wind_10m`, `rad` (UTC datetime index, 30-minute resolution)

---

## 4. GRID & DEMAND DATA (National Grid ESO via NESO Open Data Portal)

### API Endpoints

#### NESO SQL API
- **Base URL**: `https://api.neso.energy/api/3/action/datastore_search_sql`
- **Query method**: POST with SQL WHERE clauses

#### NESO REST API
- **Base URL**: `https://api.neso.energy/api/3/action/datastore_search`
- **Method**: GET with resource_id + limit params

### Data Sources (Resource IDs)

| Metric | Resource ID | Query Type | Description |
|--------|-------------|-----------|-------------|
| **Demand (Actual)** | `bf5ab335-9b40-4ea4-b93a-ab4af7bce003` | SQL | Settlement-period demand (fallback 1) |
| | `f6d02c0f-957b-48cb-82ee-09003f2ba759` | SQL | Settlement-period demand (fallback 2) |
| **BM Wind** | `7524ec65-f782-4258-aaf8-5b926c17b966` | SQL | Incentive wind forecast (40k limit) |
| **Solar/Wind (Historical)** | `f93d1835-75bc-43e5-84ad-12472b180a98` | SQL | Embedded solar + total wind (20k limit) |
| **Embedded Solar/Wind (Forecast)** | `db6c038f-98af-4570-ab60-24d71ebd0ae5` | REST | Solar + embedded wind forecast |
| **Demand (Forecast)** | `7c0411cd-2714-4bb5-a408-adb065edf34d` | SQL | National demand forecast |

### Pull Frequency & Lookback
- **Historical window**: Since provided `start_date` (typically last 27-62 days)
- **Frequency**: 30-minute resampled from settlement periods (half-hourly native)
- **Forecasts**: Updated daily with new day-ahead windows

### Error Handling
- **Strategy**: Per-source try/except; individual failures logged as warnings (non-blocking)
- **Fallback chain** for demand:
  1. Try Elexon INDO (28-day window)
  2. If fails → Try NESO resource 1 (settlement-period)
  3. If fails → Try NESO resource 2 (settlement-period)
  4. If all fail → Empty series returned
- **Return**: DataFrame/Series with `dtype=float` even if empty

### Retry Logic
- **Retries**: 3 attempts per source
- **Backoff**: Exponential (2.0x multiplier, delays: 0s, 2s, 4s)
- **Timeout**: 30 seconds
- **Implementation**: `_retry(fn, retries=3, backoff=2.0)`

### Code Locations
- **Main module**: [`backend/src/ml/ingest/grid_weather.py`](backend/src/ml/ingest/grid_weather.py)
  - `fetch_grid_weather_features(lookback_days=62, now=None)` — orchestrator function
  - `_fetch_neso_demand(start_date)` — demand with Elexon fallback
  - `_fetch_neso_bm_wind(start_date)` — BM wind forecast
  - `_fetch_neso_solar_wind(start_date)` — historical solar/wind
  - `_fetch_neso_embedded(start_date)` — forecast embedded solar/wind
- **SQL helper**: `_neso_sql(resource_id, where_clause, limit)`
- **JSON parser**: `_get_json(url, params, timeout)`

### Output Columns (30-min UTC index)
Requires: `bm_wind`, `solar`, `emb_wind`, `demand`, `temp_2m`, `wind_10m`, `rad`
- Fills missing columns with 0 and forward/backward fills
- Drops rows where >half are still NaN

---

## 5. ELEXON BMRS - DEMAND FORECASTS (NDF)

### API Endpoints
- **URL**: `https://data.elexon.co.uk/bmrs/api/v1/datasets/NDF`
  - NDF = National Demand Forecast (half-hourly 14-day window)
- **Method**: GET with date range params

### Query Parameters
```
publishDateTimeFrom = {now.normalize()}    [YYYY-MM-DD]
publishDateTimeTo   = {now.normalize() + 1D} [YYYY-MM-DD]
```

### Pull Frequency
- **Schedule**: Daily for current + next day forecast
- **Window**: 14-day lookahead
- **Frequency**: Half-hourly (30-minute periods)

### Error Handling
- **Strategy**: Caught in dataset aggregation; individual fetch failures non-fatal
- **Fallback**: NESO demand data used if Elexon unavailable
- **Logger**: Module-level warnings

### Retry Logic
- **Retries**: 3 per call via `_retry` wrapper
- **Backoff**: Exponential (2.0x)

### Code Location
- **Usage**: [`backend/src/ml/ingest/grid_weather.py`](backend/src/ml/ingest/grid_weather.py#L64) — `_fetch_neso_demand(start_date)` (tries Elexon first)
- **Legacy reference**: [`config/utils.py`](config/utils.py#L143) — `get_latest_forecast()` contains direct NDF call

---

## 6. ELEXON BMRS - ACTUAL DEMAND (INDO)

### API Endpoints
- **URL**: `https://data.elexon.co.uk/bmrs/api/v1/datasets/INDO`
  - INDO = Initial National Demand Outturn (actual demand, last 28 days)
- **Method**: GET with date range

### Query Parameters
```
publishDateTimeFrom = {now - 27D}  [YYYY-MM-DD]
publishDateTimeTo   = {now + 1D}   [YYYY-MM-DD]
format              = json
```

### Pull Frequency
- **Lookback**: 28 days rolling window
- **Frequency**: 30-minute actual measurements
- **Update cadence**: As new data published (typically half-hourly)

### Error Handling
- **Strategy**: Try/except; on failure, falls back to NESO settlement-period demand
- **Logger**: Warning message on Elexon failure

### Retry Logic
- **Retries**: 3 attempts
- **Backoff**: Exponential (2.0x)

### Code Location
- **Usage**: [`backend/src/ml/ingest/grid_weather.py`](backend/src/ml/ingest/grid_weather.py#L64) — `_fetch_neso_demand(start_date)` (primary try)

---

## 7. ELEXON BMRS - FUEL MIX & INTERCONNECTORS (FUELINST)

### API Endpoints
- **URL**: `https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST/stream`
- **Method**: GET with date range

### Query Parameters
```
publishDateTimeFrom = {start_dt}  [ISO 8601 with Z]
publishDateTimeTo   = {end_dt}    [ISO 8601 with Z]
```

### Metrics Extracted
| Fuel Type | Aggregation | Column Name |
|-----------|-------------|------------|
| CCGT + OCGT | Sum | `gas_mw` |
| WIND | Direct | `wind_mw` |
| NUCLEAR | Direct | `nuclear_mw` |
| PS | Direct | `pumped_storage_mw` |
| INT* (all interconnectors) | Sum | `interconnector_net_mw` |

### Pull Frequency
- **Lookback**: User-specified (typically 62+ days)
- **Frequency**: 30-minute resampled from native settlement periods
- **Status**: Wave A future feed (deferred, not yet in primary pipeline)

### Error Handling
- **Strategy**: Non-blocking try/except, logs warnings
- **Failure mode**: Returns empty DataFrame with all columns present

### Retry Logic
- **Retries**: 3 with exponential backoff (2.0x)

### Code Location
- **Function**: [`backend/src/ml/ingest/system_context.py`](backend/src/ml/ingest/system_context.py) — `fetch_fuelinst_context(start_dt, end_dt)`
- **Status**: Proposed for future improvements; not currently integrated

---

## 8. CARBON INTENSITY (Future: Wave A Deferred Feed)

### API Endpoints
- **URL**: `https://api.carbonintensity.org.uk/intensity/{start}/{end}`
  - Date format: `YYYY-MM-DDTHH:MMZ` (ISO 8601 UTC)

### Metric
- **Unit**: gCO2/kWh
- **Resolution**: 30-minute with interpolation

### Pull Frequency
- **Lookback**: User-specified window
- **Frequency**: Resampled to 30-minute

### Error Handling
- **Strategy**: Try/except; returns empty Series on failure
- **Logger**: Warnings on fetch failure

### Retry Logic
- **Retries**: 3 with exponential backoff (2.0x)

### Code Location
- **Function**: [`backend/src/ml/ingest/system_context.py`](backend/src/ml/ingest/system_context.py) — `fetch_carbon_intensity(start_dt, end_dt)`
- **Status**: Proposed for Wave A; not yet in primary forecast pipeline
- **Rationale**: Correlates with fuel/wind/solar generation patterns

---

## SUMMARY TABLE: All Feeds

| Feed | API Endpoint | Frequency | Regions/Locations | Error Handling | Retry Logic | Code Location |
|------|--------------|-----------|-------------------|----------------|-------------|---------------|
| **Agile UK** | `api.octopus.energy/v1/products/E-1R-*` | 30-min | 15 (A-P, X) | Try/catch, silent fail | 3x (no backoff) | `config/utils.py` |
| **Day-Ahead (Nordpool)** | `dataportal-api.nordpoolgroup.com/api/DayAheadPrices` | Daily | UK-only | Exception on fail | 3x (exp 2.0x) | `backend/src/ml/ingest/nordpool.py` |
| **Weather (Open-Meteo)** | `archive-api.open-meteo.com`, `api.open-meteo.com` | 62-day + 14D forecast | Central UK (54.0, 2.3) | Per-source warnings | 3x (exp 2.0x) | `backend/src/ml/ingest/grid_weather.py` |
| **NESO Demand** | `api.neso.energy/api/3/action/datastore_search_sql` | 30-min | UK national | Fallback chain (3-tier) | 3x (exp 2.0x) | `backend/src/ml/ingest/grid_weather.py` |
| **NESO BM Wind** | `api.neso.energy/api/3/action/datastore_search_sql` | 30-min | UK national | Warning on fail | 3x (exp 2.0x) | `backend/src/ml/ingest/grid_weather.py` |
| **NESO Solar/Wind** | `api.neso.energy/api/3/action/datastore_search_sql` | 30-min | UK national | Warning on fail | 3x (exp 2.0x) | `backend/src/ml/ingest/grid_weather.py` |
| **NESO Embedded Solar/Wind** | `api.neso.energy/api/3/action/datastore_search` | 30-min | UK national (forecast) | Warning on fail | 3x (exp 2.0x) | `backend/src/ml/ingest/grid_weather.py` |
| **Elexon INDO (demand actual)** | `data.elexon.co.uk/bmrs/api/v1/datasets/INDO` | 28-day window | UK national | Fallback to NESO | 3x (exp 2.0x) | `backend/src/ml/ingest/grid_weather.py` |
| **Elexon NDF (demand forecast)** | `data.elexon.co.uk/bmrs/api/v1/datasets/NDF` | Daily 14-day | UK national | Non-fatal | 3x (exp 2.0x) | `backend/src/ml/ingest/grid_weather.py` |
| **Elexon FUELINST** | `data.elexon.co.uk/bmrs/api/v1/datasets/FUELINST/stream` | 30-min (future) | UK national | Non-fatal | 3x (exp 2.0x) | `backend/src/ml/ingest/system_context.py` |
| **Carbon Intensity** | `api.carbonintensity.org.uk/intensity/` | 30-min (deferred) | GB | Non-fatal | 3x (exp 2.0x) | `backend/src/ml/ingest/system_context.py` |

---

## KEY CONFIGURATIONS & SETTINGS

### Core Configuration
- **Django Settings**: [`config/settings.py`](config/settings.py)
  - Dataset sources + names → `GLOBAL_SETTINGS["DATASETS"]`
  - Region definitions + conversion factors → `GLOBAL_SETTINGS["REGIONS"]`
  - Logging → `LOGGING` section with file + console handlers
  
- **Backend Runtime Settings**: [`backend/src/core/settings.py`](backend/src/core/settings.py)
  - Auto-bootstrap regions: `AUTO_BOOTSTRAP_REGIONS` (default: "X,G")
  - Auto-update interval: `AUTO_UPDATE_INTERVAL_SECONDS` (default: 1800s = 30min)
  - Allow ingest fallback: `ALLOW_INGEST_FALLBACK`
  - Allow ML fallback: `ALLOW_ML_FALLBACK`

- **Region Conversion Factors**: [`backend/src/core/regions.py`](backend/src/core/regions.py)
  - Maps region codes (A-P, X) to (multiplier, offset) tuples for Day-Ahead → Agile conversion
  - Example: region "G" = (0.21, 12.0) means: `agile = day_ahead * 0.21 + 12.0` (16:00-19:00 offset +12, otherwise +0)

### Retry Configuration (Global Defaults)
- **Max retries**: 3 attempts
- **Backoff**: Exponential with 2.0x multiplier
- **Backoff timing**:
  - Attempt 1: 0s
  - Attempt 2: 2s (2¹)
  - Attempt 3: 4s (2²)
- **Implementation**: `_retry(fn, retries=3, backoff=2.0)` in `grid_weather.py`, `nordpool.py`, `system_context.py`

### Logging
- **Logger name**: `config.utils` (for legacy) and module-level loggers in backend
- **Log file**: `logs/utils.log`
- **Level**: DEBUG (handlers) → INFO (logger)
- **Handlers**: File + console
- **Format**: `{asctime} {levelname} {name} {message}`

---

## MANAGEMENT COMMANDS (Legacy Ingestion)

### Active Commands
- **`prices/management/commands/latest_agile.py`**: Fetches latest Agile + Day-Ahead prices
  - Uses: `get_agile()`, `day_ahead_to_agile()`, `df_to_Model()`
  - Output: Stores to `PriceHistory` model
  
- **`prices/management/commands/update.py`**: Main forecast update pipeline with ML training
  - Uses: `get_latest_history()`, `get_latest_forecast()`, XGBoost ML
  - Output: Writes `History`, `ForecastData`, `AgileData` models
  
- **`prices/management/commands/full_hist.py`**: Hydrates full historical dataset
  - Uses: `get_latest_history()` (all grid+weather feeds)
  - Deletive: Clears and rebuilds `History` model

### Deprecated Commands
- **`nordpool_v_agile.py`**: Legacy Nordpool comparison (uses old `get_nordpool()`)
  - Model: `prices_nordpool` (migration 0026 deleted this table)
  
- **`get_local.py`**: Syncs data from production via `flyctl ssh`

---

## PIPELINE ARCHITECTURE

### Currently Active Pipeline
```
Backend API Lifecycle:
  1. Auto-bootstrap on startup (ENABLED)
     ├─ Fetch: get_latest_history() → fetches all NESO, Elexon, Open-Meteo
     ├─ Fetch: Nordpool day-ahead prices (if available)
     └─ Bootstrap: Deterministic or ML mode writing forecasts
  
  2. Auto-update (30-min interval, can be disabled)
     ├─ Fetch: Grid + weather features (62D lookback)
     ├─ Fetch: Nordpool prices (current day-ahead)
     ├─ ML: Train/predict on features
     └─ Write: ForecastData + AgileData by region
```

### Feature Assembly Order
1. **Demand** (Elexon INDO → NESO fallback)
2. **BM Wind** (NESO)
3. **Solar/Wind historical** (NESO)
4. **Embedded forecast** (NESO)
5. **Weather** (Open-Meteo archive + forecast blend)
6. **Validation**: Ensure all 7 required columns; fill missing with 0, forward/backward fill

### Deterministic Mode (Bootstrap/Fallback)
- Uses hardcoded base values + periodic variation
- Configuration: [`backend/src/domain/bootstrap_bundle.py`](backend/src/domain/bootstrap_bundle.py)
- Replaces ML when ingest fails or ML disabled

---

## FAILURE MODES & RESILIENCE

### Cascade Failures (Non-blocking)
1. **Nordpool unavailable** → Uses day-ahead=None in training data; forecast still runs on historical prices
2. **Weather unavailable** → Logs warning; uses fallback forecast data
3. **NESO demand unavailable** → Tries Elexon; if both fail, demand=0 or fallback
4. **Individual NESO source fails** → Continues with remaining sources; raises error only if all fail

### Fatal Failures (Pipeline stops)
- All NESO + Elexon + Open-Meteo sources fail simultaneously
- Error: `RuntimeError("All grid/weather feature sources failed")`
- Fallback: Switches to deterministic mode if `ALLOW_INGEST_FALLBACK=true`

### Data Freshness Monitoring
- Diagnostic endpoint: `/api/v1/diagnostics/ingest-pipeline-health`
- Checks: Last 24h data availability per source
- Alert sources:
  - `neso_bm_wind`, `neso_solar`, `neso_embedded_wind`
  - `elexon_demand`, `openmeteo_temp`, `openmeteo_wind`, `openmeteo_rad`
  - `nordpool_day_ahead`

---

## ENVIRONMENT VARIABLES & CONFIGURATION

### Not Required (Public APIs, No Auth)
- All endpoints are **unauthenticated public APIs**
- No API keys or credentials stored for external feeds

### Related Environment Variables
```
DATABASE_URL              # PostgreSQL connection; needed for storing fetched data
AUTO_UPDATE_INTERVAL_SECONDS  # Control fetch frequency (default: 1800 = 30min)
AUTO_BOOTSTRAP_REGIONS    # Regions to bootstrap (default: "X,G")
ALLOW_INGEST_FALLBACK     # Fallback to deterministic on ingest failure
AUTO_UPDATE_ENABLED       # Enable auto-update job (default: true)
```

---

## NOTES FOR EXTERNAL FEED IMPROVEMENTS

### Wave A Candidates (Deferred)
1. **Carbon Intensity** (`fetch_carbon_intensity()`)
   - Ready: `backend/src/ml/ingest/system_context.py`
   - Status: Implemented but not integrated into pipeline
   
2. **Fuel Mix** (`fetch_fuelinst_context()`)
   - Ready: `backend/src/ml/ingest/system_context.py`
   - Status: Proposed; provides gas, nuclear, interconnector context

3. **Multiple Weather Locations**
   - Currently: Single location (54.0, 2.3 central UK)
   - Future: Regional variations (offshore wind bias, southern solar, etc.)

4. **Gas & Emissions**
   - UK pricing frequently gas-marginal
   - Requires unit normalization (EUR/MWh → GBP/MWh, p/therm conversions)
   - Wait for publication-time alignment guarantees

### Integration Pattern (for new feeds)
1. Create function in `backend/src/ml/ingest/{module}.py`
2. Implement retry/fallback logic matching existing pattern
3. Add to `fetch_grid_weather_features()` orchestrator
4. Update diagnostics endpoint in `backend/src/api/v1/routes/diagnostics.py`
5. Add column to required `["bm_wind", "solar", ...]` list

---

## References

### Key Files
- [config/utils.py](config/utils.py) — Legacy ingestion utilities (get_agile, day_ahead_to_agile, DataSet class)
- [backend/src/ml/ingest/](backend/src/ml/ingest/) — Modern ingestion modules
  - [grid_weather.py](backend/src/ml/ingest/grid_weather.py) — NESO, Elexon, Open-Meteo harmonization
  - [nordpool.py](backend/src/ml/ingest/nordpool.py) — Nordpool day-ahead prices
  - [system_context.py](backend/src/ml/ingest/system_context.py) — Future feeds (carbon, fuel mix)
- [backend/src/jobs/pipelines/update_forecast.py](backend/src/jobs/pipelines/update_forecast.py) — Main forecast pipeline using all feeds
- [backend/src/api/v1/routes/diagnostics.py](backend/src/api/v1/routes/diagnostics.py) — Feed health monitoring endpoints
- [config/settings.py](config/settings.py) — Regional factors + dataset definitions

### Documentation
- [docs/external-feed-improvement-plan.md](docs/external-feed-improvement-plan.md) — Deferred feed proposals
- [docs/implementation-roadmap.md](docs/implementation-roadmap.md) — Nordpool + parity migration history

---

**Last Updated**: 2026-03-20
**Generator**: Comprehensive external data ingestion codebase scan
