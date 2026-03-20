from __future__ import annotations

import json
from pathlib import Path

from src.core.settings import CONFIG_DIR

ML_RUNTIME_CONFIG_PATH = Path(CONFIG_DIR) / "ml-runtime-config.json"
_DEFAULT_CONFIG: dict[str, bool] = {"gpu_enabled": False}


def read_ml_runtime_config() -> dict[str, bool]:
    if not ML_RUNTIME_CONFIG_PATH.exists():
        return dict(_DEFAULT_CONFIG)

    try:
        payload = json.loads(ML_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT_CONFIG)

    if not isinstance(payload, dict):
        return dict(_DEFAULT_CONFIG)

    return {
        "gpu_enabled": bool(payload.get("gpu_enabled", _DEFAULT_CONFIG["gpu_enabled"])),
    }


def write_ml_runtime_config(*, gpu_enabled: bool) -> dict[str, bool]:
    payload = {"gpu_enabled": bool(gpu_enabled)}
    try:
        ML_RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ML_RUNTIME_CONFIG_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return payload
