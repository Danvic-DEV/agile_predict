#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from urllib.request import urlopen


@dataclass
class EndpointCheck:
    name: str
    legacy_url: str
    migrated_url: str


@dataclass
class DataStatsCheck:
    name: str
    legacy_prices_url: str
    migrated_prices_url: str
    migrated_data_stats_url_template: str


def _normalize_payload(payload: list[dict]) -> list[dict]:
    normalized = []
    for item in payload:
        name = item.get("name", "")
        created_at = item.get("created_at", "")
        prices = item.get("prices", [])
        first_time = prices[0].get("date_time") if prices else None
        last_time = prices[-1].get("date_time") if prices else None
        first_pred = round(float(prices[0].get("agile_pred", 0.0)), 4) if prices else None
        last_pred = round(float(prices[-1].get("agile_pred", 0.0)), 4) if prices else None
        normalized.append(
            {
                "name": name,
                "created_at": created_at,
                "prices_len": len(prices),
                "first_time": first_time,
                "last_time": last_time,
                "first_pred": first_pred,
                "last_pred": last_pred,
            }
        )
    return sorted(normalized, key=lambda x: (x["name"], x["created_at"]))


def _prediction_map(payload: list[dict]) -> dict[tuple[str, str, str], float]:
    out: dict[tuple[str, str, str], float] = {}
    for forecast in payload:
        name = str(forecast.get("name", ""))
        created_at = str(forecast.get("created_at", ""))
        for point in forecast.get("prices", []):
            date_time = str(point.get("date_time", ""))
            try:
                pred = float(point.get("agile_pred", 0.0))
            except (TypeError, ValueError):
                continue
            if not isfinite(pred):
                continue
            out[(name, created_at, date_time)] = pred
    return out


def _prediction_error_metrics(legacy_payload: list[dict], migrated_payload: list[dict]) -> dict[str, float | int | None]:
    legacy_map = _prediction_map(legacy_payload)
    migrated_map = _prediction_map(migrated_payload)
    keys = sorted(set(legacy_map).intersection(migrated_map))
    if not keys:
        return {
            "common_points": 0,
            "mean_abs_diff": None,
            "max_abs_diff": None,
        }

    diffs = [abs(legacy_map[k] - migrated_map[k]) for k in keys]
    sorted_diffs = sorted(diffs)
    p95_index = max(0, min(len(sorted_diffs) - 1, int(round(0.95 * (len(sorted_diffs) - 1)))))
    return {
        "common_points": len(diffs),
        "mean_abs_diff": round(sum(diffs) / len(diffs), 6),
        "max_abs_diff": round(max(diffs), 6),
        "p95_abs_diff": round(sorted_diffs[p95_index], 6),
    }


def run_check(check: EndpointCheck, timeout: int = 20, pred_tolerance: float = 0.25) -> dict:
    with urlopen(check.legacy_url, timeout=timeout) as response:
        legacy = json.loads(response.read().decode("utf-8"))

    with urlopen(check.migrated_url, timeout=timeout) as response:
        migrated = json.loads(response.read().decode("utf-8"))

    legacy_norm = _normalize_payload(legacy)
    migrated_norm = _normalize_payload(migrated)
    shape_parity = legacy_norm == migrated_norm
    metrics = _prediction_error_metrics(legacy, migrated)
    max_abs_diff = metrics["max_abs_diff"]
    prediction_parity = max_abs_diff is not None and float(max_abs_diff) <= pred_tolerance
    parity = shape_parity and prediction_parity

    mismatch = None
    if not parity:
        mismatch = {
            "legacy_first": legacy_norm[:1],
            "migrated_first": migrated_norm[:1],
        }

    return {
        "name": check.name,
        "legacy_count": len(legacy),
        "migrated_count": len(migrated),
        "parity": parity,
        "shape_parity": shape_parity,
        "prediction_parity": prediction_parity,
        "prediction_tolerance": pred_tolerance,
        "prediction_metrics": metrics,
        "legacy_preview": legacy_norm[:3],
        "migrated_preview": migrated_norm[:3],
        "mismatch": mismatch,
    }


def _first_forecast(payload: list[dict]) -> dict | None:
    if not payload:
        return None
    return payload[0]


def _legacy_prices_stats(payload: list[dict]) -> dict[str, float | int | str | None]:
    first = _first_forecast(payload)
    if first is None:
        return {"count": 0, "first_date_time": None, "last_date_time": None, "agile_pred_mean": None}

    prices = first.get("prices", [])
    if not prices:
        return {"count": 0, "first_date_time": None, "last_date_time": None, "agile_pred_mean": None}

    preds = [float(p.get("agile_pred", 0.0)) for p in prices]
    return {
        "count": len(prices),
        "first_date_time": prices[0].get("date_time"),
        "last_date_time": prices[-1].get("date_time"),
        "agile_pred_mean": round(sum(preds) / len(preds), 6),
    }


def run_data_stats_check(check: DataStatsCheck, timeout: int = 20) -> dict:
    with urlopen(check.legacy_prices_url, timeout=timeout) as response:
        legacy_prices = json.loads(response.read().decode("utf-8"))

    with urlopen(check.migrated_prices_url, timeout=timeout) as response:
        migrated_prices = json.loads(response.read().decode("utf-8"))

    legacy_first = _first_forecast(legacy_prices)
    migrated_first = _first_forecast(migrated_prices)
    if not legacy_first or not migrated_first:
        return {
            "name": check.name,
            "parity": False,
            "reason": "missing forecast payload",
        }

    migrated_id = migrated_first.get("id")
    if migrated_id is None:
        return {
            "name": check.name,
            "parity": False,
            "reason": "migrated payload missing forecast id",
        }

    url = check.migrated_data_stats_url_template.format(forecast_id=migrated_id)
    with urlopen(url, timeout=timeout) as response:
        migrated_stats = json.loads(response.read().decode("utf-8"))

    legacy_stats = _legacy_prices_stats(legacy_prices)
    count_match = int(legacy_stats["count"] or 0) == int(migrated_stats.get("count", 0) or 0)
    first_match = legacy_stats["first_date_time"] == migrated_stats.get("first_date_time")
    last_match = legacy_stats["last_date_time"] == migrated_stats.get("last_date_time")

    return {
        "name": check.name,
        "parity": bool(count_match and first_match and last_match),
        "legacy_stats": legacy_stats,
        "migrated_stats": migrated_stats,
        "checks": {
            "count_match": count_match,
            "first_date_time_match": first_match,
            "last_date_time_match": last_match,
        },
    }


def evaluate_report(
    results: list[dict],
    data_stats_results: list[dict],
    min_common_points: int,
    max_mean_abs_diff: float,
    max_max_abs_diff: float,
    max_p95_abs_diff: float,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for result in results:
        name = str(result["name"])
        metrics = result.get("prediction_metrics", {})
        common_points = int(metrics.get("common_points", 0) or 0)
        mean_abs_diff = metrics.get("mean_abs_diff")
        max_abs_diff = metrics.get("max_abs_diff")
        p95_abs_diff = metrics.get("p95_abs_diff")

        if not bool(result.get("shape_parity", False)):
            failures.append(f"{name}: shape parity failed")

        if common_points < min_common_points:
            failures.append(
                f"{name}: common points {common_points} below threshold {min_common_points}"
            )

        if mean_abs_diff is None or float(mean_abs_diff) > max_mean_abs_diff:
            failures.append(
                f"{name}: mean abs diff {mean_abs_diff} above threshold {max_mean_abs_diff}"
            )

        if max_abs_diff is None or float(max_abs_diff) > max_max_abs_diff:
            failures.append(
                f"{name}: max abs diff {max_abs_diff} above threshold {max_max_abs_diff}"
            )

        if p95_abs_diff is None or float(p95_abs_diff) > max_p95_abs_diff:
            failures.append(
                f"{name}: p95 abs diff {p95_abs_diff} above threshold {max_p95_abs_diff}"
            )

    for result in data_stats_results:
        if not bool(result.get("parity", False)):
            failures.append(f"{result.get('name', 'data_stats')}: data stats parity failed")

    return len(failures) == 0, failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple API parity check runner")
    parser.add_argument("--legacy-base", required=True)
    parser.add_argument("--migrated-base", required=True)
    parser.add_argument("--pred-tolerance", type=float, default=0.25)
    parser.add_argument("--min-common-points", type=int, default=1)
    parser.add_argument("--max-mean-abs-diff", type=float, default=0.15)
    parser.add_argument("--max-max-abs-diff", type=float, default=0.25)
    parser.add_argument("--max-p95-abs-diff", type=float, default=0.2)
    parser.add_argument("--report-file", default="")
    args = parser.parse_args()

    checks = [
        EndpointCheck(
            name="latest_forecasts",
            legacy_url=f"{args.legacy_base.rstrip('/')}/api/",
            migrated_url=f"{args.migrated_base.rstrip('/')}/api/v1/forecasts/prices?forecast_count=1",
        ),
        EndpointCheck(
            name="all_regions_count2_14d",
            legacy_url=f"{args.legacy_base.rstrip('/')}/api/?forecast_count=2&days=14&high_low=true",
            migrated_url=f"{args.migrated_base.rstrip('/')}/api/v1/forecasts/prices?forecast_count=2&days=14&high_low=true",
        ),
        EndpointCheck(
            name="region_g_forecasts",
            legacy_url=f"{args.legacy_base.rstrip('/')}/api/G/?forecast_count=1&days=7&high_low=true",
            migrated_url=f"{args.migrated_base.rstrip('/')}/api/v1/forecasts/prices?region=G&forecast_count=1&days=7&high_low=true",
        ),
    ]

    data_stats_checks = [
        DataStatsCheck(
            name="region_g_data_stats",
            legacy_prices_url=f"{args.legacy_base.rstrip('/')}/api/G/?forecast_count=1&days=7&high_low=true",
            migrated_prices_url=f"{args.migrated_base.rstrip('/')}/api/v1/forecasts/prices?region=G&forecast_count=1&days=7&high_low=true",
            migrated_data_stats_url_template=f"{args.migrated_base.rstrip('/')}/api/v1/forecasts/{{forecast_id}}/data-stats?limit=336",
        )
    ]

    results = [run_check(c, pred_tolerance=args.pred_tolerance) for c in checks]
    data_stats_results = [run_data_stats_check(c) for c in data_stats_checks]
    all_passed, failures = evaluate_report(
        results,
        data_stats_results,
        min_common_points=args.min_common_points,
        max_mean_abs_diff=args.max_mean_abs_diff,
        max_max_abs_diff=args.max_max_abs_diff,
        max_p95_abs_diff=args.max_p95_abs_diff,
    )

    report: dict[str, object] = {
        "results": results,
        "data_stats_results": data_stats_results,
        "all_passed": all_passed,
        "failures": failures,
        "thresholds": {
            "pred_tolerance": args.pred_tolerance,
            "min_common_points": args.min_common_points,
            "max_mean_abs_diff": args.max_mean_abs_diff,
            "max_max_abs_diff": args.max_max_abs_diff,
            "max_p95_abs_diff": args.max_p95_abs_diff,
        },
    }
    serialized = json.dumps(report, indent=2)
    print(serialized)

    if args.report_file:
        target = Path(args.report_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized + "\n", encoding="utf-8")

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
