# Release Rehearsal Checklist

Run this checklist before cutting a migration release candidate.

1. Backend regression tests

```bash
./bin/test_backend.sh
```

Expected result: all backend tests pass in the containerized test environment.

2. Local stack smoke

```bash
./bin/start_local_stack.sh
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/api/v1/health
./bin/stop_local_stack.sh
```

Expected result: health endpoints return HTTP 200 and stack starts/stops cleanly.

3. Parity smoke (migrated vs migrated)

```bash
./bin/start_local_stack.sh
LEGACY_BASE=http://localhost:8000 \
MIGRATED_BASE=http://localhost:8000 \
./bin/parity_gate.sh
./bin/stop_local_stack.sh
```

Expected result: parity report is generated and all checks pass with zero-diff thresholds.

4. Real parity signoff (legacy vs migrated)

```bash
LEGACY_BASE=http://legacy-host:8000 \
MIGRATED_BASE=http://migrated-host:8010 \
./bin/parity_signoff.sh
```

Expected result: `parity_all_passed=True` and non-zero exit is not returned.

5. Diagnostics check

```bash
curl -fsS http://localhost:8000/api/v1/diagnostics/latest-summary
```

Expected result: payload includes update marker fields (`update_source`, `update_retries_used`, `update_ingest_error`, `update_source_updated_at`).

6. Artifact verification

- Confirm latest parity artifact exists at `shared/parity/last-report.json`.
- Confirm archive entry exists under `shared/parity/history/`.
- Save CI artifacts from `backend-tests` and `parity-gate` workflows for release notes.
- Confirm the GHCR workflow published the expected image tags for the release candidate (`dev`, `sha-<short-sha>`, and release tags when applicable).
