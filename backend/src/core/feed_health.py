"""
Feed health tracking for external data sources.

Persists last successful pull timestamp, record count, and error state per feed endpoint.
Used by diagnostics UI to show which feeds are healthy/stale/failed.
"""

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
from src.core.settings import CONFIG_DIR


# Feed source identifiers matching ingest code
FEED_SOURCES = {
    # Agile UK (Octopus Energy) - 15 regional endpoints
    "agile_octopus_A": {"name": "Agile UK - Region A", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_B": {"name": "Agile UK - Region B", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_C": {"name": "Agile UK - Region C", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_D": {"name": "Agile UK - Region D", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_E": {"name": "Agile UK - Region E", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_F": {"name": "Agile UK - Region F", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_G": {"name": "Agile UK - Region G", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_H": {"name": "Agile UK - Region H", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_J": {"name": "Agile UK - Region J", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_K": {"name": "Agile UK - Region K", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_L": {"name": "Agile UK - Region L", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_M": {"name": "Agile UK - Region M", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_N": {"name": "Agile UK - Region N", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "agile_octopus_P": {"name": "Agile UK - Region P", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    
    # Day-Ahead Prices
    "nordpool_da": {"name": "Nordpool Day-Ahead (UK)", "freq_seconds": 86400, "staleness_threshold_seconds": 172800},
    
    # Weather
    "weather_open_meteo": {"name": "Weather (Open-Meteo)", "freq_seconds": 3600, "staleness_threshold_seconds": 7200},
    
    # NESO Grid Data
    "neso_demand": {"name": "NESO Demand (UK)", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "neso_bm_wind": {"name": "NESO BM Wind (UK)", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "neso_solar_wind": {"name": "NESO Solar/Wind (UK)", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "neso_embedded_solar_wind": {"name": "NESO Embedded Solar/Wind", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    
    # Elexon BMRS (fallback)
    "elexon_indo": {"name": "Elexon BMRS INDO (28D actual)", "freq_seconds": 1800, "staleness_threshold_seconds": 3600},
    "elexon_ndf": {"name": "Elexon BMRS NDF (14D forecast)", "freq_seconds": 86400, "staleness_threshold_seconds": 172800},
    "elexon_fuelinst": {"name": "Elexon BMRS FUELINST", "freq_seconds": 3600, "staleness_threshold_seconds": 7200},
}


@dataclass
class FeedHealthEntry:
    """Health status for a single feed source."""
    source_id: str
    name: str
    last_successful_pull: Optional[str] = None  # ISO 8601 timestamp
    records_received: int = 0
    last_error: Optional[str] = None
    error_count: int = 0
    last_error_time: Optional[str] = None  # ISO 8601 timestamp
    validation_status: Optional[str] = None  # pass | warn | fail
    validation_issues: list[str] = field(default_factory=list)
    validation_metrics: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @staticmethod
    def from_dict(data: dict) -> "FeedHealthEntry":
        return FeedHealthEntry(**data)


def _get_health_file_path() -> Path:
    """Get path to feed health persistence file."""
    path = Path(CONFIG_DIR) / "feed_health.json"
    return path


def _read_feed_health() -> Dict[str, FeedHealthEntry]:
    """Read feed health state from disk, initialize if missing."""
    path = _get_health_file_path()
    
    if not path.exists():
        # Initialize with all known sources
        health = {
            source_id: FeedHealthEntry(source_id=source_id, name=FEED_SOURCES[source_id]["name"])
            for source_id in FEED_SOURCES
        }
        return health
    
    try:
        with open(path, "r") as f:
            data = json.load(f)
        loaded = {
            source_id: FeedHealthEntry.from_dict(entry)
            for source_id, entry in data.items()
        }

        # Reconcile to current source registry:
        # - keep known sources (preserving their state)
        # - add missing known sources with empty state
        # - drop deprecated/unknown sources
        reconciled: Dict[str, FeedHealthEntry] = {}
        for source_id, cfg in FEED_SOURCES.items():
            reconciled[source_id] = loaded.get(
                source_id,
                FeedHealthEntry(source_id=source_id, name=cfg["name"]),
            )
            # Ensure display name stays aligned with current config.
            reconciled[source_id].name = cfg["name"]

        return reconciled
    except (json.JSONDecodeError, KeyError, TypeError):
        # Reset if corrupted
        return {
            source_id: FeedHealthEntry(source_id=source_id, name=FEED_SOURCES[source_id]["name"])
            for source_id in FEED_SOURCES
        }


def _write_feed_health(health: Dict[str, FeedHealthEntry]) -> None:
    """Write feed health state to disk."""
    path = _get_health_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(path, "w") as f:
        json.dump(
            {source_id: entry.to_dict() for source_id, entry in health.items()},
            f,
            indent=2,
            default=str,
        )


def record_feed_success(
    source_id: str,
    records_received: int = 0,
    validation_status: str = "pass",
    validation_issues: list[str] | None = None,
    validation_metrics: dict | None = None,
) -> None:
    """Record successful pull for a feed source."""
    if source_id not in FEED_SOURCES:
        return
    
    health = _read_feed_health()
    entry = health.get(source_id, FeedHealthEntry(source_id=source_id, name=FEED_SOURCES[source_id]["name"]))
    
    entry.last_successful_pull = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    entry.records_received = records_received
    entry.last_error = None
    entry.error_count = 0
    entry.validation_status = validation_status
    entry.validation_issues = list(validation_issues or [])
    entry.validation_metrics = dict(validation_metrics or {})
    
    health[source_id] = entry
    _write_feed_health(health)


def record_feed_error(
    source_id: str,
    error_message: str,
) -> None:
    """Record failed pull for a feed source."""
    if source_id not in FEED_SOURCES:
        return
    
    health = _read_feed_health()
    entry = health.get(source_id, FeedHealthEntry(source_id=source_id, name=FEED_SOURCES[source_id]["name"]))
    
    entry.last_error = error_message
    entry.error_count = entry.error_count + 1
    entry.last_error_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    entry.validation_status = "fail"
    entry.validation_issues = [error_message]
    
    health[source_id] = entry
    _write_feed_health(health)


def get_feed_health() -> Dict[str, dict]:
    """Get current health status for all feeds, with computed status."""
    health = _read_feed_health()
    now = datetime.now(timezone.utc)
    
    result = {}
    for source_id, entry in health.items():
        config = FEED_SOURCES.get(source_id, {})
        
        # Determine status
        status = "unknown"
        if entry.last_error is not None:
            status = "error"
        elif entry.validation_status in {"warn", "fail"}:
            status = "degraded"
        elif entry.last_successful_pull is not None:
            last_pull_time = datetime.fromisoformat(entry.last_successful_pull.replace("Z", "+00:00"))
            seconds_since_pull = (now - last_pull_time).total_seconds()
            staleness_threshold = config.get("staleness_threshold_seconds", 7200)
            
            if seconds_since_pull > staleness_threshold:
                status = "stale"
            else:
                status = "healthy"
        elif source_id == "elexon_ndf":
            # This feed is tracked but may not be pulled in every runtime path.
            status = "inactive"
        
        result[source_id] = {
            "name": entry.name,
            "status": status,
            "last_successful_pull": entry.last_successful_pull,
            "records_received": entry.records_received,
            "last_error": entry.last_error,
            "error_count": entry.error_count,
            "last_error_time": entry.last_error_time,
            "validation_status": entry.validation_status,
            "validation_issues": entry.validation_issues,
            "validation_metrics": entry.validation_metrics,
            "expected_frequency_seconds": config.get("freq_seconds"),
            "staleness_threshold_seconds": config.get("staleness_threshold_seconds"),
        }
    
    return result
