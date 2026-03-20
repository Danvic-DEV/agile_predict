from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.core.settings import CONFIG_DIR
from src.core.discord_runtime_config import read_discord_runtime_config

log = logging.getLogger(__name__)
DISCORD_NOTIFICATION_STATE_PATH = Path(CONFIG_DIR) / "discord-notification-state.json"

PARITY_ALERT_THRESHOLDS: dict[str, float] = {
    "mae": 8.0,
    "p95_abs": 20.0,
    "max_abs": 35.0,
}


def _read_notification_state() -> dict[str, Any]:
    if not DISCORD_NOTIFICATION_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(DISCORD_NOTIFICATION_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_notification_state(payload: dict[str, Any]) -> None:
    try:
        DISCORD_NOTIFICATION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DISCORD_NOTIFICATION_STATE_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def _field(name: str, value: Any, *, inline: bool = True) -> dict[str, Any] | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if not rendered:
        return None
    return {
        "name": name,
        "value": rendered[:1024],
        "inline": inline,
    }


def _post_to_webhook(webhook_url: str, payload: dict[str, Any]) -> tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "agile-predict/discord",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            status_code = getattr(response, "status", 200)
            if 200 <= status_code < 300:
                return True, "Notification sent to Discord."
            return False, f"Discord webhook returned status {status_code}."
    except HTTPError as exc:
        return False, f"Discord webhook HTTP error: {exc.code}"
    except URLError as exc:
        return False, f"Discord webhook connection failed: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, f"Discord webhook send failed: {exc}"


def send_discord_notification(
    *,
    preference_key: str | None,
    title: str,
    description: str,
    color: int,
    fields: list[dict[str, Any] | None] | None = None,
    force: bool = False,
) -> tuple[bool, str]:
    config = read_discord_runtime_config()
    webhook_url = str(config.get("webhook_url") or "").strip()
    notifications = config.get("notifications") or {}

    if not webhook_url:
        return False, "Discord webhook URL is not configured."

    if preference_key and not force and not bool(notifications.get(preference_key, False)):
        return False, f"Discord notification '{preference_key}' is disabled."

    payload = {
        "username": "Agile Predict",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": [field for field in (fields or []) if field is not None],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }
    sent, detail = _post_to_webhook(webhook_url, payload)
    if not sent:
        log.warning("Discord notification failed: %s", detail)
    return sent, detail


def send_discord_test_notification() -> tuple[bool, str]:
    return send_discord_notification(
        preference_key=None,
        title="Discord integration test",
        description="Agile Predict can reach your configured Discord webhook.",
        color=0x4AA3FF,
        fields=[
            _field("Environment", "diagnostics"),
            _field("Timestamp", datetime.now(timezone.utc).isoformat(), inline=False),
        ],
        force=True,
    )


def send_update_started_notification(*, trigger: str) -> tuple[bool, str]:
    return send_discord_notification(
        preference_key="update_started",
        title="Forecast update started",
        description="A forecast update run has been triggered and is now in progress.",
        color=0x4AA3FF,
        fields=[
            _field("Trigger", trigger),
        ],
    )


def send_update_success_notification(
    *,
    forecast_name: str,
    source: str,
    records_written: int,
    day_ahead_points: int,
    ml_device_used: str | None,
    training_mode: bool,
    ml_compare_mae: float | None,
    ml_compare_p95_abs: float | None,
    ml_compare_max_abs: float | None,
    ml_error: str | None,
) -> tuple[bool, str]:
    fields = [
        _field("Forecast", forecast_name),
        _field("Source", source),
        _field("Records Written", records_written),
        _field("Day-Ahead Points", day_ahead_points),
        _field("Device", ml_device_used or "cpu"),
        _field("Training Mode", "enabled" if training_mode else "disabled"),
        _field("MAE vs deterministic", None if ml_compare_mae is None else f"{ml_compare_mae:.3f}"),
        _field("P95 abs diff", None if ml_compare_p95_abs is None else f"{ml_compare_p95_abs:.3f}"),
        _field("Max abs diff", None if ml_compare_max_abs is None else f"{ml_compare_max_abs:.3f}"),
        _field("Warnings", ml_error, inline=False),
    ]
    return send_discord_notification(
        preference_key="update_success",
        title="Forecast update completed",
        description="The update forecast job finished successfully.",
        color=0x2ECC71,
        fields=fields,
    )


def send_update_failure_notification(*, detail: str, trigger: str) -> tuple[bool, str]:
    return send_discord_notification(
        preference_key="update_failure",
        title="Forecast update failed",
        description="The update forecast job raised an error before completion.",
        color=0xE74C3C,
        fields=[
            _field("Trigger", trigger),
            _field("Error", detail, inline=False),
        ],
    )


def send_gpu_alert_notification(*, reason: str, gpu_name: str | None) -> tuple[bool, str]:
    return send_discord_notification(
        preference_key="gpu_alert",
        title="GPU acceleration unavailable",
        description="GPU was requested for ML training, but the runtime fell back to CPU.",
        color=0xF39C12,
        fields=[
            _field("GPU", gpu_name or "Unknown"),
            _field("Reason", reason, inline=False),
        ],
    )


def send_parity_alert_notification(
    *,
    forecast_name: str,
    mae: float | None,
    p95_abs: float | None,
    max_abs: float | None,
) -> tuple[bool, str]:
    return send_discord_notification(
        preference_key="parity_alert",
        title="ML parity drift detected",
        description="ML candidate output drifted beyond the configured notification threshold.",
        color=0xE67E22,
        fields=[
            _field("Forecast", forecast_name),
            _field("MAE vs deterministic", None if mae is None else f"{mae:.3f}"),
            _field("P95 abs diff", None if p95_abs is None else f"{p95_abs:.3f}"),
            _field("Max abs diff", None if max_abs is None else f"{max_abs:.3f}"),
            _field(
                "Thresholds",
                f"MAE>{PARITY_ALERT_THRESHOLDS['mae']}, P95>{PARITY_ALERT_THRESHOLDS['p95_abs']}, MAX>{PARITY_ALERT_THRESHOLDS['max_abs']}",
                inline=False,
            ),
        ],
    )


def send_daily_digest_notification(
    *,
    forecast_name: str,
    source: str,
    records_written: int,
    ml_device_used: str | None,
    day_ahead_values: tuple[float, ...],
) -> tuple[bool, str]:
    today_key = datetime.now(timezone.utc).date().isoformat()
    state = _read_notification_state()
    if state.get("daily_digest_date") == today_key:
        return False, "Daily digest already sent today."

    if not day_ahead_values:
        return False, "Daily digest skipped because there were no forecast values."

    next_day_window = day_ahead_values[:48] if len(day_ahead_values) >= 48 else day_ahead_values
    low_value = min(next_day_window)
    high_value = max(next_day_window)
    avg_value = sum(next_day_window) / len(next_day_window)

    sent, detail = send_discord_notification(
        preference_key="daily_digest",
        title="Daily forecast digest",
        description="Next trading window summary from the latest successful forecast run.",
        color=0x3498DB,
        fields=[
            _field("Forecast", forecast_name),
            _field("Source", source),
            _field("Records Written", records_written),
            _field("Device", ml_device_used or "cpu"),
            _field("Next-day Low", f"{low_value:.3f}"),
            _field("Next-day High", f"{high_value:.3f}"),
            _field("Next-day Average", f"{avg_value:.3f}"),
        ],
    )
    if sent:
        state["daily_digest_date"] = today_key
        _write_notification_state(state)
    return sent, detail


def send_pipeline_staleness_alert_notification(*, summary: str, signature: str) -> tuple[bool, str]:
    state = _read_notification_state()
    if state.get("pipeline_staleness_signature") == signature:
        return False, "Pipeline staleness alert already sent for current condition."

    sent, detail = send_discord_notification(
        preference_key="pipeline_staleness",
        title="Pipeline staleness or fallback detected",
        description="One or more upstream inputs stopped updating cleanly and the forecast pipeline degraded.",
        color=0xC0392B,
        fields=[
            _field("Condition", summary, inline=False),
        ],
    )
    if sent:
        state["pipeline_staleness_signature"] = signature
        _write_notification_state(state)
    return sent, detail


def clear_pipeline_staleness_alert_state() -> None:
    state = _read_notification_state()
    if "pipeline_staleness_signature" not in state:
        return
    del state["pipeline_staleness_signature"]
    _write_notification_state(state)