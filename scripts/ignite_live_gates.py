"""Muse Human Ignition Kit — the creator's single entry point for every live gate.

Default (and ``--check-only``) is strictly read-only: it never writes a database,
never starts ComfyUI / GPU jobs / daemons, never publishes and never spends money.
It only reports what is ready and prints the exact command for each live step.

Interactive ignition (``--step N``) requires typing the confirmation phrase shown
on screen; each step then runs exactly one tested, documented command:

  1  inspiration_refs: read-only audit -> optional backed-up production repair (gate G6)
  2  Reviewer Ed25519 key ceremony -> trust store + SHA-256 pin (gate G9, creator-held keys)
  3  ComfyUI 127.0.0.1:8188 probe + VRAM pre-flight -> 1-shot Wan 2.1 acceptance clip (gates G3/G2)
  4  Web Portal / Status API local launch commands + LAN pairing check (gate G7 stays closed)
  5  Supervised activation of selected Commander routines (gate G4)

See docs/HUMAN_IGNITION_RUNBOOK.md for the narrative guide.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
COMFY_URL = "http://127.0.0.1:8188"
MIN_FREE_VRAM_GIB = 7.7
REQUIRED_FILES = (
    "docs/AGENT_BUILD_CHARTER.md", "docs/BUILD_LEDGER_OMEGA.md", "docs/HUMAN_IGNITION_RUNBOOK.md",
    "scripts/audit_and_repair_inspiration_refs.py", "scripts/provision_reviewer_keys.py",
    "src/muse/reviewer_attestation.py", "src/muse/signed_review.py", "src/muse/inspiration_ref_repair.py",
    "src/muse/revenue_ledger.py",
)


def _result(step: str, name: str, status: str, detail: str, command: str = "") -> dict:
    return {"step": step, "name": name, "status": status, "detail": detail, "ignite_command": command}


def _python() -> str:
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def check_files() -> dict:
    missing = [item for item in REQUIRED_FILES if not (ROOT / item).is_file()]
    return _result("0", "构建产物与文档", "PASS" if not missing else "BLOCKED",
                   "全部存在" if not missing else "缺失: " + ", ".join(missing))


def check_python() -> dict:
    ok = sys.version_info >= (3, 11)
    return _result("0", "Python 版本", "PASS" if ok else "BLOCKED", sys.version.split()[0],
                   "" if ok else r"powershell -File scripts\Repair-MuseVenv.ps1")


def check_inspiration_refs() -> dict:
    database = ROOT / "data" / "initiatives" / "muse.sqlite3"
    if not database.is_file():
        return _result("1", "inspiration_refs 审计", "INFO", "生产 initiatives 库不存在")
    uri = "file:" + quote(database.resolve().as_posix(), safe="/:") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            temp = 0
            for (refs_json,) in connection.execute("SELECT inspiration_refs_json FROM creator_initiatives"):
                try:
                    refs = json.loads(refs_json or "[]")
                except json.JSONDecodeError:
                    continue
                temp += sum(1 for ref in refs if isinstance(ref, str) and "\\appdata\\local\\temp\\" in ref.lower())
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        return _result("1", "inspiration_refs 审计", "WARN", f"只读打开失败: {exc}")
    command = f'"{_python()}" scripts\\audit_and_repair_inspiration_refs.py'
    if temp == 0:
        return _result("1", "inspiration_refs 审计", "PASS", "无临时路径引用", command)
    return _result("1", "inspiration_refs 审计", "WARN",
                   f"{temp} 条引用指向已删除的临时目录（只读统计，未修改）", command)


def check_reviewer_trust() -> dict:
    sys.path.insert(0, str(SRC))
    try:
        from muse.reviewer_attestation import ReviewAttestationError, active_backend, load_trust_store_from_environment
    except Exception as exc:  # pragma: no cover - import failure is itself the finding
        return _result("2", "Reviewer 密钥信任库", "BLOCKED", f"模块导入失败: {exc}")
    command = (f'"{_python()}" scripts\\provision_reviewer_keys.py keygen --purpose production --key-id rk-creator-1 '
               '--reviewer-id creator --private-out <离线盘>\\rk-creator-1.json --trust-store <离线盘>\\reviewer-trust-store.json')
    try:
        registry = load_trust_store_from_environment()
    except ReviewAttestationError as exc:
        return _result("2", "Reviewer 密钥信任库", "BLOCKED", f"信任库配置无效: {exc}", command)
    production = [key for key, item in registry.items() if item["purpose"] == "production" and item["status"] == "trusted"]
    if not production:
        return _result("2", "Reviewer 密钥信任库", "BLOCKED",
                       f"未配置生产 Reviewer 公钥（后端 {active_backend()}）；真实独立审查暂不可用", command)
    return _result("2", "Reviewer 密钥信任库", "PASS",
                   f"{len(production)} 把生产公钥受信（后端 {active_backend()}）；Worker 用 --signed-review-kind 启用签名审查门",
                   "muse-signed-review list  →  export  →  muse-reviewer-attest sign  →  muse-signed-review submit")


def probe_comfyui(timeout: float = 1.5) -> dict:
    """Read-only GET of /system_stats on loopback. Never starts anything."""
    try:
        with urllib.request.urlopen(COMFY_URL + "/system_stats", timeout=timeout) as response:
            if response.status != 200:
                return {"live": False, "error": f"HTTP {response.status}"}
            payload = json.loads(response.read(256 * 1024).decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"live": False, "error": str(exc)[:200]}
    devices = payload.get("devices") if isinstance(payload, dict) else None
    best = None
    for device in devices if isinstance(devices, list) else []:
        if isinstance(device, dict) and isinstance(device.get("vram_free"), (int, float)):
            free = device["vram_free"] / 1024 ** 3
            best = free if best is None else max(best, free)
    return {"live": True, "vram_free_gib": None if best is None else round(best, 2)}


def check_gpu() -> dict:
    probe = probe_comfyui()
    smoke = f'"{_python()}" scripts\\run_wan_gpu_acceptance.py'
    if not probe["live"]:
        return _result("3", "ComfyUI + GPU 预检", "BLOCKED",
                       "127.0.0.1:8188 未运行（本脚本不会启动它；G3）", r"scripts\muse-comfyui-start.cmd  然后  " + smoke)
    free = probe.get("vram_free_gib")
    if free is None:
        return _result("3", "ComfyUI + GPU 预检", "WARN", "ComfyUI 在线，但 VRAM 空闲量未知 → fail-closed", smoke)
    if free < MIN_FREE_VRAM_GIB:
        return _result("3", "ComfyUI + GPU 预检", "BLOCKED", f"空闲 VRAM {free} GiB < {MIN_FREE_VRAM_GIB} GiB", smoke)
    return _result("3", "ComfyUI + GPU 预检", "PASS", f"ComfyUI 在线，空闲 VRAM {free} GiB", smoke)


def check_portal() -> dict:
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    lan = sorted(address for address in addresses if not address.startswith("127."))
    return _result("4", "Web Portal / Status API", "INFO",
                   "本机局域网地址: " + (", ".join(lan) or "未检测到") + "；公网 DNS/防火墙保持关闭（G7）",
                   "muse-status-api  /  muse-web-portal  （启动参数见 docs/WEB_PORTAL_SERVER_DEPLOYMENT_V1.md）")


def check_runtime() -> dict:
    return _result("5", "Commander 例程 / 自治运行时", "INFO",
                   "本脚本从不启动后台守护进程；默认保持 stopped（G4）",
                   "muse-routines --help  →  人工逐个启用所选例程；muse-autonomous --help 查看 pause/stop/kill-switch")


def check_revenue() -> dict:
    database = ROOT / "data" / "commerce" / "revenue-ledger.sqlite3"
    if not database.is_file():
        return _result("C", "净收入账本", "INFO", "已结算收入 US$0.00（尚无账本；未导入任何结算回执）",
                       f'"{_python()}" -m muse.revenue_ledger summary')
    uri = "file:" + quote(database.resolve().as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        settled = connection.execute(
            "SELECT COALESCE(SUM(amount_minor),0) FROM ledger_entries WHERE kind='settlement' AND currency='USD'"
        ).fetchone()[0]
    finally:
        connection.close()
    return _result("C", "净收入账本", "INFO", f"已结算收入 US${int(settled) // 100:,}.{int(settled) % 100:02d}（仅计入已验证回执）")


def run_checks() -> list[dict]:
    return [check() for check in (check_files, check_python, check_inspiration_refs, check_reviewer_trust,
                                  check_gpu, check_portal, check_runtime, check_revenue)]


def _confirm(phrase: str) -> bool:
    print(f"\n要继续，请完整输入: {phrase}")
    try:
        return input("> ").strip() == phrase
    except EOFError:
        return False


def ignite(step: int) -> int:
    import subprocess  # only interactive ignition ever spawns a process

    py = _python()
    env = dict(os.environ, PYTHONPATH=str(SRC))
    if step == 1:
        subprocess.run([py, str(ROOT / "scripts" / "audit_and_repair_inspiration_refs.py")], cwd=ROOT, env=env, check=False)
        print("\n审计只读完成。若要修复生产库：先停止所有 Muse 进程，再复制上面输出的 source_sha256_before。")
        digest = input("粘贴生产库当前 SHA-256（留空则退出）> ").strip()
        if not digest or not _confirm("IGNITE 1 REPAIR"):
            print("已取消，未做任何修改。")
            return 0
        return subprocess.run([py, str(ROOT / "scripts" / "audit_and_repair_inspiration_refs.py"),
                               "--apply-on-db", str(ROOT / "data" / "initiatives" / "muse.sqlite3"),
                               "--allow-production", "--confirm-sha256", digest], cwd=ROOT, env=env).returncode
    if step == 2:
        key_dir = input("离线密钥目录（必须在 Muse 仓库之外，例如 E:\\MuseKeys）> ").strip()
        key_id = input("key_id（例如 rk-creator-2026）> ").strip()
        if not key_dir or not key_id or not _confirm("IGNITE 2 KEYGEN"):
            print("已取消。")
            return 0
        folder = Path(key_dir)
        return subprocess.run([py, str(ROOT / "scripts" / "provision_reviewer_keys.py"), "keygen", "--purpose",
                               "production", "--key-id", key_id, "--reviewer-id", "creator",
                               "--private-out", str(folder / f"{key_id}.json"),
                               "--trust-store", str(folder / "reviewer-trust-store.json")], cwd=ROOT, env=env).returncode
    if step == 3:
        result = check_gpu()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "PASS":
            print("GPU 预检未通过：请先运行 scripts\\muse-comfyui-start.cmd 并关闭占用显存的程序。")
            return 3
        if not _confirm("IGNITE 3 GPU"):
            return 0
        return subprocess.run([py, str(ROOT / "scripts" / "run_wan_gpu_acceptance.py")], cwd=ROOT, env=env).returncode
    if step in (4, 5):
        print(json.dumps(check_portal() if step == 4 else check_runtime(), ensure_ascii=False, indent=2))
        print("此步骤需要你在终端中亲自运行上面的命令（本脚本不会代为启动长驻服务）。")
        return 0
    print("未知步骤；可选 1-5。")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse Human Ignition Kit (read-only by default)")
    parser.add_argument("--check-only", action="store_true", help="read-only readiness report (default)")
    parser.add_argument("--step", type=int, choices=[1, 2, 3, 4, 5], help="interactive ignition of one live gate")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if args.step and not args.check_only:
        return ignite(args.step)
    results = run_checks()
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(f"[{item['status']:<7}] 步骤{item['step']} {item['name']}: {item['detail']}")
            if item["ignite_command"]:
                print(f"           ↳ {item['ignite_command']}")
    blocked_core = [item for item in results if item["step"] == "0" and item["status"] == "BLOCKED"]
    return 1 if blocked_core else 0


if __name__ == "__main__":
    raise SystemExit(main())
