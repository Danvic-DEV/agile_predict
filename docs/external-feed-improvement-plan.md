# External Feed Improvement Plan (Deferred)

## Purpose
This document defines a future plan for expanding external data feeds to improve 14-day price prediction accuracy.

Status: deferred. No implementation work starts until the current setup is stable and verified.

## Scope Guardrail
- Do not implement now.
- Use this as the reference plan for the next optimization phase.

## Phased Plan
1. Phase 1: Scope and data contract lock
- Confirm feed list, units, timezone policy, and forecast-time availability constraints.

2. Phase 2: Source qualification
- Validate endpoint reliability, historical depth, licensing/auth, and revision behavior.

3. Phase 3: Schema and feature design
- Define canonical columns, nullability, and derived feature logic for each feed.

4. Phase 4: Ingestion rollout in waves
- Wave A (highest expected ROI): carbon intensity, fuel mix, interconnector link flows, gas benchmark.
- Wave B: market-index context, demand revision deltas.
- Wave C: pumped/non-BM storage and optional continental coupling.

5. Phase 5: ML integration and ablation
- Enable feed groups behind feature flags.
- Measure incremental impact on MAE/P95 by horizon bucket: day 1-2, day 3-7, day 8-14.

6. Phase 6: Shadow validation and promotion gates
- Run rolling validation windows (for example: 30, 60, 90 updates).
- Promote only feed groups that improve accuracy and remain stable.

7. Phase 7: Diagnostics/operator clarity
- Show per-feed proof in diagnostics: last seen, rows in 24h, total rows, freshness, and error reason.
- Keep stage-level next-action guidance for blocked readiness states.

8. Phase 8: Controlled cutover
- Activate winning feed set with rollback flags.
- Revalidate periodically for drift, seasonality, and feed reliability.

## Candidate Feeds and Intended Value
1. Carbon intensity
- Regime indicator for scarcity and fossil-heavy periods.
- Expected value: moderate.

2. Full fuel mix (CCGT/OCGT/nuclear/wind/solar/hydro/biomass)
- Direct marginal-cost context and system-state signal.
- Expected value: high.

3. Interconnector link-level flows (IFA/IFA2/ElecLink/Nemo/BritNed/Moyle/EWIC/NSL/Viking/Greenlink)
- Coupling and import/export stress signal.
- Expected value: moderate to high.

4. Gas benchmark (TTF and/or UK NBP/NTS)
- Core marginal-cost proxy, especially during low-renewable periods.
- Expected value: high.

5. Elexon market-index context price
- Context/residual correction signal with strict anti-leakage timing controls.
- Expected value: moderate.

6. Demand forecast revision deltas
- Uncertainty/tightness signal from forecast changes.
- Expected value: low to moderate.

7. Pumped/non-BM storage
- Flexibility buffer and scarcity-response context.
- Expected value: low to moderate.

8. Continental coupling prices (optional)
- Cross-market spread/coupling feature where interconnector exposure is strong.
- Expected value: moderate.

## Gas Feed Recommendation
Include gas in Wave A once implementation begins.

Rationale:
- UK pricing is frequently gas-marginal when wind/solar output is weak.
- Gas-price plus fuel-mix interactions are likely to add substantial predictive signal.

Conditions before use:
- Strict publication-time alignment.
- Unit normalization and conversion consistency (for example EUR/MWh, p/therm, FX handling).

## Reuse Points in Current Codebase
- backend/src/jobs/pipelines/update_forecast.py
- backend/src/ml/ingest/grid_weather.py
- backend/src/ml/ingest/nordpool.py
- backend/src/ml/parity/day_ahead_xgb.py
- backend/src/repositories/sql_models.py
- backend/src/api/v1/routes/diagnostics.py
- backend/src/schemas/diagnostics.py
- frontend/src/features/diagnostics/DiagnosticsPanel.tsx
- frontend/src/features/diagnostics/api.ts
- frontend/src/lib/api/types.ts

## Promotion Criteria
1. Source integrity checks
- Endpoint auth/schema/timestamp handling and retry behavior are reliable.

2. Data quality checks
- Freshness SLA, null rates, duplicate rates, and revision behavior are acceptable.

3. Leakage checks
- No feature value uses information unavailable at prediction timestamp.

4. Model impact checks
- Ablation proves measurable MAE/P95 improvement per horizon bucket.

5. Stability checks
- Shadow runs show no unacceptable drift or outlier behavior.

6. Operator clarity checks
- Diagnostics unambiguously report stage status and source health.

## Decision Record
- Plan approved as deferred roadmap.
- Implementation starts only after current pipeline operation is stable and trusted.
