from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response


def _env_str(name: str, default: str) -> str:
    import os

    return str(os.getenv(name, default)).strip()


def _env_int(name: str, default: int) -> int:
    import os

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer for {name}: {raw}") from exc
    return value


def _describe_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


UPSTREAM_BASE_URL = _env_str("UPSTREAM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
PUBLIC_BASE_URL = _env_str("PUBLIC_BASE_URL", "http://localhost:8001").rstrip("/")
CACHE_REFRESH_SECONDS = _env_int("CACHE_REFRESH_SECONDS", 3600)
CACHE_REQUEST_TIMEOUT_SECONDS = _env_int("CACHE_REQUEST_TIMEOUT_SECONDS", 20)
PUBLIC_RATE_LIMIT_PER_MINUTE = _env_int("PUBLIC_RATE_LIMIT_PER_MINUTE", 120)
PUBLIC_CACHE_WARM_CONCURRENCY = _env_int("PUBLIC_CACHE_WARM_CONCURRENCY", 6)

# Public cache supports a constrained set of customer-safe forecast query profiles.
DEFAULT_DAYS = 7
MIN_DAYS = 1
MAX_DAYS = 14
DEFAULT_FORECAST_COUNT = 1
DEFAULT_HIGH_LOW = True


@dataclass
class CacheSnapshot:
    forecasts: list[dict[str, Any]] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    prices_by_region_and_days: dict[str, dict[int, list[dict[str, Any]]]] = field(default_factory=dict)
    training_days: int | None = None
    refreshed_at: str | None = None
    last_error: str | None = None


class PublicCache:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._snapshot = CacheSnapshot()

    async def set_snapshot(
        self,
        *,
        forecasts: list[dict[str, Any]],
        regions: list[str],
        prices_by_region_and_days: dict[str, dict[int, list[dict[str, Any]]]],
        training_days: int | None = None,
    ) -> None:
        async with self._lock:
            self._snapshot = CacheSnapshot(
                forecasts=forecasts,
                regions=regions,
                prices_by_region_and_days=prices_by_region_and_days,
                training_days=training_days,
                refreshed_at=datetime.now(timezone.utc).isoformat(),
                last_error=None,
            )

    async def set_error(self, error_message: str) -> None:
        async with self._lock:
            self._snapshot.last_error = error_message

    async def upsert_prices_variant(self, *, region: str, days: int, prices_payload: list[dict[str, Any]]) -> None:
        async with self._lock:
            existing = self._snapshot.prices_by_region_and_days.get(region, {})
            updated = {cached_days: list(payload) for cached_days, payload in existing.items()}
            updated[days] = list(prices_payload)
            self._snapshot.prices_by_region_and_days[region] = updated
            self._snapshot.refreshed_at = datetime.now(timezone.utc).isoformat()
            self._snapshot.last_error = None

    async def get_snapshot(self) -> CacheSnapshot:
        async with self._lock:
            # Return detached copy to avoid mutation by callers.
            return CacheSnapshot(
                forecasts=list(self._snapshot.forecasts),
                regions=list(self._snapshot.regions),
                prices_by_region_and_days={
                    region: {days: list(payload) for days, payload in by_days.items()}
                    for region, by_days in self._snapshot.prices_by_region_and_days.items()
                },
                refreshed_at=self._snapshot.refreshed_at,
                last_error=self._snapshot.last_error,
            )


class RateLimiter:
    def __init__(self, limit_per_minute: int) -> None:
        self.limit_per_minute = max(1, limit_per_minute)
        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        async with self._lock:
            window = self._hits.setdefault(key, deque())
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self.limit_per_minute:
                return False
            window.append(now)
            return True


cache = PublicCache()
limiter = RateLimiter(PUBLIC_RATE_LIMIT_PER_MINUTE)
app = FastAPI(title="agile-predict-public-ui", version="1.0.0")
PUBLIC_UI_LOGO_PATH = Path("/app/assets/public-ui-logo-128.png")

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Agile Predict favicon">
    <defs>
        <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#18323b" />
            <stop offset="100%" stop-color="#0a1016" />
        </linearGradient>
        <linearGradient id="line" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stop-color="#67d2ff" />
            <stop offset="100%" stop-color="#f2b84b" />
        </linearGradient>
    </defs>
    <rect x="4" y="4" width="56" height="56" rx="16" fill="url(#bg)" />
    <path d="M16 40 L26 30 L34 35 L47 20" fill="none" stroke="url(#line)" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" />
    <circle cx="47" cy="20" r="4" fill="#f2b84b" />
    <path d="M16 48 H48" stroke="rgba(201,214,220,0.35)" stroke-width="3" stroke-linecap="round" />
</svg>"""


def _upstream_url(path: str) -> str:
    if not path.startswith("/"):
        raise RuntimeError("Upstream path must start with '/'.")
    return f"{UPSTREAM_BASE_URL}{path}"


async def _fetch_json(client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> Any:
    response = await client.get(_upstream_url(path), params=params)
    if response.status_code >= 400:
        raise RuntimeError(f"Upstream request failed for {path}: status={response.status_code}")
    return response.json()


async def _fetch_prices_variant(client: httpx.AsyncClient, *, region: str, days: int) -> list[dict[str, Any]]:
    prices_payload = await _fetch_json(
        client,
        "/api/v1/forecasts/prices",
        {
            "region": region,
            "days": days,
            "forecast_count": DEFAULT_FORECAST_COUNT,
            "high_low": str(DEFAULT_HIGH_LOW).lower(),
        },
    )
    if not isinstance(prices_payload, list):
        raise RuntimeError(f"Upstream prices payload is not a list for region={region}, days={days}.")
    return prices_payload


async def _build_snapshot(
    days_by_region: dict[str, set[int]] | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, dict[int, list[dict[str, Any]]]], int | None]:
    timeout = httpx.Timeout(CACHE_REQUEST_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout) as client:
        forecasts_payload = await _fetch_json(client, "/api/v1/forecasts", {"limit": 5})
        regions_payload = await _fetch_json(client, "/api/v1/forecasts/regions")

        training_days: int | None = None
        try:
            diag_payload = await _fetch_json(client, "/api/v1/diagnostics/latest-summary")
            if isinstance(diag_payload, dict) and diag_payload.get("update_ml_training_rows"):
                training_days = round(diag_payload["update_ml_training_rows"] / 48)
        except Exception:  # noqa: S110
            pass

        if not isinstance(forecasts_payload, list):
            raise RuntimeError("Upstream forecasts payload is not a list.")
        if not isinstance(regions_payload, list):
            raise RuntimeError("Upstream regions payload is not a list.")

        regions: list[str] = [str(region) for region in regions_payload]
        prices_by_region_and_days: dict[str, dict[int, list[dict[str, Any]]]] = {}
        warm_semaphore = asyncio.Semaphore(max(1, PUBLIC_CACHE_WARM_CONCURRENCY))

        requested_days_by_region: dict[str, set[int]] = {}
        for region in regions:
            requested_days = set(days_by_region.get(region, {DEFAULT_DAYS}) if days_by_region else {DEFAULT_DAYS})
            bounded_days = {days for days in requested_days if MIN_DAYS <= days <= MAX_DAYS}
            requested_days_by_region[region] = bounded_days or {DEFAULT_DAYS}

        async def fetch_prices_for(region: str, days: int) -> tuple[str, int, list[dict[str, Any]]]:
            async with warm_semaphore:
                prices_payload = await _fetch_prices_variant(client, region=region, days=days)
            return region, days, prices_payload

        fetch_tasks = [
            fetch_prices_for(region, days)
            for region in regions
            for days in sorted(requested_days_by_region[region])
        ]
        fetched_prices = await asyncio.gather(*fetch_tasks)

        for region, days, prices_payload in fetched_prices:
            by_days = prices_by_region_and_days.setdefault(region, {})
            by_days[days] = prices_payload

    return forecasts_payload, regions, prices_by_region_and_days, training_days


async def refresh_cache_once(days_by_region: dict[str, set[int]] | None = None) -> None:
    forecasts, regions, prices_by_region_and_days, training_days = await _build_snapshot(days_by_region)
    await cache.set_snapshot(
        forecasts=forecasts,
        regions=regions,
        prices_by_region_and_days=prices_by_region_and_days,
        training_days=training_days,
    )


async def _warm_additional_variants_once() -> None:
    snapshot = await cache.get_snapshot()
    if not snapshot.regions:
        return

    timeout = httpx.Timeout(CACHE_REQUEST_TIMEOUT_SECONDS)
    warm_semaphore = asyncio.Semaphore(max(1, PUBLIC_CACHE_WARM_CONCURRENCY))

    async with httpx.AsyncClient(timeout=timeout) as client:
        async def warm_variant(region: str, days: int) -> None:
            async with warm_semaphore:
                prices_payload = await _fetch_prices_variant(client, region=region, days=days)
            await cache.upsert_prices_variant(region=region, days=days, prices_payload=prices_payload)

        warm_tasks = []
        for region in snapshot.regions:
            cached_days = set(snapshot.prices_by_region_and_days.get(region, {}).keys())
            for days in range(MIN_DAYS, MAX_DAYS + 1):
                if days in cached_days:
                    continue
                warm_tasks.append(warm_variant(region, days))

        if not warm_tasks:
            return

        results = await asyncio.gather(*warm_tasks, return_exceptions=True)
        failures = [result for result in results if isinstance(result, Exception)]
        if failures:
            raise RuntimeError(_describe_exception(failures[0]))


async def _variant_warm_loop() -> None:
    while True:
        try:
            await _warm_additional_variants_once()
        except Exception as exc:  # noqa: BLE001
            await cache.set_error(_describe_exception(exc))
        await asyncio.sleep(max(30, CACHE_REFRESH_SECONDS))


async def _refresh_loop() -> None:
    while True:
        await asyncio.sleep(max(30, CACHE_REFRESH_SECONDS))
        try:
            snapshot = await cache.get_snapshot()
            days_by_region = {
                region: set(by_days.keys()) or {DEFAULT_DAYS}
                for region, by_days in snapshot.prices_by_region_and_days.items()
            }
            await refresh_cache_once(days_by_region or None)
        except Exception as exc:  # noqa: BLE001
            await cache.set_error(_describe_exception(exc))


@app.middleware("http")
async def public_api_rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        client_ip = request.client.host if request.client else "unknown"
        allowed = await limiter.allow(client_ip)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": "60"},
            )
    return await call_next(request)


@app.on_event("startup")
async def startup_event() -> None:
    # Fail closed: service does not start until cache warm succeeds.
    try:
        await refresh_cache_once()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Public cache warm failed: {_describe_exception(exc)}") from exc

    app.state.refresh_task = asyncio.create_task(_refresh_loop())
    app.state.variant_warm_task = asyncio.create_task(_variant_warm_loop())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    tasks = [
        getattr(app.state, "refresh_task", None),
        getattr(app.state, "variant_warm_task", None),
    ]
    for task in tasks:
        if task is not None:
            task.cancel()
    for task in tasks:
        if task is None:
            continue
        try:
            await task
        except asyncio.CancelledError:
            continue


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    snapshot = await cache.get_snapshot()
    if not snapshot.refreshed_at:
        raise HTTPException(status_code=503, detail="cache not warmed")
    return {
        "status": "ok",
        "refreshed_at": snapshot.refreshed_at,
        "upstream": UPSTREAM_BASE_URL,
    }


@app.get("/favicon.svg")
async def favicon() -> Response:
    return Response(content=FAVICON_SVG, media_type="image/svg+xml")


@app.get("/public-ui-logo-128.png")
async def public_ui_logo() -> Response:
    if not PUBLIC_UI_LOGO_PATH.exists():
        raise HTTPException(status_code=404, detail="public UI logo asset missing")
    return Response(content=PUBLIC_UI_LOGO_PATH.read_bytes(), media_type="image/png")


@app.get("/api/v1/forecasts")
async def public_forecasts(limit: int = 5):
    snapshot = await cache.get_snapshot()
    if not snapshot.refreshed_at:
        raise HTTPException(status_code=503, detail="cache unavailable")
    bounded = max(1, min(limit, len(snapshot.forecasts)))
    return snapshot.forecasts[:bounded]


@app.get("/api/v1/forecasts/regions")
async def public_regions():
    snapshot = await cache.get_snapshot()
    if not snapshot.refreshed_at:
        raise HTTPException(status_code=503, detail="cache unavailable")
    return snapshot.regions


@app.get("/api/v1/forecasts/availability")
async def public_forecast_availability():
    snapshot = await cache.get_snapshot()
    if not snapshot.refreshed_at:
        raise HTTPException(status_code=503, detail="cache unavailable")
    return {
        "regions": snapshot.regions,
        "days_by_region": {
            region: sorted(by_days.keys())
            for region, by_days in snapshot.prices_by_region_and_days.items()
        },
        "refreshed_at": snapshot.refreshed_at,
        "default_days": DEFAULT_DAYS,
        "min_days": MIN_DAYS,
        "max_days": MAX_DAYS,
    }


@app.get("/api/v1/forecasts/prices")
async def public_prices(region: str = "G", days: int = DEFAULT_DAYS, forecast_count: int = DEFAULT_FORECAST_COUNT, high_low: bool = DEFAULT_HIGH_LOW):
    if days < MIN_DAYS or days > MAX_DAYS or forecast_count != DEFAULT_FORECAST_COUNT or high_low is not DEFAULT_HIGH_LOW:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported query profile. "
                f"Use days={MIN_DAYS}-{MAX_DAYS}, forecast_count={DEFAULT_FORECAST_COUNT}, high_low={str(DEFAULT_HIGH_LOW).lower()}"
            ),
        )

    snapshot = await cache.get_snapshot()
    if not snapshot.refreshed_at:
        raise HTTPException(status_code=503, detail="cache unavailable")

    if region not in snapshot.regions:
        raise HTTPException(status_code=404, detail=f"region not cached: {region}")

    selected_by_days = snapshot.prices_by_region_and_days.get(region)
    selected = None if selected_by_days is None else selected_by_days.get(days)
    if selected is None:
        raise HTTPException(
            status_code=503,
            detail=f"cached variant not ready yet for region={region}, days={days}",
        )
    return selected


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html = """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Agile Predict</title>
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\" />
    <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin />
    <link href=\"https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap\" rel=\"stylesheet\" />
  <style>
        :root {
            font-family: \"IBM Plex Sans\", \"Segoe UI\", sans-serif;
            color: #f4f1e8;
            background: radial-gradient(circle at 20% 20%, #25444d, #121b24 45%, #0a1016 100%);
        }
        * {
            box-sizing: border-box;
        }
        body {
            margin: 0;
            min-height: 100vh;
            color: #f4f1e8;
        }
        .page {
            max-width: 1100px;
            margin: 0 auto;
            padding: 32px 20px;
        }
        .hero h1 {
            margin: 0;
            font-size: clamp(2rem, 6vw, 4rem);
            letter-spacing: 0.04em;
        }
        .hero-brand {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .hero-brand img {
            width: 56px;
            height: 56px;
            border-radius: 14px;
            flex-shrink: 0;
        }
        .hero p {
            margin-top: 8px;
            color: #c9d6dc;
        }
        .card {
            margin-top: 24px;
            border: 1px solid rgba(201, 214, 220, 0.3);
            border-radius: 14px;
            padding: 20px;
            background: rgba(7, 16, 24, 0.5);
            backdrop-filter: blur(2px);
        }
        .controls-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 10px;
            margin-bottom: 14px;
        }
        .controls-row label {
            display: flex;
            flex-direction: column;
            gap: 6px;
            font-size: 0.85rem;
        }
        .controls-row input,
        .controls-row select,
        .controls-row button {
            border-radius: 8px;
            border: 1px solid rgba(201, 214, 220, 0.35);
            background: rgba(10, 20, 30, 0.65);
            color: #f4f1e8;
            padding: 8px 10px;
        }
        .controls-row button {
            cursor: pointer;
            align-self: end;
            font-weight: 600;
        }
        .controls-row button:hover {
            border-color: rgba(242, 184, 75, 0.55);
            color: #f2b84b;
        }
        .control-note {
            border-radius: 8px;
            border: 1px solid rgba(201, 214, 220, 0.35);
            background: rgba(10, 20, 30, 0.65);
            color: #f4f1e8;
            padding: 8px 10px;
            min-height: 38px;
            display: inline-flex;
            align-items: center;
        }
        .metric-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-bottom: 12px;
        }
        .metric-grid div {
            border: 1px solid rgba(201, 214, 220, 0.18);
            border-radius: 10px;
            padding: 10px;
            background: rgba(17, 26, 35, 0.45);
        }
        .label {
            display: block;
            font-size: 0.75rem;
            color: #9eb2be;
            margin-bottom: 6px;
        }
        .metric-value {
            font-size: 1.05rem;
            font-weight: 700;
        }
        .table-card {
            margin-top: 12px;
            border: 1px solid rgba(201, 214, 220, 0.18);
            border-radius: 10px;
            padding: 12px;
            background: rgba(17, 26, 35, 0.45);
        }
        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 12px;
            margin-bottom: 10px;
        }
        .chart-header h3 {
            margin: 0;
            font-size: 1rem;
            color: #f4f1e8;
        }
        .chart-header span {
            color: #9eb2be;
            font-size: 0.85rem;
        }
        .forecast-chart {
            width: 100%;
            height: auto;
            display: block;
            border-radius: 8px;
            background:
                linear-gradient(to top, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0)),
                rgba(10, 20, 30, 0.65);
        }
        .forecast-chart-line {
            fill: none;
            stroke: #f2b84b;
            stroke-width: 3;
            stroke-linecap: round;
            stroke-linejoin: round;
        }
        .forecast-chart-band {
            fill: rgba(242, 184, 75, 0.2);
            stroke: none;
        }
        .forecast-chart-gridline {
            stroke: rgba(201, 214, 220, 0.2);
            stroke-width: 1;
        }
        .forecast-chart-midnight {
            stroke: rgba(107, 210, 255, 0.5);
            stroke-width: 1;
            stroke-dasharray: 4 4;
        }
        .forecast-chart-axis-text,
        .forecast-chart-axis-title,
        .forecast-chart-midnight-label {
            fill: #9eb2be;
            font-size: 11px;
            letter-spacing: 0.01em;
        }
        .forecast-chart-axis-title {
            font-size: 10px;
            text-transform: uppercase;
        }
        .forecast-chart-midnight-label {
            fill: #c1d4df;
            font-size: 10px;
        }
        .chart-legend {
            margin-top: 8px;
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            font-size: 0.78rem;
            color: #b8c9d2;
        }
        .legend-item {
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .legend-swatch {
            width: 16px;
            height: 8px;
            border-radius: 2px;
            border: 1px solid rgba(201, 214, 220, 0.35);
        }
        .legend-swatch-line {
            height: 0;
            border: 0;
            border-top: 3px solid #f2b84b;
            width: 16px;
        }
        .legend-swatch-band {
            background: rgba(242, 184, 75, 0.2);
        }
        .legend-item-midnight {
            color: #9eb2be;
        }
        .day-tabs {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 10px;
        }
        .day-tab-button {
            border: 1px solid rgba(201, 214, 220, 0.3);
            background: rgba(10, 20, 30, 0.55);
            color: #c9d6dc;
            border-radius: 999px;
            padding: 6px 10px;
            font-size: 0.78rem;
            cursor: pointer;
        }
        .day-tab-button:hover {
            border-color: rgba(242, 184, 75, 0.5);
            color: #f4f1e8;
        }
        .day-tab-button.active {
            border-color: rgba(242, 184, 75, 0.65);
            color: #f2b84b;
            background: rgba(242, 184, 75, 0.14);
        }
        .slot-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }
        .slot-table th,
        .slot-table td {
            padding: 8px 6px;
            border-bottom: 1px solid rgba(201, 214, 220, 0.12);
            text-align: left;
        }
        .slot-table th {
            color: #9eb2be;
            font-weight: 600;
        }
        .slot-table tbody tr:last-child td {
            border-bottom: 0;
        }
        .value-pill {
            display: inline-block;
            min-width: 62px;
            text-align: center;
            border-radius: 999px;
            padding: 3px 9px;
            font-weight: 700;
            line-height: 1.2;
        }
        .value-pill-blue {
            color: #cde7ff;
            background: rgba(34, 111, 204, 0.35);
            border: 1px solid rgba(98, 164, 245, 0.55);
        }
        .value-pill-green {
            color: #d2f7de;
            background: rgba(30, 130, 72, 0.32);
            border: 1px solid rgba(97, 191, 133, 0.52);
        }
        .value-pill-orange {
            color: #ffe7cc;
            background: rgba(182, 104, 21, 0.34);
            border: 1px solid rgba(241, 157, 69, 0.56);
        }
        .value-pill-red {
            color: #ffd6d6;
            background: rgba(171, 43, 43, 0.35);
            border: 1px solid rgba(230, 108, 108, 0.6);
        }
        .value-pill-na {
            color: #d5e2ea;
            background: rgba(62, 78, 94, 0.38);
            border: 1px solid rgba(158, 178, 190, 0.4);
        }
        .delta-pill {
            display: inline-block;
            min-width: 62px;
            text-align: center;
            border-radius: 999px;
            padding: 3px 9px;
            font-weight: 700;
            line-height: 1.2;
        }
        .delta-pill-in {
            color: #d2f7de;
            background: rgba(30, 130, 72, 0.32);
            border: 1px solid rgba(97, 191, 133, 0.52);
        }
        .delta-pill-out {
            color: #ffd6d6;
            background: rgba(171, 43, 43, 0.35);
            border: 1px solid rgba(230, 108, 108, 0.6);
        }
        .delta-pill-na {
            color: #d5e2ea;
            background: rgba(62, 78, 94, 0.38);
            border: 1px solid rgba(158, 178, 190, 0.4);
        }
        .status {
            margin: 0 0 12px;
            color: #9eb2be;
            font-size: 0.82rem;
        }
        .err {
            color: #ffb0b0;
            font-size: 0.9rem;
            margin-top: 10px;
        }
        .api-docs-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px;
            margin-bottom: 12px;
        }
        .api-doc-card {
            border: 1px solid rgba(201, 214, 220, 0.18);
            border-radius: 10px;
            padding: 12px;
            background: rgba(12, 21, 30, 0.5);
        }
        .api-doc-card h4 {
            margin: 0 0 8px;
            font-size: 0.95rem;
            color: #f4f1e8;
        }
        .api-doc-card p {
            margin: 0 0 8px;
            color: #c9d6dc;
            font-size: 0.82rem;
            line-height: 1.45;
        }
        .api-doc-card code,
        .api-example code {
            display: block;
            font-family: Consolas, "Courier New", monospace;
            font-size: 0.76rem;
            color: #dff0ff;
            background: rgba(8, 14, 20, 0.78);
            border: 1px solid rgba(117, 151, 173, 0.25);
            border-radius: 8px;
            padding: 8px 10px;
            overflow-x: auto;
            white-space: nowrap;
        }
        .api-note {
            margin: 0 0 10px;
            color: #9eb2be;
            font-size: 0.8rem;
            line-height: 1.45;
        }
        .api-example {
            margin-top: 10px;
        }
        .api-list {
            margin: 8px 0 0;
            padding-left: 18px;
            color: #c9d6dc;
            font-size: 0.8rem;
            line-height: 1.45;
        }
        .api-list li + li {
            margin-top: 4px;
        }
        @media (max-width: 740px) {
            .page {
                padding: 20px 12px;
            }
            .hero-brand {
                gap: 10px;
            }
            .hero-brand img {
                width: 44px;
                height: 44px;
                border-radius: 12px;
            }
            .slot-table {
                font-size: 0.8rem;
            }
            .slot-table th,
            .slot-table td {
                padding: 7px 4px;
            }
            .hero h1 {
                font-size: clamp(1.7rem, 9vw, 2.6rem);
            }
        }
  </style>
</head>
<body>
    <main class=\"page\">
        <header class=\"hero\">
            <div class=\"hero-brand\">
                <img src=\"/public-ui-logo-128.png\" alt=\"Agile Predict public logo\" width=\"56\" height=\"56\" />
                <h1>Agile Predict</h1>
            </div>
            <p>Estimate upcoming Octopus Agile electricity prices so you can plan when to use power at lower-cost periods. These values are model predictions from recent data, not guaranteed tariff prices, and should be used as guidance only.</p>
        </header>
        <section class=\"card\">
            <div class=\"controls-row\">
                <label>
                    Region
                    <select id=\"region\"></select>
                </label>
                <label>
                    Day Window
                    <select id=\"days\"></select>
                </label>
                <label>
                    Data Age
                    <span id=\"cache_age\" class=\"control-note\">-</span>
                </label>
                <button id=\"refresh\" type=\"button\">Refresh View</button>
            </div>
            <p class=\"status\" id=\"status\">Loading cached forecast view...</p>
            <div class=\"metric-grid\" id=\"summary\">
                <div><span class=\"label\">Min Pred</span><span class=\"metric-value\" id=\"min_pred\">-</span></div>
                <div><span class=\"label\">Avg Pred</span><span class=\"metric-value\" id=\"avg_pred\">-</span></div>
                <div><span class=\"label\">Max Pred</span><span class=\"metric-value\" id=\"max_pred\">-</span></div>
                <div><span class=\"label\">Slots</span><span class=\"metric-value\" id=\"slot_count\">-</span></div>
            </div>
            <div class=\"table-card\">
                <div class=\"chart-header\">
                    <h3>Latest Agile Prediction Curve</h3>
                    <span id=\"chart_slot_count\">0 half-hour slots</span>
                </div>
                <svg
                    id=\"chart_svg\"
                    viewBox=\"0 0 720 240\"
                    class=\"forecast-chart\"
                    role=\"img\"
                    aria-label=\"Latest agile prediction with min-max range and midnight day markers\"
                ></svg>
                <div class=\"chart-legend\" aria-hidden=\"true\">
                    <span class=\"legend-item\">
                        <span class=\"legend-swatch legend-swatch-line\"></span>
                        Pred
                    </span>
                    <span class=\"legend-item\">
                        <span class=\"legend-swatch legend-swatch-band\"></span>
                        Min-Max
                    </span>
                    <span class=\"legend-item legend-item-midnight\">Midnight markers show day boundaries</span>
                </div>
            </div>
            <div class=\"table-card\">
                <div class=\"chart-header\">
                    <h3>Upcoming Slots</h3>
                    <span id=\"upcoming_slot_count\">0 slots</span>
                </div>
                <div class=\"day-tabs\" id=\"day_tabs\"></div>
                <table class=\"slot-table\">
                    <thead>
                        <tr>
                            <th>Slot (London)</th>
                            <th>Pred</th>
                            <th>Low</th>
                            <th>High</th>
                            <th>Actual</th>
                            <th>Delta</th>
                        </tr>
                    </thead>
                    <tbody id=\"rows\"></tbody>
                </table>
            </div>
            <div class=\"table-card\">
                <div class=\"chart-header\">
                    <h3>Public API</h3>
                    <span>Cache-served endpoints only</span>
                </div>
                <p class=\"api-note\">These endpoints are intended for public read access and are served from this service's in-memory cache. They expose forecast data and cache availability only, with no admin, control, or diagnostics actions.</p>
                <div class=\"api-docs-grid\">
                    <div class=\"api-doc-card\">
                        <h4>Availability</h4>
                        <p>Returns warmed day windows by region so clients can discover which cached variants are ready.</p>
                        <code>GET /api/v1/forecasts/availability</code>
                        <ul class="api-list">
                            <li>Includes regions, days_by_region, default_days, min_days, and max_days.</li>
                            <li>Use this first if you need to build a client against warmed ranges only.</li>
                        </ul>
                    </div>
                    <div class=\"api-doc-card\">
                        <h4>Regions</h4>
                        <p>Returns the set of public region codes currently exposed by the cache.</p>
                        <code>GET /api/v1/forecasts/regions</code>
                        <ul class="api-list">
                            <li>Response is a simple JSON array of region codes.</li>
                            <li>Use together with availability when building selectors.</li>
                        </ul>
                    </div>
                    <div class=\"api-doc-card\">
                        <h4>Forecast Prices</h4>
                        <p>Returns cached forecast data for a region and warmed day range. Use forecast_count=1 and high_low=true.</p>
                        <code>GET /api/v1/forecasts/prices?region=G&amp;days=7&amp;forecast_count=1&amp;high_low=true</code>
                        <ul class="api-list">
                            <li>Required query params: region and days.</li>
                            <li>Supported query profile: forecast_count=1 and high_low=true.</li>
                            <li>Response is a JSON array of forecast bundles with a prices array per bundle.</li>
                            <li>If a day range is not warmed yet, it is intentionally unavailable until cache warming completes.</li>
                        </ul>
                    </div>
                    <div class=\"api-doc-card\">
                        <h4>Health</h4>
                        <p>Returns cache warm status, last refresh time, and upstream target for operational checks.</p>
                        <code>GET /healthz</code>
                        <ul class="api-list">
                            <li>Useful for uptime probes and confirming the cache is populated.</li>
                            <li>Returns status, refreshed_at, and upstream.</li>
                        </ul>
                    </div>
                </div>
                <div class=\"api-example\">
                    <code>curl __PUBLIC_BASE_URL__/api/v1/forecasts/prices?region=G&amp;days=7&amp;forecast_count=1&amp;high_low=true</code>
                </div>
            </div>
            <div id=\"error\" class=\"err\"></div>
        </section>
    </main>
    <script>
        const CHART_WIDTH = 720;
        const CHART_HEIGHT = 240;
        const CHART_MARGIN = {
            top: 16,
            right: 18,
            bottom: 36,
            left: 62,
        };

        function toNumber(value) {
            if (value == null) {
                return null;
            }
            const n = Number(value);
            return Number.isFinite(n) ? n : null;
        }

        function toLondonTimeParts(dateTime) {
            const parts = new Intl.DateTimeFormat('en-GB', {
                day: '2-digit',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
                timeZone: 'Europe/London',
            }).formatToParts(new Date(dateTime));
            return parts.reduce((acc, part) => {
                if (part.type !== 'literal') {
                    acc[part.type] = part.value;
                }
                return acc;
            }, {});
        }

        function isLondonMidnight(dateTime) {
            const parts = toLondonTimeParts(dateTime);
            return parts.hour === '00' && parts.minute === '00';
        }

        function buildChartModel(points) {
            if (!points.length) {
                return {
                    predPath: '',
                    bandPath: '',
                    yTicks: [],
                    midnightMarkers: [],
                };
            }

            const lows = points.map((point) => {
                const low = toNumber(point.agile_low);
                const pred = toNumber(point.agile_pred);
                return low == null ? (pred == null ? 0 : pred) : low;
            });
            const highs = points.map((point) => {
                const high = toNumber(point.agile_high);
                const pred = toNumber(point.agile_pred);
                return high == null ? (pred == null ? 0 : pred) : high;
            });
            const preds = points.map((point) => {
                const pred = toNumber(point.agile_pred);
                return pred == null ? 0 : pred;
            });
            const values = lows.concat(highs).concat(preds);
            const minValue = Math.min(...values);
            const maxValue = Math.max(...values);
            const range = Math.max(maxValue - minValue, 1);
            const paddedMin = minValue - range * 0.08;
            const paddedMax = maxValue + range * 0.08;
            const tickStep = 5;
            const tickStart = Math.floor(paddedMin / tickStep) * tickStep;
            const tickEnd = Math.ceil(paddedMax / tickStep) * tickStep;
            const axisMin = tickStart;
            const axisMax = tickEnd > tickStart ? tickEnd : tickStart + tickStep;
            const axisRange = Math.max(axisMax - axisMin, 0.0001);
            const width = CHART_WIDTH - CHART_MARGIN.left - CHART_MARGIN.right;
            const height = CHART_HEIGHT - CHART_MARGIN.top - CHART_MARGIN.bottom;

            const scaleX = (index) => CHART_MARGIN.left + (index / Math.max(points.length - 1, 1)) * width;
            const scaleY = (value) => CHART_MARGIN.top + ((axisMax - value) / axisRange) * height;

            const predPath = points
                .map((point, index) => {
                    const pred = toNumber(point.agile_pred);
                    const yValue = pred == null ? 0 : pred;
                    const x = scaleX(index);
                    const y = scaleY(yValue);
                    return (index === 0 ? 'M' : 'L') + x.toFixed(2) + ',' + y.toFixed(2);
                })
                .join(' ');

            const highPath = points
                .map((point, index) => {
                    const high = toNumber(point.agile_high);
                    const pred = toNumber(point.agile_pred);
                    const yValue = high == null ? (pred == null ? 0 : pred) : high;
                    return 'L' + scaleX(index).toFixed(2) + ',' + scaleY(yValue).toFixed(2);
                })
                .join(' ');

            const lowPath = points
                .slice()
                .reverse()
                .map((point, reverseIndex) => {
                    const low = toNumber(point.agile_low);
                    const pred = toNumber(point.agile_pred);
                    const yValue = low == null ? (pred == null ? 0 : pred) : low;
                    return 'L' + scaleX(points.length - reverseIndex - 1).toFixed(2) + ',' + scaleY(yValue).toFixed(2);
                })
                .join(' ');

            const firstHigh = toNumber(points[0].agile_high);
            const firstPred = toNumber(points[0].agile_pred);
            const bandStart = firstHigh == null ? (firstPred == null ? 0 : firstPred) : firstHigh;
            const bandPath =
                'M' +
                scaleX(0).toFixed(2) +
                ',' +
                scaleY(bandStart).toFixed(2) +
                ' ' +
                highPath +
                ' ' +
                lowPath +
                ' Z';

            const yTicks = [];
            for (let value = tickEnd; value >= tickStart; value -= tickStep) {
                yTicks.push({ value, y: scaleY(value) });
            }

            const midnightMarkers = points
                .map((point, index) => ({ point, index }))
                .filter(({ point }) => isLondonMidnight(point.date_time))
                .map(({ point, index }) => {
                    const parts = toLondonTimeParts(point.date_time);
                    return {
                        x: scaleX(index),
                        label: (parts.day || '') + ' ' + (parts.month || ''),
                    };
                });

            return {
                predPath,
                bandPath,
                yTicks,
                midnightMarkers,
            };
        }

        function renderChart(points) {
            const chartSvg = document.getElementById('chart_svg');
            const chartSlotCount = document.getElementById('chart_slot_count');
            chartSlotCount.textContent = String(points.length) + ' half-hour slots';

            if (!points.length) {
                chartSvg.innerHTML = '';
                return;
            }

            const chart = buildChartModel(points);
            const parts = [];

            chart.yTicks.forEach((tick) => {
                parts.push(
                    '<line x1="' + CHART_MARGIN.left + '" y1="' + tick.y + '" x2="' + (CHART_WIDTH - CHART_MARGIN.right) + '" y2="' + tick.y + '" class="forecast-chart-gridline"></line>'
                );
                parts.push(
                    '<text x="' + (CHART_MARGIN.left - 8) + '" y="' + (tick.y + 4) + '" text-anchor="end" class="forecast-chart-axis-text">' + tick.value.toFixed(0) + '</text>'
                );
            });

            chart.midnightMarkers.forEach((marker) => {
                parts.push(
                    '<line x1="' + marker.x + '" y1="' + CHART_MARGIN.top + '" x2="' + marker.x + '" y2="' + (CHART_HEIGHT - CHART_MARGIN.bottom) + '" class="forecast-chart-midnight"></line>'
                );
                parts.push(
                    '<text x="' + marker.x + '" y="' + (CHART_HEIGHT - 10) + '" text-anchor="middle" class="forecast-chart-midnight-label">' + marker.label + '</text>'
                );
            });

            parts.push('<path d="' + chart.bandPath + '" class="forecast-chart-band"></path>');
            parts.push('<path d="' + chart.predPath + '" class="forecast-chart-line"></path>');
            parts.push(
                '<text x="' + (CHART_MARGIN.left - 8) + '" y="' + (CHART_MARGIN.top - 2) + '" text-anchor="end" class="forecast-chart-axis-title">p/kWh</text>'
            );

            chartSvg.innerHTML = parts.join('');
        }

        function formatSlotLabel(dateTime) {
            return new Intl.DateTimeFormat('en-GB', {
                day: '2-digit',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
                timeZone: 'Europe/London',
            }).format(new Date(dateTime));
        }

        function getLondonDayKey(dateTime) {
            return new Intl.DateTimeFormat('en-CA', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                timeZone: 'Europe/London',
            }).format(new Date(dateTime));
        }

        function formatLondonDayLabel(dateTime) {
            return new Intl.DateTimeFormat('en-GB', {
                weekday: 'short',
                day: '2-digit',
                month: 'short',
                timeZone: 'Europe/London',
            }).format(new Date(dateTime));
        }

        function getValuePillClass(value) {
            if (value == null) {
                return 'value-pill value-pill-na';
            }
            if (value < 0) {
                return 'value-pill value-pill-blue';
            }
            if (value < 20) {
                return 'value-pill value-pill-green';
            }
            if (value < 30) {
                return 'value-pill value-pill-orange';
            }
            return 'value-pill value-pill-red';
        }

        function getActualInRange(slot) {
            const actual = toNumber(slot.agile_actual);
            const low = toNumber(slot.agile_low);
            const high = toNumber(slot.agile_high);
            if (actual == null || low == null || high == null) {
                return null;
            }
            return actual >= low && actual <= high;
        }

        function formatDelta(slot) {
            const actual = toNumber(slot.agile_actual);
            const pred = toNumber(slot.agile_pred);
            if (actual == null || pred == null) {
                return { cls: 'delta-pill delta-pill-na', text: 'n/a' };
            }
            const delta = actual - pred;
            const inRange = getActualInRange(slot);
            const cls = inRange === null ? 'delta-pill delta-pill-na' : inRange ? 'delta-pill delta-pill-in' : 'delta-pill delta-pill-out';
            return { cls, text: (delta >= 0 ? '+' : '') + delta.toFixed(2) };
        }

        async function loadAvailability() {
            const res = await fetch('/api/v1/forecasts/availability');
            if (!res.ok) {
                throw new Error('Failed loading cache availability');
            }
            return await res.json();
        }

        async function loadPrices(region, days) {
            const url = '/api/v1/forecasts/prices?region=' + encodeURIComponent(region) + '&days=' + encodeURIComponent(days) + '&forecast_count=1&high_low=true';
            const res = await fetch(url);
            if (!res.ok) {
                throw new Error('Failed loading prices for region ' + region + ' and day window ' + days);
            }
            return await res.json();
        }

        function formatAgeText(refreshedAt) {
            if (!refreshedAt) {
                return '-';
            }
            const refreshedMs = new Date(refreshedAt).getTime();
            if (!Number.isFinite(refreshedMs)) {
                return '-';
            }
            const ageSeconds = Math.max(0, Math.floor((Date.now() - refreshedMs) / 1000));
            if (ageSeconds < 60) {
                return ageSeconds + 's ago';
            }
            const ageMinutes = Math.floor(ageSeconds / 60);
            if (ageMinutes < 60) {
                return ageMinutes + 'm ago';
            }
            const ageHours = Math.floor(ageMinutes / 60);
            const remMinutes = ageMinutes % 60;
            return ageHours + 'h ' + remMinutes + 'm ago';
        }

        function updateCacheAge(refreshedAt) {
            const ageEl = document.getElementById('cache_age');
            ageEl.textContent = formatAgeText(refreshedAt);
        }

        function formatPillValue(value) {
            return value == null ? 'n/a' : value.toFixed(2);
        }

        function getAvailableDaysForRegion(availability, region) {
            const byRegion = availability && availability.days_by_region ? availability.days_by_region : {};
            const days = byRegion[region];
            if (!Array.isArray(days) || !days.length) {
                return [];
            }
            return days.map((value) => Number(value)).filter((value) => Number.isInteger(value));
        }

        function syncDaysDropdown(daysSelect, availability, region, preferredDays) {
            const availableDays = getAvailableDaysForRegion(availability, region);
            daysSelect.innerHTML = '';
            availableDays.forEach((days) => {
                const option = document.createElement('option');
                option.value = String(days);
                option.textContent = String(days);
                daysSelect.appendChild(option);
            });

            if (!availableDays.length) {
                daysSelect.disabled = true;
                return null;
            }

            daysSelect.disabled = false;
            const defaultDays = Number(availability.default_days || 7);
            let selectedDays = availableDays.includes(preferredDays) ? preferredDays : defaultDays;
            if (!availableDays.includes(selectedDays)) {
                selectedDays = availableDays[0];
            }
            daysSelect.value = String(selectedDays);
            return selectedDays;
        }

        function groupByDay(slots) {
            const groups = [];
            const byKey = new Map();
            for (const slot of slots) {
                const key = getLondonDayKey(slot.date_time);
                if (!byKey.has(key)) {
                    const group = { key, label: formatLondonDayLabel(slot.date_time), slots: [] };
                    byKey.set(key, group);
                    groups.push(group);
                }
                byKey.get(key).slots.push(slot);
            }
            return groups;
        }

        function summarize(slots) {
            if (!slots.length) {
                return null;
            }
            const values = slots.map((s) => toNumber(s.agile_pred)).filter((v) => v != null);
            if (!values.length) {
                return null;
            }
            const total = values.reduce((sum, value) => sum + value, 0);
            return {
                min: Math.min(...values),
                max: Math.max(...values),
                avg: total / values.length,
                count: values.length,
            };
        }

        function updateSummary(slots) {
            const summary = summarize(slots);
            const minEl = document.getElementById('min_pred');
            const avgEl = document.getElementById('avg_pred');
            const maxEl = document.getElementById('max_pred');
            const countEl = document.getElementById('slot_count');
            const trainingEl = document.getElementById('training');
            const trainingContainer = document.getElementById('training_container');

            if (!summary) {
                minEl.textContent = '-';
                avgEl.textContent = '-';
                maxEl.textContent = '-';
                countEl.textContent = '0';
                return;
            }

            minEl.textContent = summary.min.toFixed(2) + ' p/kWh';
            avgEl.textContent = summary.avg.toFixed(2) + ' p/kWh';
            maxEl.textContent = summary.max.toFixed(2) + ' p/kWh';
            countEl.textContent = String(summary.count);
        }

        function updateTrainingData(trainingDays) {
            const trainingEl = document.getElementById('training');
            const trainingContainer = document.getElementById('training_container');
            if (trainingDays != null && trainingDays > 0) {
                trainingContainer.style.display = '';
                const days = Number(trainingDays);
                trainingEl.textContent = String(days) + (days === 1 ? ' day' : ' days');
            } else {
                trainingContainer.style.display = 'none';
            }
        }

        function renderDayTabs(dayGroups, selectedDayKey, onSelect) {
            const tabs = document.getElementById('day_tabs');
            tabs.innerHTML = '';
            dayGroups.forEach((group) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'day-tab-button' + (group.key === selectedDayKey ? ' active' : '');
                btn.textContent = group.label + ' (' + group.slots.length + ')';
                btn.onclick = () => onSelect(group.key);
                tabs.appendChild(btn);
            });
        }

        function renderRows(slots) {
            const tbody = document.getElementById('rows');
            document.getElementById('upcoming_slot_count').textContent = String(slots.length) + ' slots';
            tbody.innerHTML = '';
            for (const slot of slots) {
                const pred = toNumber(slot.agile_pred);
                const low = toNumber(slot.agile_low);
                const high = toNumber(slot.agile_high);
                const actual = toNumber(slot.agile_actual);
                const delta = formatDelta(slot);

                const tr = document.createElement('tr');
                tr.innerHTML =
                    '<td>' + formatSlotLabel(slot.date_time) + '</td>' +
                    '<td><span class="' + getValuePillClass(pred) + '">' + formatPillValue(pred) + '</span></td>' +
                    '<td><span class="' + getValuePillClass(low) + '">' + formatPillValue(low) + '</span></td>' +
                    '<td><span class="' + getValuePillClass(high) + '">' + formatPillValue(high) + '</span></td>' +
                    '<td><span class="' + getValuePillClass(actual) + '">' + formatPillValue(actual) + '</span></td>' +
                    '<td><span class="' + delta.cls + '">' + delta.text + '</span></td>';
                tbody.appendChild(tr);
            }
        }

        async function boot() {
            const error = document.getElementById('error');
            const status = document.getElementById('status');
            const select = document.getElementById('region');
            const daysSelect = document.getElementById('days');
            error.textContent = '';

            let selectedDayKey = '';
            let availability = null;

            const syncSelectedDays = () => {
                const preferredDays = Number(daysSelect.value || '7');
                return syncDaysDropdown(daysSelect, availability, select.value || 'G', preferredDays);
            };

            const load = async () => {
                status.textContent = 'Refreshing cached forecast view...';
                error.textContent = '';
                try {
                    availability = await loadAvailability();
                    updateCacheAge(availability.refreshed_at);
                    updateTrainingData(availability.training_days);
                    const selectedDays = syncSelectedDays();
                    if (selectedDays == null) {
                        throw new Error('Selected region has no warmed day ranges yet');
                    }
                    const payload = await loadPrices(select.value || 'G', selectedDays);
                    const first = Array.isArray(payload) && payload.length ? payload[0] : null;
                    const slots = first && Array.isArray(first.prices) ? first.prices : [];
                    const nowMs = Date.now();
                    const futureSlots = slots.filter((slot) => {
                        const ts = new Date(slot.date_time).getTime();
                        return Number.isFinite(ts) && ts >= nowMs;
                    });

                    renderChart(slots);
                    updateSummary(futureSlots);
                    const dayGroups = groupByDay(futureSlots);
                    if (dayGroups.length > 0 && !dayGroups.some((group) => group.key === selectedDayKey)) {
                        selectedDayKey = dayGroups[0].key;
                    }
                    const handleDaySelect = (newKey) => {
                        selectedDayKey = newKey;
                        const day = dayGroups.find((group) => group.key === selectedDayKey);
                        renderRows(day ? day.slots : []);
                        renderDayTabs(dayGroups, selectedDayKey, handleDaySelect);
                    };
                    renderDayTabs(dayGroups, selectedDayKey, handleDaySelect);
                    const selected = dayGroups.find((group) => group.key === selectedDayKey);
                    renderRows(selected ? selected.slots : []);
                    status.textContent = 'Serving cached ' + selectedDays + '-day forecast data for region ' + (select.value || 'G') + '.';
                } catch (err) {
                    updateCacheAge(null);
                    renderChart([]);
                    updateSummary([]);
                    document.getElementById('day_tabs').innerHTML = '';
                    document.getElementById('rows').innerHTML = '';
                    error.textContent = String(err);
                    status.textContent = 'Public cache unavailable right now.';
                }
            };

            try {
                availability = await loadAvailability();
                updateCacheAge(availability.refreshed_at);
                const regions = Array.isArray(availability.regions) ? availability.regions : [];
                select.innerHTML = '';
                regions.forEach((region) => {
                    const option = document.createElement('option');
                    option.value = region;
                    option.textContent = region;
                    select.appendChild(option);
                });
                syncSelectedDays();

                document.getElementById('refresh').onclick = load;
                select.onchange = () => {
                    syncSelectedDays();
                    load();
                };
                daysSelect.onchange = load;
                await load();
            } catch (err) {
                error.textContent = String(err);
                status.textContent = 'Failed loading public dashboard.';
            }
        }

        boot();
    </script>
</body>
</html>
"""
    html = html.replace("__PUBLIC_BASE_URL__", PUBLIC_BASE_URL)
    return HTMLResponse(content=html)
