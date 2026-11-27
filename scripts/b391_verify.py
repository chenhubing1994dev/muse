"""B391 one-shot acceptance on the real Windows workstation (B391-P2 armoured edition).

Runs, in order, and records every result. The report is rewritten atomically after EVERY step, so even a
crash or a closed window leaves a readable report behind:

* ``reports/b391_acceptance/LATEST_REPORT.json`` / ``LATEST_REPORT.md``  (new, structured)
* ``reports/b391_review/verify_latest.json`` / ``verify_latest.md``     (kept for compatibility)
* ``reports/b391_review/logs/<step>.log``                             (full stdout + stderr per step)

Steps
  0. environment snapshot – Python / Node / npm / FFmpeg / ffprobe / Remotion / CantoTTS / ASR / ComfyUI /
     GPU, console encoding, whether the project path contains spaces. Missing tools are reported as
     missing or unknown; nothing is guessed.
  1. ``compileall`` over src / tests / scripts
  2. targeted B391 Python test modules (one process each → isolated temp dirs, no cross-test file locks)
  3. the full Python suite (``unittest discover``), unless ``--skip-full``
  4. L2.5 front-end ↔ back-end contract + DOM/CSP check (``scripts/b391_l25_contract.py``)
  5. the existing smoke script ``scripts/muse_smoke.py``
  6. UI: ``npm test`` and ``npm run build`` (dist must contain portal.html and os.html)
  7. ``--sample``: one REAL local-motion sample through ``VideoProductBuilder.build_from_source``
     (CantoTTS → Whisper ASR → Remotion → FFmpeg → QA → Product Vault) and the SHA-256 of the MP4
  8. read-only ComfyUI probe on 127.0.0.1:8188 for path B (never approves or dispatches anything)

Never runs git, never commits, never pushes, never deletes; writes to ``data/`` only for the optional
real sample product. All subprocesses use argument lists (``shell=False``) so the space in
``X:\\PATH\\TO\\Muse`` can never split a path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "b391_review"
LOG_DIR = REPORT_DIR / "logs"
ACCEPT_DIR = ROOT / "reports" / "b391_acceptance"

TARGETED = (
    "test_b391_control_center",
    "test_b391_p2_hardening",
    "test_status_api",
    "test_autonomous_runtime",
    "test_video_admission_runtime",
    "test_video_product_api",
    "test_video_product_builder",
    "test_business_opportunities",
    "test_unified_runtime",
    "test_video_task_worker",
    "test_local_dashboard",
    "test_obsidian_vault",
    "test_muse_sidecar",
    "test_muse_sidecar_feedback",
    "test_omega_security_contract",
    "test_xuanshu_symbolic_fallback",
    "test_cyber_cosmos_api",
)

SAMPLE_SCRIPT = (
    # Kept short (~7 s) so it fits the 4 × 3 s teaser clips; longer narration is correctly
    # rejected by _validate_narration_timeline instead of being truncated.
    "呢段係 Muse 喺本機生成嘅樣片。玄學內容只係靈感，唔係事實。"
)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # B391-P2-30: ROOT first so ``from tests.<helper> import …`` works when a test module is run from tests/.
    extra = os.pathsep.join([str(ROOT), str(ROOT / "src"), str(ROOT / "tests")])
    env["PYTHONPATH"] = extra + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    # B391-P2-31: tests talk to 127.0.0.1 servers; a system proxy (e.g. 127.0.0.1:7890) must never sit in between.
    # With NO_PROXY set, urllib uses environment proxy settings only, so loopback traffic is always direct.
    loopback = "127.0.0.1,localhost,::1"
    for key in ("NO_PROXY", "no_proxy"):
        current = env.get(key, "")
        env[key] = loopback + ("," + current if current else "")
    return env


def _short(command: list[str], timeout: int = 20) -> str | None:
    """First line of a version probe, or None when the tool is missing / fails."""
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, cwd=str(ROOT), env=_env(), creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    return text[0][:200] if proc.returncode == 0 and text else None


def _node() -> str | None:
    found = shutil.which("node") or shutil.which("node.exe")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"
    return str(default) if default.is_file() else None


def _npm_command() -> list[str] | None:
    """Prefer ``node npm-cli.js`` (no .cmd quoting rules involved); fall back to npm.cmd."""
    node = _node()
    if node:
        cli = Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if cli.is_file():
            return [node, str(cli)]
    for candidate in ("npm.cmd", "npm"):
        found = shutil.which(candidate)
        if found:
            return [found]
    return None


def _remotion_version() -> str | None:
    base = ROOT / "video-motion" / "node_modules" / "@remotion"
    for name in ("cli", "renderer", "bundler"):
        manifest = base / name / "package.json"
        if manifest.is_file():
            try:
                return f"@remotion/{name} " + str(json.loads(manifest.read_text(encoding="utf-8")).get("version"))
            except (OSError, json.JSONDecodeError):
                return f"@remotion/{name} (version unreadable)"
    return None


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def _gpu_names() -> str | None:
    if shutil.which("nvidia-smi"):
        return _short(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if os.name == "nt" and shutil.which("powershell"):
        return _short(["powershell", "-NoProfile", "-Command",
                       "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '; '"], timeout=30)
    return None


def environment_snapshot() -> dict:
    started = time.monotonic()
    npm = _npm_command()
    node = _node()
    snapshot = {
        "name": "environment_snapshot", "ok": None,
        "python": {"executable": sys.executable, "version": sys.version.split()[0],
                   "implementation": platform.python_implementation()},
        "platform": platform.platform(), "machine": platform.machine(),
        "project_root": str(ROOT), "project_root_has_space": " " in str(ROOT),
        "encoding": {"stdout": getattr(sys.stdout, "encoding", None), "preferred": locale.getpreferredencoding(False),
                     "PYTHONUTF8": os.environ.get("PYTHONUTF8"), "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING")},
        "tools": {
            "node": _short([node, "--version"]) if node else None,
            "npm": _short(npm + ["--version"]) if npm else None,
            "ffmpeg": _short(["ffmpeg", "-hide_banner", "-version"]) if shutil.which("ffmpeg") else None,
            "ffprobe": _short(["ffprobe", "-hide_banner", "-version"]) if shutil.which("ffprobe") else None,
            "remotion": _remotion_version(),
        },
        "models": {
            "canto_tts_manifest": (ROOT / "data" / "models" / "canto-tts-nano" / "browser_poc_manifest.json").is_file(),
            "canto_tts_py314_runtime": (ROOT / ".muse-canto-tts-py314").is_dir(),
            "cantonese_asr_model": (ROOT / "models" / "whisper-small-cantonese" / "cts" / "model.bin").is_file(),
        },
        "services": {"comfyui_8188": _port_open(8188), "lm_studio_1234": _port_open(1234)},
        "gpu": _gpu_names(),
        "video_sources": sorted(p.name for p in (ROOT / "data" / "video_sources").iterdir() if p.is_dir())[:30]
        if (ROOT / "data" / "video_sources").is_dir() else [],
        "seconds": None,
        "note": "None / false = 未检测到（如实记录，不推测）。",
    }
    snapshot["seconds"] = round(time.monotonic() - started, 1)
    return snapshot


def _failure_blocks(output: str, limit: int = 40) -> list[str]:
    """Full unittest FAIL/ERROR blocks (header + traceback) so the root cause is in the report."""
    blocks = re.split(r"\n={60,}\n", "\n" + output)
    found = []
    for block in blocks:
        head = block.lstrip()
        if head.startswith(("FAIL: ", "ERROR: ")):
            found.append(head.split("\n" + "-" * 70 + "\nRan ")[0][:4000])
        if len(found) >= limit:
            break
    return found


def _unittest_summary(output: str) -> dict:
    ran = None
    failing = []
    for line in output.splitlines():
        if line.startswith("Ran ") and " test" in line:
            try:
                ran = int(line.split()[1])
            except (IndexError, ValueError):
                pass
        if line.startswith(("FAIL: ", "ERROR: ")):
            failing.append(line[:200])
    return {"tests_ran": ran, "failing": failing[:60], "failure_details": _failure_blocks(output)}


def _run(name: str, command: list[str], *, cwd: Path = ROOT, timeout: int = 1800) -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{name}.log"
    started = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=str(cwd), env=_env(), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        output = (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = f"TIMEOUT after {timeout}s\n" + str(exc.stdout or "") + "\n" + str(exc.stderr or "")
        code = -1
    except OSError as exc:
        output = f"could not start {command[:2]!r}: {type(exc).__name__}: {exc}"
        code = -2
    log.write_text(output, encoding="utf-8")
    return {"name": name, "ok": code == 0, "exit_code": code, "seconds": round(time.monotonic() - started, 1),
            "command": [str(part) for part in command][:8], "log": str(log.relative_to(ROOT)),
            **_unittest_summary(output), "tail": output.strip().splitlines()[-40:]}


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gpu_precheck() -> dict | None:
    """B391-P2-32: fail fast (instead of a 75 s lease timeout) when the GPU cannot be handed to the encoder.

    Reads the Muse GPU coordination database read-only. A healthy model endpoint that the Muse supervisor did not
    start itself (no managed child) is an external process; by design Muse never stops it, so the render would
    wait for a GPU hand-off that cannot happen.
    """
    import sqlite3
    database = ROOT / "data" / "gpu-resources.sqlite3"
    if not database.is_file():
        return None
    uri = "file:" + database.resolve().as_posix().replace(" ", "%20") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=3)
        try:
            row = connection.execute(
                "SELECT heartbeat_at, action, child_pid, detail FROM gpu_supervisor_state WHERE singleton=1").fetchone()
            lease = connection.execute("SELECT owner_kind FROM gpu_lease WHERE singleton=1 AND expires_at>?",
                                       (time.time(),)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if lease is not None:
        return {"blocked": True, "reason": f"GPU 正被 {lease[0]} 租用中，请等它结束后重跑样片"}
    if row is None:
        return None
    try:
        detail = json.loads(row[3] or "{}")
    except json.JSONDecodeError:
        detail = {}
    age = time.time() - float(row[0])
    if detail.get("model_status") == "healthy" and row[2] is None:
        return {"blocked": True, "supervisor_action": row[1], "supervisor_heartbeat_age_seconds": round(age),
                "reason": ("本地模型服务正在运行，且不是 Muse 模型监督器自己启动的进程（例如 LM Studio :1234）。"
                           "按安全策略 Muse 不会停止外部进程，样片编码拿不到 GPU。"
                           "请先在 LM Studio 里卸载模型（或退出 LM Studio），等约 30 秒后再重跑。")}
    return {"blocked": False, "supervisor_action": row[1], "supervisor_heartbeat_age_seconds": round(age)}


def _supervisor_pid() -> int | None:
    import sqlite3
    database = ROOT / "data" / "gpu-resources.sqlite3"
    if not database.is_file():
        return None
    uri = "file:" + database.resolve().as_posix().replace(" ", "%20") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=3)
        try:
            row = connection.execute("SELECT pid FROM gpu_supervisor_state WHERE singleton=1").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return int(row[0]) if row and isinstance(row[0], int) else None


def _sample(python: str) -> dict:
    precheck = _gpu_precheck()
    if precheck and precheck.get("blocked"):
        return {"name": "real_local_sample", "ok": False, "reason": precheck["reason"], "gpu_precheck": precheck}
    sources = ROOT / "data" / "video_sources"
    candidates = [p.name for p in sorted(sources.iterdir()) if p.is_dir()] if sources.is_dir() else []
    preferred = [n for n in ("muse-prometheus-teaser-v1", "muse-cosmos-film", "muse-prometheus-film-v1") if n in candidates]
    source = (preferred or candidates or [None])[0]
    if source is None:
        return {"name": "real_local_sample", "ok": False, "reason": "data/video_sources 下没有素材文件夹"}
    code = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from muse.video_product_builder import VideoProductBuilder\n"
        f"root=Path({str(ROOT)!r})\n"
        f"result=VideoProductBuilder(root).build_from_source({source!r}, 'B391 本地动效真实样片', "
        f"narration_script={SAMPLE_SCRIPT!r}, motion_profile='brand_scene_transitions', audio_mode='music')\n"
        "keep={k: result.get(k) for k in ('initiative_id','version','version_id','relative_path','status','validated','quality_gate','motion','narration_synthesis','narration_asr','video','files','qa') if k in result}\n"
        "print('B391_SAMPLE_JSON='+json.dumps(keep, ensure_ascii=False, default=str)[:20000])\n"
    )
    record = _run("real_local_sample", [python, "-c", code], timeout=3600)
    record["source"] = source
    for line in reversed((ROOT / record["log"]).read_text(encoding="utf-8").splitlines()):
        if line.startswith("B391_SAMPLE_JSON="):
            try:
                record["product"] = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                record["product"] = {"unparsed": line[:500]}
            break
    product = record.get("product") if isinstance(record.get("product"), dict) else {}
    relative = product.get("relative_path")
    if not relative and product.get("initiative_id"):
        relative = f"data/products/{product['initiative_id']}/{product.get('version_id') or product.get('version')}"
    if relative:
        video = ROOT / Path(str(relative)) / "video.mp4"
        record["video_path"] = str(video.relative_to(ROOT)) if video.is_file() else None
        record["video_sha256"] = _sha256(video)
    if not record["ok"] and "waiting for the Muse model supervisor" in "\n".join(record.get("tail") or []):
        # B391-P2-35: a supervisor started before 2026-11-25 only yields the GPU to ``video_generation`` leases and
        # silently ignores the ``video_encoding`` lease the local sample uses; only restarting it loads current code.
        stale = _gpu_precheck() or {}
        pid = _supervisor_pid()
        record["reason"] = ("模型监督器在租约期内没有让出 GPU。多半是它仍在运行 2026-11-25 之前加载的旧代码"
                            "（旧版只响应 video_generation 租约，不响应本地样片的 video_encoding 租约）。"
                            f"请结束监督器进程{'（PID ' + str(pid) + '）' if pid else ''}后重新登录或启动计划任务 "
                            "MuseLocalModelSupervisor，再重跑。")
        record["gpu_precheck"] = stale
        record["supervisor_pid"] = pid
    if record["ok"] and not record.get("video_sha256"):
        record["ok"] = False
        record["reason"] = "样片进程返回成功，但没有找到 video.mp4，按失败处理"
    return record


def _comfy_probe() -> dict:
    reachable = _port_open(8188)
    return {"name": "path_b_comfyui_probe", "ok": None, "comfyui_reachable": reachable,
            "note": ("ComfyUI 可达：按 docs/B391_L5_PATH_B_RUNBOOK.md 在 Super Agent OS 提单并人工批准后验收路径 B。"
                     if reachable else "ComfyUI 未运行：路径 B 实机待验收（本脚本不会启动它，也不会自动批准）。")}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(5):  # Windows: the previous report may be open in an editor / AV scanner
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            time.sleep(0.4 * (attempt + 1))
    path.write_text(text, encoding="utf-8", newline="\n")


def _render_md(report: dict) -> str:
    lines = [f"# B391 验收报告 — {report['overall']}", "",
             f"- 开始 {report['started']} · 更新 {report['updated']} · Python {report['python']}",
             f"- 当前阶段：{report.get('current_step') or '已结束'}", ""]
    env = report.get("environment") or {}
    if env:
        tools = env.get("tools", {})
        lines += ["## 环境快照", "", "| 项目 | 结果 |", "|---|---|"]
        for key, value in [("项目路径含空格", env.get("project_root_has_space")), ("Python", env.get("python", {}).get("version")),
                           ("Node", tools.get("node")), ("npm", tools.get("npm")), ("FFmpeg", tools.get("ffmpeg")),
                           ("ffprobe", tools.get("ffprobe")), ("Remotion", tools.get("remotion")),
                           ("CantoTTS 模型", env.get("models", {}).get("canto_tts_manifest")),
                           ("粤语 ASR 模型", env.get("models", {}).get("cantonese_asr_model")),
                           ("ComfyUI :8188", env.get("services", {}).get("comfyui_8188")), ("GPU", env.get("gpu")),
                           ("控制台编码", env.get("encoding", {}).get("stdout"))]:
            shown = "是" if value is True else "否" if value is False else ("未检测到/未知" if value in (None, "") else value)
            lines.append(f"| {key} | {shown} |")
        lines.append("")
    lines += ["## 步骤", "", "| 步骤 | 结果 | 测试数 | 用时(s) | 日志 |", "|---|---|---:|---:|---|"]
    for s in report["steps"]:
        flag = "✅" if s.get("ok") else ("ℹ️" if s.get("ok") is None else "❌")
        lines.append(f"| {s['name']} | {flag} | {s.get('tests_ran') or ''} | {s.get('seconds', '')} | "
                     f"{s.get('log', s.get('reason', s.get('note', '')))} |")
    for s in report["steps"]:
        if s.get("video_sha256"):
            lines += ["", f"**样片 SHA-256**：`{s['video_sha256']}`（{s.get('video_path')}）"]
    for s in report["steps"]:
        if s.get("ok") is False:
            lines += ["", f"## ❌ {s['name']}", ""]
            if s.get("reason"):
                lines.append("- 原因：" + str(s["reason"]))
            for item in s.get("failing", [])[:40]:
                lines.append(f"- {item}")
            for block in s.get("failure_details", [])[:5]:
                lines += ["", "```text", block[:3000], "```"]
            if s.get("exception"):
                lines += ["", "```text", s["exception"][:3000], "```"]
            if s.get("tail"):
                lines += ["", "<details><summary>输出末尾 40 行</summary>", "", "```text", *s["tail"], "```", "</details>"]
    lines += ["", "> " + report["honesty_note"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse B391 acceptance (never commits)")
    parser.add_argument("--skip-full", action="store_true")
    parser.add_argument("--sample", action="store_true", help="render one real local-motion sample")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    python = sys.executable
    report = {"started": datetime.now().isoformat(timespec="seconds"), "updated": None, "python": sys.version.split()[0],
              "overall": "RUNNING", "current_step": None, "failed_steps": [], "steps": [], "environment": None,
              "honesty_note": "单元/模拟测试通过 ≠ 实机验收；路径 B 需要 ComfyUI + GPU + 人工审批后另行验收。本脚本从不提交或推送代码。"}

    def flush() -> None:
        report["updated"] = datetime.now().isoformat(timespec="seconds")
        report["failed_steps"] = [s["name"] for s in report["steps"] if s.get("ok") is False]
        text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
        md = _render_md(report)
        for directory, stem in ((ACCEPT_DIR, "LATEST_REPORT"), (REPORT_DIR, "verify_latest")):
            _atomic_write(directory / f"{stem}.json", text)
            _atomic_write(directory / f"{stem}.md", md)

    def step(name: str, action) -> None:
        report["current_step"] = name
        flush()
        started = time.monotonic()
        try:
            record = action()
        except Exception:  # a harness bug must still leave a precise report
            record = {"name": name, "ok": False, "exception": traceback.format_exc()[-6000:]}
        record.setdefault("seconds", round(time.monotonic() - started, 1))
        report["steps"].append(record)
        flag = "PASS" if record.get("ok") else ("INFO" if record.get("ok") is None else "FAIL")
        print(f"[{flag}] {record.get('name', name)} {record.get('tests_ran') or ''} {record.get('seconds', '')}s", flush=True)
        flush()

    def snapshot() -> dict:
        env = environment_snapshot()
        report["environment"] = env
        return env

    step("environment_snapshot", snapshot)
    step("compileall", lambda: _run("compileall", [python, "-m", "compileall", "-q", "src", "tests", "scripts"], timeout=600))
    for module in TARGETED:
        if (ROOT / "tests" / f"{module}.py").is_file():
            step("unit_" + module, lambda m=module: _run("unit_" + m, [python, "-m", "unittest", "-v", m],
                                                       cwd=ROOT / "tests", timeout=1800))
        else:
            step("unit_" + module, lambda m=module: {"name": "unit_" + m, "ok": None, "reason": "test module not present"})
    if not args.skip_full:
        step("full_suite", lambda: _run("full_suite", [python, "-m", "unittest", "discover", "-s", "tests", "-t", "tests"],
                                        timeout=5400))
    if (ROOT / "scripts" / "b391_l25_contract.py").is_file():
        step("l25_contract_dom_csp", lambda: _run("l25_contract_dom_csp", [python, "scripts/b391_l25_contract.py"], timeout=600))
    if (ROOT / "scripts" / "muse_smoke.py").is_file():
        step("smoke", lambda: _run("smoke", [python, "scripts/muse_smoke.py"], timeout=1800))
    npm = _npm_command()
    ui = ROOT / "ui-prototype"
    if npm:
        step("ui_npm_test", lambda: _run("ui_npm_test", npm + ["test"], cwd=ui, timeout=900))

        def build() -> dict:
            record = _run("ui_npm_build", npm + ["run", "build"], cwd=ui, timeout=900)
            record["portal_in_dist"] = (ui / "dist" / "portal.html").is_file()
            record["os_in_dist"] = (ui / "dist" / "os.html").is_file()
            record["ok"] = record["ok"] and record["portal_in_dist"] and record["os_in_dist"]
            return record
        step("ui_npm_build", build)
    else:
        step("ui", lambda: {"name": "ui", "ok": False, "reason": "没有找到 node / npm（请安装 Node.js LTS 后重跑）"})
    if args.sample:
        step("real_local_sample", lambda: _sample(python))
    step("path_b_comfyui_probe", _comfy_probe)

    report["current_step"] = None
    report["overall"] = "PASS" if not [s for s in report["steps"] if s.get("ok") is False] else "FAIL"
    flush()
    print("report:", ACCEPT_DIR / "LATEST_REPORT.md")
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
