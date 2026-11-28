#!/usr/bin/env python3
"""Non-interactive diagnostic runner (no pause, no prompts).

* runs ``scripts/muse_diag.py`` in a child process with an overall timeout (default 1200 s,
  ``MUSE_DIAG_TOTAL_TIMEOUT``); per-probe timeout is enforced inside muse_diag (120 s);
* full child stdout/stderr → ``reports/diag_last_run.log``;
* summary → ``reports/diag_latest.json`` + ``reports/diag_latest.md`` (timestamp, probe and AC
  counts, FAIL / ERROR / SKIP details);
* exit code 0 only when the child exited 0 *and* a fresh latest.json shows 0 FAIL / 0 ERROR.
A timeout or a stale/missing report is a failure, never a pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
LATEST = REPORTS / "muse_diag" / "latest.json"


def main(argv: list[str]) -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    total_timeout = float(os.environ.get("MUSE_DIAG_TOTAL_TIMEOUT", "1200"))
    started_wall = datetime.now().astimezone()
    started = time.time()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "PYTHONPATH": str(ROOT / "src"), "MUSE_DIAG_NONINTERACTIVE": "1"}
    cmd = [sys.executable, str(ROOT / "scripts" / "muse_diag.py"), *argv]
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=total_timeout, env=env, stdin=subprocess.DEVNULL)
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out, rc = True, 124
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    elapsed = round(time.time() - started, 1)
    (REPORTS / "diag_last_run.log").write_text(
        f"# command: {cmd}\n# started: {started_wall.isoformat()}  elapsed: {elapsed}s  rc: {rc}  timed_out: {timed_out}\n"
        f"===== STDOUT =====\n{out}\n===== STDERR =====\n{err}\n", encoding="utf-8")

    # P0-1 unit-test stage + P0-2 sync fence / source manifest (new stages; probe verdicts unchanged).
    sys.path.insert(0, str(ROOT / "scripts"))
    import diag_unit_stage  # noqa: E402  (scripts/diag_unit_stage.py)
    fence = diag_unit_stage.read_fence()
    manifest = diag_unit_stage.source_manifest()
    (REPORTS / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        unit = diag_unit_stage.run_unit_stage()
    except Exception as exc:  # a broken stage is a failure, never a silent pass
        unit = {"overall": "FAIL", "error": f"{type(exc).__name__}: {exc}"[:1000], "tests_ran": 0}
        (REPORTS / "pytest_latest.json").write_text(json.dumps(unit, ensure_ascii=False, indent=2), encoding="utf-8")

    report: dict = {}
    fresh = LATEST.exists() and LATEST.stat().st_mtime >= started - 1
    if fresh:
        report = json.loads(LATEST.read_text(encoding="utf-8"))
    probes = report.get("probes", [])
    bad = [{"id": p["id"], "status": p["status"], "name": p["name"], "detail": str(p.get("detail"))[:1500]}
           for p in probes if p.get("status") != "PASS"]
    summary = report.get("summary", {})
    ac_summary = report.get("ac_summary", {})
    probes_ok = (not timed_out and rc == 0 and fresh and summary.get("FAIL", 1) == 0 and summary.get("ERROR", 1) == 0)
    # G: performance budget (new stage). Collects 3 runs, then fails on >1.5x regressions beyond tolerance.
    try:
        import perf_budget  # noqa: E402  (scripts/perf_budget.py)
        perf = perf_budget.evaluate(elapsed, unit) if fresh else {"status": "skipped_no_fresh_report", "regressions": []}
    except Exception as exc:
        perf = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:500], "regressions": []}
    ok = probes_ok and unit.get("overall") == "PASS" and perf.get("status") in ("collecting", "within_budget")
    result = {
        "schema": "muse.diag_auto.v1", "started_at": started_wall.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(), "elapsed_seconds": elapsed,
        "exit_code": 0 if ok else (rc or 1), "child_exit_code": rc, "timed_out": timed_out,
        "report_fresh": fresh, "report_generated_at": report.get("generated_at"),
        "probe_count": len(probes), "probe_summary": summary,
        "ac_count": len(report.get("ac_verdicts", {})), "ac_summary": ac_summary,
        "ac_not_pass": {k: v for k, v in report.get("ac_verdicts", {}).items() if v != "PASS"},
        "non_pass_probes": bad, "probes_overall": "PASS" if probes_ok else "FAIL",
        "unit_tests": {k: unit.get(k) for k in ("overall", "runner", "tests_ran", "passed", "unittest_summary", "seconds",
                                                "timeouts", "real_data_guard", "error")},
        "unit_tests_failing": (unit.get("failing") or [])[:100],
        "perf_budget": {k: perf.get(k) for k in ("status", "regressions", "runs", "needed", "error")},
        "sync_fence_token": fence, "source_manifest_sha256": manifest["manifest_sha256"],
        "source_file_count": manifest["file_count"],
        "overall": "PASS" if ok else "FAIL",
    }
    (REPORTS / "diag_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # P0-4: the four-state ledger is generated from these reports, never hand-written.
    try:
        import ledger_summary  # noqa: E402  (scripts/ledger_summary.py)
        result["ledger_counts"] = ledger_summary.build()["counts"]
        (REPORTS / "diag_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # a broken ledger generator fails the run instead of hiding
        result["ledger_error"] = f"{type(exc).__name__}: {exc}"[:500]
        result["overall"], result["exit_code"] = "FAIL", result["exit_code"] or 1
        (REPORTS / "diag_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# Muse 诊断结果（{result['finished_at']}）", "",
          f"- 总体：**{result['overall']}**（退出码 {result['exit_code']}，子进程 {rc}，超时 {timed_out}，报告新鲜 {fresh}）",
          f"- 耗时：{elapsed}s",
          f"- 探针：{len(probes)} 个 · {summary}",
          f"- 验收项：{result['ac_count']} 条 · {ac_summary}",
          f"- 单元测试（{unit.get('runner', 'unittest')}）：{unit.get('overall')} · ran={unit.get('tests_ran')} · {unit.get('unittest_summary')}",
          f"- 同步栅栏：{fence} · 源码清单 {manifest['file_count']} 个文件 sha256={manifest['manifest_sha256'][:16]}", ""]
    if unit.get("failing"):
        md += ["## 单元测试失败", ""] + [f"- {t}" for t in unit["failing"][:60]] + [""]
    if bad:
        md += ["## 非通过探针", "", "| 探针 | 状态 | 名称 | 详情 |", "|---|---|---|---|"]
        md += [f"| {b['id']} | {b['status']} | {b['name']} | {b['detail'][:300].replace('|', '/')} |" for b in bad]
    else:
        md.append("全部探针通过。")
    if result["ac_not_pass"]:
        md += ["", "## 未通过验收项", ""] + [f"- {k}: {v}" for k, v in result["ac_not_pass"].items()]
    (REPORTS / "diag_latest.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
