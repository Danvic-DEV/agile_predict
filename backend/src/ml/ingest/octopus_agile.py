"""
Ingest actual Agile tariff prices from Octopus Energy public API.

Octopus releases prices ~4pm UTC for the next day (24-hour window).
This fetches historical prices for all 15 UK regions for backfill + ongoing collection.

Public endpoint: https://api.octopus.energy/v1/products/AGILE-23-12-01/electricity-tariffs/...
No authentication required.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen, Request
import json
import logging

from src.core.feed_health import record_feed_error, record_feed_success

log = logging.getLogger(__name__)

# Optional override for known active Agile product code.
AGILE_PRODUCT_ID_OVERRIDE = os.getenv("OCTOPUS_AGILE_PRODUCT_ID", "").strip()

# Base URL for Octopus public API
OCTOPUS_BASE_URL = "https://api.octopus.energy/v1"


def _resolve_agile_product_id(timeout: int = 20) -> str:
    """Resolve active Agile import product code.

    Prefers env override. Otherwise discovers latest import Agile product.
    """
    if AGILE_PRODUCT_ID_OVERRIDE:
        return AGILE_PRODUCT_ID_OVERRIDE

    req = Request(
        f"{OCTOPUS_BASE_URL}/products/?page_size=250",
        headers={"User-Agent": "Mozilla/5.0 (compatible; agile-predict)"},
    )
    with urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    # Import products look like AGILE-24-10-01 (exclude AGILE-OUTGOING-...)
    candidates = [
        item.get("code", "")
        for item in payload.get("results", [])
        if re.fullmatch(r"AGILE-\d{2}-\d{2}-\d{2}", item.get("code", ""))
    ]
    if not candidates:
        raise RuntimeError("No active Octopus Agile import product code found")

    # Lexicographic max works for YY-MM-DD code format.
    return max(candidates)


def _resolve_agile_regions(product_id: str, timeout: int = 20) -> list[str]:
    """Resolve valid region codes for a given Agile import product.

    Regions are published under `single_register_electricity_tariffs` as keys
    like `_A`, `_B`, ... `_P`.
    """
    req = Request(
        f"{OCTOPUS_BASE_URL}/products/{product_id}/",
        headers={"User-Agent": "Mozilla/5.0 (compatible; agile-predict)"},
    )
    with urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    tariff_keys = (payload.get("single_register_electricity_tariffs") or {}).keys()
    regions = sorted(k.replace("_", "").strip().upper() for k in tariff_keys if k.startswith("_"))
    if not regions:
        raise RuntimeError(f"No Agile regions found for product {product_id}")
    return regions


def build_tariff_url(
    region: str,
    product_id: str,
    page: int | None = None,
    period_from: datetime | None = None,
    period_to: datetime | None = None,
) -> str:
    """Build Octopus API URL for a specific region's standard unit rates."""
    tariff_code = f"E-1R-{product_id}-{region}"
    url = f"{OCTOPUS_BASE_URL}/products/{product_id}/electricity-tariffs/{tariff_code}/standard-unit-rates/"
    params: dict = {"page_size": 1500}  # Max results per page
    if page is not None:
        params["page"] = page
    if period_from is not None:
        params["period_from"] = period_from.strftime("%Y-%m-%dT%H:%M:%SZ")
    if period_to is not None:
        params["period_to"] = period_to.strftime("%Y-%m-%dT%H:%M:%SZ")
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
    product_id: str,
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
        url = build_tariff_url(region, product_id, page, period_from=from_date, period_to=to_date)
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

        all_prices.update(page_prices)

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
    product_id = _resolve_agile_product_id(timeout=timeout)
    regions = _resolve_agile_regions(product_id=product_id, timeout=timeout)
    log.info("Using Octopus Agile product code: %s", product_id)
    log.info("Using Octopus Agile regions: %s", ",".join(regions))

    results = {}
    failed_regions = []

    for region in regions:
        try:
            prices = fetch_agile_prices_for_region(
                region=region,
                product_id=product_id,
                from_date=from_date,
                to_date=to_date,
                timeout=timeout,
            )
            results[region] = prices
            record_feed_success(
                source_id=f"agile_octopus_{region}",
                records_received=len(prices),
            )
        except Exception as exc:
            log.warning(f"Failed to fetch Agile prices for region {region}: {exc}")
            failed_regions.append(region)
            record_feed_error(
                source_id=f"agile_octopus_{region}",
                error_message=str(exc),
            )

    if failed_regions:
        failed = ", ".join(failed_regions)
        raise RuntimeError(f"Agile fetch failed for regions: {failed}")

    return results
