from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypedDict

from src.core.settings import CONFIG_DIR

ML_RUNTIME_CONFIG_PATH = Path(CONFIG_DIR) / "ml-runtime-config.json"
MlWriteMode = Literal["deterministic", "shadow", "ml"]


class MlRuntimeConfig(TypedDict):
    gpu_enabled: bool
    write_mode: MlWriteMode | None


_DEFAULT_CONFIG: MlRuntimeConfig = {
    "gpu_enabled": False,
    "write_mode": None,
}
_ALLOWED_WRITE_MODES: set[str] = {"deterministic", "shadow", "ml"}


def read_ml_runtime_config() -> MlRuntimeConfig:
    if not ML_RUNTIME_CONFIG_PATH.exists():
        return dict(_DEFAULT_CONFIG)

    try:
        payload = json.loads(ML_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT_CONFIG)

    if not isinstance(payload, dict):
        return dict(_DEFAULT_CONFIG)

    write_mode = payload.get("write_mode")
    resolved_write_mode: MlWriteMode | None
    if isinstance(write_mode, str) and write_mode in _ALLOWED_WRITE_MODES:
        resolved_write_mode = write_mode
    else:
        resolved_write_mode = None

    return {
        "gpu_enabled": bool(payload.get("gpu_enabled", _DEFAULT_CONFIG["gpu_enabled"])),
        "write_mode": resolved_write_mode,
    }


def write_ml_runtime_config(*, gpu_enabled: bool | None = None, write_mode: MlWriteMode | None = None) -> MlRuntimeConfig:
    current = read_ml_runtime_config()

    next_gpu_enabled = current["gpu_enabled"] if gpu_enabled is None else bool(gpu_enabled)
    next_write_mode = current["write_mode"] if write_mode is None else write_mode

    payload: MlRuntimeConfig = {
        "gpu_enabled": next_gpu_enabled,
        "write_mode": next_write_mode,
    }
    try:
        ML_RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ML_RUNTIME_CONFIG_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return payload
