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
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
REPORT_DIR = ROOT / "reports" / "muse_diag"
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
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        sys.stdout.write("")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = (ROOT / "tmp" / f"muse_diag run {stamp} 诊断").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MUSE_ERROR_JOURNAL"] = str(run_dir / "journal" / "suppressed.jsonl")
    ctx = Ctx(run_dir)
    only = {x.strip().upper() for x in args.only.split(",") if x.strip()}
    results, log = [], []
    print(f"[Muse diag] root={ROOT}\n[Muse diag] python={sys.executable} {sys.version.split()[0]}\n[Muse diag] run_dir={run_dir}")
    for pid, layer, name, acs, fn in PROBES:
        if only and layer not in only:
            continue
        started = time.perf_counter()
        entry: dict[str, Any] = {"id": pid, "layer": layer, "name": name, "ac": list(acs)}
        try:
            entry["detail"] = fn(ctx)
            entry["status"] = PASS
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
