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

    report: dict = {}
    fresh = LATEST.exists() and LATEST.stat().st_mtime >= started - 1
    if fresh:
        report = json.loads(LATEST.read_text(encoding="utf-8"))
    probes = report.get("probes", [])
    bad = [{"id": p["id"], "status": p["status"], "name": p["name"], "detail": str(p.get("detail"))[:1500]}
           for p in probes if p.get("status") != "PASS"]
    summary = report.get("summary", {})
    ac_summary = report.get("ac_summary", {})
    ok = (not timed_out and rc == 0 and fresh and summary.get("FAIL", 1) == 0 and summary.get("ERROR", 1) == 0)
    result = {
        "schema": "muse.diag_auto.v1", "started_at": started_wall.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(), "elapsed_seconds": elapsed,
        "exit_code": 0 if ok else (rc or 1), "child_exit_code": rc, "timed_out": timed_out,
        "report_fresh": fresh, "report_generated_at": report.get("generated_at"),
        "probe_count": len(probes), "probe_summary": summary,
        "ac_count": len(report.get("ac_verdicts", {})), "ac_summary": ac_summary,
        "ac_not_pass": {k: v for k, v in report.get("ac_verdicts", {}).items() if v != "PASS"},
        "non_pass_probes": bad, "overall": "PASS" if ok else "FAIL",
    }
    (REPORTS / "diag_latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# Muse 诊断结果（{result['finished_at']}）", "",
          f"- 总体：**{result['overall']}**（退出码 {result['exit_code']}，子进程 {rc}，超时 {timed_out}，报告新鲜 {fresh}）",
          f"- 耗时：{elapsed}s",
          f"- 探针：{len(probes)} 个 · {summary}",
          f"- 验收项：{result['ac_count']} 条 · {ac_summary}", ""]
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
