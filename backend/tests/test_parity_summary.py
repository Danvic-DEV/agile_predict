from __future__ import annotations

import json
import os

from src.api.v1.routes import diagnostics as diagnostics_routes


def test_parity_summary_missing_report(monkeypatch, tmp_path) -> None:
    missing_report = tmp_path / "missing-report.json"
    monkeypatch.setattr(diagnostics_routes, "PARITY_REPORT_PATH", missing_report)

    summary = diagnostics_routes.parity_last_summary()

    assert summary.report_available is False
    assert summary.all_passed is None
    assert summary.failure_count is None
    assert summary.report_updated_at is None
    assert summary.report_path is None
    assert summary.report_sha256 is None


def test_parity_summary_reads_latest_report(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "last-report.json"
    report_payload = {
        "results": [
            {
                "name": "latest_forecasts",
                "prediction_metrics": {
                    "common_points": 6,
                    "mean_abs_diff": 0.11,
                    "max_abs_diff": 0.24,
                    "p95_abs_diff": 0.2,
                },
            },
            {
                "name": "region_g_forecasts",
                "prediction_metrics": {
                    "common_points": 3,
                    "mean_abs_diff": 0.04,
                    "max_abs_diff": 0.09,
                    "p95_abs_diff": 0.08,
                },
            },
        ],
        "data_stats_results": [{"name": "region_g_data_stats", "parity": True}],
        "all_passed": False,
        "failures": [
            "latest_forecasts: max abs diff 0.24 above threshold 0.2",
            "latest_forecasts: p95 abs diff 0.2 above threshold 0.18",
        ],
        "thresholds": {
            "pred_tolerance": 0.25,
            "min_common_points": 1,
            "max_mean_abs_diff": 0.15,
            "max_max_abs_diff": 0.2,
            "max_p95_abs_diff": 0.18,
        },
    }
    report_path.write_text(json.dumps(report_payload), encoding="utf-8")
    monkeypatch.setattr(diagnostics_routes, "PARITY_REPORT_PATH", report_path)

    summary = diagnostics_routes.parity_last_summary()

    assert summary.report_available is True
    assert summary.all_passed is False
    assert summary.failure_count == 2
    assert summary.endpoint_count == 2
    assert summary.data_stats_check_count == 1
    assert summary.min_common_points == 3
    assert summary.worst_mean_abs_diff == 0.11
    assert summary.worst_max_abs_diff == 0.24
    assert summary.worst_p95_abs_diff == 0.2
    assert summary.thresholds is not None
    assert summary.thresholds["max_max_abs_diff"] == 0.2
    assert summary.report_updated_at is not None
    assert summary.report_path is not None
    assert summary.report_path.endswith("last-report.json")
    assert summary.report_sha256 is not None
    assert len(summary.report_sha256) == 64


def test_parity_history_returns_recent_reports(monkeypatch, tmp_path) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    first_payload = {
        "results": [{"prediction_metrics": {"common_points": 4, "mean_abs_diff": 0.02, "max_abs_diff": 0.1, "p95_abs_diff": 0.08}}],
        "data_stats_results": [],
        "all_passed": True,
        "failures": [],
        "thresholds": {"max_max_abs_diff": 0.25},
    }
    second_payload = {
        "results": [{"prediction_metrics": {"common_points": 2, "mean_abs_diff": 0.2, "max_abs_diff": 0.3, "p95_abs_diff": 0.25}}],
        "data_stats_results": [],
        "all_passed": False,
        "failures": ["example failure"],
        "thresholds": {"max_max_abs_diff": 0.25},
    }

    older = history_dir / "report-older.json"
    newer = history_dir / "report-newer.json"
    older.write_text(json.dumps(first_payload), encoding="utf-8")
    newer.write_text(json.dumps(second_payload), encoding="utf-8")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    monkeypatch.setattr(diagnostics_routes, "PARITY_HISTORY_DIR", history_dir)

    result = diagnostics_routes.parity_history(limit=5)

    assert len(result.items) == 2
    assert result.items[0].all_passed is False
    assert result.items[1].all_passed is True
    assert all(item.report_sha256 is not None for item in result.items)


def test_parity_history_filters_status_and_since(monkeypatch, tmp_path) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    pass_payload = {
        "results": [{"prediction_metrics": {"common_points": 4, "mean_abs_diff": 0.02, "max_abs_diff": 0.1, "p95_abs_diff": 0.08}}],
        "data_stats_results": [],
        "all_passed": True,
        "failures": [],
        "thresholds": {"max_max_abs_diff": 0.25},
    }
    fail_payload = {
        "results": [{"prediction_metrics": {"common_points": 2, "mean_abs_diff": 0.2, "max_abs_diff": 0.3, "p95_abs_diff": 0.25}}],
        "data_stats_results": [],
        "all_passed": False,
        "failures": ["example failure"],
        "thresholds": {"max_max_abs_diff": 0.25},
    }

    older = history_dir / "report-pass.json"
    newer = history_dir / "report-fail.json"
    older.write_text(json.dumps(pass_payload), encoding="utf-8")
    newer.write_text(json.dumps(fail_payload), encoding="utf-8")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    monkeypatch.setattr(diagnostics_routes, "PARITY_HISTORY_DIR", history_dir)

    failed_only = diagnostics_routes.parity_history(limit=10, status="fail")
    assert len(failed_only.items) == 1
    assert failed_only.items[0].all_passed is False

    since_filtered = diagnostics_routes.parity_history(limit=10, since="1970-01-01T00:25:00Z")
    assert len(since_filtered.items) == 1
    assert since_filtered.items[0].all_passed is False


def test_parity_history_supports_offset_pagination(monkeypatch, tmp_path) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    for idx in range(3):
        payload = {
            "results": [
                {
                    "prediction_metrics": {
                        "common_points": 1,
                        "mean_abs_diff": 0.01 * (idx + 1),
                        "max_abs_diff": 0.02 * (idx + 1),
                        "p95_abs_diff": 0.015 * (idx + 1),
                    }
                }
            ],
            "data_stats_results": [],
            "all_passed": idx % 2 == 0,
            "failures": [] if idx % 2 == 0 else ["failed"],
            "thresholds": {"max_max_abs_diff": 0.25},
        }
        path = history_dir / f"report-{idx}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(path, (1000 + idx, 1000 + idx))

    monkeypatch.setattr(diagnostics_routes, "PARITY_HISTORY_DIR", history_dir)

    page = diagnostics_routes.parity_history(limit=2, offset=1)

    assert page.limit == 2
    assert page.offset == 1
    assert page.total == 3
    assert page.returned == 2
    assert len(page.items) == 2
