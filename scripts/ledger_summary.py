#!/usr/bin/env python3
"""Generate the four-state ledger from disk reports only (P0-4) — never hand-written numbers.

Inputs : reports/diag_latest.json (probes + AC verdicts) and reports/pytest_latest.json (unit stage).
Output : docs_quick_state/05_LEDGER_AUTO.md and reports/ledger_latest.json.
States : 🟢 = AC verdict PASS in the latest fresh run · 🔴 = FAIL/ERROR · ⚪ = SKIPPED_ENV (environment not
present, not counted as passed) · 🟡 = listed in the acceptance table but NOT_RUN in the latest report.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build() -> dict:
    diag = json.loads((ROOT / "reports" / "diag_latest.json").read_text(encoding="utf-8"))
    probes_report = json.loads((ROOT / "reports" / "muse_diag" / "latest.json").read_text(encoding="utf-8"))
    unit_path = ROOT / "reports" / "pytest_latest.json"
    unit = json.loads(unit_path.read_text(encoding="utf-8")) if unit_path.is_file() else {}
    table = (ROOT / "docs_quick_state" / "00_ACCEPTANCE_CRITERIA.md").read_text(encoding="utf-8")
    rows = {}
    for line in table.splitlines():
        if line.startswith("| AC-"):
            cells = [c.strip() for c in line.split("|")]
            rows[cells[1]] = cells[2]
    verdicts = probes_report.get("ac_verdicts", {})
    state = {"🟢": [], "🔴": [], "⚪": [], "🟡": []}
    for ac in rows:
        v = verdicts.get(ac, "NOT_RUN")
        key = "🟢" if v == "PASS" else "🔴" if v in ("FAIL", "ERROR") else "⚪" if v == "SKIPPED_ENV" else "🟡"
        state[key].append(ac)
    skips = [{"probe": p["id"], "reason": str(p.get("detail"))[:200]} for p in probes_report.get("probes", [])
             if p.get("status") == "SKIPPED_ENV"]
    ledger = {
        "generated_at": datetime.now().astimezone().isoformat(), "source_reports": {
            "diag_latest": diag.get("finished_at"), "probes_latest": probes_report.get("generated_at"),
            "unit_latest": unit.get("generated_at")},
        "sync_fence_token": diag.get("sync_fence_token"), "overall": diag.get("overall"),
        "acs_total": len(rows), "counts": {k: len(v) for k, v in state.items()}, "by_state": state,
        "probes": diag.get("probe_summary"), "probe_count": diag.get("probe_count"),
        "unit": {k: unit.get(k) for k in ("overall", "runner", "tests_ran", "passed", "unittest_summary")},
        "unit_skips": unit.get("skipped", []), "probe_skips": skips,
    }
    (ROOT / "reports" / "ledger_latest.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# 05 · 四态账本（脚本自动生成，勿手改）", "",
          f"- 生成时间：{ledger['generated_at']}（来源：diag_latest {ledger['source_reports']['diag_latest']}；"
          f"单测 {ledger['source_reports']['unit_latest']}；栅栏 {ledger['sync_fence_token']}）",
          f"- 总体：{ledger['overall']} · 探针 {ledger['probe_count']} 个 {ledger['probes']}",
          f"- 单元测试（{ledger['unit'].get('runner')}）：{ledger['unit'].get('overall')} · ran={ledger['unit'].get('tests_ran')} · {ledger['unit'].get('unittest_summary')}",
          "", f"| 状态 | 数量（共 {len(rows)} 条 AC） | 编号 |", "|---|---:|---|"]
    names = {"🟢": "已运行实测通过", "🔴": "失败/报错", "⚪": "环境缺失 SKIP（不计通过）", "🟡": "未运行"}
    for key in ("🟢", "🔴", "⚪", "🟡"):
        md.append(f"| {key} {names[key]} | {len(state[key])} | {', '.join(state[key]) or '—'} |")
    if skips or ledger["unit_skips"]:
        md += ["", "## SKIP 原因", ""] + [f"- 探针 {s['probe']}：{s['reason']}" for s in skips] + \
              [f"- 单测 {s['test']}：{s['reason']}" for s in ledger["unit_skips"]]
    (ROOT / "docs_quick_state" / "05_LEDGER_AUTO.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return ledger


if __name__ == "__main__":
    print(json.dumps(build()["counts"], ensure_ascii=False))
