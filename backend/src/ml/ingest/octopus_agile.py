"""
Ingest actual Agile tariff prices from Octopus Energy public API.

Octopus releases prices ~4pm UTC for the next day (24-hour window).
This fetches historical prices for all 15 UK regions for backfill + ongoing collection.

Public endpoint: https://api.octopus.energy/v1/products/AGILE-23-12-01/electricity-tariffs/...
No authentication required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen, Request
import json
import logging

log = logging.getLogger(__name__)

# Octopus public tariff product ID (as of 2026)
AGILE_PRODUCT_ID = "AGILE-23-12-01"

# All 15 UK regions (DSO areas)
AGILE_REGIONS = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "X"]

# Base URL for Octopus public API
OCTOPUS_BASE_URL = "https://api.octopus.energy/v1"


def build_tariff_url(region: str, page: int | None = None) -> str:
    """Build Octopus API URL for a specific region's standard unit rates."""
    tariff_code = f"E-1R-{AGILE_PRODUCT_ID}-{region}"
    url = f"{OCTOPUS_BASE_URL}/products/{AGILE_PRODUCT_ID}/electricity-tariffs/{tariff_code}/standard-unit-rates/"
    params = {"page_size": 1500}  # Max results per page
    if page is not None:
        params["page"] = page
    return f"{url}?{urlencode(params)}"


def parse_agile_payload(payload: dict) -> dict[datetime, float]:
    """
    Parse Octopus API response into {datetime: price_pence_per_kwh} dict.
    
    Octopus returns prices in pence per kWh; we store as-is for direct comparison.
    """
    results = payload.get("results", [])
    data: dict[datetime, float] = {}

    for result in results:
        valid_from = result.get("valid_from")
        value_exc_vat = result.get("value_exc_vat")  # Price in pence/kWh
        
        if valid_from is None or value_exc_vat is None:
            continue

        # Octopus returns ISO format with Z for UTC
        dt = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
        data[dt] = float(value_exc_vat)

    return dict(sorted(data.items(), key=lambda item: item[0]))


def fetch_agile_prices_for_region(
    region: str,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    timeout: int = 20,
) -> dict[datetime, float]:
    """
    Fetch all Agile prices for a single region between dates.
    
    If from_date/to_date not specified, fetches last 30 days.
    Pagination-aware: keeps fetching until no more pages.
    """
    if from_date is None:
        from_date = datetime.now(timezone.utc) - timedelta(days=30)
    if to_date is None:
        to_date = datetime.now(timezone.utc)

    # Ensure UTC
    from_date = from_date.replace(tzinfo=timezone.utc)
    to_date = to_date.replace(tzinfo=timezone.utc)

    all_prices: dict[datetime, float] = {}
    page = 1
    max_pages = 500  # Safety limit to prevent infinite loops

    while page <= max_pages:
        url = build_tariff_url(region, page)
        log.debug(f"Fetching Agile prices for region {region}, page {page}")

        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; agile-predict)"})
            with urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            log.error(f"Failed to fetch Agile prices for region {region} page {page}: {exc}")
            raise

        page_prices = parse_agile_payload(payload)
        if not page_prices:
            break

        # Filter to requested date range
        filtered = {
            dt: price
            for dt, price in page_prices.items()
            if from_date <= dt <= to_date
        }
        all_prices.update(filtered)

        # Check if there's a next page
        next_url = payload.get("next")
        if not next_url:
            break

        page += 1

    log.info(
        f"Fetched {len(all_prices)} Agile prices for region {region} "
        f"between {from_date.date()} and {to_date.date()}"
    )
    return all_prices


def fetch_agile_prices_all_regions(
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    timeout: int = 20,
) -> dict[str, dict[datetime, float]]:
    """
    Fetch Agile prices for all 15 UK regions.
    
    Returns {region: {datetime: price}} structure.
    Failures in individual regions are logged but don't fail the whole operation.
    """
    results = {}
    failed_regions = []

    for region in AGILE_REGIONS:
        try:
            prices = fetch_agile_prices_for_region(
                region=region,
                from_date=from_date,
                to_date=to_date,
                timeout=timeout,
            )
            results[region] = prices
        except Exception as exc:
            log.warning(f"Failed to fetch Agile prices for region {region}: {exc}")
            failed_regions.append(region)

    if failed_regions:
        log.warning(f"Failed regions: {', '.join(failed_regions)}")

    return results
