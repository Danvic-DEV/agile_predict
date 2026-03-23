# Agile Predict

Agile Predict forecasts Octopus Agile electricity prices using a pipeline that combines market, grid, and weather data.

## Usage

### Public Web UI

The public web interface is available at **[agilepredict.danvic.dev](https://agilepredict.danvic.dev/)** and provides:

- Up to 13-day electricity price forecasts for all UK regions
- Interactive charts comparing predictions with actual prices
- Real-time cache status and model training data indicators
- Read-only REST API for programmatic access

### Home Assistant Integration

Integrate Agile Predict forecasts directly into Home Assistant for smart home automation:

- **REST sensor** for automatic price updates every 30 minutes
- **ApexCharts visualization** comparing predicted vs actual Octopus Agile prices
- **Automation examples** for cheap period notifications and smart charging

See the **[Home Assistant integration guide](home_assistant/README.md)** for complete setup instructions, automation examples, and configuration options.

### API Access

The public API is documented at [agilepredict.danvic.dev](https://agilepredict.danvic.dev/) and provides cache-served endpoints:

- `GET /api/v1/forecasts/availability` - Cache metadata and warmed regions
- `GET /api/v1/forecasts/regions` - Available region codes
- `GET /api/v1/forecasts/prices` - Price predictions with min/max ranges

All endpoints are read-only and require no authentication.

## Source lineage

This repository is derived from the original source project:

- https://github.com/fboundy/agile_predict

That upstream repository should be referenced when tracing the original implementation history and project roots.

---

## Current architecture

The project currently supports two containerized runtime surfaces:

1. Internal application container

- Image: `ghcr.io/<repository-owner>/agile-predict`
- Role: full FastAPI application + embedded Postgres runtime
- Port: `8000`
- Includes internal/admin/diagnostics routes
- Requires persistent `/config` mount

2. Public UI container

- Image: `ghcr.io/<repository-owner>/agile-predict-public-ui`
- Role: customer-facing public app with its own webpage and cached APIs
- Port: `8001`
- Stateless (no persistent volumes)
- No database connection
- Cache warm on startup (fails closed if warm fails)
- Hourly in-memory refresh from internal app APIs
- Serves responses from its own cache, not pass-through proxy
- Requires `UPSTREAM_BASE_URL` environment variable at startup (for example `http://agile-predict-main:8000`)

---

## GHCR publishing

Workflow:

- `.github/workflows/ghcr.yml`

Build sources:

- `deploy/docker/backend.Dockerfile`
- `deploy/docker/public-ui.Dockerfile`

Published images:

- `ghcr.io/<repository-owner>/agile-predict`
- `ghcr.io/<repository-owner>/agile-predict-public-ui`

Tag behavior for both images:

- every push: `sha-<short-sha>`
- push to `main`: `latest`
- release tags like `v2.3.0`: `v2.3.0` and `2.3` (and `latest`)

---

## Deployment runbook (both images)

Use immutable `sha-...` tags for production deployments when possible.

### 1) Deploy internal app container

Requirements:

- keep `8000` private/internal where possible
- mount persistent config directory to `/config`

Example:

```bash
docker network create agile-net

docker run -d \
  --name agile-predict-main \
  --network agile-net \
  -p 8000:8000 \
  -v /path/to/runtime-config:/config \
  ghcr.io/<repository-owner>/agile-predict:sha-<short-sha>
```

Health check:

```bash
curl http://<internal-host>:8000/api/v1/health
```

### 2) Deploy public UI container

Requirements:

- expose `8001` publicly
- no persistent volume required
- provide `UPSTREAM_BASE_URL` pointing to the internal app
- if `UPSTREAM_BASE_URL` uses container DNS (for example `agile-predict-main`), both containers must share a user-defined Docker network

Optional public app env vars:

- `CACHE_REFRESH_SECONDS` (default `3600`)
- `CACHE_REQUEST_TIMEOUT_SECONDS` (default `20`)
- `PUBLIC_RATE_LIMIT_PER_MINUTE` (default `120`)
- `PUBLIC_BASE_URL` (default `http://localhost:8001`) used in the public page's API examples

Example:

```bash
docker run -d \
  --name agile-predict-public-ui \
  --network agile-net \
  -p 8001:8001 \
  -e UPSTREAM_BASE_URL=http://agile-predict-main:8000 \
  -e PUBLIC_BASE_URL=https://public.example.com \
  -e CACHE_REFRESH_SECONDS=3600 \
  -e PUBLIC_RATE_LIMIT_PER_MINUTE=120 \
  ghcr.io/<repository-owner>/agile-predict-public-ui:sha-<short-sha>
```

Alternative when targeting host-published internal app:

```bash
docker run -d \
  --name agile-predict-public-ui \
  -p 8001:8001 \
  -e UPSTREAM_BASE_URL=http://host.docker.internal:8000 \
  -e PUBLIC_BASE_URL=https://public.example.com \
  ghcr.io/<repository-owner>/agile-predict-public-ui:sha-<short-sha>
```

Health check:

```bash
curl http://<public-host>:8001/healthz
```

### 3) Rollout order

1. Pull both target images first.
2. Update `agile-predict` and verify `8000` health.
3. Update `agile-predict-public-ui` and verify `8001` health.
4. Keep previous image tags available for rollback.

---

## Security baseline

- Do not expose internal diagnostics/admin APIs publicly.
- Prefer private networking for `8000` and public exposure only for `8001`.
- Use least-privilege registry credentials (`read:packages`) on deployment hosts.

---

## Local development

### Python environment

```bash
cd agile_predict
python3 -m venv .venv
```

Windows activation:

```bash
./.venv/Scripts/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

### Start local stack

```bash
./bin/start_local_stack.sh
```

This starts the internal app container, initializes embedded Postgres, and auto-seeds on first run.

### Build public UI image locally

```bash
docker build -f deploy/docker/public-ui.Dockerfile -t agile-predict-public-ui:local .
```

### Backend tests

```bash
./bin/test_backend.sh
```

---

## Parity tooling

Run parity gate:

```bash
LEGACY_BASE=http://localhost:8000 MIGRATED_BASE=http://localhost:8010 ./bin/parity_gate.sh
```

Parity report outputs:

- latest: `shared/parity/last-report.json`
- history: `shared/parity/history/`

Parity history API examples:

```bash
curl "http://localhost:8000/api/v1/diagnostics/parity-history?limit=5&status=fail"
curl "http://localhost:8000/api/v1/diagnostics/parity-history?limit=5&offset=5"
```

Additional implementation context:

- `docs/implementation-roadmap.md`
