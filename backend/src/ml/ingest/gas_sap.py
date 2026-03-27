"""Fetch National Gas System Average Price (SAP) for a date range.

Source: National Gas Transmission open data portal.
  API: https://data.nationalgas.com/api/find-gas-data-download
  Feed: PUBOB603 — System Average Price, Actual Day Ahead
  Published: daily ~12:40 UK time, covering that gas day.

The SAP is published before the 16:15 forecast creation window, so it is
available at inference time with no lookahead risk.
"""

from __future__ import annotations

import io
import logging
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen

import pandas as pd

from src.core.feed_health import record_feed_error, record_feed_success

log = logging.getLogger(__name__)

_GAS_SAP_BASE_URL = "https://data.nationalgas.com/api/find-gas-data-download"
_FEED_SOURCE_ID = "national_gas_sap"
_TIMEOUT = 30


def fetch_gas_sap(date_from: str, date_to: str) -> dict[date, float]:
    """Fetch daily gas System Average Price (p/kWh) for a date range.

    Args:
        date_from: ISO date string YYYY-MM-DD (inclusive).
        date_to:   ISO date string YYYY-MM-DD (inclusive).

    Returns:
        Mapping of calendar date → gas SAP in p/kWh.

    Raises:
        RuntimeError: on network failure or empty/unparseable response.
    """
    url = (
        f"{_GAS_SAP_BASE_URL}"
        f"?applicableFor=N"
        f"&dateFrom={date_from}T00:00:00"
        f"&dateTo={date_to}T23:59:59"
        f"&dateType=NORMALDAY&latestFlag=Y&ids=PUBOB603&type=CSV"
    )
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; agile-predict)",
            "Accept": "text/csv,*/*",
        },
    )
    try:
        with urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as exc:
        record_feed_error(_FEED_SOURCE_ID, str(exc))
        raise RuntimeError(f"gas SAP fetch failed ({date_from} → {date_to}): {exc}") from exc

    try:
        frame = pd.read_csv(io.StringIO(raw))
        frame.columns = [c.strip() for c in frame.columns]

        actual_rows = frame[frame["Data Item"].str.contains("Actual Day", na=False)].copy()
        if actual_rows.empty:
            record_feed_error(_FEED_SOURCE_ID, "no Actual Day rows in response")
            raise RuntimeError(
                f"gas SAP response contained no 'Actual Day' rows for {date_from} → {date_to}"
            )

        actual_rows["_date"] = pd.to_datetime(
            actual_rows["Applicable For"], dayfirst=True, utc=True
        ).dt.normalize()
        actual_rows["_gas_sap"] = pd.to_numeric(actual_rows["Value"], errors="coerce")
        actual_rows = actual_rows.dropna(subset=["_gas_sap"])
        # Keep only the most recently published value per date (latestFlag=Y already
        # filters, but sort ensures determinism if duplicates arrive).
        actual_rows = (
            actual_rows.sort_values("Applicable At").drop_duplicates("_date", keep="last")
        )

        result: dict[date, float] = {
            row["_date"].date(): float(row["_gas_sap"])
            for _, row in actual_rows.iterrows()
        }
    except RuntimeError:
        raise
    except Exception as exc:
        record_feed_error(_FEED_SOURCE_ID, str(exc))
        raise RuntimeError(f"gas SAP parse failed: {exc}") from exc

    record_feed_success(_FEED_SOURCE_ID, len(result))
    log.info("Fetched %d gas SAP days (%s → %s)", len(result), date_from, date_to)
    return result


def to_orm_rows(sap_by_date: dict[date, float]) -> list[dict]:
    """Convert fetch_gas_sap output to dicts ready for GasSapWriteRepository.upsert_many."""
    return [
        {
            "date": datetime.combine(d, datetime.min.time()).replace(tzinfo=timezone.utc),
            "gas_sap": v,
        }
        for d, v in sap_by_date.items()
    ]
