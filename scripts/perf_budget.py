#!/usr/bin/env python3
"""Performance budget stage for diag_auto (backlog G): measure first, then guard against regressions.

* Every run appends its timings to ``reports/perf_history.jsonl``: per-probe seconds, unit-stage seconds,
  total diag seconds and the L6.8 ``perf_metrics`` (ticket overhead, health p50/p95, ledger read).
* While fewer than 3 runs exist the baseline is "collecting" (never fails). Then
  ``reports/perf_baseline.json`` is frozen as the per-metric median of the first 3 runs.
* A metric regresses when ``current > 1.5 × baseline`` **and** ``current - baseline > tolerance``
  (3 s for second-valued metrics, 25 ms for millisecond metrics). Probes absent from the baseline and the
  diagnostic total (which grows legitimately when probes are added) are reported but not budgeted.
Result: ``reports/perf_latest.json``; diag_auto fails the run on any regression.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
HISTORY = REPORTS / "perf_history.jsonl"
BASELINE = REPORTS / "perf_baseline.json"
RATIO, SECONDS_TOL, MS_TOL, BASELINE_RUNS = 1.5, 3.0, 25.0, 3


def _current(diag_total_seconds: float, unit: dict[str, Any]) -> dict[str, float]:
    probes = json.loads((REPORTS / "muse_diag" / "latest.json").read_text(encoding="utf-8")).get("probes", [])
    metrics: dict[str, float] = {f"probe:{p['id']}:s": float(p.get("seconds") or 0) for p in probes
                                 if p.get("status") == "PASS"}
    for p in probes:
        detail = p.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("perf_metrics"), dict):
            metrics.update({f"metric:{k}": float(v) for k, v in detail["perf_metrics"].items()})
    if unit.get("seconds") is not None:
        metrics["unit_stage:s"] = float(unit["seconds"])
    metrics["diag_total:s"] = float(diag_total_seconds)
    return metrics


def evaluate(diag_total_seconds: float, unit: dict[str, Any]) -> dict[str, Any]:
    current = _current(diag_total_seconds, unit)
    REPORTS.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"at": datetime.now().astimezone().isoformat(), "metrics": current}, ensure_ascii=False) + "\n")
    history = [json.loads(line)["metrics"] for line in HISTORY.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not BASELINE.is_file():
        if len(history) < BASELINE_RUNS:
            report = {"status": "collecting", "runs": len(history), "needed": BASELINE_RUNS, "regressions": [],
                      "current": current}
            (REPORTS / "perf_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return report
        first = history[:BASELINE_RUNS]
        keys = set.intersection(*(set(h) for h in first))
        baseline = {k: round(statistics.median(h[k] for h in first), 4) for k in sorted(keys)}
        BASELINE.write_text(json.dumps({"frozen_at": datetime.now().astimezone().isoformat(), "runs": BASELINE_RUNS,
                                        "metrics": baseline}, ensure_ascii=False, indent=2), encoding="utf-8")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["metrics"]
    regressions = []
    for key, base in baseline.items():
        if key == "diag_total:s" or key not in current:
            continue
        tol = SECONDS_TOL if key.endswith(":s") else MS_TOL
        if current[key] > base * RATIO and current[key] - base > tol:
            regressions.append({"metric": key, "baseline": base, "current": current[key]})
    report = {"status": "regression" if regressions else "within_budget", "regressions": regressions,
              "baseline_metrics": len(baseline), "current": current}
    (REPORTS / "perf_latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
