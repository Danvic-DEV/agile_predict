# Implementation Roadmap

This repository now contains migration scaffolding for a standard stack:

- Backend API: FastAPI
- Frontend UI: React + Vite
- Database: Postgres embedded in the app container with persisted data under `/config/postgresql`
- Packaging: GHCR-ready single app image
- Runtime contract: `/config` volume with first-run `.env` generation and persistent DB data

## Local startup (app container)

Preferred full local stack startup:

```bash
./bin/start_local_stack.sh
```

This launches the single app container, initializes Postgres on startup, creates tables, and seeds deterministic forecast data when the database is empty.

Startup seeding mode is controlled by `AUTO_BOOTSTRAP_MODE` in `/config/.env`:

- `update` (default): run the update pipeline first; if it writes no rows or errors, fallback to bootstrap seed.
- `bootstrap`: skip update pipeline and write bootstrap seed directly.

Related startup controls:

- `AUTO_BOOTSTRAP_ON_STARTUP=true|false`
- `AUTO_BOOTSTRAP_POINTS=<int>`
- `AUTO_BOOTSTRAP_REGIONS=<comma-separated regions>`

Stop the stack with:

```bash
./bin/stop_local_stack.sh
```

Manual app-container startup:

1. Build image

```bash
docker build -f deploy/docker/backend.Dockerfile -t agile-predict:dev .
```

2. Run image with persistent config

```bash
docker run --rm -p 8000:8000 -v "$PWD/runtime-config:/config" agile-predict:dev
```

On first run, `/config/.env` is created from `deploy/docker/default.env` and Postgres data is initialized under `/config/postgresql`.

The built frontend is served by the same container on `http://localhost:8000`.

## Backend tests (migration stack)

```bash
./bin/test_backend.sh
```

The script builds the backend image and runs pytest inside the container with a test-safe PostgreSQL URL for config validation.

Current test coverage includes:

- ML region transforms (day-ahead to agile and reverse)
- Time feature generation
- Idempotent bootstrap bundle domain orchestration
- Runtime startup seeding behavior (update mode, bootstrap mode, and fallback path)
- API contract smoke checks for core endpoints (`/healthz`, `/api/v1/health`, `/api/v1/forecasts/regions`)
- Parity summary diagnostics endpoint behavior (missing and populated report cases)

Automated test workflow:

- `.github/workflows/backend-tests.yml` runs `./bin/test_backend.sh` on PRs and manual dispatch.

## Notes

- The legacy Django app remains untouched.
- New scaffolding is under `backend`, `frontend`, `deploy`, and `shared`, but runtime packaging is now a single container.
- Migration implementation is active on branch `migration/standard-stack-foundation`.

## Current API Endpoints (Migration Backend)

- `GET /healthz`
- `GET /api/v1/health`
- `GET /api/v1/forecasts?limit=1`
- `GET /api/v1/forecasts/regions`
- `GET /api/v1/forecasts/prices?region=G&days=7&forecast_count=1&high_low=true`
- `GET /api/v1/forecasts/{forecast_id}/data?limit=336`
- `GET /api/v1/forecasts/{forecast_id}/data-stats?limit=336`
- `GET /api/v1/diagnostics/latest-summary`
- `GET /api/v1/diagnostics/parity-last-summary`
- `GET /api/v1/diagnostics/parity-history?limit=5&offset=0&status=fail&since=2026-03-10T00:00:00Z`
- `POST /api/v1/admin-jobs/bootstrap-forecast`
- `POST /api/v1/admin-jobs/bootstrap-forecast-bundle`
- `POST /api/v1/admin-jobs/run-update-forecast-job`

## Implemented Migration Components

- SQLAlchemy table mappings for forecasts, agile data, price history, history, and forecast data.
- Repository implementations for read and write paths, including UnitOfWork composition.
- Idempotent write-side admin jobs for seeded forecast data generation.
- Update-forecast job now resolves day-ahead inputs via Nordpool ingestion first, with deterministic fallback when unavailable.
- Update-forecast job now runs concrete modular stages: ingest, quality alignment, feature generation, and inference preview before write-side persistence.
- Update-forecast job horizon is controlled by `AUTO_BOOTSTRAP_POINTS` (for example, 96 points -> 96 forecast rows and 192 agile rows across X/G).
- Latest update-job source marker is persisted in `/config/update-job-last-run.json` and surfaced in diagnostics.
- Diagnostics now surfaces persisted update marker fields: source, forecast name, records written, day-ahead points, updated timestamp, ingest error, raw points, aligned points, interpolated points, and retry count.
- Domain bundle orchestration extraction in `backend/src/domain/bootstrap_bundle.py`.
- Modular ML utilities:
  - region transforms (`day_ahead_to_agile`, `agile_to_day_ahead`)
  - deterministic time feature builder
  - first extracted Nordpool ingestion parser module
- Forecast list/price responses include `id` for follow-on data and stats calls.
- React/Vite frontend panels now call migration forecast and diagnostics APIs.
- Forecast dashboard supports interactive region/days/forecast-count controls.
- Forecast dashboard now renders latest forecast summary metrics, an SVG price curve, and an upcoming slots table.
- Diagnostics panel can trigger bundle seeding and update-forecast jobs, then refresh live status.
- Diagnostics panel includes a compact "Last Update Run" card with relative time (for example, `2m ago`) and full marker details.
- Relative update age in the card auto-refreshes on a lightweight UI timer (no additional backend polling required).
- Diagnostics seed controls are configurable in UI (points, regions, replace-existing, agile-data write toggle).
- Diagnostics panel shows latest parity report summary (pass/fail, failures, worst diffs, thresholds, and report timestamp).
- Diagnostics panel includes parity artifact traceability (report path and SHA-256 hash snippet).
- Diagnostics panel shows recent parity run history with status, failures, and hash snippet.
- Diagnostics panel supports parity history filters (status and time window hours).
- Diagnostics panel supports parity history paging (previous/next runs).

## Parity Harness

Run API parity checks when both legacy and migration stacks are running:

```bash
LEGACY_BASE=http://localhost:8000 \
MIGRATED_BASE=http://localhost:8010 \
./bin/parity_gate.sh
```

Parity gate behavior:

- Exits non-zero when thresholds are exceeded.
- Compares payload structure and prediction deltas.
- Compares forecast-data stats alignment using migrated forecast ids.
- Archives each run to `shared/parity/history/report-<timestamp>.json`.

Release parity signoff command:

```bash
LEGACY_BASE=http://legacy-host:8000 \
MIGRATED_BASE=http://migrated-host:8010 \
./bin/parity_signoff.sh
```

Example bootstrap write call:

```bash
curl -X POST http://localhost:8010/api/v1/admin-jobs/bootstrap-forecast \
  -H "Content-Type: application/json" \
  -d '{"points": 48, "regions": ["X", "G"], "base_price": 22.0, "spread": 1.5, "idempotency_key": "seed-run-1", "replace_existing": true}'
```

Example bundle write call:

```bash
curl -X POST http://localhost:8010/api/v1/admin-jobs/bootstrap-forecast-bundle \
  -H "Content-Type: application/json" \
  -d '{"points": 48, "regions": ["X", "G"], "idempotency_key": "bundle-run-1", "replace_existing": true, "write_agile_data": true}'
```

## CI Parity Gate

- Workflow: `.github/workflows/parity-gate.yml`
- PRs run parity smoke (`migrated vs migrated`) to validate contracts and parity harness wiring.
- Manual `workflow_dispatch` runs full parity gate against configurable legacy and migrated base URLs.
- Uploads report artifact from `shared/parity/last-report.json`.

## Parity Signoff and Release Rehearsal

- Use `./bin/parity_signoff.sh` for release-candidate parity approval; it fails non-zero when parity is not fully passing.
- Follow `docs/release-rehearsal.md` for the full pre-release checklist (tests, parity smoke, real parity signoff, diagnostics, artifacts).

## ML Parity Plan

- Legacy Django ML behavior is being ported with architecture parity in `docs/ml-parity-plan.md`.
- This plan captures the exact legacy `update.py` training behavior (features, XGBoost parameters, sample weighting, confidence interval generation, and regional output conversion) and maps it to the FastAPI pipeline.
