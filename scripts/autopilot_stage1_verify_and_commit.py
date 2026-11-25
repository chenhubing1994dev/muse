"""Stage-1 autopilot: verify everything on the real PC and save the core code (double-click target).

Run by ``1_双击一键验收并保存核心代码.bat``. Every step is logged; nothing is pushed to any
remote, nothing is published, no money is spent, no data is deleted.

Steps
  1. Environment probe (Python version, git, node/npm availability).
  2. Consistent backup of every production SQLite database under data/ (SQLite backup API,
     read-only source) into .muse-backups/autopilot-<stamp>/ with a SHA-256 manifest.
  3. inspiration_refs: read-only audit, then backup-verified non-destructive repair of the
     production initiatives DB (skipped automatically if Muse is still writing to it).
  4. Full Python unittest suite (tests/), plus the B391-B398 suites reported separately.
  5. UI: ``npm test`` and ``npm run build`` in ui-prototype/ when npm is available.
  6. Human Ignition Kit read-only check.
  7. Git: switch to agent/omega-build-20261125 (never main), stage the exact batch file
     list, commit locally. Never pushes.
  8. Writes reports/autopilot/latest.json + latest.md (the next AI session reads these).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "agent/omega-build-20261125"
STAMP = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
REPORT_DIR = ROOT / "reports" / "autopilot" / STAMP
BATCH_FILES = [
    "docs/AGENT_BUILD_CHARTER.md", "docs/BUILD_LEDGER_OMEGA.md", "docs/HUMAN_IGNITION_RUNBOOK.md",
    "docs/PROGRESS_B391_OMEGA_REVIEW_REPAIR_LEDGER.md", "docs/PROGRESS_B393_B398_OMEGA.md",
    "docs/guides/REVIEWER_ATTESTATION.md", "docs/guides/INSPIRATION_REF_REPAIR.md",
    "docs/guides/REVENUE_LEDGER.md", "docs/guides/REVIEW_DESK.md", "docs/guides/UNIT_ECONOMICS_QUOTES.md",
    "QUICK_STATE.md", "pyproject.toml", "1_双击一键验收并保存核心代码.bat",
    "src/muse/reviewer_attestation.py", "src/muse/signed_review.py", "src/muse/review_desk.py",
    "src/muse/task_queue.py", "src/muse/worker.py", "src/muse/status_api.py",
    "src/muse/inspiration_ref_repair.py", "src/muse/revenue_ledger.py",
    "src/muse/unit_economics.py", "src/muse/quote_ingest.py", "src/muse/opportunity_radar",
    "scripts/audit_and_repair_inspiration_refs.py", "scripts/provision_reviewer_keys.py",
    "scripts/ignite_live_gates.py", "scripts/autopilot_stage1_verify_and_commit.py",
    "tests/test_reviewer_attestation.py", "tests/test_signed_review_gate.py", "tests/test_review_desk.py",
    "tests/test_inspiration_ref_repair.py", "tests/test_revenue_ledger.py",
    "tests/test_opportunity_radar_repair.py", "tests/test_unit_economics_quote_ingest.py",
    "ui-prototype/review-desk.html", "ui-prototype/review-desk.js", "ui-prototype/review-desk.css",
    "ui-prototype/vite.config.js",
    # ---- B394-B398 (Omega cycle 5) ----
    ".gitignore", "src/muse/evolution_executor.py", "src/muse/autonomous_runtime.py",
    "src/muse/evolution_guard.py", "src/muse/autonomy_safety.py", "src/muse/plugin_capability_broker.py",
    "src/muse/consciousness_core.py", "src/muse/memory_lifecycle.py", "src/muse/research_closed_loop.py",
    "src/muse/xuanshu_fusion.py", "src/muse/feedback_bridge.py", "src/muse/cinematic_factory.py",
    "src/muse/omega_api.py", "src/muse/security_guards.py", "src/muse/island_registry.py",
    "tests/test_evolution_guard.py", "tests/test_consciousness_memory_lifecycle.py",
    "tests/test_research_xuanshu_feedback.py", "tests/test_cinematic_factory.py",
    "tests/test_omega_security_contract.py",
    "ui-prototype/src/App.svelte", "ui-prototype/omega-common.js", "ui-prototype/omega.css",
    "ui-prototype/unit-economics.html", "ui-prototype/unit-economics.js",
    "ui-prototype/consciousness-map.html", "ui-prototype/consciousness-map.js",
    "ui-prototype/xuanshu-lab.html", "ui-prototype/xuanshu-lab.js",
    "ui-prototype/evolution-lab.html", "ui-prototype/evolution-lab.js",
    "ui-prototype/research-bridge.html", "ui-prototype/research-bridge.js",
    "ui-prototype/video-factory.html", "ui-prototype/video-factory.js",
    "docs/PROGRESS_B394_B398_OMEGA.md",
]
NEW_SUITES = [
    "tests.test_reviewer_attestation", "tests.test_signed_review_gate", "tests.test_review_desk",
    "tests.test_inspiration_ref_repair", "tests.test_revenue_ledger", "tests.test_opportunity_radar_repair",
    "tests.test_unit_economics_quote_ingest",
    "tests.test_evolution_guard", "tests.test_consciousness_memory_lifecycle", "tests.test_research_xuanshu_feedback",
    "tests.test_cinematic_factory", "tests.test_omega_security_contract", "tests.test_evolution_executor",
]
SANDBOX_LIMITED_SUITES = ["tests.test_task_queue", "tests.test_worker", "tests.test_review_gate"]

GREEN, RED, YELLOW, CYAN, RESET = "\x1b[92m", "\x1b[91m", "\x1b[93m", "\x1b[96m", "\x1b[0m"
report: dict = {"stamp": STAMP, "root": str(ROOT), "steps": {}}


def say(text: str, color: str = "") -> None:
    print(f"{color}{text}{RESET if color else ''}", flush=True)


def run(command: list[str], *, cwd: Path = ROOT, timeout: int = 600, env: dict | None = None) -> dict:
    started = time.time()
    try:
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout)
        return {"returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr,
                "seconds": round(time.time() - started, 1)}
    except FileNotFoundError:
        return {"returncode": None, "stdout": "", "stderr": "command not found", "seconds": 0}
    except subprocess.TimeoutExpired as exc:
        return {"returncode": None, "stdout": str(exc.stdout or ""), "stderr": f"timeout after {timeout}s",
                "seconds": timeout}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def python_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def step_environment() -> None:
    git = run(["git", "--version"])
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    report["steps"]["environment"] = {
        "python": sys.version.split()[0], "python_executable": sys.executable,
        "python_ok": sys.version_info >= (3, 11), "git": git["stdout"].strip() or None,
        "npm": npm,
    }
    say(f"[1/7] 环境：Python {sys.version.split()[0]} · git {'可用' if git['returncode'] == 0 else '不可用'} · npm {'可用' if npm else '不可用'}", CYAN)


def step_backup() -> None:
    target = ROOT / ".muse-backups" / f"autopilot-{STAMP}"
    target.mkdir(parents=True, exist_ok=True)
    manifest, failures = [], []
    for database in sorted((ROOT / "data").rglob("*.sqlite3")):
        relative = database.relative_to(ROOT)
        if "handoff" in relative.parts or ".muse-backups" in relative.parts:
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            source = sqlite3.connect("file:" + quote(database.resolve().as_posix(), safe="/:") + "?mode=ro",
                                     uri=True, timeout=10)
            try:
                writer = sqlite3.connect(destination)
                try:
                    source.backup(writer)
                finally:
                    writer.close()
            finally:
                source.close()
            check = sqlite3.connect(destination)
            try:
                integrity = check.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                check.close()
            manifest.append({"database": relative.as_posix(), "sha256": sha256(destination), "quick_check": integrity})
        except sqlite3.DatabaseError as exc:
            failures.append({"database": relative.as_posix(), "error": str(exc)[:200]})
    (target / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report["steps"]["backup"] = {"directory": str(target), "databases": len(manifest), "failures": failures}
    say(f"[2/7] 已备份 {len(manifest)} 个生产数据库 → {target.relative_to(ROOT)}"
        + (f"（{len(failures)} 个失败）" if failures else ""), GREEN if not failures else YELLOW)


def step_inspiration_refs() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from muse.inspiration_ref_repair import RepairRefused, apply_repair, audit
    production = ROOT / "data" / "initiatives" / "muse.sqlite3"
    reports_dir = ROOT / "data" / "research" / "reports"
    if not production.is_file():
        report["steps"]["inspiration_refs"] = {"status": "no_database"}
        say("[3/7] inspiration_refs：生产库不存在，跳过", YELLOW)
        return
    result = audit(production, reports_dir=reports_dir)
    temp_refs = result["summary"].get("temp_refs", 0)
    outcome = {"audit_temp_refs": temp_refs, "audit_integrity": result["integrity_check"]}
    if temp_refs == 0:
        outcome["status"] = "clean"
        say("[3/7] inspiration_refs：没有临时路径引用，无需修复", GREEN)
    else:
        try:
            repaired = apply_repair(production, reports_dir=reports_dir, production_database=production,
                                    allow_production=True, confirm_sha256=sha256(production),
                                    backup_dir=ROOT / ".muse-backups" / f"autopilot-{STAMP}" / "ref-repair")
            outcome.update({"status": "repaired", "rows_rewritten": repaired["rows_rewritten"],
                            "backup_path": repaired["backup_path"], "backup_sha256": repaired["backup_sha256"],
                            "integrity_check_after": repaired["integrity_check_after"],
                            "rollback_command": repaired["rollback_command"]})
            say(f"[3/7] inspiration_refs：已无损修复 {repaired['rows_rewritten']} 行（原值已存档，可一键回滚）", GREEN)
        except RepairRefused as exc:
            outcome.update({"status": "skipped", "reason": str(exc)})
            say(f"[3/7] inspiration_refs：本次跳过修复（{exc}）。请先关闭 Muse 再双击本脚本。", YELLOW)
    report["steps"]["inspiration_refs"] = outcome


def _parse_unittest(text: str) -> dict:
    ran = re.findall(r"Ran (\d+) tests? in", text)
    failed = re.search(r"FAILED \(([^)]*)\)", text)
    names = re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", text, re.M)
    return {"ran": int(ran[-1]) if ran else 0, "ok": bool(re.search(r"^OK\b", text, re.M)) and not failed,
            "summary": failed.group(0) if failed else ("OK" if re.search(r"^OK\b", text, re.M) else "no summary"),
            "failing": [f"{module} :: {name}" for name, module in names][:200]}


def step_tests() -> bool:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    env = python_env()
    say("[4/7] 正在真机运行全仓单元测试（可能需要几分钟）…", CYAN)
    full = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], timeout=3600, env=env)
    (REPORT_DIR / "unittest-full.log").write_text(full["stdout"] + "\n" + full["stderr"], encoding="utf-8")
    parsed_full = _parse_unittest(full["stderr"] + "\n" + full["stdout"])
    targeted = {}
    for group, suites in (("omega_new", NEW_SUITES), ("sandbox_limited", SANDBOX_LIMITED_SUITES)):
        result = run([sys.executable, "-m", "unittest", *suites], timeout=1800, env=env)
        (REPORT_DIR / f"unittest-{group}.log").write_text(result["stdout"] + "\n" + result["stderr"], encoding="utf-8")
        targeted[group] = _parse_unittest(result["stderr"] + "\n" + result["stdout"])
    compile_result = run([sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"], timeout=600, env=env)
    report["steps"]["tests"] = {"full": parsed_full, **targeted,
                                "compileall_ok": compile_result["returncode"] == 0,
                                "full_seconds": full["seconds"]}
    color = GREEN if parsed_full["ok"] else RED
    say(f"      全仓：运行 {parsed_full['ran']} 个测试 → {parsed_full['summary']}（{full['seconds']} 秒）", color)
    for group, label in (("omega_new", "Omega 新增"), ("sandbox_limited", "沙箱受限的队列/Worker/审查")):
        item = targeted[group]
        say(f"      {label}：{item['ran']} 个 → {item['summary']}", GREEN if item["ok"] else RED)
    say(f"      语法编译：{'全部通过' if compile_result['returncode'] == 0 else '有错误，见日志'}",
        GREEN if compile_result["returncode"] == 0 else RED)
    return parsed_full["ok"]


def step_ui() -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    ui = ROOT / "ui-prototype"
    if not npm or not (ui / "node_modules").is_dir():
        report["steps"]["ui"] = {"status": "skipped", "reason": "npm or node_modules not available"}
        say("[5/7] 界面测试：未检测到 npm / node_modules，跳过（审查台页面已直接放入 dist，可正常使用）", YELLOW)
        return
    test = run([npm, "test"], cwd=ui, timeout=900)
    build = run([npm, "run", "build"], cwd=ui, timeout=900)
    (REPORT_DIR / "ui.log").write_text(test["stdout"] + test["stderr"] + "\n----\n" + build["stdout"] + build["stderr"],
                                       encoding="utf-8")
    desk_built = (ui / "dist" / "review-desk.html").is_file()
    report["steps"]["ui"] = {"test_ok": test["returncode"] == 0, "build_ok": build["returncode"] == 0,
                             "review_desk_in_dist": desk_built}
    say(f"[5/7] 界面：测试{'通过' if test['returncode'] == 0 else '失败'} · 构建{'通过' if build['returncode'] == 0 else '失败'}"
        f" · 审查台{'已打包' if desk_built else '缺失'}",
        GREEN if test["returncode"] == 0 and build["returncode"] == 0 and desk_built else RED)


def step_ignition() -> None:
    result = run([sys.executable, str(ROOT / "scripts" / "ignite_live_gates.py"), "--check-only", "--json"],
                 env=python_env(), timeout=120)
    try:
        checks = json.loads(result["stdout"])
    except ValueError:
        checks = [{"status": "ERROR", "detail": result["stderr"][:300]}]
    report["steps"]["ignition_check"] = checks
    say(f"[6/7] 实机点火体检：{sum(1 for item in checks if item.get('status') == 'PASS')} 项通过，"
        f"{sum(1 for item in checks if item.get('status') == 'BLOCKED')} 项等待你实机点火", CYAN)


def step_git(tests_ok: bool) -> None:
    head = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if head["returncode"] != 0:
        report["steps"]["git"] = {"status": "git_unavailable"}
        say("[7/7] Git：不可用，跳过保存", YELLOW)
        return
    current = head["stdout"].strip()
    if current != BRANCH:
        exists = run(["git", "rev-parse", "--verify", "--quiet", BRANCH])["returncode"] == 0
        switched = run(["git", "switch", BRANCH] if exists else ["git", "switch", "-c", BRANCH])
        if switched["returncode"] != 0:
            report["steps"]["git"] = {"status": "switch_failed", "error": switched["stderr"][-400:]}
            say("[7/7] Git：无法切换到 Omega 分支（工作区与分支冲突），未提交；详情见报告", RED)
            return
    present = [item for item in BATCH_FILES if (ROOT / item).exists()]
    add = run(["git", "add", "--", *present])
    staged = run(["git", "diff", "--cached", "--name-only"])["stdout"].split()
    leaked = [name for name in staged if re.search(r"(^|/)\.env($|\.)|\.sqlite3?$|config/muse\.toml$|__pycache__|\.pyc$", name)]
    if leaked:
        run(["git", "reset", "-q", "--", *leaked])
        staged = [name for name in staged if name not in leaked]
    if add["returncode"] != 0 or not staged:
        report["steps"]["git"] = {"status": "nothing_to_commit" if add["returncode"] == 0 else "add_failed",
                                  "error": add["stderr"][-300:]}
        say("[7/7] Git：没有需要保存的新改动", GREEN if add["returncode"] == 0 else RED)
        return
    full = report["steps"].get("tests", {}).get("full", {})
    message = (f"Omega B391-B398: signed review/evolution gates, safety controller, consciousness core, "
               f"memory lifecycle, research loop, xuanshu, feedback bridge, film factory, omega pages\n\n"
               f"autopilot {STAMP}: unittest ran={full.get('ran')} result={full.get('summary')}; "
               f"files={len(staged)}; verified={'yes' if tests_ok else 'NO - see reports/autopilot'}")
    commit = run(["git", "commit", "-q", "-m", message])
    sha = run(["git", "rev-parse", "--short", "HEAD"])["stdout"].strip()
    report["steps"]["git"] = {"status": "committed" if commit["returncode"] == 0 else "commit_failed",
                              "branch": BRANCH, "commit": sha, "files": staged, "pushed": False,
                              "excluded_sensitive": leaked, "error": commit["stderr"][-300:] if commit["returncode"] else ""}
    if commit["returncode"] == 0:
        say(f"[7/7] Git：已在本机分支 {BRANCH} 保存 {len(staged)} 个文件（提交 {sha}，未上传到任何远端）", GREEN)
    else:
        say("[7/7] Git：提交失败，详情见报告", RED)


def write_report() -> None:
    latest = ROOT / "reports" / "autopilot"
    latest.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    (REPORT_DIR / "report.json").write_text(text, encoding="utf-8")
    (latest / "latest.json").write_text(text, encoding="utf-8")
    tests = report["steps"].get("tests", {})
    lines = [f"# Muse 一键验收报告 {STAMP}", ""]
    for key, value in report["steps"].items():
        lines.append(f"## {key}")
        lines.append("```json")
        lines.append(json.dumps(value, ensure_ascii=False, indent=2, default=str)[:6000])
        lines.append("```")
    (latest / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok = tests.get("full", {}).get("ok")
    say("")
    say("══════════════════════════════════════════════", GREEN if ok else YELLOW)
    say("  Muse 一键验收完成" + ("：全部测试通过 ✔" if ok else "：有测试未通过，已保存详细日志"), GREEN if ok else YELLOW)
    say(f"  报告：reports\\autopilot\\latest.md", GREEN if ok else YELLOW)
    say("  下一次和 AWS Quick 对话时说“看一下验收报告”即可，它会自动读取并修复。", GREEN if ok else YELLOW)
    say("══════════════════════════════════════════════", GREEN if ok else YELLOW)


def main() -> int:
    os.system("")  # enable ANSI colours in the Windows console
    os.chdir(ROOT)
    say("Muse 一键验收并保存核心代码 · 只在本机运行 · 不上传 · 不发布 · 不花钱", CYAN)
    tests_ok = False
    for name, step in (("environment", step_environment), ("backup", step_backup),
                       ("inspiration_refs", step_inspiration_refs)):
        try:
            step()
        except Exception as exc:  # keep going; every failure is reported
            report["steps"][name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:400]}
            say(f"[{name}] 出错：{type(exc).__name__}: {exc}", RED)
    try:
        tests_ok = step_tests()
    except Exception as exc:
        report["steps"]["tests"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:400]}
        say(f"[tests] 出错：{exc}", RED)
    for name, step in (("ui", step_ui), ("ignition_check", step_ignition)):
        try:
            step()
        except Exception as exc:
            report["steps"][name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:400]}
    try:
        step_git(tests_ok)
    except Exception as exc:
        report["steps"]["git"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:400]}
    write_report()
    return 0 if tests_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
