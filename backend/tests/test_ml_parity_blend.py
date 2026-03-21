from __future__ import annotations

import pandas as pd

from src.ml.parity.day_ahead_xgb import _apply_legacy_scale_blend


def test_apply_legacy_scale_blend_pins_known_window_then_reverts_to_model() -> None:
    idx = pd.date_range("2026-03-17T00:00:00Z", periods=4, freq="30min", tz="UTC")
    preds = pd.Series([100.0, 101.0, 102.0, 103.0], index=idx)
    lows = pd.Series([95.0, 96.0, 97.0, 98.0], index=idx)
    highs = pd.Series([105.0, 106.0, 107.0, 108.0], index=idx)

    ref = pd.Series([80.0, 81.0], index=idx[:2])

    blended_pred, blended_low, blended_high = _apply_legacy_scale_blend(
        preds=preds,
        lows=lows,
        highs=highs,
        reference_day_ahead=ref,
    )

    # Known window should be pinned to reference with +/-1 shifts.
    assert blended_pred.iloc[0] == 80.0
    assert blended_low.iloc[0] == 79.0
    assert blended_high.iloc[0] == 81.0

    assert blended_pred.iloc[1] == 81.0
    assert blended_low.iloc[1] == 80.0
    assert blended_high.iloc[1] == 82.0

    # Beyond the known window, retain model output unchanged.
    assert blended_pred.iloc[2] == 102.0
    assert blended_low.iloc[2] == 97.0
    assert blended_high.iloc[2] == 107.0


def test_apply_legacy_scale_blend_uses_bridge_window_with_shift_5() -> None:
    idx = pd.date_range("2026-03-17T00:00:00Z", periods=6, freq="30min", tz="UTC")
    preds = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0], index=idx)
    lows = pd.Series([95.0, 96.0, 97.0, 98.0, 99.0, 100.0], index=idx)
    highs = pd.Series([105.0, 106.0, 107.0, 108.0, 109.0, 110.0], index=idx)

    ref = pd.Series([80.0, 81.0], index=idx[:2])
    bridge = pd.Series([83.0, 84.0], index=idx[2:4])

    blended_pred, blended_low, blended_high = _apply_legacy_scale_blend(
        preds=preds,
        lows=lows,
        highs=highs,
        reference_day_ahead=ref,
        bridge_day_ahead=bridge,
    )

    # Bridge window should pin prediction and apply +/-5 legacy shift.
    assert blended_pred.iloc[2] == 83.0
    assert blended_low.iloc[2] == 78.0
    assert blended_high.iloc[2] == 88.0

    assert blended_pred.iloc[3] == 84.0
    assert blended_low.iloc[3] == 79.0
    assert blended_high.iloc[3] == 89.0


def test_apply_legacy_scale_blend_preserves_model_when_reference_index_misaligned() -> None:
    preds_idx = pd.date_range("2026-03-17T22:51:04Z", periods=4, freq="30min", tz="UTC")
    ref_idx = pd.date_range("2026-03-17T22:30:00Z", periods=4, freq="30min", tz="UTC")

    preds = pd.Series([120.0, 121.0, 122.0, 123.0], index=preds_idx)
    lows = pd.Series([110.0, 111.0, 112.0, 113.0], index=preds_idx)
    highs = pd.Series([130.0, 131.0, 132.0, 133.0], index=preds_idx)
    ref = pd.Series([80.0, 81.0, 82.0, 83.0], index=ref_idx)

    blended_pred, blended_low, blended_high = _apply_legacy_scale_blend(
        preds=preds,
        lows=lows,
        highs=highs,
        reference_day_ahead=ref,
    )

    assert float(blended_pred.min()) > 0.0
    assert blended_pred.tolist() == preds.tolist()
    assert blended_low.tolist() == lows.tolist()
    assert blended_high.tolist() == highs.tolist()
