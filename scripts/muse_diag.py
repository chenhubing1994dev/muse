#!/usr/bin/env python3
"""Muse layered diagnostic & acceptance engine (L1–L7).

Run:  run_diag.bat   (or)   python "scripts\\muse_diag.py" [--keep] [--only L1,L4]
Output: reports/muse_diag/latest.json · latest.log · history/<stamp>.json

Every probe is isolated with ``try/except BaseException``; one failure never stops the
rest. Probes write only inside a throw-away run directory whose path deliberately contains
spaces and CJK characters. Nothing is published, uploaded, paid for or deleted outside it.
Status vocabulary: PASS · FAIL · ERROR · SKIPPED_ENV.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
# MUSE_DIAG_SRC_ROOT lets the L7 mutation probe run probes against a mutated copy of src/.
SRC = Path(os.environ.get("MUSE_DIAG_SRC_ROOT") or (ROOT / "src")).resolve()
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
REPORT_DIR = Path(os.environ.get("MUSE_DIAG_REPORT_DIR") or (ROOT / "reports" / "muse_diag")).resolve()
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build",
             "target", ".muse-hf-cache", ".muse-canto-wheelhouse", ".muse-canto-tts-py314", ".muse-test-deps",
             ".qw-backups", ".muse-backups", ".muse-safety-backups", "third_party", "external", "RuiC-oc-ferry",
             "dsh-our-free-model", "开箱即用安装包", "models", ".tools", "out", "outputs", "tmp"}
PASS, FAIL, ERROR, SKIP = "PASS", "FAIL", "ERROR", "SKIPPED_ENV"


class Skip(Exception):
    """Probe cannot run on this machine (missing binary/service/config)."""


class Check(Exception):
    """Assertion failure inside a probe."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise Check(message)


PROBES: list[tuple[str, str, str, tuple[str, ...], Callable[["Ctx"], Any]]] = []


def probe(pid: str, layer: str, name: str, acs: tuple[str, ...]):
    def wrap(fn):
        PROBES.append((pid, layer, name, acs, fn))
        return fn
    return wrap


class Ctx:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.facts: dict[str, Any] = {}

    def sub(self, name: str) -> Path:
        p = self.run_dir / name
        p.mkdir(parents=True, exist_ok=True)
        return p


def table_counts(db: Path) -> dict[str, int]:
    if not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {n: con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


def state_diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {k: after.get(k, 0) - before.get(k, 0) for k in sorted(set(before) | set(after))
            if after.get(k, 0) != before.get(k, 0)}


def iter_files(base: Path, suffixes: tuple[str, ...], max_bytes: int = 1_500_000):
    for dirpath, dirnames, filenames in os.walk(base):
        # Skip any embedded virtualenv (pyvenv.cfg) and vendored site-packages: third-party code
        # (e.g. joblib's deliberately non-UTF-8 fixtures) is not Muse source.
        if "pyvenv.cfg" in filenames:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".muse-")
                       and d not in ("site-packages", "Lib") and not d.endswith(("-venv", "_venv"))
                       and ".legacy-broken-" not in d]
        for f in filenames:
            if f.endswith(suffixes):
                p = Path(dirpath) / f
                try:
                    if p.stat().st_size <= max_bytes:
                        yield p
                except OSError:
                    continue


# =============================== L1 · static ===============================
@probe("L1.1", "L1", "全仓 Python AST 语法", ("AC-001",))
def l1_syntax(ctx: Ctx):
    bad, count = [], 0
    for p in iter_files(ROOT, (".py",)):
        count += 1
        try:
            ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            bad.append(f"{p.relative_to(ROOT)}: {type(exc).__name__}: {exc}"[:300])
    ctx.facts["py_files"] = count
    require(not bad, f"{len(bad)} files fail to parse: {bad[:15]}")
    return {"files": count}


class _TagBalance(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "param"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            self.stack.pop()
        else:
            self.errors.append(f"stray </{tag}>")


@probe("L1.2", "L1", "配置/前端文件格式", ("AC-002",))
def l1_formats(ctx: Ctx):
    import tomllib
    bad, counts = [], {"json": 0, "toml": 0, "html": 0, "js": 0}
    for base in (ROOT / "config", ROOT / "web-portal"):
        if not base.is_dir():
            continue
        for p in iter_files(base, (".json", ".toml", ".html", ".js", ".webmanifest")):
            if p.name in ("muse.toml", "model-runtime.json") or "opencode.local" in p.name:
                continue  # may hold secrets: never read
            try:
                text = p.read_text(encoding="utf-8")
                if p.suffix in (".json", ".webmanifest"):
                    json.loads(text); counts["json"] += 1
                elif p.suffix == ".toml":
                    tomllib.loads(text); counts["toml"] += 1
                elif p.suffix == ".html":
                    parser = _TagBalance(); parser.feed(text)
                    if parser.errors[:1] or "</html>" not in text.casefold():
                        bad.append(f"{p.name}: {parser.errors[:3] or 'missing </html>'}")
                    counts["html"] += 1
                elif p.suffix == ".js":
                    counts["js"] += 1
                    node = shutil.which("node")
                    if node:
                        r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=30)
                        if r.returncode != 0:
                            bad.append(f"{p.name}: {r.stderr.strip()[:200]}")
            except (ValueError, UnicodeDecodeError, OSError, subprocess.SubprocessError) as exc:
                bad.append(f"{p.relative_to(ROOT)}: {type(exc).__name__}: {exc}"[:240])
    require(not bad, f"format errors: {bad[:10]}")
    return {**counts, "node_check": bool(shutil.which("node"))}


def _empty_value(v: ast.AST) -> bool:
    if isinstance(v, (ast.List, ast.Tuple, ast.Set)) and not v.elts:
        return True
    if isinstance(v, ast.Dict) and not v.keys:
        return True
    if isinstance(v, ast.Constant) and v.value in (None, "", 0, False):
        return True
    return isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in ("dict", "list", "set") and not v.args


@probe("L1.3", "L1", "核心目录静默吞错扫描（pass / 返回空值 / continue）", ("AC-003",))
def l1_swallow(ctx: Ctx):
    hits = []
    for p in iter_files(SRC / "muse", (".py",)):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or len(node.body) != 1:
                continue
            kind = ast.unparse(node.type) if node.type else "<bare>"
            if kind not in ("<bare>", "Exception", "BaseException"):
                continue
            body = node.body[0]
            silent = (isinstance(body, (ast.Pass, ast.Continue))
                      or (isinstance(body, ast.Return) and (body.value is None or _empty_value(body.value)))
                      or (isinstance(body, ast.Assign) and _empty_value(body.value)))
            if silent:
                hits.append(f"{p.relative_to(ROOT)}:{node.lineno} except {kind}: {ast.unparse(body)[:40]}")
    require(not hits, f"{len(hits)} silent swallows: {hits[:12]}")
    return {"silent_swallows": 0}


def _duplicate_defs(body: list, where: str, rel: str, out: list[str]) -> None:
    exempt = (".setter", ".deleter", "overload", ".register")
    seen: dict[str, int] = {}
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if not any(d.endswith(exempt) or "overload" in d for d in decorators):
                if node.name in seen:
                    out.append(f"{rel}:{node.lineno} {where}.{node.name} shadows line {seen[node.name]}")
                seen[node.name] = node.lineno
            if isinstance(node, ast.ClassDef):
                _duplicate_defs(node.body, node.name, rel, out)


@probe("L1.7", "L1", "代码健康：src/muse+scripts+tests 全部 .py 在“警告即错误”下编译通过，同作用域重复 def/class 为 0", ("AC-104",))
def l1_code_health(ctx: Ctx):
    import warnings
    files = [p for top in (SRC / "muse", ROOT / "scripts", ROOT / "tests") for p in iter_files(top, (".py",))]
    compile_errors: list[str] = []
    duplicates: list[str] = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.as_posix()
        text = p.read_text(encoding="utf-8-sig")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            try:
                compile(text, str(p), "exec", dont_inherit=True)
            except (SyntaxError, Warning) as exc:
                compile_errors.append(f"{rel}: {type(exc).__name__}: {str(exc)[:160]}")
                continue
        _duplicate_defs(ast.parse(text).body, "<module>", rel, duplicates)
    require(len(files) >= 300, f"code health scanned too few files: {len(files)}")
    require(not compile_errors, f"{len(compile_errors)} files fail compile with warnings as errors: {compile_errors[:12]}")
    require(not duplicates, f"{len(duplicates)} duplicate definitions in one scope: {duplicates[:12]}")
    return {"files": len(files), "compile_errors": 0, "duplicate_definitions": 0}


@probe("L1.4", "L1", "批处理脚本空格路径/编码规范", ("AC-004",))
def l1_bat(ctx: Ctx):
    issues, checked = [], []
    for name in ("run_diag.bat", "start_muse.bat"):
        p = ROOT / name
        if not p.exists():
            issues.append(f"{name} missing"); continue
        text = p.read_text(encoding="utf-8", errors="replace")
        checked.append(name)
        if "chcp 65001" not in text:
            issues.append(f"{name}: no chcp 65001")
        if 'cd /d "%~dp0"' not in text:
            issues.append(f'{name}: no cd /d "%~dp0"')
        if "PYTHONIOENCODING=utf-8" not in text:
            issues.append(f"{name}: no PYTHONIOENCODING")
    require(not issues, "; ".join(issues))
    return {"checked": checked}


@probe("L1.5", "L1", "认知标签治理：全仓私有标签必须映射到八类且不得升级为证据", ("AC-047",))
def l1_epistemic_governance(ctx: Ctx):
    from muse.epistemics import LEGACY_TAG_MAP, NON_LABEL_LITERALS, EPISTEMIC_TAGS, canonical_tag, legacy_map_violations
    keys = {"epistemic_status", "epistemic_label", "epistemic", "epistemic_type", "evidence_kind", "epistemic_role"}
    found: dict[str, set[str]] = {}
    for p in iter_files(SRC / "muse", (".py",)):
        if p.name == "epistemics.py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            literals: list[ast.AST] = []
            if isinstance(node, ast.Dict):
                literals = [v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and k.value in keys]
            elif isinstance(node, ast.Compare) and "epistemic" in ast.unparse(node.left):
                literals = list(node.comparators)
            elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and "EPISTEMIC" in node.targets[0].id:
                literals = [node.value]
            for lit in literals:
                for c in ast.walk(lit):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        found.setdefault(c.value, set()).add(p.name)
    unmapped = sorted(v for v in found if v not in LEGACY_TAG_MAP and v not in NON_LABEL_LITERALS and v not in EPISTEMIC_TAGS
                      and v not in ("unverified_hypothesis",))
    violations = legacy_map_violations()
    require(not unmapped, f"unmapped private epistemic labels: {[(v, sorted(found[v])[:2]) for v in unmapped[:15]]}")
    require(not violations, f"illegal legacy mappings: {violations}")
    require(canonical_tag("reported") == "rumor" and canonical_tag("symbolic_hypothesis_only") == "symbolic_inspiration", "map broken")
    return {"literals": len(found), "mapped": len(LEGACY_TAG_MAP)}


@probe("L1.6", "L1", "红线静态审计（警报线，非沙箱）：shell=True / pip·npm install / git push / 非本机网络 / Muse 外写删 / User Track 写入点", ("AC-096",))
def l1_redline_audit(ctx: Ctx):
    import re as _re
    from muse import redline_policy as pol
    url_re = _re.compile(r"^https?://(?!127\.0\.0\.1|localhost|\[::1\])\S+")
    drive_re = _re.compile(r"^(?:[A-Za-z]:[\\/]|/(?:Users|home|etc|var|tmp)/)")
    muse_root = str(ROOT).replace("\\", "/").casefold()
    write_attrs = {"write_text", "write_bytes", "unlink", "rmdir", "touch", "rename", "replace"}
    write_funcs = {"shutil.rmtree", "shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.move", "shutil.copytree",
                   "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.makedirs", "os.replace", "os.rename"}
    sources = [("src/muse/" + p.relative_to(SRC / "muse").as_posix(), p) for p in (SRC / "muse").rglob("*.py")
               if "__pycache__" not in p.parts]
    for top in ("scripts", "tests"):
        sources += [(p.relative_to(ROOT).as_posix(), p) for p in iter_files(ROOT / top, (".py",))]
    hits: dict[str, list[str]] = {k: [] for k in ("shell_true", "package_install", "git_push", "network",
                                                  "outside_write", "user_track_write", "candidate_approve")}
    for rel, path in sources:
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError):
            continue  # L1.1 owns syntax/encoding failures
        is_src = rel.startswith("src/")
        uses_net = bool(_re.search(r"\burlopen\(|\bRequest\(|http\.client|create_connection\(|build_opener\(", text))
        external_url = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and url_re.match(node.value):
                external_url = True
            if not isinstance(node, ast.Call):
                if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                    vals = [e.value for e in node.elts[:6] if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    if "install" in vals and ("pip" in vals or "npm" in vals) and rel not in pol.PACKAGE_INSTALL_ALLOWLIST:
                        hits["package_install"].append(f"{rel}:{node.lineno}")
                    if vals[:1] == ["git"] and "push" in vals:
                        hits["git_push"].append(f"{rel}:{node.lineno}")
                continue
            for kw in node.keywords:
                if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    hits["shell_true"].append(f"{rel}:{node.lineno}")
            func = ast.unparse(node.func)
            attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            is_write = attr in write_attrs or func in write_funcs or (
                func == "open" and any(isinstance(a, ast.Constant) and isinstance(a.value, str) and _re.search(r"[wax+]", a.value)
                                       for a in node.args[1:2]))
            if is_write:
                for const in ast.walk(node):
                    if (isinstance(const, ast.Constant) and isinstance(const.value, str) and drive_re.match(const.value)
                            and not const.value.replace("\\", "/").casefold().startswith(muse_root)
                            and const.value not in pol.OUTSIDE_WRITE_ALLOWLIST):
                        hits["outside_write"].append(f"{rel}:{node.lineno}:{const.value[:60]}")
            if is_src and attr in ("add", "propose", "revise"):
                track = [k for k in node.keywords if k.arg == "track"]
                if track and isinstance(track[0].value, ast.Constant) and track[0].value.value == "user" \
                        and rel not in pol.USER_TRACK_WRITERS:
                    hits["user_track_write"].append(f"{rel}:{node.lineno}")
            if is_src and attr in ("remember_user_statement",) and rel not in pol.USER_TRACK_WRITERS \
                    and not rel.endswith(("memory_cli.py",)):
                hits["user_track_write"].append(f"{rel}:{node.lineno}:remember_user_statement")
            if is_src and attr == "approve_candidate" and rel not in pol.CANDIDATE_APPROVERS:
                hits["candidate_approve"].append(f"{rel}:{node.lineno}")
        if is_src and uses_net and external_url and rel not in pol.NETWORK_ALLOWLIST:
            hits["network"].append(rel)
    stale = [k for k in pol.NETWORK_ALLOWLIST if not (SRC / "muse" / k[len("src/muse/"):]).is_file()]
    found = {k: v for k, v in hits.items() if v}
    require(not found, f"red-line hits outside the allowlists: {found}")
    require(not stale, f"stale network allowlist entries: {stale}")
    return {"files_scanned": len(sources), "note": "static alarm line, not a sandbox",
            "allowlisted": {"package_install": len(pol.PACKAGE_INSTALL_ALLOWLIST), "network": len(pol.NETWORK_ALLOWLIST),
                            "user_track_writers": len(pol.USER_TRACK_WRITERS), "candidate_approvers": len(pol.CANDIDATE_APPROVERS)}}


# =============================== L2 · imports ===============================
@probe("L2.1", "L2", "src/muse 全模块动态导入", ("AC-005", "AC-006"))
def l2_imports(ctx: Ctx):
    import importlib
    failures, slow, timings = {}, {}, {}
    names = sorted({p.relative_to(SRC).with_suffix("").as_posix().replace("/", ".")
                    for p in (SRC / "muse").rglob("*.py") if "__pycache__" not in p.parts})
    for name in names:
        mod = name[:-9] if name.endswith(".__init__") else name
        started = time.perf_counter()
        try:
            importlib.import_module(mod)
        except BaseException as exc:  # SystemExit/KeyboardInterrupt from module top-level too
            failures[mod] = f"{type(exc).__name__}: {exc}"[:300]
        elapsed = time.perf_counter() - started
        timings[mod] = round(elapsed, 3)
        if elapsed > 8:
            slow[mod] = round(elapsed, 2)
    ctx.facts["import_failures"] = failures
    require(not failures and not slow, f"import failures={len(failures)} {dict(list(failures.items())[:12])}; slow={slow}")
    return {"modules": len(names), "slowest": sorted(timings.items(), key=lambda kv: -kv[1])[:5]}


# =============================== L3 · storage / memory ===============================
@probe("L3.1", "L3", "空格+中文路径 UTF-8 与 SQLite 往返", ("AC-007",))
def l3_paths(ctx: Ctx):
    d = ctx.sub("空格 路径 测试 dir")
    sample = "Muse 知识之火 🔥 𠀋 é ß — \u2028行"
    f = d / "utf8 样本.md"
    f.write_text(sample, encoding="utf-8")
    require(f.read_text(encoding="utf-8") == sample, "file UTF-8 round trip mismatch")
    db = d / "中文 库.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(v TEXT)"); con.execute("INSERT INTO t VALUES (?)", (sample,)); con.commit()
    got = con.execute("SELECT v FROM t").fetchone()[0]; con.close()
    require(got == sample, "sqlite UTF-8 round trip mismatch")
    r = subprocess.run([sys.executable, "-c", "import sys;print(sys.argv[1])", str(f)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=30,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    require(r.stdout.strip() == str(f), f"subprocess argv with spaces mangled: {r.stdout!r} {r.stderr[-200:]!r}")
    from muse.config import load_config
    example = ROOT / "config" / "muse.example.toml"
    cfg = load_config(example) if example.exists() else None
    return {"path": str(d), "config_example_loaded": cfg is not None}


@probe("L3.2", "L3", "二次失败结构化落账 error_journal", ("AC-008",))
def l3_journal(ctx: Ctx):
    os.environ["MUSE_ERROR_JOURNAL"] = str(ctx.sub("journal") / "suppressed.jsonl")
    from muse.error_journal import read_journal_tail, record_suppressed_error, suppressed_error_stats
    before = suppressed_error_stats()["total"]
    record_suppressed_error("diag.probe", RuntimeError("probe secondary failure 二次失败"), k="v")
    stats = suppressed_error_stats()
    tail = read_journal_tail(5)
    require(stats["total"] == before + 1, "counter did not increase")
    require(tail and tail[-1]["component"] == "diag.probe" and "二次失败" in tail[-1]["error"], "journal line missing")
    return {"journal": os.environ["MUSE_ERROR_JOURNAL"], "write_failures": stats["journal_write_failures"]}


@probe("L3.3", "L3", "双轨记忆越权防污染", ("AC-010", "AC-011", "AC-012"))
def l3_dual_track(ctx: Ctx):
    from muse.dual_track_memory import DualTrackMemory
    from muse.epistemics import UserTrackContamination
    db = ctx.sub("memory") / "muse memory.sqlite3"
    with DualTrackMemory(db) as mem:
        before = table_counts(db)
        user = mem.remember_user_statement("我偏好在晚上做深度研究", source="user:chat", tag="fact", kind="preference")
        agent = mem.record_agent_experience("FFmpeg 在空格路径下需列表参数", source="run:diag", tag="experience", kind="case")
        try:
            mem.write("用户可能喜欢紫色", track="user", source="model", tag="interpretation", origin="agent_inference")
            raise Check("agent inference was written to User Track")
        except UserTrackContamination:
            pass_direct = True
        cand = mem.propose_user_promotion("用户可能喜欢简洁界面", source="diag", tag="interpretation")
        searchable = [m for m in mem.store.search("简洁界面", track="user")]
        require(not searchable, "pending candidate leaked into search")
        try:
            mem.confirm_promotion(cand["id"], approved_by="agent:self")
            raise Check("agent approved its own promotion")
        except UserTrackContamination:
            pass_self = True
        approved = mem.confirm_promotion(cand["id"], approved_by="user:owner")
        # K-2: the low-level store must refuse too (memory_cli / control center call it directly).
        raw = mem.store.propose("用户可能住在海边", source="agent_inference:diag", kind="fact", track="user")
        n_mem = table_counts(db).get("memories", 0)
        for who in ("agent:self", "muse", "dashboard"):
            try:
                mem.store.approve_candidate(raw["id"], approved_by=who)
                raise Check(f"MemoryStore approved a User Track candidate for {who!r}")
            except PermissionError:
                pass
        require(table_counts(db).get("memories", 0) == n_mem and mem.store.get_candidate(raw["id"])["status"] == "pending",
                "a refused low-level approval wrote to the store")
        low_level = mem.store.approve_candidate(raw["id"], approved_by="human:owner")
        require(low_level["status"] == "approved", "human: approval of a User Track candidate failed")
        revised = mem.store.revise(user["id"], "我偏好在深夜做深度研究", source="user:chat")
        history = mem.store.history(user["id"])
        audit = mem.contamination_audit()
        after = table_counts(db)
    stamp = user["epistemic"]
    require(stamp and stamp["epistemic_tag"] == "fact" and stamp["origin"] == "user_stated", "user stamp missing")
    require(agent["track"] == "agent", "agent memory not on agent track")
    require(len(history) >= 2, "revision chain missing")
    require(audit["clean"], f"contamination audit not clean: {audit}")
    diff = state_diff(before, after)
    require(diff.get("memories", 0) >= 3 and diff.get("memory_epistemics", 0) >= 3, f"state diff too small: {diff}")
    return {"direct_write_blocked": pass_direct, "self_approval_blocked": pass_self,
            "approved_status": approved.get("status"), "revision": revised.get("revision"), "state_diff": diff}


@probe("L3.5", "L3", "对话主链路→双轨记忆：模型抽取只提案、带 agent_inference 印章、崩溃落账", ("AC-014",))
def l3_chat_dual_track(ctx: Ctx):
    from muse.assistant import MuseAssistant
    from muse.conversations import ConversationStore
    from muse.memory import MemoryStore
    from muse.error_journal import suppressed_error_stats
    d = ctx.sub("chat dual track")

    class SimModel:
        def chat(self, messages):
            return "好的，我记住了你的说法，但需要你确认才会写入长期记忆。"

    class SimExtractor:
        def __init__(self, crash=False):
            self.crash = crash

        def extract(self, text):
            if self.crash:
                raise RuntimeError("extractor crashed 抽取器崩溃")
            return [{"content": "用户喜欢在深夜写作", "confidence": 0.95}]

    memory = MemoryStore(d / "memory.sqlite3")
    convo = ConversationStore(d / "conversations.sqlite3")
    try:
        bot = MuseAssistant(convo, SimModel(), memory=memory, memory_candidate_extractor=SimExtractor())
        out = bot.reply("t1", "我一般深夜写作")
        require(out["memory_candidates_created"] == 1, f"no candidate staged: {out}")
        require(not memory.search("深夜写作", track="user"), "unconfirmed inference became searchable User Track memory")
        cid = out["memory_candidate_ids"][0]
        row = bot.dual_track._db.execute(
            "SELECT origin, approval_status FROM memory_epistemics WHERE ref_kind='candidate' AND ref_id=?", (cid,)).fetchone()
        require(row is not None and row[0] == "agent_inference" and row[1] == "pending_user_confirmation", f"stamp: {row}")
        before = suppressed_error_stats()["total"]
        crashing = MuseAssistant(convo, SimModel(), memory=memory, memory_candidate_extractor=SimExtractor(crash=True))
        out2 = crashing.reply("t2", "再说一句")
        require(out2["memory_extraction_status"] == "failed", "crash not reported")
        require(suppressed_error_stats()["total"] == before + 1, "extractor crash was swallowed silently")
        bot.close(); crashing.close()
    finally:
        memory.close()
        close = getattr(convo, "close", None)
        if callable(close):
            close()
    return {"candidate_id": cid, "stamp": list(row)}


@probe("L3.6", "L3", "旧用户记忆迁移 legacy_unverified：备份+幂等+可回滚，原内容哈希/条数不变", ("AC-015",))
def l3_legacy_memory_migration(ctx: Ctx):
    from muse.dual_track_memory import DualTrackMemory
    from muse.memory import MemoryStore
    from muse.memory_migration import LEGACY_TAG, content_fingerprint, migrate, rollback
    d = ctx.sub("legacy memory 迁移")
    db = d / "muse memory.sqlite3"
    store = MemoryStore(db)
    try:
        a = store.add("我喜欢在清晨散步 🌅", source="user:legacy", kind="preference", track="user")
        store.add("旧的工作目标：完成 Muse", source="user:legacy", kind="fact", track="user")
        gone = store.add("已删除的旧记忆", source="user:legacy", kind="fact", track="user")
        store.revise(a["id"], "我喜欢在清晨和傍晚散步 🌅", source="user:legacy")
        store.delete(gone["id"])
        store.add("Agent 经验：空格路径需加引号", source="run:diag", kind="case", track="agent")
    finally:
        store.close()

    def snapshot():
        con = sqlite3.connect(db)
        try:
            return content_fingerprint(con)
        finally:
            con.close()

    before, tables_before = snapshot(), table_counts(db)
    dry = migrate(db, backup_dir=d / "backups", report_dir=d / "reports", apply=False)
    require(dry["rows_added"] == 0 and table_counts(db) == tables_before, "dry run modified the database")
    first = migrate(db, backup_dir=d / "backups", report_dir=d / "reports", apply=True)
    require(first["rows_added"] == 3, f"expected 3 user rows tagged (incl. soft-deleted), got {first['rows_added']}")
    require(snapshot() == before, "memory content or counts changed")
    diff = state_diff(tables_before, table_counts(db))
    require(set(diff) <= {"memory_epistemics", "memory_migrations"} and diff.get("memory_epistemics") == 3, f"unexpected table diff: {diff}")
    backup = Path(first["backup_path"])
    require(backup.is_file() and hashlib.sha256(backup.read_bytes()).hexdigest() == first["backup_sha256"], "backup missing/corrupt")
    con = sqlite3.connect(backup)
    try:
        require(content_fingerprint(con) == before, "backup is not a complete copy")
    finally:
        con.close()
    second = migrate(db, backup_dir=d / "backups", report_dir=d / "reports", apply=True)
    require(second["rows_added"] == 0 and snapshot() == before, "migration is not idempotent")
    with DualTrackMemory(db) as mem:
        audit = mem.contamination_audit()
        stamp = mem.stamp_of(a["id"])
    require(audit["clean"] and audit["legacy_unverified_count"] == 2 and audit["unstamped_legacy_count"] == 0, f"audit: {audit}")
    require(stamp["epistemic_tag"] == LEGACY_TAG and stamp["approval_status"] == LEGACY_TAG and stamp["origin"] == "legacy_import",
            f"stamp is not legacy_unverified (must never be user_confirmed): {stamp}")
    rb = rollback(db, first["migration_id"], report_dir=d / "reports")
    require(rb["rows_removed"] == 3 and snapshot() == before, f"rollback: {rb}")
    try:
        rollback(db, first["migration_id"], report_dir=d / "reports")
        raise Check("double rollback accepted")
    except ValueError:
        pass
    again = migrate(db, backup_dir=d / "backups", report_dir=d / "reports", apply=True)
    require(again["rows_added"] == 3 and snapshot() == before, "re-migration after rollback failed")
    require(len(list((d / "reports").glob("*.md"))) >= 4, "migration reports missing")
    real = ROOT / "data" / "memory" / "muse.sqlite3"
    real_state = "absent"
    if real.exists():
        rcon = sqlite3.connect(f"file:{real.as_posix()}?mode=ro", uri=True)
        try:
            unstamped = rcon.execute("SELECT count(*) FROM memories m LEFT JOIN memory_epistemics e ON e.ref_kind='memory' "
                                     "AND e.ref_id=m.id WHERE m.track='user' AND e.ref_id IS NULL").fetchone()[0]
            confirmed_by_migration = rcon.execute("SELECT count(*) FROM memory_epistemics WHERE origin='legacy_import' "
                                                  "AND approval_status!='legacy_unverified'").fetchone()[0]
            ledger = rcon.execute("SELECT backup_path, backup_sha256 FROM memory_migrations WHERE status='applied'").fetchall()
        finally:
            rcon.close()
        require(confirmed_by_migration == 0, "real store: migration rows were upgraded beyond legacy_unverified")
        require(ledger and all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == s for p, s in ledger),
                "real store: migration backup missing or altered")
        real_state = {"unstamped_user_rows": unstamped, "applied_migrations": len(ledger)}
    return {"tagged": first["rows_added"], "rollback_removed": rb["rows_removed"], "real_store": real_state}


@probe("L3.7", "L3", "旧记忆复核身份门：仅 user:<名字> 可把 legacy_unverified 升为 user_confirmed（agent:/human:/空名零写入）；备份+原文不变+幂等+可撤销", ("AC-101",))
def l3_legacy_memory_review(ctx: Ctx):
    from muse.dual_track_memory import DualTrackMemory
    from muse.epistemics import UserTrackContamination
    from muse.legacy_memory_review import ReviewerNotHuman, confirm, pending_reviews, revert, write_checklist
    from muse.memory import MemoryStore
    from muse.memory_migration import LEGACY_TAG, content_fingerprint, migrate
    d = ctx.sub("legacy memory 复核")
    db = d / "muse memory.sqlite3"
    store = MemoryStore(db)
    try:
        a = store.add("我习惯晚上写作 ✍️", source="user:legacy", kind="preference", track="user")
        b = store.add("旧目标：每周跑步三次", source="user:legacy", kind="fact", track="user")
        g = store.add("Agent 经验：先备份再迁移", source="run:diag", kind="case", track="agent")
    finally:
        store.close()
    migrate(db, backup_dir=d / "mig-backups", report_dir=d / "reports", apply=True)

    def fp():
        con = sqlite3.connect(db)
        try:
            return content_fingerprint(con), con.execute("SELECT * FROM memory_epistemics ORDER BY ref_kind, ref_id").fetchall()
        finally:
            con.close()

    before, tables_before = fp(), table_counts(db)
    require([p["memory_id"] for p in pending_reviews(db)] == [a["id"], b["id"]], "pending review list is wrong")
    bk, rep = d / "review-backups", d / "reports"
    refused = []
    for who in ("agent:muse", "human:operator", "user:", "user:   ", "muse", "", "User:alice", None):
        try:
            confirm(db, a["id"], reviewer=who, backup_dir=bk, report_dir=rep)
        except ReviewerNotHuman:
            refused.append(who)
            continue
        raise Check(f"non-user reviewer {who!r} upgraded a legacy_unverified memory")
    require(fp() == before and table_counts(db) == tables_before and not bk.exists(), "a refused reviewer wrote something")
    for bad_tag in ("symbolic_inspiration", "rumor", "fiction"):
        try:
            confirm(db, a["id"], reviewer="user:alice", tag=bad_tag, backup_dir=bk, report_dir=rep)
        except UserTrackContamination:
            continue
        raise Check(f"tag {bad_tag} was admitted to the User Track")
    try:
        confirm(db, g["id"], reviewer="user:alice", backup_dir=bk, report_dir=rep)
        raise Check("an Agent Track memory was confirmed onto the User Track")
    except ValueError:
        pass
    require(fp() == before, "refused confirmations changed state")
    cli = subprocess.run([sys.executable, str(ROOT / "scripts" / "review_legacy_memory.py"), "--db", str(db), "confirm",
                          "--id", str(a["id"]), "--by", "agent:muse"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120, stdin=subprocess.DEVNULL)
    require(cli.returncode == 3 and "reviewer_not_user" in cli.stderr and fp() == before,
            f"CLI accepted agent: reviewer (rc={cli.returncode}) {cli.stderr[-300:]}")
    ok = confirm(db, a["id"], reviewer="user:alice", tag="fact", backup_dir=bk, report_dir=rep)
    require(ok["result"] == "confirmed", f"confirm: {ok}")
    backup = Path(ok["backup_path"])
    require(backup.is_file() and hashlib.sha256(backup.read_bytes()).hexdigest() == ok["backup_sha256"], "review backup missing/corrupt")
    require(fp()[0] == before[0], "original memory content changed by review")
    diff = state_diff(tables_before, table_counts(db))
    require(diff == {"memory_legacy_reviews": 1}, f"unexpected table diff: {diff}")
    with DualTrackMemory(db) as mem:
        stamp, audit = mem.stamp_of(a["id"]), mem.contamination_audit()
        other = mem.stamp_of(b["id"])
    require(stamp["origin"] == "user_confirmed" and stamp["approval_status"] == "user_confirmed"
            and stamp["approved_by"] == "user:alice", f"stamp after confirm: {stamp}")
    require(other["approval_status"] == LEGACY_TAG and audit["clean"] and audit["legacy_unverified_count"] == 1, f"audit: {audit}")
    require([p["memory_id"] for p in pending_reviews(db)] == [b["id"]], "pending list not updated")
    again = confirm(db, a["id"], reviewer="user:alice", backup_dir=bk, report_dir=rep)
    require(again["result"] == "already_confirmed" and table_counts(db).get("memory_legacy_reviews") == 1, "confirm not idempotent")
    try:
        revert(db, ok["review_id"], reviewer="agent:muse", report_dir=rep)
        raise Check("agent: reviewer reverted a review")
    except ReviewerNotHuman:
        pass
    rv = revert(db, ok["review_id"], reviewer="user:alice", report_dir=rep)
    require(rv["result"] == "reverted" and fp() == before, "revert did not restore the exact previous stamp")
    try:
        revert(db, ok["review_id"], reviewer="user:alice", report_dir=rep)
        raise Check("double revert accepted")
    except ValueError:
        pass
    checklist = write_checklist(db, rep)
    require(checklist["pending"] == 2 and Path(checklist["checklist"]).is_file(), f"checklist: {checklist}")
    real = ROOT / "data" / "memory" / "muse.sqlite3"
    real_state: Any = "absent"
    if real.exists():
        rcon = sqlite3.connect(f"file:{real.as_posix()}?mode=ro", uri=True)
        try:
            has = rcon.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_legacy_reviews'").fetchone()
            bad = rcon.execute("SELECT count(*) FROM memory_legacy_reviews WHERE substr(reviewer, 1, 5) != 'user:' "
                               "OR length(trim(substr(reviewer, 6))) = 0").fetchone()[0] if has else 0
        finally:
            rcon.close()
        require(bad == 0, f"real store: {bad} legacy reviews by a non-user reviewer")
        real_state = {"pending_legacy": len(pending_reviews(real)), "non_user_reviews": bad}
    return {"refused_reviewers": len(refused), "real_store": real_state}


@probe("L3.4", "L3", "Obsidian 幂等同步且不覆盖手写笔记", ("AC-013",))
def l3_obsidian(ctx: Ctx):
    from muse.memory import MemoryStore
    from muse.obsidian_vault import export_approved_memories
    vault = ctx.sub("obsidian vault 库")
    handwritten = vault / "我的手写笔记.md"
    handwritten.write_text("---\ntags: [mine]\n---\n# 手写\n[[双链]] 不可被覆盖\n", encoding="utf-8")
    hw_hash = hashlib.sha256(handwritten.read_bytes()).hexdigest()
    store = MemoryStore(ctx.sub("obsidian mem") / "m.sqlite3")
    try:
        store.add("Muse 的使命是让知识之火不断传递", source="user:charter", kind="fact", track="user")
        first = export_approved_memories(store, vault)
        snap1 = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in vault.rglob("*.md")}
        second = export_approved_memories(store, vault)
        snap2 = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in vault.rglob("*.md")}
    finally:
        store.close()
    require(len(snap1) >= 2, f"export produced no notes: {snap1}")
    require(snap1 == snap2, "second export changed files (not idempotent)")
    require(hashlib.sha256(handwritten.read_bytes()).hexdigest() == hw_hash, "handwritten note was modified")
    return {"notes": len(snap1), "first": str(first)[:200], "second": str(second)[:200]}


# =============================== L4 · Commander / gates / adapters ===============================
def _commander(ctx: Ctx, name: str):
    from muse.muse_commander import MuseCommander
    return MuseCommander(ctx.sub(f"commander {name}"))


def _ok_handler(c):
    artifact = f"artifact for {c['payload']['step_key']} @ {c['task_id']}"
    return {"artifact": artifact, "builder_id": f"worker:{c['payload']['role']}", "evidence": "unit: PASS",
            "usage": {"provider": "sim_local", "model": "deterministic-sim", "token_source": "estimated",
                      "input_tokens": 12, "output_tokens": 30, "cost": "0"}}


@probe("L4.1", "L4", "Commander DAG 顺序 + 高风险挂起 + 非人类批准拒绝", ("AC-020", "AC-021", "AC-022"))
def l4_dag(ctx: Ctx):
    order: list[str] = []
    with _commander(ctx, "dag") as cmd:
        def h(c):
            order.append(c["payload"]["step_key"]); return _ok_handler(c)
        cmd.register_handler("sim", h)
        plan = cmd.submit_goal("调研并生成摘要", [
            {"key": "collect", "title": "收集资料", "role": "researcher", "objective": "收集公开资料", "handler": "sim"},
            {"key": "summarize", "title": "写摘要", "role": "writer", "objective": "写摘要", "handler": "sim", "depends_on": ["collect"]},
            {"key": "publish", "title": "发布", "role": "operator", "objective": "发布到公网", "handler": "sim", "depends_on": ["summarize"]},
        ])
        risks = {t["key"]: (t["risk"], t["status"]) for t in plan["tickets"]}
        cmd.drain()
        require(order == ["collect", "summarize"], f"unexpected order before approval: {order}")
        pub = plan["tickets"][2]["task_id"]
        require(cmd.queue.get(pub)["status"] == "waiting_approval", "high-risk step was not held")
        try:
            cmd.approve(pub, approved_by="agent:commander"); raise Check("non-human approval accepted")
        except PermissionError:
            pass
        cmd.approve(pub, approved_by="user:owner")
        cmd.drain()
        require(order == ["collect", "summarize", "publish"], f"order after approval: {order}")
        require(cmd.queue.get(pub)["status"] == "succeeded", "approved step did not succeed")
    return {"risks": risks, "order": order}


@probe("L4.2", "L4", "Kill Switch 传播 + 超时熔断 + 崩溃隔离", ("AC-023", "AC-024", "AC-061"))
def l4_kill(ctx: Ctx):
    with _commander(ctx, "kill") as cmd:
        def slow(c):
            c["cancel_event"].wait(5); return {"artifact": "late", "evidence": "PASS"}
        def boom(c):
            raise RuntimeError("plugin crashed 插件崩溃")
        cmd.register_handler("slow", slow); cmd.register_handler("boom", boom); cmd.register_handler("ok", _ok_handler)
        p = cmd.submit_goal("隔离测试", [
            {"key": "crash", "role": "researcher", "objective": "运行插件", "handler": "boom"},
            {"key": "after", "role": "researcher", "objective": "继续工作", "handler": "ok"},
            {"key": "slow", "role": "researcher", "objective": "慢任务", "handler": "slow"}])
        outcomes = [cmd.run_next(timeout_seconds=1.0) for _ in range(3)]
        statuses = {o.get("task_id"): o["status"] for o in outcomes}
        ids = {t["key"]: t["task_id"] for t in p["tickets"]}
        require(statuses.get(ids["crash"]) == "failed", f"crash not isolated: {statuses}")
        require(statuses.get(ids["after"]) == "succeeded", "work after crash did not proceed")
        slow_out = [o for o in outcomes if o.get("task_id") == ids["slow"]][0]
        require(slow_out["status"] == "failed" and "timeout" in slow_out["result"]["error_code"], f"timeout not enforced: {slow_out}")
        pending = cmd.submit_goal("待取消", [{"key": "x", "role": "researcher", "objective": "整理", "handler": "ok"},
                                             {"key": "y", "role": "operator", "objective": "部署", "handler": "ok"}])
        killed = cmd.kill(actor="user:owner", reason="diag emergency stop")
        blocked = cmd.run_next()
        require(blocked["status"] == "blocked" and blocked["gate"]["reason"] == "kill_switch", f"dispatch not blocked: {blocked}")
        for t in pending["tickets"]:
            require(cmd.queue.get(t["task_id"])["status"] == "cancelled", "unstarted ticket survived kill switch")
    return {"cancelled": len(killed["cancelled"]), "statuses": list(statuses.values())}


@probe("L4.3", "L4", "独立 Reviewer 与客观证据核验", ("AC-025",))
def l4_review(ctx: Ctx):
    from muse.review_gate import IndependentReviewGate, ReviewStore
    from muse.muse_commander import objective_reviewer, sha256_text
    store = ReviewStore(ctx.sub("review") / "r.sqlite3")
    try:
        gate = IndependentReviewGate(store, reviewer_id="reviewer:objective", call=objective_reviewer)
        try:
            gate.review(artifact_id="a1", builder_id="reviewer:objective", artifact="x" * 20, test_evidence="PASS", lineage="l")
            raise Check("self-review accepted")
        except PermissionError:
            pass
        art = "real artifact body 正文"
        good = gate.review(artifact_id="a2", builder_id="worker:dev", artifact=art, test_evidence="tests: PASS",
                           lineage=f"artifact_sha256={sha256_text(art)}")
        tampered = gate.review(artifact_id="a3", builder_id="worker:dev", artifact=art + " tampered", test_evidence="tests: PASS",
                               lineage=f"artifact_sha256={sha256_text(art)}")
        failing = gate.review(artifact_id="a4", builder_id="worker:dev", artifact=art, test_evidence="1 FAIL",
                              lineage=f"artifact_sha256={sha256_text(art)}")
    finally:
        store.close()
    require(good.approved and not tampered.approved and not failing.approved, "objective review verdicts wrong")
    return {"good": good.approved, "tampered": tampered.approved, "failing": failing.approved}


@probe("L4.4", "L4", "成本主权：未知免费额度阻断付费云", ("AC-026",))
def l4_paid(ctx: Ctx):
    from muse.provider_policy import (CloudExecutionPolicy, FreeOnlyRouter, NoFreeProviderAvailable,
                                      PaidCloudExecutionDisabled, ProviderBudget)
    router = FreeOnlyRouter()
    blocked = []
    for budget in (None, ProviderBudget("openrouter", True, False, False, None)):
        try:
            CloudExecutionPolicy().authorize("openrouter", budget, router)
            raise Check(f"paid/unknown quota allowed for {budget}")
        except (PaidCloudExecutionDisabled, NoFreeProviderAvailable) as exc:
            blocked.append(type(exc).__name__)
    fresh = ProviderBudget("free_x", True, True, True, 5, quota_checked_at=datetime.now(timezone.utc).isoformat(), quota_source="diag")
    require(router.select([fresh]).provider_id == "free_x", "verified fresh free quota not selectable")
    return {"blocked": blocked}


@probe("L4.5", "L4", "资源感知并发上限（未知项诚实）", ("AC-027",))
def l4_capacity(ctx: Ctx):
    from muse.muse_commander import MuseCommander
    cap = MuseCommander.capacity()
    snap = cap["snapshot"]
    require(snap["logical_cpu_count"], "CPU count not measured")
    require(snap["gpu_available_memory_bytes"] == "unknown" or isinstance(snap["gpu_available_memory_bytes"], int), "GPU value fabricated")
    ctx.facts["capacity"] = cap
    return cap


@probe("L4.6", "L4", "适配器无授权调用被拒 + 八类认知标签", ("AC-028", "AC-040"))
def l4_adapter_grant(ctx: Ctx):
    from muse.adapter_hub import AdapterHub, GrantError
    from muse import epistemics as ep
    hub = AdapterHub(os.urandom(32))
    hub.register("sim_echo", lambda r: {"echo": r})
    try:
        hub.dispatch("sim_echo", {"q": 1}, task_id="t1", grant="forged"); raise Check("forged grant accepted")
    except GrantError:
        pass
    ok = hub.dispatch("sim_echo", {"q": 1}, task_id="t1", grant=hub.issue_grant(adapter="sim_echo", task_id="t1"))
    require(ok == {"echo": {"q": 1}}, "granted dispatch failed")
    require(len(ep.EPISTEMIC_TAGS) == 8 and ep.normalize_tag("象征灵感") == "symbolic_inspiration", "tag table wrong")
    for bad in ("truth", ""):
        try:
            ep.normalize_tag(bad); raise Check(f"illegal tag accepted: {bad!r}")
        except ep.EpistemicTagError:
            pass
    sym = ep.tag_payload({"text": "乾卦意象"}, "symbolic_inspiration", source="symbolic_lab", confidence=0.9, origin="symbolic_lab")
    require(sym["is_evidence"] is False and sym["confidence"] <= 0.6, "symbolic item promoted")
    sym["is_evidence"] = True
    try:
        ep.require_tag(sym); raise Check("symbolic evidence upgrade accepted")
    except ep.EpistemicTagError:
        pass
    return {"audit": hub.audit}


@probe("L4.7", "L4", "专家舰队注册与执行器绑定", ("AC-029",))
def l4_fleet(ctx: Ctx):
    from muse.expert_fleet import FLEET, fleet_health, install_fleet
    d = ctx.sub("fleet")
    res = install_fleet(specialist_db=d / "specialists.sqlite3", agent_db=d / "agents.sqlite3")
    health = fleet_health()
    unbound = [h["expert_id"] for h in health if not h["executor_bound"]]
    require(len(FLEET) >= 15 and res["listed_count"] == len(FLEET), f"registry incomplete: {res}")
    require(not unbound, f"executors not bound: {unbound}")
    return {"experts": len(FLEET)}


@probe("L4.9", "L4", "全局 Kill Switch 覆盖所有派发路径（Commander / Ruflo DAG / 自治运行时）", ("AC-034",))
def l4_global_gate(ctx: Ctx):
    from muse.commander_gate import check_global_gate, commander_root
    from muse.muse_commander import MuseCommander
    from muse.unified_runtime import MuseRuntime, RuntimeBlocked
    ws = ctx.sub("global gate ws")
    (ws / "data" / "tasks").mkdir(parents=True, exist_ok=True)
    root = commander_root(ws)
    require(check_global_gate(ws)["allowed"], "fresh workspace should be open")
    with MuseCommander(root) as cmd:
        cmd.kill(actor="user:owner", reason="diag global gate")
    gate = check_global_gate(ws)
    require(not gate["allowed"] and gate["reason"] == "kill_switch", f"global gate not closed: {gate}")
    runtime = MuseRuntime(ws / "data" / "tasks" / "muse.sqlite3", workspace=ws, video_reviewer_public_keys={})
    try:
        runtime.dispatch_commander_once(task_id="deadbeef", approved=True)
        raise Check("Ruflo DAG dispatch ran while the global kill switch was engaged")
    except RuntimeBlocked as exc:
        require("global gate" in str(exc), f"blocked for another reason first: {exc}")
    from muse.autonomous_runtime import AutonomousSupervisor
    sup = AutonomousSupervisor(task_db=ws / "data/tasks/muse.sqlite3", routine_db=ws / "data/routines/r.sqlite3",
                               state_db=ws / "data/autonomy/s.sqlite3", improvement_db=ws / "data/improvements/i.sqlite3",
                               knowledge_db=ws / "data/research/k.sqlite3", reminder_db=ws / "data/reminders/r.sqlite3",
                               memory_db=ws / "data/memory/m.sqlite3", conversation_db=ws / "data/conversations/c.sqlite3",
                               workspace=ws)
    tick = sup.run_tick()
    require(tick.get("safety", {}).get("reason") == "kill_switch" and tick["task"] is None, f"autonomous tick not blocked: {tick}")
    from muse.autonomy_safety import KILL_RESET_PHRASE
    (root / "KILL_SWITCH").unlink()
    require(not check_global_gate(ws)["allowed"], "removing only the file must not reopen a killed Commander")
    with MuseCommander(root) as cmd:
        cmd.safety.reset_kill_switch(actor="user:owner", confirmation=KILL_RESET_PHRASE)
        require(not check_global_gate(ws)["allowed"], "reset alone must leave Commander paused (two-step release)")
        cmd.safety.resume(actor="user:owner")
    require(check_global_gate(ws)["allowed"], "gate did not reopen after reset + resume")
    return {"blocked_paths": ["muse_commander", "unified_runtime.dispatch_commander_once", "autonomous_runtime.run_tick"]}


@probe("L4.10", "L4", "Ruflo 执行收归统帅：执行型工具必须属于运行中且持租约的票据；swarm_init 走 Commander 票据", ("AC-035",))
def l4_ruflo_commander(ctx: Ctx):
    from muse.commander_gate import BypassCommanderError, clear_kill_file, engage_kill_file, ruflo_ticket_scope
    from muse.muse_commander import MuseCommander
    from muse.ruflo_adapter import RufloMCPAdapter
    from muse.task_queue import TaskQueue
    from muse.unified_runtime import MuseRuntime
    ws = ctx.sub("ruflo commander ws")
    (ws / "data" / "tasks").mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, ...]] = []
    adapter = RufloMCPAdapter((sys.executable, "-c", "pass"), workspace=ws,
                              enabled_tools=("swarm_init", "agent_spawn", "agent_execute", "memory_store"))

    def fake_run(args, *, timeout=None):  # replaces only the external Node process; the guard is real code
        calls.append(tuple(args))
        return 'Result: {"success": true, "swarmId": "swarm-diag", "agentCount": 0, "maxAgents": 8}'

    adapter._run = fake_run
    adapter.call_tool("swarm_status", {})  # read-only stays allowed without a ticket
    refused = 0
    try:
        adapter.call_tool("agent_execute", {"agentId": "x", "task": "y"}, approved=True)
        raise Check("agent_execute ran without any Commander ticket")
    except BypassCommanderError:
        refused += 1
    db = ws / "data" / "tasks" / "muse.sqlite3"
    with TaskQueue(db) as q:
        ticket = q.enqueue("ruflo diag", {"kind": "diag"})
        with ruflo_ticket_scope(db, ticket["id"], "w1", ws):
            try:
                adapter.call_tool("agent_execute", {"agentId": "x", "task": "y"}, approved=True)
                raise Check("queued (unclaimed) ticket authorised execution")
            except BypassCommanderError:
                refused += 1
        q.claim_next(worker_id="w1", task_id=ticket["id"], lease_seconds=60)
        with ruflo_ticket_scope(db, ticket["id"], "w1", ws):
            adapter.call_tool("agent_execute", {"agentId": "x", "task": "y"}, approved=True)
        with ruflo_ticket_scope(db, ticket["id"], "w2", ws):
            try:
                adapter.call_tool("agent_execute", {"agentId": "x", "task": "y"}, approved=True)
                raise Check("another worker rode on a leased ticket")
            except BypassCommanderError:
                refused += 1
        engage_kill_file(ws, actor="user:diag", reason="ruflo gate test")
        try:
            with ruflo_ticket_scope(db, ticket["id"], "w1", ws):
                try:
                    adapter.call_tool("agent_execute", {"agentId": "x", "task": "y"}, approved=True)
                    raise Check("Ruflo executed while the global kill switch was engaged")
                except BypassCommanderError:
                    refused += 1
        finally:
            clear_kill_file(ws)
    executed = [c for c in calls if len(c) > 3 and c[3] == "agent_execute"]
    require(len(executed) == 1, f"exactly one authorised agent_execute expected, got {len(executed)}")
    runtime = MuseRuntime(db, workspace=ws, ruflo=adapter, video_reviewer_public_keys={})
    swarm = runtime.initialize_swarm(approved=True, max_agents=4)
    require(swarm.get("swarm_id") == "swarm-diag" and swarm.get("commander_ticket_id"), f"swarm via commander: {swarm}")
    with MuseCommander(ws / "data" / "commander") as cmd:
        status = cmd.queue.get(swarm["commander_ticket_id"])["status"]
        chain = cmd.verify_receipt_chain()
    require(status == "succeeded" and chain["ok"] and chain["count"] == 1, f"swarm ticket {status} chain {chain}")
    ruflo_log = [json.loads(x) for x in (ws / "data" / "commander" / "ruflo_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    require(any(e["tool"] == "swarm_init" and e["task_id"] == swarm["commander_ticket_id"] for e in ruflo_log), "swarm_init not audited to its ticket")
    audit = (ws / "data" / "commander" / "bypass_audit.jsonl").read_text(encoding="utf-8").splitlines()
    require(len(audit) >= refused == 4, f"refusals not audited: refused={refused} audit={len(audit)}")
    from muse import unified_runtime as ur
    tree = ast.parse(Path(ur.__file__).read_text(encoding="utf-8"))
    executing = {"swarm_init", "agent_spawn", "agent_execute", "memory_store"}
    unbound = []
    # Judge by the outermost function (module-level function or class method): closures defined inside a
    # ticket-bound method run in its context (scope is propagated to pools via contextvars.copy_context)
    # and every call is still re-verified at runtime by require_ruflo_scope.
    outermost = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        outermost += [m for m in cls.body if isinstance(m, ast.FunctionDef)]
    for node in outermost:
        if True:
            decorated = any(getattr(d, "id", "") == "_ruflo_ticket_bound" for d in node.decorator_list)
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "call_tool" and call.args
                        and isinstance(call.args[0], ast.Constant) and call.args[0].value in executing
                        and not decorated and node.name != "_initialize_swarm_impl"):
                    unbound.append(f"{node.name}:{call.lineno}")
    require(not unbound, f"executing Ruflo calls outside a ticket-bound method: {unbound}")
    return {"refused": refused, "swarm_ticket": swarm["commander_ticket_id"], "authorised_executes": len(executed)}


@probe("L4.11", "L4", "外部智能体适配器（dsh / Super Agent Party）统一协议：契约测试（本地假服务端，非集成测试）+ 真实健康探测", ("AC-036",))
def l4_external_agents(ctx: Ctx):
    import http.server
    from muse.commander_gate import BypassCommanderError
    from muse.commander_routes import build_route_handlers
    from muse.external_agents import EgressDenied, ExternalAgentAdapter, ExternalAgentError, request_external_invoke
    from muse.muse_commander import MuseCommander
    received: list[dict] = []

    class FakeGateway(http.server.BaseHTTPRequestHandler):  # contract test double, NOT the real dsh/SAP
        def log_message(self, *args):
            return None

        def _json(self, code: int, obj: dict) -> None:
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

        def do_GET(self):
            self._json(200, {"data": [{"id": "fake-model"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            received.append(body)
            self._json(200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGateway)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    ws = ctx.sub("external agents ws")
    cfg = ws / "external_agents.json"
    cfg.write_text(json.dumps({"dsh": {"enabled": True, "base_url": f"http://127.0.0.1:{fake.server_address[1]}",
                                       "kernel_url": "", "timeout_seconds": 10}}), encoding="utf-8")
    try:
        dsh = ExternalAgentAdapter("dsh", config_path=cfg)
        sap = ExternalAgentAdapter("super_agent_party", config_path=cfg)
        require(dsh.health()["state"] == "live" and dsh.capabilities() == ["chat_completions"], f"dsh health: {dsh.health()}")
        require(sap.health()["state"] == "not_configured" and sap.capabilities() == [], f"sap must stay dormant: {sap.health()}")
        good = {"model": "fake-model", "messages": [{"role": "user", "content": "你好"}], "max_tokens": 8}
        try:
            dsh.invoke(good, commander_ctx=None, workspace=ws)
            raise Check("external invoke ran without a Commander ticket")
        except BypassCommanderError:
            pass
        for bad in ({**good, "api_key": "x"}, {"model": "m", "messages": [{"role": "assistant", "content": "x"}]},
                    {"model": "m", "messages": [{"role": "user", "content": "x" * 9000}]}):
            try:
                request_external_invoke(ws, "dsh", bad, request_key="bad", config_path=cfg)
                raise Check(f"egress allowlist accepted {list(bad)}")
            except EgressDenied:
                pass
        bad_cfg = ws / "bad.json"
        bad_cfg.write_text(json.dumps({"dsh": {"base_url": "http://example.com:8326"}}), encoding="utf-8")
        try:
            ExternalAgentAdapter("dsh", config_path=bad_cfg)
            raise Check("non-loopback external agent URL accepted")
        except ExternalAgentError:
            pass
        pending = request_external_invoke(ws, "dsh", good, request_key="good-1", config_path=cfg)
        require(pending["status"] == "pending_approval" and pending["risk"] == "high" and not received,
                f"external call must wait for a human: {pending} received={len(received)}")
        with MuseCommander(ws / "data" / "commander") as cmd:
            for name, fn in build_route_handlers(ws).items():
                cmd.register_handler(name, fn)
            try:
                cmd.approve(pending["ticket_id"], approved_by="agent:operator")
                raise Check("agent identity approved an outbound call")
            except PermissionError:
                pass
            cmd.approve(pending["ticket_id"], approved_by="user:owner")
            outcome = cmd.run_next(task_id=pending["ticket_id"], timeout_seconds=30)
            chain = cmd.verify_receipt_chain()
    finally:
        fake.shutdown(); fake.server_close()
    require(outcome["status"] == "succeeded" and chain["ok"], f"approved invoke: {outcome}")
    require(len(received) == 1 and set(received[0]) == {"model", "messages", "max_tokens"}, f"egress payload: {received}")
    real = {name: ExternalAgentAdapter(name).health() for name in ("dsh", "super_agent_party")}
    require(real["super_agent_party"]["state"] == "not_configured", "real SAP state must be honest (no address known)")
    return {"test_kind": "contract_test_not_integration", "real_health": {k: v["state"] for k, v in real.items()},
            "real_dsh_probes": real["dsh"].get("probes")}


@probe("L4.12", "L4", "全入口审计：入口枚举驱动（HTTP/例程/Worker/钩子/CLI 全部登记且分类有证据；债务只减不增）", ("AC-037",))
def l4_entrypoint_audit(ctx: Ctx):
    import re as _re
    from muse import entrypoint_registry as reg
    text = (SRC / "muse" / "status_api.py").read_text(encoding="utf-8")
    body = text[text.find("        def do_POST"):]
    lines = body.splitlines()
    opener = _re.compile(r"^ {12}if .*(parsed\.path|_match\b|_match:)")
    starts = [i for i, line in enumerate(lines) if opener.match(line)]
    blocks: dict[str, list[str]] = {}
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        blocks.setdefault(lines[start].strip(), []).append("\n".join(lines[start:end]))
    commander_ev = _re.compile(r"\b(submit_goal|submit_and_wait|MuseCommander|handle_commander|require_commander_ticket|on_video_completed)\b")
    approval_ev = _re.compile(r"(confirmation|_confirm\(|require_approval=True|approved_by|\.approve\(|waiting_approval|SIGN-REVIEW|APPROVE|decide\()")
    problems: list[str] = []
    actual_status = set(blocks)
    registered_status = set(reg.STATUS_API_POST_BLOCKS)
    problems += [f"unregistered status_api POST block: {k}" for k in sorted(actual_status - registered_status)]
    problems += [f"stale registry entry (block gone): {k}" for k in sorted(registered_status - actual_status)]

    def check(name: str, cls: str, segment: str) -> None:
        if cls not in reg.CLASSES:
            problems.append(f"{name}: unknown class {cls}")
        elif cls == "commander_ticket" and not commander_ev.search(segment):
            problems.append(f"{name}: declared commander_ticket without Commander evidence")
        elif cls == "approval_gated" and not approval_ev.search(segment):
            problems.append(f"{name}: declared approval_gated without approval evidence")
        elif cls == "safety_stop" and ("audit_safety_action" not in segment or _re.search(r"\.(start|resume|run_once)\(", segment)):
            problems.append(f"{name}: safety_stop must be audited and must never start work")
        elif cls == "guard_only" and _re.search(r"daemon\.|submit_and_wait|\.enqueue\(|_send\(20", segment):
            problems.append(f"{name}: guard_only block performs an action")

    for key, segs in blocks.items():
        if key in reg.STATUS_API_POST_BLOCKS:
            for seg in segs:
                check(f"status_api {key[:60]}", reg.STATUS_API_POST_BLOCKS[key][0], seg)
    ctrl = (SRC / "muse" / "control_center_api.py").read_text(encoding="utf-8")
    tree = ast.parse(ctrl)
    functions = {n.name: ast.get_source_segment(ctrl, n) or "" for n in tree.body if isinstance(n, ast.FunctionDef)}
    route_post = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_route_post")
    actual_ctrl: dict[str, set[str]] = {}
    body_stmts = list(route_post.body)
    for index, stmt in enumerate(body_stmts):
        in_fstring = {id(v) for j in ast.walk(stmt) if isinstance(j, ast.JoinedStr) for v in j.values}
        literals = [c.value for c in ast.walk(stmt) if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    and c.value.startswith("/api/v1/control/") and id(c) not in in_fstring]
        for jstr in (c for c in ast.walk(stmt) if isinstance(c, ast.JoinedStr)):
            literals.append("".join(v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}"
                                    for v in jstr.values))
        # `match = re.fullmatch(...)` is followed by `if match: ...` that calls the handler.
        scope = [stmt] + ([body_stmts[index + 1]] if isinstance(stmt, ast.Assign) and index + 1 < len(body_stmts) else [])
        called = {c.func.id for part in scope for c in ast.walk(part) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        for lit in literals:
            if lit.startswith("/api/v1/control/"):
                actual_ctrl.setdefault(lit, set()).update(called)
    problems += [f"unregistered control route: {k}" for k in sorted(set(actual_ctrl) - set(reg.CONTROL_POST_ROUTES))]
    problems += [f"stale control route: {k}" for k in sorted(set(reg.CONTROL_POST_ROUTES) - set(actual_ctrl))]
    for route, fns in actual_ctrl.items():
        if route in reg.CONTROL_POST_ROUTES:
            # one level of delegation (e.g. creation_feedback -> _creation_feedback)
            seg = "\n".join(functions.get(f, "") for f in fns)
            inner = {c.func.id for f in fns if f in functions for c in ast.walk(ast.parse(functions[f]))
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            seg += "\n" + "\n".join(functions.get(f, "") for f in inner)
            check(f"control {route}", reg.CONTROL_POST_ROUTES[route][0], seg)
    auto = (SRC / "muse" / "autonomous_runtime.py").read_text(encoding="utf-8")
    handlers_src = auto[auto.find("self._routine_handlers = {"):]
    handlers_src = handlers_src[:handlers_src.find("\n        }") + 10]
    routines = set(_re.findall(r'^\s+"([a-z_]+)":', handlers_src, _re.M))
    if _re.search(r"^\s+ROUTINE_KIND:", handlers_src, _re.M):
        radar = (SRC / "muse" / "autonomous_value_radar.py").read_text(encoding="utf-8")
        routines |= set(_re.findall(r'^ROUTINE_KIND\s*=\s*"([a-z_]+)"', radar, _re.M))
    routines |= set(_re.findall(r'\(\s*"[a-z_-]+",\s*"([a-z_]+)",', auto[auto.find("    ROUTINES"):auto.find("    ROUTINES") + 2500]))
    problems += [f"unregistered routine: {r}" for r in sorted(routines - set(reg.ROUTINES))]
    if "check_global_gate(self.workspace)" not in auto[auto.find("def run_tick"):auto.find("def _run_tick_unguarded")]:
        problems.append("routines declared kill_switch_gated but run_tick lacks check_global_gate")
    worker = (SRC / "muse" / "worker.py").read_text(encoding="utf-8")
    handler_block = _re.search(r"DEFAULT_HANDLERS[^=]*=\s*\{(.*?)\n\}", worker, _re.S)
    handlers = set(_re.findall(r'"([a-z_]+)"\s*:', handler_block.group(1))) if handler_block else set()
    handlers |= set(_re.findall(r'handlers\["([a-z_]+)"\]\s*=', auto))
    problems += [f"unregistered worker handler: {h}" for h in sorted(handlers - set(reg.WORKER_HANDLERS))]
    if "claim_next(" not in worker:
        problems.append("worker handlers declared legacy_ticket but worker.py no longer claims tickets")
    vtw = (SRC / "muse" / "video_task_worker.py").read_text(encoding="utf-8")
    check("hook trigger_publish_bundle_after_video", reg.HOOKS["video_task_worker.trigger_publish_bundle_after_video"][0],
          vtw[vtw.find("def trigger_publish_bundle_after_video"):vtw.find("def trigger_publish_bundle_after_video") + 1500])
    pp = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    sect = pp[pp.find("[project.scripts]"):]
    sect = sect[:sect.find("\n[", 5)] if "\n[" in sect[5:] else sect
    scripts = {f"{n}={m}" for n, m in _re.findall(r'^([\w-]+)\s*=\s*"([\w.]+:\w+)"', sect, _re.M)}
    mains = {"muse." + p.stem for p in (SRC / "muse").glob("*.py") if '__name__ == "__main__"' in p.read_text(encoding="utf-8")}
    problems += [f"unregistered console script: {s}" for s in sorted(scripts - reg.CONSOLE_SCRIPTS)]
    problems += [f"unregistered python -m module: {m}" for m in sorted(mains - reg.MAIN_MODULES)]
    problems += [f"stale console script: {s}" for s in sorted(reg.CONSOLE_SCRIPTS - scripts)]
    debt = sorted(k for k, (cls, _why) in reg.all_entries().items() if cls == "debt")
    debt_blocks = sum(len(blocks.get(k[len("http:status_api:"):], [None])) if k.startswith("http:status_api:") else 1 for k in debt)
    if debt_blocks > reg.DEBT_CEILING:
        problems.append(f"entry-point debt grew: {debt_blocks} blocks > ceiling {reg.DEBT_CEILING}")
    require(not problems, f"{len(problems)} entry-point audit problems: {problems[:20]}")
    counts: dict[str, int] = {}
    for cls, _why in reg.all_entries().values():
        counts[cls] = counts.get(cls, 0) + 1
    return {"classes": counts, "debt": debt, "debt_blocks": debt_blocks, "cli": len(scripts | mains), "status_blocks": len(blocks),
            "control_routes": len(actual_ctrl), "routines": sorted(routines), "worker_handlers": sorted(handlers)}


@probe("L4.8", "L4", "插件 Manifest 校验", ("AC-060",))
def l4_plugin(ctx: Ctx):
    from muse.plugin_platform import PluginError, validate_manifest
    for bad in ({}, {"id": "../evil", "version": "x"}):
        try:
            validate_manifest(bad); raise Check(f"invalid manifest accepted: {bad}")
        except (PluginError, ValueError, TypeError, KeyError):
            pass
    return {"rejected": 2}


# =============================== L5 · deterministic workflows ===============================
@probe("L5.B", "L5", "Workflow B 统帅调度与可信交付闭环", ("AC-031", "AC-032", "AC-033"))
def l5_b(ctx: Ctx):
    root = ctx.sub("commander wf-b")
    from muse.muse_commander import MuseCommander
    with MuseCommander(root) as cmd:
        cmd.register_handler("sim", _ok_handler)
        b_tasks, b_usage = table_counts(root / "tasks.sqlite3"), table_counts(root / "usage.sqlite3")
        plan = cmd.submit_goal("把灵感做成可验证交付", [
            {"key": "plan", "role": "planner", "objective": "拆解目标", "handler": "sim"},
            {"key": "build", "role": "researcher", "objective": "生成草稿", "handler": "sim", "depends_on": ["plan"]},
            {"key": "ship", "role": "operator", "objective": "发布 release", "handler": "sim", "depends_on": ["build"]}])
        cmd.drain()
        cmd.approve(plan["tickets"][2]["task_id"], approved_by="user:owner")
        cmd.drain()
        chain = cmd.verify_receipt_chain()
        status = cmd.status()
        a_tasks, a_usage = table_counts(root / "tasks.sqlite3"), table_counts(root / "usage.sqlite3")
    require(status["tasks"].get("succeeded") == 3, f"not all succeeded: {status['tasks']}")
    require(chain["ok"] and chain["count"] == 3, f"receipt chain: {chain}")
    tampered = (root / "receipts.jsonl").read_text(encoding="utf-8").replace("worker:", "w0rker:", 1)
    (root / "receipts.jsonl").write_text(tampered, encoding="utf-8")
    with MuseCommander(root) as cmd2:
        require(not cmd2.verify_receipt_chain()["ok"], "tampered receipt chain still verifies")
    du = state_diff(b_usage, a_usage)
    require(sum(du.values()) >= 3, f"usage ledger not written: {du}")
    return {"task_diff": state_diff(b_tasks, a_tasks), "usage_diff": du, "receipts": chain}


@probe("L5.A", "L5", "Workflow A 求真与创造闭环", ("AC-041", "AC-042", "AC-043", "AC-044", "AC-045"))
def l5_a(ctx: Ctx):
    from muse.metaphysics_label import symbolic_to_hypotheses, write_hypotheses_to_wiki
    from muse.obsidian_vault import export_research_wiki
    from muse.research_wiki import WIKI_OBJECTS, ResearchWiki
    from muse.xuanshu_fusion import XuanshuLabelError, fuse, validate_labels
    from muse.epistemics import EpistemicTagError
    db = ctx.sub("wiki") / "knowledge 库.sqlite3"
    fused = fuse("如何让知识之火在个人系统中持续传递？", seed="muse-diag-seed", symbolic_fallback=True)
    validate_labels(fused)
    forged = json.loads(json.dumps(fused, ensure_ascii=False)); forged["is_evidence"] = True
    try:
        validate_labels(forged); raise Check("symbolic result upgraded to evidence")
    except XuanshuLabelError:
        pass
    hyps = symbolic_to_hypotheses(fused)
    with ResearchWiki(db) as wiki:
        before = table_counts(db)
        written = write_hypotheses_to_wiki(wiki.knowledge, hyps)
        claim = wiki.add_claim("每日回顾能提高长期记忆保持率", tag="hypothesis")
        s1 = wiki.add_source("https://example.org/study-a", title="Study A")
        try:
            wiki.add_evidence(claim, s1, stance="supports", excerpt="..."); raise Check("pending URL used as evidence")
        except EpistemicTagError:
            pass
        empty = wiki.evidence_split(claim)
        require(empty["verdict"] == "insufficient_evidence", f"no-evidence verdict: {empty}")
        s1 = wiki.mark_source_read(s1, excerpt="回顾组保持率更高", content_sha256="a" * 64)
        s2 = wiki.add_source("https://news.example.org/copy", title="Syndicated", syndicated_from="example.org")
        s2 = wiki.mark_source_read(s2, excerpt="转载 Study A", content_sha256="b" * 64)
        s3 = wiki.add_source("https://other-lab.net/replication", title="Replication fails")
        s3 = wiki.mark_source_read(s3, excerpt="复现未发现差异", content_sha256="c" * 64)
        wiki.add_evidence(claim, s1, stance="supports", excerpt="回顾组保持率更高")
        wiki.add_evidence(claim, s2, stance="supports", excerpt="转载 Study A")
        wiki.add_evidence(claim, s3, stance="refutes", excerpt="复现未发现差异")
        split = wiki.evidence_split(claim)
        wiki.add_experiment(claim, "两周 A/B：每日回顾 vs 不回顾", success_signal="7 日保持率差 ≥10%")
        wiki.add_decision(claim, "暂不作为默认策略", rationale="证据存在争议")
        wiki.add_failure(claim, "首轮实验样本不足", lesson="样本量 < 20 无法得出结论")
        wiki.add_alternative(claim, "差异来自练习时长而非回顾本身")
        census = wiki.object_census()
        vault = ctx.sub("wiki vault")
        wiki_ids = [r[0] for r in wiki.knowledge.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE deleted_at IS NULL AND "
            "json_extract(metadata_json,'$.wiki_object') IS NOT NULL ORDER BY id LIMIT 200").fetchall()]
        exported = export_research_wiki(wiki.knowledge, vault, node_ids=wiki_ids)
        require(exported["created"] == len(wiki_ids) and exported["existing_notes_overwritten"] == 0, f"wiki export: {exported}")
        after = table_counts(db)
        claim_node = wiki.knowledge.get_node(claim["id"])
    require(split["supporting_raw"] == 2 and split["supporting_independent"] == 1, f"syndication not collapsed: {split}")
    require(split["verdict"] == "contested", f"verdict: {split}")
    missing = [o for o in WIKI_OBJECTS if census.get(o, 0) < 1]
    require(not missing, f"wiki objects missing: {missing} census={census}")
    require(claim_node["status"] not in ("supported",), "claim auto-promoted to supported")
    require(any(vault.rglob("*.md")), "research wiki export wrote nothing")
    return {"census": census, "split": split, "symbolic_claims": len(written.get("claims", [])) if isinstance(written, dict) else None,
            "state_diff": state_diff(before, after), "exported": str(exported)[:200]}


@probe("L5.R", "L5", "生产研究流水线写入 Research Wiki 契约（离线确定性替身）", ("AC-046",))
def l5_pipeline_wiki(ctx: Ctx):
    from muse.research_fetch import ResearchEvidenceStore
    from muse.research_pipeline import ResearchPipeline
    from muse.research_wiki import ResearchWiki

    read_url = "https://93.184.216.34/study-retention"
    hit_only_url = "https://93.184.216.35/unread-hit"
    body = ("每日回顾组在第七天的记忆保持率显著高于对照组。" * 20)

    class SimSearch:
        def search(self, query, limit=8):
            return [{"url": read_url, "title": "记忆保持 研究 retention study", "snippet": f"{query} retention 记忆",
                     "engine": "sim"},
                    {"url": hit_only_url, "title": "记忆保持 研究 retention unread", "snippet": f"{query} retention 记忆",
                     "engine": "sim"}]

    class SimReader:
        def read(self, url, **_kw):
            if url != read_url:
                raise OSError("sim reader: not fetched")
            return {"url": url, "text": body, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    "fetched_at": datetime.now(timezone.utc).isoformat(), "truncated": False,
                    "content_type": "text/html"}

    d = ctx.sub("pipeline wiki")
    with ResearchWiki(d / "knowledge.sqlite3") as wiki:
        evidence = ResearchEvidenceStore(d / "evidence.sqlite3")
        try:
            pipeline = ResearchPipeline(SimSearch(), SimReader(), evidence, max_workers=2,
                                        knowledge_store=wiki.knowledge)
            before = table_counts(d / "knowledge.sqlite3")
            report = pipeline.run(("记忆保持 retention",), max_sources=2)
            after = table_counts(d / "knowledge.sqlite3")
        finally:
            evidence.connection.close()
        rows = {r[0]: (json.loads(r[1]) if r[1] else {}) for r in wiki.knowledge.connection.execute(
            "SELECT external_ref, metadata_json FROM knowledge_nodes WHERE node_type='source'")}
        census = wiki.object_census()
        claim = wiki.add_claim("每日回顾提高第七天保持率", tag="hypothesis")
        read_node = wiki.knowledge.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE external_ref=?", (read_url,)).fetchone()[0]
        hit_node = wiki.knowledge.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE external_ref=?", (hit_only_url,)).fetchone()[0]
        wiki.add_evidence(claim, wiki.knowledge.get_node(read_node), stance="supports", excerpt=body[:60])
        from muse.epistemics import EpistemicTagError
        try:
            wiki.add_evidence(claim, wiki.knowledge.get_node(hit_node), stance="supports", excerpt="x")
            raise Check("unread search hit from production pipeline was accepted as evidence")
        except EpistemicTagError:
            pass
    require(rows.get(read_url, {}).get("fetch_status") == "read", f"read page not marked read: {rows.get(read_url)}")
    require(rows.get(hit_only_url, {}).get("fetch_status") == "pending_fetch", f"hit not pending: {rows.get(hit_only_url)}")
    require(rows[read_url].get("epistemic_tag") == "observation" and rows[read_url].get("wiki_object") == "source", "contract fields missing")
    require(census.get("source", 0) >= 2, f"pipeline sources not counted as wiki objects: {census}")
    require(state_diff(before, after).get("knowledge_nodes", 0) >= 2, "pipeline did not persist nodes")
    return {"census": census, "pages": len(report.get("pages", [])), "failures": report.get("failures", [])[:5]}


@probe("L5.R2", "L5", "Research claim 状态机：supported 只能由证据驱动（伪造边/他 claim 证据/未读来源/单一来源/直接改状态/未运行或失败实验 均无法绕过 insufficient_evidence）", ("AC-102",))
def l5_research_claim_state_machine(ctx: Ctx):
    from muse.research_wiki import ResearchWiki
    d = ctx.sub("research claim 状态机")
    with ResearchWiki(d / "knowledge.sqlite3") as wiki:
        k = wiki.knowledge
        claim = wiki.add_claim("午睡 20 分钟提升下午专注度", tag="hypothesis")
        other = wiki.add_claim("另一个无关的断言", tag="hypothesis")
        for i, dom in enumerate(("forged-a.org", "forged-b.net")):          # 1 forged supports edges
            fake = k.upsert_node("idea", f"伪造支持 {i}", metadata={"registrable_domain": dom, "stance": "supports"})
            k.add_edge(fake["id"], claim["id"], "supports")
        dec = wiki.add_decision(claim, "就当它成立", rationale="伪造")
        k.add_edge(dec["id"], claim["id"], "supports")
        split = wiki.evidence_split(claim)
        require(split["verdict"] == "insufficient_evidence" and split["supporting_raw"] == 0 and split["ignored_edges"] >= 3,
                f"forged supports edges were counted: {split}")
        s_a = wiki.mark_source_read(wiki.add_source("https://lab-a.org/nap", title="A"), excerpt="午睡组专注度更高", content_sha256="a" * 64)
        s_b = wiki.mark_source_read(wiki.add_source("https://lab-b.net/nap", title="B"), excerpt="复现：午睡组更专注", content_sha256="b" * 64)
        ev_other = wiki.add_evidence(other, s_a, stance="supports", excerpt="午睡组专注度更高")   # 2 another claim's evidence
        k.add_edge(ev_other["id"], claim["id"], "supports")
        pending = wiki.add_source("https://lab-c.io/unread", title="C (unread)")                 # 3 evidence-shaped forgery
        forged = k.upsert_node("idea", "[supports] forged", external_ref=f"wiki-evidence:{claim['id']}:{pending['id']}:supports",
                               metadata={"wiki_object": "evidence", "stance": "supports", "claim_id": claim["id"],
                                         "source_id": pending["id"], "registrable_domain": "lab-c.io"})
        k.add_edge(forged["id"], claim["id"], "supports")
        split = wiki.evidence_split(claim)
        require(split["verdict"] == "insufficient_evidence" and split["supporting_raw"] == 0,
                f"foreign or forged evidence was counted: {split}")
        wiki.add_evidence(claim, s_a, stance="supports", excerpt="午睡组专注度更高")                # 4 one source only
        one = wiki.evidence_split(claim)
        require(one["verdict"] != "supported" and one["supporting_independent"] == 1, f"a single source promoted the claim: {one}")
        wiki.add_evidence(claim, s_b, stance="supports", excerpt="复现：午睡组更专注")
        two = wiki.evidence_split(claim)
        require(two["verdict"] == "supported" and two["honest"], f"two independent read sources should support: {two}")
        wiki.add_failure(claim, "内部复现失败", lesson="falsified in our own A/B")                # 5 own failure blocks it
        blocked = wiki.evidence_split(claim)
        require(blocked["verdict"] != "supported", f"an own falsifying failure did not block supported: {blocked}")
        for attempt in (lambda: k.upsert_node("claim", "直接已支持", status="supported"),          # 6 direct status write
                        lambda: k.upsert_node("claim", claim["title"], external_ref=claim.get("external_ref"), status="supported")):
            try:
                attempt()
                raise Check("claim status written without transition_claim")
            except ValueError:
                pass
        designed = wiki.add_experiment(claim, "两周 A/B", success_signal="专注评分 +10%")          # 7 non-successful runs
        failed = k.upsert_node("experiment", "失败的运行", external_ref="run:diag-failed",
                               metadata={"status": "failed", "raw_evidence_sha256": "c" * 64})
        no_hash = k.upsert_node("experiment", "无原始输出", external_ref="run:diag-nohash", metadata={"status": "succeeded"})
        for exp in (designed, failed, no_hash):
            try:
                k.transition_claim(claim["id"], "supported", evidence_refs=(exp["id"],), reviewer_id="diag-critic")
                raise Check(f"claim supported by experiment {exp['title']!r}")
            except ValueError:
                pass
        require(k.get_node(claim["id"])["status"] == "proposed", "claim status changed by refused transitions")
    return {"forged_ignored": split["ignored_edges"], "two_source_verdict": two["verdict"], "after_own_failure": blocked["verdict"]}


@probe("L5.A2", "L5", "Workflow A 经统帅票据端到端：灵感→研究→沉淀→原型→进化提案（人工门）", ("AC-048",))
def l5_workflow_a_commander(ctx: Ctx):
    from muse.workflow_a import approve_and_finish, run_workflow_a
    ws = ctx.sub("workflow a 工作区")
    vault = ctx.sub("workflow a vault")
    first = run_workflow_a(ws, "如何让知识之火在个人系统中持续传递？", obsidian_vault=vault, plan_id="diagwfa000000001")
    st = first["statuses"]
    require(all(st[k] == "succeeded" for k in ("inspire", "research", "memory", "build")), f"statuses: {st} {first['outcomes']}")
    require(st["evolve"] == "waiting_approval", f"evolution proposal not held for a human: {st}")
    run_dir = Path(first["run_dir"])
    require(not (run_dir / "evolve.json").exists(), "evolution step ran before approval")
    inspire = json.loads((run_dir / "inspire.json").read_text(encoding="utf-8"))
    research = json.loads((run_dir / "research.json").read_text(encoding="utf-8"))
    build = json.loads((run_dir / "build.json").read_text(encoding="utf-8"))
    require(inspire["claim_ids"] and inspire["is_evidence"] is False, "inspire state")
    require(set(research["verdicts"].values()) == {"insufficient_evidence"}, f"dishonest offline verdict: {research}")
    html = ws / build["prototype"] / "index.html"
    require(html.is_file() and hashlib.sha256(html.read_bytes()).hexdigest() == build["html_sha256"], "prototype missing/tampered")
    require(any(vault.rglob("*.md")), "Obsidian create-only export wrote nothing")
    _con = sqlite3.connect(ws / "data" / "memory" / "muse.sqlite3")
    try:
        mem_counts = _con.execute("SELECT track, count(*) FROM memories GROUP BY track").fetchall()
    finally:
        _con.close()
    require(dict(mem_counts).get("user", 0) == 0 and dict(mem_counts).get("agent", 0) >= 1, f"track leak: {mem_counts}")
    try:
        approve_and_finish(ws, first, approved_by="agent:architect")
        raise Check("agent approved its own evolution proposal")
    except PermissionError:
        pass
    done = approve_and_finish(ws, first, approved_by="user:owner")
    require(done["statuses"]["evolve"] == "succeeded", f"evolve after approval: {done}")
    proposal = json.loads((run_dir / "evolve.json").read_text(encoding="utf-8"))
    require(proposal["patch_applied"] is False and proposal["status"] == "draft_not_applied", "evolution applied a patch")
    require(done["receipts"]["ok"] and done["receipts"]["count"] == 5, f"receipts: {done['receipts']}")
    return {"statuses": done["statuses"], "claims": len(inspire["claim_ids"]), "prototype": build["prototype"]}


@probe("L5.A3", "L5", "status_api 灵感/研究写接口经统帅票据（落盘票据+凭证+成本账本；绕过被拒）", ("AC-049",))
def l5_api_commander_routes(ctx: Ctx):
    from muse.commander_routes import BypassCommanderError, capture_inspiration_core
    from muse.muse_commander import MuseCommander
    from muse.research_knowledge import ResearchKnowledgeStore
    from muse.status_api import create_status_server
    from muse.task_queue import TaskQueue
    ws = ctx.sub("api routes ws 接口")
    data = ws / "data"
    for sub in ("tasks", "progress", "capabilities", "research"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    kdb = data / "research" / "knowledge.sqlite3"
    ResearchKnowledgeStore(kdb).close()
    token = "diag-" + hashlib.sha256(os.urandom(16)).hexdigest()
    server, bearer = create_status_server(task_db=data / "tasks" / "muse.sqlite3", progress_db=data / "progress" / "p.sqlite3",
                                          capability_db=data / "capabilities" / "c.sqlite3", knowledge_db=kdb, port=0, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def call(method: str, route: str, body: dict | None = None) -> tuple[int, dict]:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {bearer}"}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=raw, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    croot = data / "commander"
    try:
        s1, cap = call("POST", "/api/v1/inspirations", {"content": "把灵感变成可检验的实验 🔥", "allow_external_research": False})
        require(s1 == 201 and cap.get("commander", {}).get("status") == "succeeded", f"capture: {s1} {cap}")
        s1b, cap2 = call("POST", "/api/v1/inspirations", {"content": "把灵感变成可检验的实验 🔥", "allow_external_research": False})
        require(s1b == 201 and cap2["inspiration"]["id"] == cap["inspiration"]["id"]
                and cap2["commander"]["ticket_id"] == cap["commander"]["ticket_id"], f"idempotent replay: {cap2}")
        rid_low, rid_video = "a" * 32, "b" * 32
        s2, evo = call("POST", "/api/v1/inspirations/evolve", {"request_id": rid_low})
        require(s2 == 202 and evo["commander"]["status"] == "succeeded" and evo["task_id"], f"evolve low: {s2} {evo}")
        s3, evo_v = call("POST", "/api/v1/inspirations/evolve", {"request_id": rid_video, "enqueue_video": True})
        require(s3 == 202 and evo_v["status"] == "pending_approval" and evo_v["task_id"] is None, f"evolve video: {s3} {evo_v}")
        with TaskQueue(data / "tasks" / "muse.sqlite3") as runtime_q:
            require(runtime_q.get_by_idempotency_key("inspiration-evolution:" + rid_low) is not None, "low-risk evolution not handed off")
            require(runtime_q.get_by_idempotency_key("inspiration-evolution:" + rid_video) is None, "video evolution enqueued before approval")
        held = evo_v["commander"]["ticket_id"]
        sa, _ = call("POST", "/api/v1/commander/approve", {"task_id": held, "approved_by": "agent:self", "confirmation": "APPROVE"})
        require(sa == 403, f"agent approval not refused: {sa}")
        sb, appr = call("POST", "/api/v1/commander/approve", {"task_id": held, "approved_by": "user:owner", "confirmation": "APPROVE"})
        require(sb == 200 and (appr.get("execution") or {}).get("status") == "succeeded", f"approve+run: {sb} {appr}")
        with TaskQueue(data / "tasks" / "muse.sqlite3") as runtime_q:
            require(runtime_q.get_by_idempotency_key("inspiration-evolution:" + rid_video) is not None, "approved evolution not handed off")
        s4, xs = call("POST", "/api/v1/control/xuanshu/hypotheses", {"question": "知识之火如何传递？", "seed": "diag-a3",
                                                                     "write_to_wiki": True, "confirmation": "WRITE-HYPOTHESES"})
        require(s4 == 201 and xs.get("commander", {}).get("status") == "succeeded" and xs["written"]["claims"], f"xuanshu: {s4} {str(xs)[:400]}")
    finally:
        server.shutdown(); server.server_close()
    tickets = [cap["commander"]["ticket_id"], evo["commander"]["ticket_id"], held, xs["commander"]["ticket_id"]]
    with MuseCommander(croot) as cmd:
        statuses = {t: cmd.queue.get(t)["status"] for t in tickets}
        chain = cmd.verify_receipt_chain()
        stored = {t: cmd.stored_result(t) for t in tickets}
    require(set(statuses.values()) == {"succeeded"}, f"ticket statuses: {statuses}")
    require(chain["ok"] and chain["count"] == len(tickets), f"receipt chain: {chain}")
    receipts = [json.loads(line) for line in (croot / "receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    require({r["task_id"] for r in receipts} == set(tickets), "receipts do not match tickets")
    require(all(stored[t] and stored[t]["result"]["receipt_sha256"] for t in tickets), "results not persisted to disk")
    usage_rows = sum(table_counts(croot / "usage.sqlite3").values())
    require(usage_rows >= len(tickets), f"cost ledger rows {usage_rows} < tickets {len(tickets)}")
    with ResearchKnowledgeStore(kdb) as k:
        node = k.get_node(cap["inspiration"]["id"])
        nodes_before = k.connection.execute("SELECT count(*) FROM knowledge_nodes").fetchone()[0]
    require(node["metadata"].get("commander_task_id") == cap["commander"]["ticket_id"], "node not linked to its ticket")
    fields = {"content": "绕过尝试", "title": "x", "allow_external_research": False, "tags": [], "research_domains": [], "source": "user_manual"}
    for forged in (None, {"task_id": tickets[0], "commander_root": str(croot)}, {"task_id": "f" * 32, "commander_root": str(croot)}):
        try:
            capture_inspiration_core(ws, kdb, fields, commander_ctx=forged)
            raise Check(f"bypass accepted with ctx={forged}")
        except BypassCommanderError:
            pass
    with ResearchKnowledgeStore(kdb) as k:
        require(k.connection.execute("SELECT count(*) FROM knowledge_nodes").fetchone()[0] == nodes_before, "bypass wrote data")
    audit = (croot / "bypass_audit.jsonl").read_text(encoding="utf-8").splitlines()
    require(len(audit) >= 3, f"bypass attempts not audited: {audit}")
    return {"tickets": statuses, "receipts": chain["count"], "usage_rows": usage_rows, "bypass_audited": len(audit)}


@probe("L5.C1", "L5", "Workflow C 经统帅票据端到端：机会→制作→成本→交付包→收益回写（发布人工门）", ("AC-076",))
def l5_workflow_c_commander(ctx: Ctx):
    from muse.workflow_c import approve_publish, run_workflow_c
    ws = ctx.sub("workflow c 工作区")
    first = run_workflow_c(ws, "为个人创作者做一个灵感整理小工具", plan_id="diagwfc000000001")
    st = first["statuses"]
    require(all(st[k] == "succeeded" for k in ("opportunity", "produce", "cost", "bundle", "revenue")), f"statuses: {st} {first['outcomes']}")
    require(st["publish"] == "waiting_approval", f"publish not held for a human: {st}")
    rd = Path(first["run_dir"])
    require(not (rd / "publish.json").exists(), "publish step ran before approval")
    opp = json.loads((rd / "opportunity.json").read_text(encoding="utf-8"))
    require(opp["is_forecast"] is False and opp["epistemic_tag"] == "hypothesis", f"opportunity labels: {opp}")
    con = sqlite3.connect(ws / "data" / "business" / "opportunities.sqlite3")
    try:
        ev = con.execute("SELECT epistemic_status FROM business_opportunity_evidence").fetchall()
    finally:
        con.close()
    require(ev and {e[0] for e in ev} == {"assumption"}, f"opportunity evidence must stay an assumption: {ev}")
    prod = json.loads((rd / "produce.json").read_text(encoding="utf-8"))
    for key, path in (("index.html", prod["prototype_html"]), ("offer_brief.md", prod["brief"])):
        require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == prod["sha256"][key], f"{key} tampered/missing")
    cost = json.loads((rd / "cost.json").read_text(encoding="utf-8"))
    require(cost["usage_rows"] >= 2 and cost["cost_kind"] == "estimated", f"cost accounting: {cost}")
    manifest = json.loads((rd / "bundle" / "publish_manifest.json").read_text(encoding="utf-8"))
    require(manifest["all_artifacts_valid"] and manifest["publish_allowed"] is False
            and manifest["status"] == "awaiting_human_publish_approval", f"bundle: {manifest['status']}")
    rev = json.loads((rd / "revenue.json").read_text(encoding="utf-8"))
    require(rev["confirmed_revenue"] == "unknown" and rev["verified"] is False, f"fabricated revenue: {rev}")
    ledger_rows = table_counts(ws / "data" / "revenue" / "ledger.sqlite3").get("ledger_entries", 0)
    require(ledger_rows == 0, f"revenue ledger got {ledger_rows} invented entries")
    mcon = sqlite3.connect(ws / "data" / "memory" / "muse.sqlite3")
    try:
        tracks = dict(mcon.execute("SELECT track, count(*) FROM memories GROUP BY track").fetchall())
    finally:
        mcon.close()
    require(tracks.get("user", 0) == 0 and tracks.get("agent", 0) >= 1, f"track leak: {tracks}")
    try:
        approve_publish(ws, first, approved_by="agent:operator")
        raise Check("agent identity approved the external publish step")
    except PermissionError:
        pass
    done = approve_publish(ws, first, approved_by="user:owner")
    require(done["statuses"]["publish"] == "succeeded", f"publish after approval: {done}")
    pub = json.loads((rd / "publish.json").read_text(encoding="utf-8"))
    after = json.loads((rd / "bundle" / "publish_manifest.json").read_text(encoding="utf-8"))
    require(pub["published_by_muse"] is False and pub["approved_by"] == "user:owner", f"publish state: {pub}")
    require(after["status"] == "approved_for_manual_publish" and after["publish_allowed"] is False
            and after["publish_mode"] == "manual_by_human_only", f"manifest after approval: {after}")
    require(done["receipts"]["ok"] and done["receipts"]["count"] == 6, f"receipts: {done['receipts']}")
    return {"statuses": done["statuses"], "cost": cost["total_cost_usd"], "revenue": rev["confirmed_revenue"]}


@probe("L5.C3", "L5", "视频成片完成钩子→Workflow C 交付包+凭证链（复用 T3 票据，发布人工门）", ("AC-077",))
def l5_video_completion_bundle(ctx: Ctx):
    exe = shutil.which("ffmpeg")
    if not exe:
        raise Skip("ffmpeg not on PATH; cannot produce a real video package")
    from muse.initiative_engine import InitiativeStore
    from muse import video_task_worker as vtw
    tree = ast.parse(Path(vtw.__file__).read_text(encoding="utf-8"))
    hook_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", "") == "trigger_publish_bundle_after_video"]
    require(len(hook_calls) >= 2, f"completion hook not wired into both video success paths: {len(hook_calls)}")
    ws = ctx.sub("video hook ws 视频")
    with InitiativeStore(ws / "data" / "initiatives" / "muse.sqlite3") as store:
        item = store.propose("视频成片测试", kind="creative_output", creator_value="v", concept="c", first_step="f",
                             expected_output="mp4")
        initiative_id = item["id"]
        pdir = ws / "data" / "products" / initiative_id / "v1"
        pdir.mkdir(parents=True)
        r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1",
                            "-f", "lavfi", "-i", "sine=frequency=330:duration=1", "-shortest", "-c:v", "mpeg4", "-c:a", "aac",
                            str(pdir / "video.mp4")], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        require(r.returncode == 0, f"ffmpeg: {r.stderr[-400:]}")
        r2 = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(pdir / "video.mp4"), "-frames:v", "1",
                             str(pdir / "cover.jpg")], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        require(r2.returncode == 0, f"cover: {r2.stderr[-400:]}")
        (pdir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n成片字幕\n", encoding="utf-8")
        package_sha = hashlib.sha256((pdir / "video.mp4").read_bytes()).hexdigest()
        store.record_product_artifact(initiative_id, relative_path=f"data/products/{initiative_id}/v1",
                                      package_sha256=package_sha, validated=True)
    out = vtw.trigger_publish_bundle_after_video(ws, initiative_id=initiative_id, package_sha256=package_sha)
    require(out.get("statuses", {}).get("bundle") == "succeeded" and out["statuses"]["revenue"] == "succeeded", f"hook: {out}")
    require(out["statuses"]["publish"] == "waiting_approval", f"publish not held: {out['statuses']}")
    rd = Path(out["run_dir"])
    manifest = json.loads((rd / "bundle" / "publish_manifest.json").read_text(encoding="utf-8"))
    names = {Path(a["path"]).name for a in manifest["artifacts"]}
    require(manifest["all_artifacts_valid"] and manifest["publish_allowed"] is False and names == {"video.mp4", "subtitles.srt", "cover.jpg"},
            f"video bundle: {manifest.get('status')} {names}")
    require(out["receipts"]["ok"] and out["receipts"]["count"] == 2, f"receipt chain: {out['receipts']}")
    again = vtw.trigger_publish_bundle_after_video(ws, initiative_id=initiative_id, package_sha256=package_sha)
    require(again["plan_id"] == out["plan_id"] and again["receipts"]["count"] == 2, f"hook not idempotent: {again['receipts']}")
    with InitiativeStore(ws / "data" / "initiatives" / "muse.sqlite3") as store:
        bad = store.propose("坏视频包", kind="creative_output", creator_value="v", concept="c", first_step="f", expected_output="mp4")
        bdir = ws / "data" / "products" / bad["id"] / "v1"
        bdir.mkdir(parents=True)
        shutil.copyfile(pdir / "video.mp4", bdir / "video.mp4")
        bad_sha = hashlib.sha256(b"bad-package").hexdigest()
        store.record_product_artifact(bad["id"], relative_path=f"data/products/{bad['id']}/v1", package_sha256=bad_sha, validated=True)
    broken = vtw.trigger_publish_bundle_after_video(ws, initiative_id=bad["id"], package_sha256=bad_sha)
    require(broken.get("statuses", {}).get("bundle") == "failed" and broken["statuses"]["publish"] != "succeeded",
            f"package without subtitles was bundled: {broken}")
    return {"plan_id": out["plan_id"], "statuses": out["statuses"], "broken": broken["statuses"]}


def _nvidia_smi() -> str | None:
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for candidate in (system_root / "System32" / "nvidia-smi.exe",
                      Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe")):
        if candidate.is_file():
            return str(candidate)
    # DCH drivers ship nvidia-smi only inside the driver store.
    store = system_root / "System32" / "DriverStore" / "FileRepository"
    if store.is_dir():
        for candidate in sorted(store.glob("nv*/nvidia-smi.exe")):
            if candidate.is_file():
                return str(candidate)
    return None


def _gpu_memory(exe: str) -> dict[str, int]:
    r = subprocess.run([exe, "--query-gpu=memory.used,memory.free,memory.total", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    if r.returncode != 0 or not r.stdout.strip():
        raise Skip(f"nvidia-smi query failed rc={r.returncode}: {r.stderr.strip()[:200]}")
    used, free, total = (int(float(x)) for x in r.stdout.strip().splitlines()[0].split(","))
    return {"used_mib": used, "free_mib": free, "total_mib": total}


@probe("L5.M1", "L5", "本地模型最小推理（max_tokens≤16、显存保护、仅用已加载模型、结束后显存回落）", ("AC-093",))
def l5_local_model_min_inference(ctx: Ctx):
    exe = _nvidia_smi()
    if not exe:
        raise Skip("nvidia-smi not found: GPU memory cannot be measured, refusing to run inference blind")
    before = _gpu_memory(exe)
    min_free = int(os.environ.get("MUSE_DIAG_MIN_FREE_VRAM_MIB", "1024"))
    if before["free_mib"] < min_free:
        raise Skip(f"insufficient free VRAM: {before['free_mib']} MiB < {min_free} MiB")
    base = os.environ.get("MUSE_LMSTUDIO_URL", "http://127.0.0.1:1234").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    if not base.startswith(("http://127.0.0.1", "http://localhost")):
        raise Skip("configured model endpoint is not loopback; diagnostic inference is local-only")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + "/api/v0/models", timeout=5) as resp:
            listing = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise Skip(f"no local model server answering at {base} ({type(exc).__name__}); nothing is started or downloaded")
    loaded = [m for m in listing.get("data", []) if m.get("state") == "loaded" and m.get("type", "llm") in ("llm", "vlm")]
    if not loaded:
        raise Skip("no model is already loaded; refusing to trigger a JIT load (would allocate VRAM)")
    model = loaded[0]["id"]
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "回复一个字：好"}],
                       "max_tokens": 16, "temperature": 0, "stream": False}).encode("utf-8")
    req = urllib.request.Request(base + "/v1/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with opener.open(req, timeout=120) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    latency = round(time.perf_counter() - started, 2)
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage = out.get("usage") or {}
    require(text.strip(), f"empty completion from {model}: {str(out)[:300]}")
    require(int(usage.get("completion_tokens", 0) or 0) <= 16, f"max_tokens not honoured: {usage}")
    time.sleep(3)
    after = _gpu_memory(exe)
    tolerance = int(os.environ.get("MUSE_DIAG_VRAM_TOLERANCE_MIB", "512"))
    require(after["used_mib"] <= before["used_mib"] + tolerance,
            f"VRAM not released after inference: before={before['used_mib']} after={after['used_mib']} MiB")
    from muse.usage_ledger import UsageLedger
    ledger = UsageLedger(ctx.sub("model usage") / "usage.sqlite3")
    try:
        has_counts = usage.get("prompt_tokens") is not None or usage.get("completion_tokens") is not None
        ledger.record_usage("lmstudio", model, token_source="provider_reported" if has_counts else "not_reported",
                            input_tokens=usage.get("prompt_tokens") if has_counts else None,
                            output_tokens=usage.get("completion_tokens") if has_counts else None,
                            cost_amount="0", cost_currency="USD", cost_kind="estimated", cost_source="local_gpu",
                            price_version="local-v1", project_id="muse-diag")
    finally:
        ledger.close()
    return {"model": model, "latency_s": latency, "completion": text[:40], "usage": usage,
            "vram_before": before, "vram_after": after}


@probe("L5.D1", "L5", "守护进程控制经统帅：Kill 时恢复被拒、停止/暂停始终可用并审计；放开后恢复成功且有完成凭证", ("AC-038",))
def l5_daemon_control_commander(ctx: Ctx):
    from muse.commander_gate import commander_root, engage_kill_file
    from muse.muse_commander import MuseCommander
    from muse.status_api import create_status_server
    from muse.autonomy_safety import KILL_RESET_PHRASE
    ws = ctx.sub("daemon control ws")
    data = ws / "data"
    for sub in ("tasks", "progress", "capabilities", "autonomy"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    token = "diag-" + hashlib.sha256(os.urandom(16)).hexdigest()
    server, bearer = create_status_server(task_db=data / "tasks" / "muse.sqlite3", progress_db=data / "progress" / "p.sqlite3",
                                          capability_db=data / "capabilities" / "c.sqlite3", port=0, token=token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def post(route: str) -> tuple[int, dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=b"", method="POST",
                                     headers={"Authorization": f"Bearer {bearer}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    root = commander_root(ws)
    try:
        with MuseCommander(root) as cmd:
            cmd.kill(actor="user:diag", reason="daemon gate probe")  # temp workspace only
        blocked_status, blocked = post("/api/v1/daemon/resume")
        pause_status, _ = post("/api/v1/daemon/pause")
        stop_status, _ = post("/api/v1/daemon/stop")
        (root / "KILL_SWITCH").unlink()
        with MuseCommander(root) as cmd:
            cmd.safety.reset_kill_switch(actor="user:diag", confirmation=KILL_RESET_PHRASE)
            cmd.safety.resume(actor="user:diag")
        ok_status, ok = post("/api/v1/daemon/resume")
    finally:
        server.shutdown(); server.server_close()
    require(blocked_status == 409 and "global gate" in blocked.get("error", ""), f"resume under kill: {blocked_status} {blocked}")
    require(pause_status == 200 and stop_status == 200, f"safety-down actions must work under kill: pause={pause_status} stop={stop_status}")
    require(ok_status == 200 and ok.get("commander", {}).get("status") == "succeeded" and ok["commander"].get("receipt_sha256"),
            f"resume after reset: {ok_status} {ok}")
    with MuseCommander(root) as cmd:
        chain = cmd.verify_receipt_chain()
    audit = [json.loads(x) for x in (root / "safety_actions.jsonl").read_text(encoding="utf-8").splitlines()]
    require(chain["ok"] and chain["count"] == 1, f"receipt chain: {chain}")
    require({a["action"] for a in audit} >= {"daemon.pause", "daemon.stop"}, f"safety actions not audited: {audit}")
    return {"blocked": blocked_status, "pause": pause_status, "stop": stop_status, "resume": ok_status, "audited": len(audit)}


@probe("L5.D2", "L5", "统一停止开关 + 雷达扫描/进化回滚经统帅（回滚高风险、agent: 403、user: 批准后原子回滚）", ("AC-039",))
def l5_unified_gate_and_rollback(ctx: Ctx):
    from muse.commander_gate import check_global_gate
    from muse.commander_routes import build_route_handlers
    from muse.evolution_guard import EvolutionVersionLedger
    from muse.muse_commander import MuseCommander
    from muse.omega_api import handle_omega
    ws = ctx.sub("unified gate ws")
    root = ws / "data" / "commander"
    code, _ = handle_omega("POST", "/api/v1/omega/safety/control", "",
                           json.dumps({"action": "kill", "confirmation": "KILL-SWITCH", "reason": "diag"}).encode("utf-8"), ws)
    require(code == 200, f"omega kill: {code}")
    gate = check_global_gate(ws)
    require(not gate["allowed"], f"Commander global gate ignored the Omega kill switch: {gate}")
    with MuseCommander(root) as cmd:
        cmd.register_handler("sim", _ok_handler)
        plan = cmd.submit_goal("omega gate", [{"key": "x", "role": "researcher", "objective": "整理", "handler": "sim"}])
        blocked = cmd.run_next(task_id=plan["tickets"][0]["task_id"])
    require(blocked["status"] == "blocked", f"Commander ticket ran while Omega was killed: {blocked}")
    from muse.autonomy_safety import KILL_RESET_PHRASE, SafetyController
    if (ws / "KILL_SWITCH").exists():
        (ws / "KILL_SWITCH").unlink()
    with SafetyController(ws / "data" / "autonomy" / "safety.sqlite3") as omega_safety:
        omega_safety.reset_kill_switch(actor="user:diag", confirmation=KILL_RESET_PHRASE)
        omega_safety.resume(actor="user:diag")
    require(check_global_gate(ws)["allowed"], "gate did not reopen after Omega reset + resume")
    target = ws / "evolved 模块.py"
    original, patched = "VALUE = 1\n".encode("utf-8"), b"VALUE = 2\n"
    target.write_bytes(original)
    (ws / "data" / "evolution").mkdir(parents=True, exist_ok=True)
    with EvolutionVersionLedger(ws / "data" / "evolution" / "versions.sqlite3", ws) as ledger:
        version = ledger.record(task_id="diag", proposal_id="p", files=[("evolved 模块.py", original, hashlib.sha256(patched).hexdigest())])
    target.write_bytes(patched)
    code, body = handle_omega("POST", "/api/v1/omega/evolution/rollback", "",
                              json.dumps({"version": version, "confirmation": f"ROLLBACK-{version}"}).encode("utf-8"), ws)
    require(code == 202 and body["commander"]["status"] == "pending_approval" and body["commander"]["risk"] == "high",
            f"rollback must wait for a human: {code} {body}")
    require(target.read_bytes() == patched, "rollback touched files before approval")
    ticket = body["commander"]["ticket_id"]
    with MuseCommander(root) as cmd:
        for name, fn in build_route_handlers(ws).items():
            cmd.register_handler(name, fn)
        try:
            cmd.approve(ticket, approved_by="agent:evolver")
            raise Check("agent approved an evolution rollback")
        except PermissionError:
            pass
        cmd.approve(ticket, approved_by="user:owner")  # temp workspace only
        outcome = cmd.run_next(task_id=ticket, timeout_seconds=30)
        chain = cmd.verify_receipt_chain()
    require(outcome["status"] == "succeeded" and target.read_bytes() == original, f"approved rollback: {outcome}")
    from muse.status_api import create_status_server
    data = ws / "data"
    for sub in ("tasks", "progress", "capabilities", "improvements"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    from muse.task_queue import TaskQueue
    TaskQueue(data / "tasks" / "muse.sqlite3").close()
    server, bearer = create_status_server(task_db=data / "tasks" / "muse.sqlite3", progress_db=data / "progress" / "p.sqlite3",
                                          capability_db=data / "capabilities" / "c.sqlite3",
                                          improvement_db=data / "improvements" / "muse.sqlite3", port=0,
                                          token="diag-" + hashlib.sha256(os.urandom(16)).hexdigest())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/v1/radar/scan", data=b"",
                                     method="POST", headers={"Authorization": f"Bearer {bearer}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                radar = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            radar = {"http_status": exc.code, **json.loads(exc.read().decode("utf-8") or "{}")}
    finally:
        server.shutdown(); server.server_close()
    require(radar.get("approval_required") is True and radar.get("commander", {}).get("status") == "succeeded"
            and radar["commander"].get("receipt_sha256"), f"radar scan not ticketed: {radar}")
    with MuseCommander(root) as cmd:
        final_chain = cmd.verify_receipt_chain()
    require(chain["ok"] and final_chain["ok"] and final_chain["count"] >= 2, f"receipt chain: {final_chain}")
    return {"omega_gate": gate["reason"], "rollback": outcome["status"], "receipts": final_chain["count"]}


@probe("L5.H1", "L5", "审计链防篡改：完成凭证/越权审计/安全动作/成本账本任一被改、删、重排 → muse.audit verify 必须报错", ("AC-097",))
def l5_audit_chain_tamper(ctx: Ctx):
    from muse.audit import verify
    from muse.commander_routes import BypassCommanderError, audit_safety_action, capture_inspiration_core
    from muse.muse_commander import MuseCommander
    ws = ctx.sub("audit chain ws")
    root = ws / "data" / "commander"
    with MuseCommander(root) as cmd:
        cmd.register_handler("sim", _ok_handler)
        plan = cmd.submit_goal("审计链", [{"key": "a", "role": "researcher", "objective": "整理", "handler": "sim"},
                                          {"key": "b", "role": "researcher", "objective": "总结", "handler": "sim", "depends_on": ["a"]}])
        cmd.drain_plan([t["task_id"] for t in plan["tickets"]])
    for _ in range(2):
        try:
            capture_inspiration_core(ws, ws / "k.sqlite3", {}, commander_ctx=None)
            raise Check("bypass accepted")
        except BypassCommanderError:
            pass
    audit_safety_action(ws, action="daemon.pause", outcome="ok")
    audit_safety_action(ws, action="daemon.stop", outcome="ok")
    clean = verify(root)
    require(clean["ok"], f"clean trail does not verify: {clean}")
    counts = {c["file"]: c["count"] for c in clean["checks"]}
    require(counts.get("receipts.jsonl") == 2 and counts.get("bypass_audit.jsonl") == 2 and counts.get("safety_actions.jsonl") == 2
            and counts.get("usage.sqlite3") == 2 and counts.get("cost_chain.jsonl") == 2, f"unexpected counts: {counts}")

    def tampered(name: str, mutate) -> dict:
        copy = ctx.sub(f"audit tamper {name}") / "commander"
        shutil.copytree(root, copy)
        mutate(copy)
        return verify(copy)

    def edit_line(file: str, index: int, old: str, new: str):
        def run(base: Path):
            lines = (base / file).read_text(encoding="utf-8").splitlines()
            require(old in lines[index], f"tamper anchor {old!r} missing in {file}")
            lines[index] = lines[index].replace(old, new, 1)
            (base / file).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return run

    def drop_line(file: str, index: int):
        def run(base: Path):
            lines = (base / file).read_text(encoding="utf-8").splitlines()
            del lines[index]
            (base / file).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return run

    def swap_lines(file: str):
        def run(base: Path):
            lines = (base / file).read_text(encoding="utf-8").splitlines()
            lines[0], lines[1] = lines[1], lines[0]
            (base / file).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return run

    def sql(statement: str):
        def run(base: Path):
            con = sqlite3.connect(base / "usage.sqlite3")
            try:
                # The ledger's own append-only triggers already refuse this; emulate an attacker editing the
                # file directly by dropping them first, so the chain cross-check is what must catch it.
                for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                    con.execute(f'DROP TRIGGER "{name}"')
                con.execute(statement); con.commit()
            finally:
                con.close()
        return run

    cases = {
        "receipt_edit": edit_line("receipts.jsonl", 0, "worker:", "w0rker:"),
        "bypass_edit": edit_line("bypass_audit.jsonl", 0, "no commander ticket context", "ok"),
        "bypass_delete": drop_line("bypass_audit.jsonl", 0),
        "safety_reorder": swap_lines("safety_actions.jsonl"),
        "cost_chain_delete": drop_line("cost_chain.jsonl", 1),
        "usage_row_edit": sql("UPDATE model_usage_events SET cost_amount='9.99' WHERE rowid=1"),
        "usage_row_delete": sql("DELETE FROM model_usage_events WHERE rowid=1"),
    }
    survivors = [name for name, fn in cases.items() if tampered(name, fn)["ok"]]
    require(not survivors, f"tampering not detected: {survivors}")
    cli_env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONIOENCODING": "utf-8"}
    good = subprocess.run([sys.executable, "-m", "muse.audit", "verify", "--root", str(root)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60, env=cli_env)
    bad_root = ctx.sub("audit tamper receipt_edit") / "commander"
    bad = subprocess.run([sys.executable, "-m", "muse.audit", "verify", "--root", str(bad_root)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=60, env=cli_env)
    require(good.returncode == 0 and bad.returncode == 1, f"CLI exit codes good={good.returncode} bad={bad.returncode} {bad.stderr[-300:]}")
    return {"clean_counts": counts, "tamper_cases_detected": len(cases)}


@probe("L5.B2", "L5", "故障注入与恢复：租约过期只执行一次、凭证后崩溃不重跑、高风险崩溃须重审、凭证链半写失败关闭、并发提交/执行幂等", ("AC-098",))
def l5_fault_injection(ctx: Ctx):
    from muse.muse_commander import MuseCommander
    root = ctx.sub("fault injection") / "commander"
    calls: list[str] = []

    def counting(c):
        calls.append(c["task_id"])
        return _ok_handler(c)

    def usage_rows() -> int:
        return table_counts(root / "usage.sqlite3").get("model_usage_events", 0)

    with MuseCommander(root) as cmd:
        cmd.register_handler("count", counting)
        # (a) worker died after claiming (lease expires) → executed exactly once by the next worker
        t1 = cmd.submit_goal("崩溃恢复", [{"key": "a", "role": "researcher", "objective": "整理", "handler": "count"}],
                             plan_id="faultplan0000001")["tickets"][0]["task_id"]
        cmd.queue.claim_next(worker_id="dead-worker", task_id=t1, lease_seconds=1)
        time.sleep(1.6)
        out1 = cmd.run_next(task_id=t1)
        require(out1["status"] == "succeeded" and calls.count(t1) == 1, f"lease recovery: {out1} calls={calls}")
        # (b) crash after the receipt was written but before finish → recovered from receipt, no re-run
        t2 = cmd.submit_goal("凭证后崩溃", [{"key": "b", "role": "researcher", "objective": "整理", "handler": "count"}],
                             plan_id="faultplan0000002")["tickets"][0]["task_id"]
        cmd.queue.claim_next(worker_id="dead-worker", task_id=t2, lease_seconds=1)
        review = type("R", (), {"reviewer_id": "reviewer:objective", "review_id": "pre-crash-review"})()
        cmd._receipt(t2, cmd.queue.get(t2)["payload"], "artifact before crash", review, "worker:researcher")
        receipts_before, usage_before = cmd.verify_receipt_chain()["count"], usage_rows()
        time.sleep(1.6)
        out2 = cmd.run_next(task_id=t2)
        require(out2["status"] == "succeeded" and out2["result"].get("recovered_from_receipt") is True and t2 not in calls,
                f"receipt recovery: {out2} calls={calls}")
        require(cmd.verify_receipt_chain() == {"ok": True, "count": receipts_before} and usage_rows() == usage_before,
                "recovery duplicated a receipt or a cost row")
        # (c) high-risk ticket crashes mid-run → goes back to a human, not silently re-run
        t3 = cmd.submit_goal("高风险崩溃", [{"key": "c", "role": "operator", "objective": "发布到公网", "handler": "count"}],
                             plan_id="faultplan0000003")["tickets"][0]["task_id"]
        cmd.approve(t3, approved_by="user:diag")  # temp workspace only
        cmd.queue.claim_next(worker_id="dead-worker", task_id=t3, lease_seconds=1)
        time.sleep(1.6)
        idle = cmd.run_next(task_id=t3)
        require(idle["status"] == "idle" and cmd.queue.get(t3)["status"] == "waiting_approval" and t3 not in calls,
                f"high-risk crash must require re-approval: {idle} {cmd.queue.get(t3)['status']}")
    # (d) half-written receipt tail → the next ticket fails closed (no exception, no extension of a bad chain)
    with (root / "receipts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"task_id": "half-writ')
    with MuseCommander(root) as cmd:
        cmd.register_handler("count", counting)
        t4 = cmd.submit_goal("半写链", [{"key": "d", "role": "researcher", "objective": "整理", "handler": "count"}],
                             plan_id="faultplan0000004")["tickets"][0]["task_id"]
        out4 = cmd.run_next(task_id=t4)
    require(out4["status"] == "failed" and out4["result"]["error_code"] == "commander.receipt_chain_corrupt" and t4 not in calls,
            f"corrupt chain must fail closed: {out4}")
    # (e) concurrency on a clean root: same plan submitted twice in parallel → one ticket; two workers → one execution
    root2 = ctx.sub("fault concurrency") / "commander"
    MuseCommander(root2).close()
    results: list[Any] = []

    def submit():
        with MuseCommander(root2) as c:
            results.append(c.submit_goal("并发", [{"key": "e", "role": "researcher", "objective": "整理", "handler": "count"}],
                                         plan_id="faultplan0000005")["tickets"][0]["task_id"])

    threads = [threading.Thread(target=submit) for _ in range(4)]
    [t.start() for t in threads]; [t.join(30) for t in threads]
    require(len(results) == 4 and len(set(results)) == 1, f"concurrent submit produced {set(results)}")
    runs: list[str] = []

    def work():
        with MuseCommander(root2) as c:
            c.register_handler("count", counting)
            runs.append(c.run_next(task_id=results[0])["status"])

    workers = [threading.Thread(target=work) for _ in range(3)]
    [t.start() for t in workers]; [t.join(60) for t in workers]
    require(sorted(runs).count("succeeded") == 1 and calls.count(results[0]) == 1, f"concurrent execution: {runs} {calls}")
    require(table_counts(root2 / "usage.sqlite3").get("model_usage_events", 0) == 1, "cost recorded more than once")
    # (f) a truncated workflow state file makes the next step fail closed
    from muse.workflow_c import build_handlers
    wsc = ctx.sub("fault workflow ws")
    run_dir = wsc / "data" / "workflow_c" / "faultplan0000006"
    run_dir.mkdir(parents=True)
    (run_dir / "opportunity.json").write_text('{"opportunity_id": "x", "sc', encoding="utf-8")
    try:
        build_handlers(wsc)["wf_c.produce"]({"payload": {"plan_id": "faultplan0000006", "goal": "x"}, "task_id": "t"})
        raise Check("truncated state file was accepted")
    except json.JSONDecodeError:
        pass
    return {"lease_recovery": "once", "receipt_recovery": True, "high_risk": "waiting_approval", "concurrent_runs": runs}


@probe("L5.E", "L5", "受控进化：高风险挂起 + 版本记录 + 原子回滚 + 提醒巡检", ("AC-050", "AC-051", "AC-052"))
def l5_evolution(ctx: Ctx):
    from muse.evolution_guard import EvolutionVersionLedger
    from muse.reminders import ReminderStore
    ws = ctx.sub("evolution workspace")
    target = ws / "module 目标.py"
    original = "VALUE = 1  # 基线\n".encode("utf-8")
    target.write_bytes(original)
    with _commander(ctx, "evo") as cmd:
        applied = {}
        def apply_patch(c):
            new = b"VALUE = 2  # patched\n"
            ledger = EvolutionVersionLedger(ws / "versions.sqlite3", ws)
            try:
                version = ledger.record(task_id=c["task_id"], proposal_id="diag-proposal",
                                        files=[("module 目标.py", original, hashlib.sha256(new).hexdigest())])
            finally:
                ledger.close()
            target.write_bytes(new)
            applied["version"] = version
            return {"artifact": f"patched module version {version}", "builder_id": "worker:programmer", "evidence": "regression: PASS"}
        cmd.register_handler("patch", apply_patch)
        plan = cmd.submit_goal("自我进化补丁", [{"key": "patch", "role": "developer", "objective": "修改核心代码",
                                                "handler": "patch", "risk_override": "high"}])
        tid = plan["tickets"][0]["task_id"]
        cmd.drain()
        require(cmd.queue.get(tid)["status"] == "waiting_approval" and not applied, "evolution patch ran without approval")
        cmd.approve(tid, approved_by="human:owner")
        cmd.drain()
    require(target.read_bytes() != original, "patch not applied")
    ledger = EvolutionVersionLedger(ws / "versions.sqlite3", ws)
    try:
        rb = ledger.rollback(applied["version"], actor="human:owner")
    finally:
        ledger.close()
    require(target.read_bytes() == original, f"rollback did not restore bytes: {rb}")
    reminders = ReminderStore(ctx.sub("reminders") / "r.sqlite3")
    try:
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        reminders.add("复盘失败实验", "检查首轮样本不足", due_at=past, source="diag", dedupe_key="diag-1")
        due = reminders.due()
    finally:
        reminders.close()
    require(len(due) >= 1, "due reminder not detected")
    return {"rollback": rb, "due": len(due)}


@probe("L5.C", "L5", "Workflow C 价值创造与商业主权闭环", ("AC-070", "AC-072", "AC-073", "AC-074"))
def l5_c(ctx: Ctx):
    import math, struct, wave as wavemod
    from muse.publish_gate import approve_bundle_for_manual_publish, build_publish_bundle, validate_artifact
    from muse.revenue_ledger import LedgerError, RevenueLedger
    from muse.unit_economics import UnitEconomicsError, require_counter_evidence
    d = ctx.sub("factory 工坊")
    wav = d / "narration 旁白.wav"
    with wavemod.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(16000)))
    (d / "empty.wav").write_bytes(b"")
    (d / "fake.wav").write_bytes(b"NOTRIFF" * 10)
    (d / "script 脚本.md").write_text("# 分镜\n1. 开场：火种传递\n2. 结尾：未知是起点\n", encoding="utf-8")
    (d / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n知识之火\n", encoding="utf-8")
    v = validate_artifact(wav)
    require(v["ok"] and v["duration_seconds"] == 1.0, f"wav validation: {v}")
    require(not validate_artifact(d / "empty.wav")["ok"] and not validate_artifact(d / "fake.wav")["ok"], "bad media accepted")
    bundle = build_publish_bundle(d / "bundle", [wav, d / "script 脚本.md", d / "subs.srt"], title="知识之火 01",
                                  channel="local_preview", estimated_cost_usd="0.00", exposure_estimates={"views_estimate": 1000})
    require(bundle["all_artifacts_valid"] and bundle["publish_allowed"] is False, "bundle gate wrong")
    try:
        approve_bundle_for_manual_publish(d / "bundle", approver="agent:operator"); raise Check("agent approved publish")
    except PermissionError:
        pass
    approved = approve_bundle_for_manual_publish(d / "bundle", approver="user:owner")
    require(approved["publish_allowed"] is False and approved["publish_mode"] == "manual_by_human_only", "auto-publish enabled")
    ledger = RevenueLedger(d / "revenue.sqlite3")
    try:
        ledger.record_signal("view", source="local_preview", external_id="v-1", quantity=1000)
        try:
            ledger.record_money("settlement", amount_minor=500, currency="USD", source="stripe", external_id="fake-1")
            raise Check("settlement without receipt accepted")
        except LedgerError:
            pass
        ledger.record_money("compute_cost", amount_minor=3, currency="USD", source="local_gpu", external_id="cost-1")
        summary = ledger.summary("USD")
    finally:
        ledger.close()
    net_keys = {k: v for k, v in summary.items() if "net" in k}
    require(net_keys and all((isinstance(x, (int, float)) and x <= 0) or isinstance(x, (str, dict)) for x in net_keys.values()),
            f"net revenue not honest: {summary}")
    try:
        require_counter_evidence({}); raise Check("unit economics without counter evidence accepted")
    except UnitEconomicsError:
        pass
    return {"wav": v, "summary": summary}


# =============================== L6 · live environment ===============================
@probe("L6.1", "L6", "主机真实资源", ("AC-090",))
def l6_host(ctx: Ctx):
    total, used, free = shutil.disk_usage(str(ROOT))
    from muse.resource_governor import probe_host_resources
    snap = probe_host_resources()
    gpu = "unknown"
    smi = shutil.which("nvidia-smi")
    if smi:
        r = subprocess.run([smi, "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=20)
        gpu = r.stdout.strip() or f"nvidia-smi rc={r.returncode}"
    info = {"python": sys.version.split()[0], "platform": platform.platform(), "cpu": snap.logical_cpu_count,
            "ram_total": snap.total_memory_bytes, "ram_available": snap.available_memory_bytes,
            "disk_free_gb": round(free / 1024 ** 3, 1), "gpu": gpu}
    require(info["cpu"] and info["disk_free_gb"] >= 0, "host probe empty")
    return info


@probe("L6.2", "L6", "适配器/本地模型四态实连探测", ("AC-091",))
def l6_adapters(ctx: Ctx):
    from muse.adapter_hub import STATES, health_report
    rows = health_report()
    require(all(r["state"] in STATES for r in rows), "invalid state")
    ctx.facts["adapters"] = rows
    if os.environ.get("MUSE_DIAG_LIVE_INFERENCE") == "1" and any(r["adapter"] == "lmstudio" and r["state"] == "LIVE_CONNECTED" for r in rows):
        ctx.facts["live_inference"] = "requested; not implemented in this wave (see ISSUES P2)"
    return {r["adapter"]: r["state"] for r in rows}


@probe("L6.3", "L6", "FFmpeg 真实合成 MP4 + 视频包→发布准备包桥接", ("AC-071", "AC-075"))
def l6_ffmpeg(ctx: Ctx):
    exe = shutil.which("ffmpeg")
    if not exe:
        raise Skip("ffmpeg not on PATH")
    from muse.publish_gate import validate_artifact
    d = ctx.sub("ffmpeg 输出")
    out = d / "知识之火 sample.mp4"
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    require(r.returncode == 0, f"ffmpeg rc={r.returncode}: {r.stderr[-600:]}")
    v = validate_artifact(out)
    require(v["ok"], f"mp4 invalid: {v}")
    from muse.publish_gate import bundle_from_video_package
    pkg = ctx.sub("video package 视频包")
    shutil.copyfile(out, pkg / "video.mp4")
    (pkg / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n知识之火\n", encoding="utf-8")
    jr = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(out), "-frames:v", "1",
                         str(pkg / "cover.jpg")], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    require(jr.returncode == 0, f"cover extraction failed: {jr.stderr[-400:]}")
    bundle = bundle_from_video_package(pkg, ctx.sub("video bundle"), title="知识之火 视频 01")
    require(bundle["all_artifacts_valid"] and bundle["publish_allowed"] is False, f"video bundle: {bundle}")
    require({Path(a["path"]).name for a in bundle["artifacts"]} == {"video.mp4", "subtitles.srt", "cover.jpg"}, "bundle files")
    broken = ctx.sub("video package broken")
    (broken / "video.mp4").write_bytes(b"not a video at all")
    bad = bundle_from_video_package(broken, ctx.sub("video bundle broken"), title="broken")
    require(not bad["all_artifacts_valid"] and bad.get("missing_required") == ["subtitles.srt"], f"broken package accepted: {bad}")
    return {"mp4": v, "bundle_valid": True, "broken_rejected": True}


@probe("L6.4", "L6", "status API 瞬态启动、鉴权与 Commander 审批/Kill 接口", ("AC-080", "AC-082"))
def l6_api(ctx: Ctx):
    from muse.status_api import create_status_server
    from muse.muse_commander import MuseCommander
    base = ctx.sub("api run")
    data = base / "data"
    for sub in ("tasks", "progress", "capabilities"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    with MuseCommander(data / "commander") as cmd:
        held = cmd.submit_goal("HTTP 审批测试", [{"key": "deploy", "role": "operator", "objective": "部署到公网"}])
    held_id = held["tickets"][0]["task_id"]
    token = "diag-" + hashlib.sha256(os.urandom(16)).hexdigest()
    server, bearer = create_status_server(task_db=data / "tasks" / "muse.sqlite3", progress_db=data / "progress" / "p.sqlite3",
                                          capability_db=data / "capabilities" / "c.sqlite3", port=0, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    results = {}
    try:
        for route in ("/api/v1/health", "/api/v1/overview", "/api/v1/mind/state"):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", headers={"Authorization": f"Bearer {bearer}"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    results[route] = resp.status
            except urllib.error.HTTPError as exc:
                results[route] = exc.code
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/overview", timeout=10) as resp:
                results["unauth_overview"] = resp.status
        except urllib.error.HTTPError as exc:
            results["unauth_overview"] = exc.code

        def call(method: str, route: str, body: dict | None = None) -> tuple[int, dict]:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            headers = {"Authorization": f"Bearer {bearer}"}
            if raw is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=raw, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.status, json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

        s, approvals = call("GET", "/api/v1/commander/approvals")
        results["approvals"] = s
        require(s == 200 and any(a["task_id"] == held_id for a in approvals.get("approvals", [])), f"approvals: {approvals}")
        results["approve_agent"] = call("POST", "/api/v1/commander/approve",
                                        {"task_id": held_id, "approved_by": "agent:x", "confirmation": "APPROVE"})[0]
        results["approve_user"] = call("POST", "/api/v1/commander/approve",
                                       {"task_id": held_id, "approved_by": "user:owner", "confirmation": "APPROVE"})[0]
        results["kill_noconfirm"] = call("POST", "/api/v1/commander/kill", {"actor": "user:owner"})[0]
        results["kill"] = call("POST", "/api/v1/commander/kill", {"actor": "user:owner", "reason": "diag", "confirmation": "KILL"})[0]
        results["status"] = call("GET", "/api/v1/commander/status")[1].get("status", {}).get("safety", {}).get("mode")
    finally:
        server.shutdown(); server.server_close()
    require(results.get("/api/v1/health") == 200, f"health not 200: {results}")
    require(results.get("unauth_overview") in (401, 403), f"unauthenticated read allowed: {results}")
    require(results["approve_agent"] == 403 and results["approve_user"] == 200, f"approval gate over HTTP: {results}")
    require(results["kill_noconfirm"] == 400 and results["kill"] == 200 and results["status"] == "killed", f"kill over HTTP: {results}")
    return results


@probe("L6.6", "L6", "控制台统帅面板：前端客户端单测 + 本地构建产物含审批/Kill 接口", ("AC-083",))
def l6_ui_commander(ctx: Ctx):
    ui = ROOT / "ui-prototype"
    app = (ui / "src" / "App.svelte").read_text(encoding="utf-8")
    require("<CommanderPanel {client} {connected} />" in app, "CommanderPanel not mounted in App.svelte")
    node = shutil.which("node")
    if not node:
        raise Skip("node not on PATH")
    r = subprocess.run([node, "--test", "commander-api.test.js"], cwd=str(ui), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    require(r.returncode == 0, f"commander-api.test.js failed: {(r.stdout + r.stderr)[-1500:]}")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm or not (ui / "node_modules").is_dir():
        raise Skip("npm or ui-prototype/node_modules missing (no network install is attempted)")
    b = subprocess.run([npm, "run", "build"], cwd=str(ui), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=400)
    require(b.returncode == 0, f"vite build failed: {(b.stdout + b.stderr)[-2000:]}")
    bundles = [p for p in (ui / "dist").rglob("*.js") if p.stat().st_size < 8_000_000]
    hit = [p.name for p in bundles if "/api/v1/commander/kill" in p.read_text(encoding="utf-8", errors="replace")]
    require(hit, "built dist does not contain the commander kill route")
    return {"node_test": "pass", "bundle": hit[:3]}


@probe("L6.7", "L6", "全路由枚举：每个 JSON 响应都带八类认知标签（新增路由漏标即失败）", ("AC-084",))
def l6_route_epistemic_coverage(ctx: Ctx):
    import re as _re
    from muse.epistemics import EPISTEMIC_TAG_LABELS_ZH, EPISTEMIC_TAGS
    from muse.research_knowledge import ResearchKnowledgeStore
    from muse.status_api import create_status_server
    route_sources = ["status_api.py", "control_center_api.py", "commander_api.py", "omega_api.py"]
    routes: set[str] = set()
    for name in route_sources:
        text = (SRC / "muse" / name).read_text(encoding="utf-8")
        routes.update(r for r in _re.findall(r'"(/api/v1/[A-Za-z0-9_/\-]*)"', text))
    routes.update({"/api/v1/commander/status", "/api/v1/commander/approvals", "/api/v1/commander/capacity",
                   "/api/v1/commander/adapters", "/api/v1/commander/fleet", "/api/v1/__not_a_route__"})
    non_json_ok = {"/api/v1/events/stream"}
    require(len(routes) >= 60, f"route enumeration too small: {len(routes)}")
    ws = ctx.sub("route labels ws")
    data = ws / "data"
    for sub in ("tasks", "progress", "capabilities", "research"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    kdb = data / "research" / "knowledge.sqlite3"
    ResearchKnowledgeStore(kdb).close()
    token = "diag-" + hashlib.sha256(os.urandom(16)).hexdigest()
    server, bearer = create_status_server(task_db=data / "tasks" / "muse.sqlite3", progress_db=data / "progress" / "p.sqlite3",
                                          capability_db=data / "capabilities" / "c.sqlite3", knowledge_db=kdb, port=0, token=token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    missing, non_json, checked, by_tag = [], [], 0, {}
    try:
        # GET every enumerated route; POST only to side-effect-free targets (POST to arbitrary routes
        # could start daemons or network scans, which a diagnostic must never do).
        safe_posts = {("POST", "/api/v1/__not_a_route__"), ("POST", "/api/v1/commander/approve"),
                      ("POST", "/api/v1/control/__not_a_route__")}
        plan = [("GET", r) for r in sorted(routes) if r not in non_json_ok] + sorted(safe_posts)
        for method, route in plan:
            if True:
                data_bytes = b"{}" if method == "POST" else None
                headers = {"Authorization": f"Bearer {bearer}"}
                if data_bytes is not None:
                    headers["Content-Type"] = "application/json"
                req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=data_bytes, method=method, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        ctype, raw = resp.headers.get("Content-Type", ""), resp.read()
                except urllib.error.HTTPError as exc:
                    ctype, raw = exc.headers.get("Content-Type", ""), exc.read()
                if "application/json" not in ctype:
                    non_json.append(f"{method} {route} -> {ctype or 'no content-type'}")
                    continue
                body = json.loads(raw.decode("utf-8") or "{}")
                checked += 1
                tag = body.get("epistemic_tag") if isinstance(body, dict) else None
                if tag not in EPISTEMIC_TAGS or body.get("epistemic_label_zh") != EPISTEMIC_TAG_LABELS_ZH.get(tag):
                    missing.append(f"{method} {route}")
                else:
                    by_tag[tag] = by_tag.get(tag, 0) + 1
                    if route.startswith("/api/v1/metaphysics/") and tag != "symbolic_inspiration":
                        missing.append(f"{method} {route}: symbolic route tagged {tag}")
    finally:
        server.shutdown(); server.server_close()
    require(not missing, f"{len(missing)} JSON responses lack a valid epistemic tag: {missing[:15]}")
    require(not non_json, f"unexpected non-JSON responses: {non_json[:10]}")
    return {"routes": len(routes), "responses_checked": checked, "by_tag": by_tag}


@probe("L6.8", "L6", "性能测量：单票据开销、健康接口 p50/p95、成本账本读取（供性能预算阶段使用）", ("AC-099",))
def l6_perf_measure(ctx: Ctx):
    import statistics
    from muse.muse_commander import MuseCommander
    from muse.status_api import create_status_server
    from muse.usage_ledger import UsageLedger
    ws = ctx.sub("perf ws")
    root = ws / "data" / "commander"
    durations = []
    with MuseCommander(root) as cmd:
        cmd.register_handler("sim", _ok_handler)
        for i in range(7):
            started = time.perf_counter()
            plan = cmd.submit_goal("perf", [{"key": f"p{i}", "role": "researcher", "objective": "整理", "handler": "sim"}],
                                   plan_id=f"perfplan{i:08d}")
            cmd.run_next(task_id=plan["tickets"][0]["task_id"])
            durations.append((time.perf_counter() - started) * 1000)
    data = ws / "data"
    for sub in ("tasks", "progress", "capabilities"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    server, bearer = create_status_server(task_db=data / "tasks" / "muse.sqlite3", progress_db=data / "progress" / "p.sqlite3",
                                          capability_db=data / "capabilities" / "c.sqlite3", port=0,
                                          token="diag-" + hashlib.sha256(os.urandom(16)).hexdigest())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    latencies = []
    try:
        for _ in range(30):
            req = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/v1/health",
                                         headers={"Authorization": f"Bearer {bearer}"})
            started = time.perf_counter()
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            latencies.append((time.perf_counter() - started) * 1000)
    finally:
        server.shutdown(); server.server_close()
    ledger = UsageLedger(ws / "usage-bulk.sqlite3")
    try:
        for i in range(1000):
            ledger.record_usage("sim", "m", token_source="estimated", input_tokens=1, output_tokens=1, cost_amount="0",
                                cost_currency="USD", cost_kind="estimated", cost_source="diag", price_version="v1",
                                task_id=f"t{i % 50}", agent_id="a", project_id="perf")
        reads = []
        for _ in range(5):
            started = time.perf_counter()
            ledger.list_events(project_id="perf", limit=500)
            reads.append((time.perf_counter() - started) * 1000)
    finally:
        ledger.close()
    latencies.sort()
    metrics = {"ticket_overhead_ms_median": round(statistics.median(durations), 2),
               "health_p50_ms": round(statistics.median(latencies), 2),
               "health_p95_ms": round(latencies[int(len(latencies) * 0.95) - 1], 2),
               "usage_ledger_read500_ms_median": round(statistics.median(reads), 2)}
    require(metrics["health_p95_ms"] < 1000 and metrics["ticket_overhead_ms_median"] < 5000
            and metrics["usage_ledger_read500_ms_median"] < 2000, f"absolute performance sanity limits exceeded: {metrics}")
    return {"perf_metrics": metrics}


@probe("L6.9", "L6", "深度健康检查：票据积压/待审批/审计链/账本/磁盘/Kill/最近诊断；篡改、报告损坏、诊断失败、磁盘不足、检查抛错一律 unhealthy（fail closed），HTTP 503，只读不写", ("AC-103",))
def l6_deep_health(ctx: Ctx):
    import muse.deep_health as dh
    from muse.audit import append_chained
    from muse.muse_commander import MuseCommander
    from muse.status_api import create_status_server
    ws = ctx.sub("deep health ws")
    data = ws / "data"
    for sub in ("tasks", "progress", "capabilities"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    croot = data / "commander"
    with MuseCommander(croot) as cmd:
        cmd.submit_goal("深度健康测试", [{"key": "deploy", "role": "operator", "objective": "部署到公网"}])
    chain = croot / "safety_actions.jsonl"
    append_chained(chain, {"action": "diag-probe", "actor": "user:diag"})
    (ws / "reports").mkdir(parents=True, exist_ok=True)
    (ws / "docs").mkdir(parents=True, exist_ok=True)
    diag = ws / "reports" / "diag_latest.json"
    good_diag = json.dumps({"overall": "PASS", "exit_code": 0, "probe_summary": {"PASS": 1}})
    diag.write_text(good_diag, encoding="utf-8")
    (ws / "docs" / "PENDING_HUMAN.md").write_text("# p\n- a\n- b\n", encoding="utf-8")

    class _Du:
        def __init__(self, free: int) -> None:
            self.free = free

    plenty = lambda _p: _Du(100 * 1024 ** 3)

    def run(disk=plenty, **kw):
        return dh.build_deep_health(ws, commander_root=croot, disk_usage=disk, **kw)

    def fingerprint() -> dict[str, str]:
        return {p.relative_to(ws).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ws.rglob("*"))
                if p.is_file() and not p.name.endswith(("-wal", "-shm", "-journal"))}

    before = fingerprint()
    base = run()
    require(base["status"] == "ok" and not base["failing"] and not base["warnings"], f"healthy baseline not ok: {base}")
    tickets = base["checks"]["commander_tickets"]
    require(tickets["pending_approvals"] == 1 and tickets["backlog"] >= 1, f"pending approvals not counted: {tickets}")
    require(base["checks"]["audit_chain"]["verified_files"] >= 5, f"audit chain not verified: {base['checks']['audit_chain']}")
    require(base["checks"]["ledger_size"]["total_bytes"] > 0, f"ledger size not measured: {base['checks']['ledger_size']}")
    require(base["checks"]["pending_human"]["items"] == 2, f"PENDING_HUMAN count wrong: {base['checks']['pending_human']}")
    require(fingerprint() == before, "deep health wrote to the workspace (must be read-only)")
    cases: dict[str, str] = {}
    original_chain = chain.read_text(encoding="utf-8")
    chain.write_text(original_chain.replace("diag-probe", "diag-forged"), encoding="utf-8")
    r = run(); cases["tampered_chain"] = r["status"]
    require(r["status"] == "unhealthy" and "audit_chain" in r["failing"], f"tampered audit chain not detected: {r['checks']['audit_chain']}")
    chain.write_text(original_chain, encoding="utf-8")
    for label, body in (("diag_fail", json.dumps({"overall": "FAIL", "exit_code": 1})), ("diag_corrupt", "{not json")):
        diag.write_text(body, encoding="utf-8")
        r = run(); cases[label] = r["status"]
        require(r["status"] == "unhealthy" and "last_diagnostic" in r["failing"], f"{label} not unhealthy: {r['checks']['last_diagnostic']}")
    diag.write_text(good_diag, encoding="utf-8")
    r = run(now=time.time() + 3 * 86400); cases["diag_stale"] = r["status"]
    require(r["status"] == "degraded" and r["warnings"] == ["last_diagnostic"], f"stale diag not degraded: {r}")
    r = run(disk=lambda _p: _Du(100 * 1024 ** 2)); cases["disk_low"] = r["status"]
    require(r["status"] == "unhealthy" and r["failing"] == ["disk_free"], f"low disk not unhealthy: {r['checks']['disk_free']}")

    def broken_disk(_p):
        raise OSError("simulated disk_usage failure")

    r = run(disk=broken_disk); cases["check_raises"] = r["status"]
    require(r["status"] == "unhealthy" and r["checks"]["disk_free"]["status"] == "error", f"raising check not fail-closed: {r['checks']['disk_free']}")
    (croot / "KILL_SWITCH").write_text("actor=user:diag\n", encoding="utf-8")
    r = run(); cases["kill_switch"] = r["status"]
    (croot / "KILL_SWITCH").unlink()
    require(r["status"] == "degraded" and "kill_switch" in r["warnings"], f"kill switch not reported: {r}")
    r = run(limits={"pending_approvals_warn": 0}); cases["approval_backlog"] = r["status"]
    require(r["status"] == "degraded" and "commander_tickets" in r["warnings"], f"approval backlog not reported: {r}")
    token = "diag-" + hashlib.sha256(os.urandom(16)).hexdigest()
    server, bearer = create_status_server(task_db=data / "tasks" / "muse.sqlite3", progress_db=data / "progress" / "p.sqlite3",
                                          capability_db=data / "capabilities" / "c.sqlite3", port=0, token=token)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def get(auth: bool) -> tuple[int, dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/health/deep",
                                     headers={"Authorization": f"Bearer {bearer}"} if auth else {})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    try:
        code, body = get(True)
        require(body.get("commander_root") == str(croot.resolve()), f"HTTP deep health read the wrong commander root: {body.get('commander_root')}")
        require(set(body.get("checks", {})) == set(base["checks"]) and body.get("epistemic_tag"), f"HTTP body incomplete: {body}")
        require((code == 503) == (body.get("status") == "unhealthy"), f"HTTP code {code} inconsistent with {body.get('status')}")
        chain.write_text(original_chain.replace("diag-probe", "diag-forged"), encoding="utf-8")
        tampered_code, tampered = get(True)
        chain.write_text(original_chain, encoding="utf-8")
        require(tampered_code == 503 and "audit_chain" in tampered.get("failing", []), f"HTTP tampered chain: {tampered_code} {tampered.get('failing')}")
        unauth_code, _ = get(False)
        require(unauth_code == 401, f"unauthenticated deep health allowed: {unauth_code}")
    finally:
        server.shutdown(); server.server_close()
    return {"cases": cases, "http": {"ok": code, "tampered": tampered_code, "unauth": unauth_code},
            "pending_approvals": tickets["pending_approvals"], "verified_files": base["checks"]["audit_chain"]["verified_files"]}


@probe("L6.5", "L6", "Web Portal 静态资源", ("AC-081",))
def l6_portal(ctx: Ctx):
    wp = ROOT / "web-portal"
    missing = [n for n in ("index.html", "app.js", "styles.css") if not (wp / n).is_file() or (wp / n).stat().st_size == 0]
    require(not missing, f"portal assets missing/empty: {missing}")
    html = (wp / "index.html").read_text(encoding="utf-8")
    require("app.js" in html and "</html>" in html.casefold(), "index.html does not load app.js")
    return {"assets": "ok"}


# =============================== L7 · AC verdicts ===============================
@probe("L7.1", "L7", "启动器与实操手册存在", ("AC-092",))
def l7_docs(ctx: Ctx):
    req = [ROOT / "start_muse.bat", ROOT / "run_diag.bat", ROOT / "docs" / "USER_GUIDE_LIVE.md"]
    missing = [str(p.relative_to(ROOT)) for p in req if not p.is_file() or p.stat().st_size < 200]
    require(not missing, f"missing: {missing}")
    return {"ok": True}


PROBE_TIMEOUT_SECONDS = float(os.environ.get("MUSE_DIAG_PROBE_TIMEOUT", "120"))


class ProbeTimeout(Exception):
    """A probe exceeded its wall-clock budget; reported as FAIL, never as PASS."""


def _run_probe_with_timeout(fn: Callable[["Ctx"], Any], ctx: "Ctx", timeout: float) -> Any:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn(ctx)
        except BaseException as exc:  # re-raised in the caller thread with its traceback intact
            box["error"] = exc

    worker = threading.Thread(target=target, name=f"probe-{getattr(fn, '__name__', 'x')}", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise ProbeTimeout(f"probe exceeded {timeout:.0f}s timeout (treated as FAIL)")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ---------------------------------------------------------------- L7 · probe sensitivity (mutation tests)
MUTATIONS: dict[str, tuple[str, str, str, str]] = {
    # id: (target probe, file under src/muse, exact anchor, replacement) — each injects one real defect.
    "skip_approval_gate": ("L5.A2", "muse_commander.py",
                           'require_approval=risk in ("high", "medium"),', "require_approval=False,"),
    "accept_agent_approver": ("L4.1", "muse_commander.py",
                              '        if not isinstance(approved_by, str) or not approved_by.startswith(HUMAN_PREFIXES):\n'
                              '            raise PermissionError("high/medium-risk steps require a human approver (user:/human:)")',
                              '        if False:\n            raise PermissionError("disabled")'),
    "bypass_guard_open": ("L5.A3", "commander_routes.py",
                          "    if reason:\n        _audit_bypass(root, function=function, reason=reason, ctx=ctx)",
                          "    if False and reason:\n        _audit_bypass(root, function=function, reason=reason, ctx=ctx)"),
    "fabricate_revenue": ("L5.C1", "workflow_c.py",
                          '"confirmed_revenue": "unknown" if settled == 0 else summary',
                          '"confirmed_revenue": 0 if settled == 0 else summary'),
    "bundle_ignores_missing_subtitles": ("L5.C3", "publish_gate.py",
                                         '    missing = [name for name in ("video.mp4", "subtitles.srt") if not (package / name).exists()]',
                                         "    missing = []"),
    "drop_epistemic_tag": ("L6.7", "api_epistemics.py",
                           '    out["epistemic_tag"] = tag\n    out["epistemic_label_zh"] = EPISTEMIC_TAG_LABELS_ZH[tag]',
                           "    return payload"),
    "unwire_epistemic_envelope": ("L6.7", "status_api.py",
                                  "            payload = attach_epistemic(payload, urlsplit(self.path).path)",
                                  "            payload = payload"),
    "ruflo_scope_unchecked": ("L4.10", "ruflo_adapter.py",
                              "            require_ruflo_scope(name, workspace=self.workspace)", "            pass"),
    "migration_rewrites_content": ("L3.6", "memory_migration.py",
                                   '        after = content_fingerprint(con)\n        if after != before:\n            con.execute("ROLLBACK")\n'
                                   '            raise RuntimeError("memory content changed during migration; rolled back")',
                                   "        con.execute(\"UPDATE memories SET content = content || ' '\")\n        after = content_fingerprint(con)"),
    "egress_allowlist_open": ("L4.11", "external_agents.py",
                              '    extra = sorted(set(request) - set(policy["allowed_fields"]))', "    extra = []"),
    "shell_true_injected": ("L1.6", "error_journal.py", "MAX_FIELD_CHARS = 2000\n",
                            'MAX_FIELD_CHARS = 2000\n_MUTANT = (lambda: __import__("subprocess").run("echo", shell=True))\n'),
    "user_track_write_injected": ("L1.6", "research_wiki.py", 'WIKI_OBJECTS = (',
                                  'def _mutant(store):\n    return store.add("x", source="m", track="user")\n\n\nWIKI_OBJECTS = ('),
    "package_install_injected": ("L1.6", "adapter_hub.py", 'STATES = (',
                                 '_MUTANT_ARGV = ["pip", "install", "x"]\nSTATES = ('),
    "legacy_review_accepts_agent": ("L3.7", "legacy_memory_review.py",
                                    "    if not isinstance(reviewer, str) or not reviewer.startswith(REVIEWER_PREFIX) "
                                    "or not reviewer[len(REVIEWER_PREFIX):].strip():\n",
                                    "    if not isinstance(reviewer, str):\n"),
    "wiki_counts_any_supports_edge": ("L5.R2", "research_wiki.py",
                                      "                voice = self._valid_evidence_voice(ev, claim_id, stance)\n",
                                      "                voice = str((ev.get('metadata') or {}).get('registrable_domain') or ev.get('id'))\n"),
    "experiment_status_unchecked": ("L5.R2", "research_knowledge.py",
                                    "        if allowed_runs is not None:\n            for evidence_id in evidence_nodes:\n",
                                    "        if False:\n            for evidence_id in evidence_nodes:\n"),
    "memory_store_accepts_agent": ("L3.3", "memory.py",
                                   '            if row["track"] == "user" and not approved_by.startswith(HUMAN_APPROVER_PREFIXES):\n',
                                   "            if False:\n"),
    "deep_health_ignores_audit": ("L6.9", "deep_health.py",
                                  '    return {"status": "ok" if report["ok"] and not broken else "fail",\n',
                                  '    return {"status": "ok",\n'),
    "deep_health_swallows_check_error": ("L6.9", "deep_health.py",
                                         '            result = {"status": "error", "error": ',
                                         '            result = {"status": "ok", "error": '),
    "duplicate_def_injected": ("L1.7", "deep_health.py",
                               'SEVERITY = {"ok": 0, "warn": 1, "fail": 2, "error": 2}\n',
                               'SEVERITY = {"ok": 0, "warn": 1, "fail": 2, "error": 2}\n\n\ndef _pending_human(docs):\n    return {}\n'),
    "syntax_warning_injected": ("L1.7", "deep_health.py",
                                'SEVERITY = {"ok": 0, "warn": 1, "fail": 2, "error": 2}\n',
                                'SEVERITY = {"ok": 0, "warn": 1, "fail": 2, "error": 2}\n_MUTANT_PATTERN = "\\d+"\n'),
}


def _run_mutant(ctx: Ctx, mutation_id: str) -> dict[str, Any]:
    target, rel, anchor, replacement = MUTATIONS[mutation_id]
    base = ctx.sub(f"mutants/{mutation_id}")
    mutant_src = base / "src"
    shutil.copytree(SRC / "muse", mutant_src / "muse", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    path = mutant_src / "muse" / rel
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    count = text.count(anchor)
    if count != 1:
        return {"mutation": mutation_id, "target": target, "status": "ANCHOR_MISSING", "anchor_count": count}
    path.write_text(text.replace(anchor, replacement), encoding="utf-8")
    report_dir = base / "report"
    env = {**os.environ, "MUSE_DIAG_SRC_ROOT": str(mutant_src), "MUSE_DIAG_REPORT_DIR": str(report_dir),
           "PYTHONPATH": str(mutant_src), "MUSE_DIAG_PROBE_TIMEOUT": "90", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "muse_diag.py"), "--probe", target], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=110, env=env,
                          stdin=subprocess.DEVNULL)
    latest = report_dir / "latest.json"
    if not latest.is_file():
        return {"mutation": mutation_id, "target": target, "status": "NO_REPORT", "rc": proc.returncode,
                "tail": (proc.stderr or proc.stdout)[-600:]}
    probes = {p["id"]: p for p in json.loads(latest.read_text(encoding="utf-8"))["probes"]}
    got = probes.get(target, {}).get("status", "NOT_RUN")
    return {"mutation": mutation_id, "target": target, "status": got,
            "killed": got in (FAIL, ERROR), "detail": str(probes.get(target, {}).get("detail"))[:240]}


def _mutation_batch(ctx: Ctx, ids: list[str]) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda m: _run_mutant(ctx, m), ids))
    survivors = [o for o in outcomes if not o.get("killed")]
    require(not survivors, f"probes did not turn red under injected defects (insensitive probes): {survivors}")
    return {"killed": [(o["mutation"], o["target"], o["status"]) for o in outcomes]}


@probe("L7.2", "L7", "探针灵敏度 A：审批跳过/非人类批准/绕过守卫/编造收益/交付包放行 → 对应探针必须变红", ("AC-094",))
def l7_mutation_a(ctx: Ctx):
    return _mutation_batch(ctx, ["skip_approval_gate", "accept_agent_approver", "bypass_guard_open",
                                 "fabricate_revenue", "bundle_ignores_missing_subtitles", "legacy_review_accepts_agent",
                                 "memory_store_accepts_agent"])


@probe("L7.3", "L7", "探针灵敏度 B：去认知标签/Ruflo 守卫失效/迁移改写原文/外发白名单失效 → 对应探针必须变红", ("AC-094",))
def l7_mutation_b(ctx: Ctx):
    return _mutation_batch(ctx, ["drop_epistemic_tag", "unwire_epistemic_envelope", "ruflo_scope_unchecked",
                                 "migration_rewrites_content", "egress_allowlist_open", "wiki_counts_any_supports_edge",
                                 "experiment_status_unchecked", "deep_health_ignores_audit", "deep_health_swallows_check_error"])


@probe("L7.5", "L7", "探针灵敏度 C：注入 shell=True / User Track 直写 / pip install → 红线审计 L1.6 必须变红；注入重复定义 / 编译警告 → 代码健康 L1.7 必须变红", ("AC-094",))
def l7_mutation_c(ctx: Ctx):
    return _mutation_batch(ctx, ["shell_true_injected", "user_track_write_injected", "package_install_injected",
                                 "duplicate_def_injected", "syntax_warning_injected"])


@probe("L7.6", "L7", "实操手册可执行性：手册中的 python -m / scripts 命令逐条 --help 可运行，文档参数与子命令真实存在", ("AC-100",))
def l7_guide_commands(ctx: Ctx):
    import re as _re
    guide = (ROOT / "docs" / "USER_GUIDE_LIVE.md").read_text(encoding="utf-8")
    commands = _re.findall(r"`((?:python|py -3\.11)\s+(?:-m\s+muse\.[\w.]+|scripts[\\/][\w.]+\.py)[^`]*)`", guide)
    bats = sorted(set(_re.findall(r"`([\w]+\.bat)\b", guide)))
    require(len(commands) >= 8, f"too few documented commands found: {commands}")
    problems, checked, help_cache = [], [], {}
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(SRC)]), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    work = ctx.sub("guide commands cwd")
    for command in commands:
        tokens = command.split()
        if tokens[1] == "-m":
            target, argv = tokens[2], [sys.executable, "-m", tokens[2]]
            rest = tokens[3:]
        else:
            script = ROOT / tokens[1].replace("\\", "/")
            target, argv, rest = tokens[1], [sys.executable, str(script)], tokens[2:]
            if not script.is_file():
                problems.append(f"{command}: script missing")
                continue
        if target not in help_cache:
            proc = subprocess.run(argv + ["--help"], cwd=str(work), capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60, env=env, stdin=subprocess.DEVNULL)
            help_cache[target] = (proc.returncode, proc.stdout + proc.stderr)
        rc, text = help_cache[target]
        if rc != 0:
            problems.append(f"{target} --help exit {rc}: {text[-200:]}")
            continue
        for flag in _re.findall(r"(?<![\w-])(--[a-z][\w-]*)", " ".join(rest)):
            if flag not in text:
                problems.append(f"{command}: documented flag {flag} not in --help")
        for word in rest:
            if "|" in word and not word.startswith(("[", "<", '"')):
                for choice in word.split("|"):
                    if choice and choice not in text:
                        problems.append(f"{command}: documented sub-command {choice} not in --help")
        if rest and rest[0] and not rest[0].startswith(("-", "[", "<", '"')) and "|" not in rest[0] \
                and _re.fullmatch(r"[a-z][\w-]*", rest[0]) and rest[0] not in text:
            problems.append(f"{command}: documented sub-command {rest[0]} not in --help")
        checked.append(command)
    missing_bats = [b for b in bats if not (ROOT / b).is_file()]
    require(not problems and not missing_bats, f"user guide drift: {problems[:12]} missing_bats={missing_bats}")
    return {"commands_checked": len(checked), "targets": sorted(help_cache), "bats": bats}


@probe("L7.4", "L7", "台账一致性：AC 唯一、每条 AC 被探针引用、探针引用的 AC 均存在、标题总数一致", ("AC-095",))
def l7_ledger_consistency(ctx: Ctx):
    import re as _re
    text = (ROOT / "docs_quick_state" / "00_ACCEPTANCE_CRITERIA.md").read_text(encoding="utf-8")
    rows = [line.split("|")[1].strip() for line in text.splitlines() if line.startswith("| AC-")]
    dupes = sorted({a for a in rows if rows.count(a) > 1})
    referenced = {ac for _pid, _layer, _name, acs, _fn in PROBES for ac in acs}
    unreferenced = sorted(set(rows) - referenced)
    dangling = sorted(referenced - set(rows))
    title = _re.search(r"共 (\d+) 条", text)
    pids = [p[0] for p in PROBES]
    dup_probes = sorted({p for p in pids if pids.count(p) > 1})
    require(not dupes, f"duplicate AC rows: {dupes}")
    require(not unreferenced, f"ACs without any probe: {unreferenced}")
    require(not dangling, f"probes reference unknown ACs: {dangling}")
    require(not dup_probes, f"duplicate probe ids: {dup_probes}")
    require(title and int(title.group(1)) == len(rows), f"title count {title.group(1) if title else None} != rows {len(rows)}")
    return {"acs": len(rows), "probes": len(PROBES)}


def parse_ac_ids() -> list[str]:
    path = ROOT / "docs_quick_state" / "00_ACCEPTANCE_CRITERIA.md"
    ids = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("| AC-"):
                ids.append(line.split("|")[1].strip())
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="keep the throw-away run directory")
    parser.add_argument("--only", default="", help="comma separated layers, e.g. L1,L4")
    parser.add_argument("--probe", default="", help="comma separated probe ids, e.g. L5.A2,L4.9")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        sys.stdout.write("")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = (ROOT / "tmp" / f"muse_diag run {stamp}-{os.getpid()} 诊断").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MUSE_ERROR_JOURNAL"] = str(run_dir / "journal" / "suppressed.jsonl")
    ctx = Ctx(run_dir)
    only = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    probe_ids = {x.strip().upper() for x in args.probe.split(",") if x.strip()}
    results, log = [], []
    print(f"[Muse diag] root={ROOT}\n[Muse diag] python={sys.executable} {sys.version.split()[0]}\n[Muse diag] run_dir={run_dir}")
    for pid, layer, name, acs, fn in PROBES:
        if only and layer not in only:
            continue
        if probe_ids and pid.upper() not in probe_ids:
            continue
        started = time.perf_counter()
        entry: dict[str, Any] = {"id": pid, "layer": layer, "name": name, "ac": list(acs)}
        try:
            entry["detail"] = _run_probe_with_timeout(fn, ctx, PROBE_TIMEOUT_SECONDS)
            entry["status"] = PASS
        except ProbeTimeout as exc:
            entry["status"], entry["detail"] = FAIL, str(exc)
        except Skip as exc:
            entry["status"], entry["detail"] = SKIP, str(exc)
        except Check as exc:
            entry["status"], entry["detail"] = FAIL, str(exc)[:4000]
            entry["traceback"] = traceback.format_exc()[-4000:]
        except BaseException as exc:  # isolate everything, including SystemExit from probed code
            entry["status"], entry["detail"] = ERROR, f"{type(exc).__name__}: {exc}"[:4000]
            entry["traceback"] = traceback.format_exc()[-6000:]
        entry["seconds"] = round(time.perf_counter() - started, 3)
        results.append(entry)
        line = f"[{entry['status']:<11}] {pid:<5} {layer} {name} ({entry['seconds']}s)"
        print(line); log.append(line)
        if entry["status"] in (FAIL, ERROR):
            detail = json.dumps(entry["detail"], ensure_ascii=False, default=str)[:1500]
            print("    -> " + detail); log.append("    -> " + detail)
            if entry.get("traceback"):
                log.append(entry["traceback"])
    verdicts = {}
    for ac in parse_ac_ids():
        mapped = [r for r in results if ac in r["ac"]]
        if not mapped:
            verdicts[ac] = "NOT_RUN"
        elif any(r["status"] in (FAIL, ERROR) for r in mapped):
            verdicts[ac] = FAIL
        elif any(r["status"] == SKIP for r in mapped):
            verdicts[ac] = SKIP
        else:
            verdicts[ac] = PASS
    summary = {s: sum(1 for r in results if r["status"] == s) for s in (PASS, FAIL, ERROR, SKIP)}
    ac_summary = {s: sum(1 for v in verdicts.values() if v == s) for s in (PASS, FAIL, SKIP, "NOT_RUN")}
    report = {"schema": "muse.diag.v1", "generated_at": datetime.now().astimezone().isoformat(), "root": str(ROOT),
              "python": sys.version, "run_dir": str(run_dir), "summary": summary, "ac_summary": ac_summary,
              "ac_verdicts": verdicts, "probes": results, "facts": ctx.facts}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "history").mkdir(exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    (REPORT_DIR / "latest.json").write_text(text, encoding="utf-8")
    (REPORT_DIR / "history" / f"{stamp}.json").write_text(text, encoding="utf-8")
    table = ["", "=" * 64, "【AC 验收通过总表】", "=" * 64]
    table += [f"  {ac:<8} {v}" for ac, v in verdicts.items()]
    table += ["-" * 64, f"  探针: {summary}", f"  AC:   {ac_summary}", "=" * 64]
    print("\n".join(table))
    (REPORT_DIR / "latest.log").write_text("\n".join(log + table) + "\n", encoding="utf-8")
    if not args.keep:
        shutil.rmtree(run_dir, ignore_errors=True)
    return 0 if summary[FAIL] == 0 and summary[ERROR] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
