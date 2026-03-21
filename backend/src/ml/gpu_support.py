from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import subprocess

import numpy as np
import xgboost as xg


@dataclass(frozen=True)
class GpuProbeResult:
    tested: bool
    compatible: bool
    reason: str | None
    xgboost_version: str
    gpu_name: str | None
    tested_at: str


_CACHE_RESULT: GpuProbeResult | None = None
_CACHE_AT: datetime | None = None


def _detect_gpu_name() -> str | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    line = result.stdout.strip().splitlines()
    return line[0].strip() if line else None


def probe_xgboost_cuda(*, force: bool = False, ttl_seconds: int = 120) -> GpuProbeResult:
    global _CACHE_RESULT, _CACHE_AT

    now = datetime.now(timezone.utc)
    if (
        not force
        and _CACHE_RESULT is not None
        and _CACHE_AT is not None
        and (now - _CACHE_AT) < timedelta(seconds=max(1, ttl_seconds))
    ):
        return _CACHE_RESULT

    gpu_name = _detect_gpu_name()
    try:
        x = np.array(
            [
                [1.0, 2.0],
                [2.0, 3.0],
                [3.0, 4.0],
                [4.0, 5.0],
            ],
            dtype=np.float32,
        )
        y = np.array([1.2, 2.1, 3.1, 4.0], dtype=np.float32)
        model = xg.XGBRegressor(
            objective="reg:squarederror",
            n_estimators=2,
            max_depth=2,
            tree_method="hist",
            device="cuda",
        )
        model.fit(x, y, verbose=False)
        _ = model.get_booster().predict(xg.DMatrix(x))

        result = GpuProbeResult(
            tested=True,
            compatible=True,
            reason=None,
            xgboost_version=xg.__version__,
            gpu_name=gpu_name,
            tested_at=now.isoformat(),
        )
    except Exception as exc:  # noqa: BLE001
        result = GpuProbeResult(
            tested=True,
            compatible=False,
            reason=str(exc),
            xgboost_version=xg.__version__,
            gpu_name=gpu_name,
            tested_at=now.isoformat(),
        )

    _CACHE_RESULT = result
    _CACHE_AT = now
    return result
