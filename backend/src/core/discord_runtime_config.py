from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.settings import CONFIG_DIR

DISCORD_RUNTIME_CONFIG_PATH = Path(CONFIG_DIR) / "discord-runtime-config.json"
_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
)
_DEFAULT_NOTIFICATIONS: dict[str, bool] = {
    "update_success": True,
    "update_failure": True,
    "parity_alert": True,
    "gpu_alert": True,
    "daily_digest": True,
    "pipeline_staleness": True,
}


def is_valid_discord_webhook_url(value: str | None) -> bool:
    if value is None:
        return False
    candidate = value.strip()
    return any(candidate.startswith(prefix) for prefix in _WEBHOOK_PREFIXES)


def _normalize_notifications(payload: Any) -> dict[str, bool]:
    if not isinstance(payload, dict):
        return dict(_DEFAULT_NOTIFICATIONS)

    return {
        key: bool(payload.get(key, default_value))
        for key, default_value in _DEFAULT_NOTIFICATIONS.items()
    }


def read_discord_runtime_config() -> dict[str, Any]:
    default_payload: dict[str, Any] = {
        "webhook_url": "",
        "notifications": dict(_DEFAULT_NOTIFICATIONS),
    }

    if not DISCORD_RUNTIME_CONFIG_PATH.exists():
        return default_payload

    try:
        payload = json.loads(DISCORD_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_payload

    if not isinstance(payload, dict):
        return default_payload

    webhook_url = payload.get("webhook_url")
    if not isinstance(webhook_url, str):
        webhook_url = ""

    return {
        "webhook_url": webhook_url.strip(),
        "notifications": _normalize_notifications(payload.get("notifications")),
    }


def write_discord_runtime_config(*, webhook_url: str | None, notifications: dict[str, bool]) -> dict[str, Any]:
    normalized_url = (webhook_url or "").strip()
    payload: dict[str, Any] = {
        "webhook_url": normalized_url,
        "notifications": _normalize_notifications(notifications),
    }

    try:
        DISCORD_RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        DISCORD_RUNTIME_CONFIG_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass

    return payload