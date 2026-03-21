# Agile Predict v2.2.2

This model forecasts Octopus Agile electricity prices up to 14 days in advance using a Machine Learning model trained
on data from the Balancing Mechanism Reporting System (<a href="https://bmrs.elexon.co.uk/">BRMS</a>), National Grid
Electricity Supply Operator (<a href="https://www.nationalgrideso.com/data-portal">NG ESO</a>) and weather data from
<a href="https://open-meteo.com"> open-meteo.com</a>.<p>

---

## Developing for this project

This project runs as a FastAPI + React stack in a single container.

### Create a virtual environment

As with all python projects, it is recommended to create a virtual environment. For example, in this project, create a virtual environment using python's built in virtual environment tool `venv` to create an virtual environment in a folder `.venv`:

```
cd agile_predict
python3 -m venv .venv
```

Then, each time you are developing, activate the virtual environment according to the OS you are using.

Windows:

```
./.venv/Scripts/activate
```

### Installing dependencies

Requirements are listed in `requirements.txt`. You may install these however you like. The usual way is via python pip:

```
pip install -r requirements.txt
```

### Running the project

Use the local stack helper:

```bash
./bin/start_local_stack.sh
```

Or run the FastAPI app directly from `backend/` with your preferred tooling.

---

## Migration Scaffolding (Standard Stack)

Migration scaffolding has been added on the dedicated migration branch for:

- FastAPI backend under `backend/`
- React + Vite frontend under `frontend/`
- Single-container packaging under `deploy/` and `.github/workflows/`

Container publishing for the migration stack is handled through GitHub Container Registry (GHCR), not Fly.io or host-level shell deploys.

Current runtime contract for the new app container:

- Persistent configuration directory mounted at `/config`
- First run creates `/config/.env` from `deploy/docker/default.env`
- Embedded Postgres data persists under `/config/postgresql`
- FastAPI serves both the API and the built React frontend

GHCR image publishing:

- Workflow: `.github/workflows/ghcr.yml`
- Build source: `deploy/docker/backend.Dockerfile`
- Registry image: `ghcr.io/<repository-owner>/agile-predict`
- Push behavior:
	- every branch push updates `:dev`
	- every branch push also publishes `:sha-<short-sha>`
	- release tags like `v2.3.0` also publish versioned tags and `major.minor`

Deployment expectation:

- production or staging should pull and run the published GHCR image
- repo helper scripts under `bin/` are local or operational helpers, not the deployment mechanism
- if you are rolling out a UI or API change, the change is live only after the target runtime pulls the new GHCR image

Preferred local stack startup:

```bash
./bin/start_local_stack.sh
```

This starts the single app container, initializes embedded Postgres, and auto-seeds the migration database on first run.

Containerized backend tests:

```bash
./bin/test_backend.sh
```

This is the default migration-stack test path and avoids host Python environment drift.

Containerized parity gate:

```bash
LEGACY_BASE=http://localhost:8000 MIGRATED_BASE=http://localhost:8010 ./bin/parity_gate.sh
```

Each run updates `shared/parity/last-report.json` and writes a timestamped archive in `shared/parity/history/`.

Parity history can be queried from the migration API with optional filters, for example:

```bash
curl "http://localhost:8000/api/v1/diagnostics/parity-history?limit=5&status=fail"
```

Pagination is supported with `offset`, for example:

```bash
curl "http://localhost:8000/api/v1/diagnostics/parity-history?limit=5&offset=5"
```

See `docs/implementation-roadmap.md` for startup examples.
