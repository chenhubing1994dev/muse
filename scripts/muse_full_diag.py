"""Muse full-depth diagnostic engine (stdlib only; no network, GPU or API key required).

Run via the one-click wrapper (recommended, PowerShell, from anywhere):

    & "D:\\AI AGENT\\Muse\\scripts\\run_full_diag.ps1"

or directly:

    py -3.11 "scripts\\muse_full_diag.py"                       # every layer incl. the original smoke stages
    py -3.11 "scripts\\muse_full_diag.py" --full-tests          # + the complete unittest suite
    py -3.11 "scripts\\muse_full_diag.py" --merge-smoke reports\\qw_smoke\\smoke_part.json --merge-smoke-log reports\\qw_smoke\\smoke_part.log
                                                                # reuse a smoke run instead of repeating it

Layers (every check is isolated; a crash is recorded with traceback + innermost local
variables and never stops the following checks):

  L1  static: compile every project .py, symtable undefined-global scan of src/muse,
      every pyproject entry point resolves and answers --help
  L2  dynamic import of every muse module and every tests/*.py module
  L3  config boundary cases; Windows space/CJK paths; atomic + concurrent state saves;
      corrupt-state recovery; SQLite read-only URI on a spaced path
  L4  subsystem units: memory, context window, approval gate, planner, review gate,
      structured-output parser/repair/breaker/retry, inner-state dynamics and budget,
      risk assessment, mind_step handler approval enforcement, failure tracing
  L5  deterministic offline brain loop (DeterministicMockLLM): multi-round
      perceive -> memory -> reflection -> plan -> gate -> act -> verify -> learn ->
      restart -> approve -> heartbeat -> goal closure, plus degraded/malformed model
  L6  original smoke stages S1-S9 (+ its L-stages) and Status API endpoint sweep

Outputs (UTF-8, atomic): reports/qw_smoke/latest.log, latest.json, diag_matrix.json,
full-diag-<timestamp>.log. Exit code 0 = everything passed.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import symtable
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import muse_smoke as smoke  # noqa: E402  (also puts src/ and tests/ on sys.path)

ROOT = smoke.ROOT
SRC = smoke.SRC
TESTS = smoke.TESTS
REPORT_DIR = smoke.REPORT_DIR
if "test_consciousness_loop" not in smoke.TARGETED_TESTS:
    smoke.TARGETED_TESTS.append("test_consciousness_loop")


# --------------------------------------------------------------------------- isolated stages
def _locals_snapshot(tb) -> dict[str, str]:
    """Locals of the innermost frame that belongs to this project (bounded reprs)."""
    frame = None
    while tb is not None:
        filename = tb.tb_frame.f_code.co_filename
        if str(ROOT) in filename or "muse" in filename:
            frame = tb.tb_frame
        tb = tb.tb_next
    if frame is None:
        return {}
    snapshot = {}
    for name, value in list(frame.f_locals.items())[:25]:
        if name.startswith("__"):
            continue
        try:
            text = repr(value)
        except Exception as exc:  # noqa: BLE001
            text = f"<unrepresentable {type(exc).__name__}>"
        snapshot[name] = text[:240]
    return {"function": frame.f_code.co_name, "line": str(frame.f_lineno), **snapshot}


class FullReport(smoke.Report):
    def stage(self, name: str, title: str, layer: str):  # noqa: D401 - same contract as smoke.Report.stage
        report = self

        class _Stage:
            def __enter__(self_inner):
                self_inner.started = time.perf_counter()
                self_inner.details = {}
                self_inner.warnings = []
                self_inner.checks = [0, 0]
                report.log(f"\n=== {name} [{layer}] {title} ===")
                return self_inner

            def check(self_inner, condition: bool, label: str) -> bool:
                self_inner.checks[1] += 1
                if condition:
                    self_inner.checks[0] += 1
                    report.log(f"  ok   {label}")
                else:
                    report.log(f"  FAIL {label}")
                return bool(condition)

            def __exit__(self_inner, exc_type, exc, tb):
                elapsed = round(time.perf_counter() - self_inner.started, 2)
                failed = self_inner.checks[1] - self_inner.checks[0]
                ok = exc_type is None and failed == 0
                locals_snapshot = {}
                if exc_type is not None:
                    report.log("EXCEPTION: " + "".join(traceback.format_exception(exc_type, exc, tb)).rstrip())
                    locals_snapshot = _locals_snapshot(tb)
                    if locals_snapshot:
                        report.log("LOCALS: " + json.dumps(locals_snapshot, ensure_ascii=False)[:2000])
                for warning in self_inner.warnings:
                    report.log("  WARN " + warning)
                report.log(f"--> {name} {'PASS' if ok else 'FAIL'} ({elapsed}s, checks {self_inner.checks[0]}/{self_inner.checks[1]})")
                error = None
                if exc_type is not None:
                    error = f"{exc_type.__name__}: {str(exc)[:500]}"
                elif failed:
                    error = f"{failed} check(s) failed"
                report.stages.append({
                    "stage": name, "layer": layer, "title": title, "passed": ok, "seconds": elapsed,
                    "checks_passed": self_inner.checks[0], "checks_total": self_inner.checks[1],
                    "details": self_inner.details, "warnings": self_inner.warnings, "error": error,
                    "locals": locals_snapshot,
                })
                return True  # isolate everything, including KeyboardInterrupt inside a stage

        return _Stage()


# --------------------------------------------------------------------------- L1
_BUILTIN_NAMES = set(dir(__builtins__)) if not isinstance(__builtins__, dict) else set(__builtins__)
_BUILTIN_NAMES |= {"__file__", "__name__", "__doc__", "__spec__", "__package__", "__loader__", "__builtins__",
                   "__path__", "__annotations__", "__class__", "WindowsError"}


def stage_undefined_names(r: FullReport) -> None:
    with r.stage("D1.1", "static: symtable undefined-global scan of src/muse", "L1") as st:
        findings = []
        files = sorted((SRC / "muse").glob("*.py"))
        for path in files:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree)):
                continue
            defined = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                    defined.add(node.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    defined.update((a.asname or a.name).split(".")[0] for a in node.names)
                elif isinstance(node, ast.ExceptHandler) and node.name:
                    defined.add(node.name)
            tables = []

            def walk(table):
                tables.append(table)
                for child in table.get_children():
                    walk(child)
            walk(symtable.symtable(source, str(path), "exec"))
            seen = set()
            for table in tables:
                for symbol in table.get_symbols():
                    name = symbol.get_name()
                    if (symbol.is_referenced() and symbol.is_global() and name not in defined
                            and name not in _BUILTIN_NAMES and name not in seen):
                        seen.add(name)
                        findings.append(f"{path.name}: {name}")
        st.details = {"files": len(files), "undefined": findings}
        for item in findings:
            r.log("  " + item)
        st.check(not findings, f"{len(files)} modules without undefined global names ({len(findings)} found)")


# --------------------------------------------------------------------------- L2
def stage_import_tests(r: FullReport) -> None:
    with r.stage("D2.1", "dynamic import of every tests/*.py module", "L2") as st:
        import importlib
        failures, optional = [], []
        modules = sorted(p.stem for p in TESTS.glob("test_*.py"))
        for name in modules:
            try:
                with smoke.quiet():
                    importlib.import_module(name)
            except ModuleNotFoundError as exc:
                missing = (exc.name or "").split(".")[0]
                (optional if missing in smoke.OPTIONAL_PACKAGES else failures).append(f"{name}: {exc}")
            except BaseException as exc:  # noqa: BLE001
                failures.append(f"{name}: {type(exc).__name__}: {str(exc)[:200]}")
        st.warnings.extend(optional)
        st.details = {"modules": len(modules), "failures": failures, "optional_missing": optional}
        for item in failures:
            r.log("  " + item)
        st.check(not failures, f"{len(modules) - len(failures) - len(optional)}/{len(modules)} test modules import")


# --------------------------------------------------------------------------- L3
def stage_persistence(r: FullReport, work: Path) -> None:
    with r.stage("D3.1", "persistence: spaced/CJK paths, atomic + concurrent saves, corrupt recovery, read-only URI", "L3") as st:
        from muse.consciousness_loop import MindJournal, read_mind_status
        from muse.inner_state import InnerStateStore, read_snapshot
        base = work / "路径 含 空格" / "Muse 状态"
        path = base / "state.json"
        store = InnerStateStore(path)
        store.add_goal("持久化目标")
        store.save()
        store.apply_event("novelty")
        store.save()
        st.check(path.is_file() and Path(str(path) + ".bak").is_file(), "main + backup written under a spaced CJK path")
        st.check(json.loads(path.read_text(encoding="utf-8"))["schema"] == "muse-inner-state-v1", "state is valid UTF-8 JSON")
        errors = []

        def writer():
            try:
                for _ in range(15):
                    store.apply_event("novelty")
                    store.save()
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
        threads = [threading.Thread(target=writer) for _ in range(6)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        st.check(not errors, f"90 concurrent saves without error ({errors[:2]})")
        st.check(not list(base.glob("*.tmp")), "no temp files left behind")
        st.check(InnerStateStore(path).state["recovered_from"] is None, "file intact after concurrent saves")
        path.write_text("{half-written", encoding="utf-8")
        recovered = InnerStateStore(path)
        st.check(recovered.state["recovered_from"] == "backup", "corrupt main -> backup")
        st.check(recovered.state["goals"][0]["text"] == "持久化目标", "goal survived corruption")
        Path(str(path) + ".bak").write_text("garbage", encoding="utf-8")
        st.check(InnerStateStore(path).state["recovered_from"] == "defaults_after_corruption", "both corrupt -> explicit defaults")
        st.check(read_snapshot(base / "missing.json")["status"] == "not_initialized", "read_snapshot on missing state")
        journal = MindJournal(base / "journal.sqlite3")
        journal.start("a" * 32, source="test", input_sha256="0" * 64)
        st.check(read_mind_status(base.parent)["inner_state"]["status"] in {"not_initialized", "ok"},
                 "read_mind_status tolerates a workspace without mind data")
        workspace = work / "Muse 工作区 空格"
        (workspace / "data" / "mind").mkdir(parents=True)
        InnerStateStore(workspace / "data" / "mind" / "state.json").save()
        MindJournal(workspace / "data" / "mind" / "journal.sqlite3").start("b" * 32, source="test", input_sha256="1" * 64)
        status = read_mind_status(workspace)
        st.check(status["inner_state"]["status"] == "ok" and len(status["recent_cycles"]) == 1,
                 "read-only SQLite URI works on a spaced CJK path")


# --------------------------------------------------------------------------- L4
def stage_structured_output(r: FullReport) -> None:
    with r.stage("D4.1", "structured output: extraction, repair, schema, repair loop, breaker, retry", "L4") as st:
        from muse.structured_output import (
            CircuitBreaker, CircuitOpenError, StructuredOutputError, call_with_retry, extract_json,
            request_structured, validate,
        )
        cases = [
            ('{"a": 1}', {"a": 1}), ('前言 {"a": 1} 后记', {"a": 1}), ('```json\n{"a": [1,2,],}\n```', {"a": [1, 2]}),
            ('{"ok": True, "v": None}', {"ok": True, "v": None}), ('“{\u201ca\u201d: 1}”', {"a": 1}),
        ]
        for text, expected in cases:
            try:
                got = extract_json(text)
            except StructuredOutputError as exc:
                got = f"error: {exc}"
            st.check(got == expected, f"extract {text[:30]!r} -> {got}")
        for bad in ("", "no json", '{"a":1,"a":2}', "x" * 200_001):
            try:
                extract_json(bad)
                st.check(False, f"rejects {bad[:20]!r}")
            except StructuredOutputError:
                st.check(True, f"rejects {bad[:20]!r}")
        schema = {"type": "object", "required": ["n", "tags"], "additionalProperties": False, "properties": {
            "n": {"type": "integer", "minimum": 0}, "tags": {"type": "array", "maxItems": 2, "items": {"type": "string"}}}}
        st.check(validate({"n": 1, "tags": ["a"]}, schema) == [], "valid document")
        st.check(len(validate({"n": -1, "tags": ["a", "b", "c"], "x": 1}, schema)) == 3, "three violations reported")
        replies = iter(["garbage", '{"n": "1", "tags": []}', '{"n": 1, "tags": []}'])
        st.check(request_structured(lambda _m: next(replies), [{"role": "user", "content": "q"}], schema) == {"n": 1, "tags": []},
                 "repair loop recovers on 3rd attempt")
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=2, reset_seconds=5, clock=lambda: now[0])
        for _ in range(2):
            try:
                breaker.call(lambda: (_ for _ in ()).throw(ConnectionError("x")))
            except ConnectionError:
                pass
        try:
            breaker.call(lambda: 1)
            st.check(False, "breaker opens")
        except CircuitOpenError:
            st.check(True, "breaker opens after threshold")
        now[0] = 6.0
        st.check(breaker.call(lambda: 2) == 2 and breaker.state == "closed", "half-open probe closes breaker")
        calls = []
        st.check(call_with_retry(lambda: calls.append(1) or (_ for _ in ()).throw(TimeoutError()) if len(calls) < 3 else "ok",
                                 retries=4, sleep=lambda _s: None) == "ok", "retry recovers transient errors")


def stage_inner_state(r: FullReport, work: Path) -> None:
    with r.stage("D4.2", "inner state: events, homeostasis, budget throttle, goals, strategy, autonomy gate", "L4") as st:
        from muse.inner_state import EVENT_EFFECTS, InnerStateStore
        now = [10_000.0]
        store = InnerStateStore(work / "inner" / "s.json", clock=lambda: now[0], max_calls_per_hour=3, min_thought_interval=60)
        for kind in EVENT_EFFECTS:
            store.apply_event(kind)
        affect = store.state["affect"]
        st.check(-1 <= affect["valence"] <= 1 and 0 <= affect["energy"] <= 1 and 0 <= affect["curiosity"] <= 1,
                 f"all {len(EVENT_EFFECTS)} events keep affect bounded")
        for _ in range(6):
            store.apply_event("task_failed")
        st.check(store.mood_label() in {"cautious", "depleted"} and store.risk_appetite() == "minimal",
                 f"repeated failure -> {store.mood_label()} / minimal risk appetite")
        now[0] += 48 * 3600
        store.tick()
        st.check(store.risk_appetite() == "normal", "homeostasis restores normal appetite after 48h")
        st.check([store.try_consume(tokens=10) for _ in range(4)] == [True, True, True, False], "budget throttles 4th call")
        st.check(store.should_think() == (False, "budget_exhausted"), "autonomy gate respects budget")
        now[0] += 3601
        st.check(store.should_think() == (False, "no_active_goal"), "no goal -> no introspection")
        goal = store.add_goal("探索新灵感", priority=0.8)
        store.state["affect"]["curiosity"] = 0.9
        st.check(store.should_think() == (True, "ready"), "ready with goal, energy, curiosity, budget")
        st.check(store.should_think(exclude={goal["id"]})[0] is False, "goal with work in flight is not re-planned")
        store.mark_thought()
        st.check(store.should_think()[1] == "cooldown", "cooldown after a thought")
        st.check(store.record_cycle_outcome(succeeded=0, failed=2)["max_actions"] == 2, "failure halves max_actions")
        for _ in range(3):
            store.record_cycle_outcome(succeeded=1, failed=0)
        st.check(store.state["strategy"]["max_actions"] == 3, "success streak grows max_actions")


def stage_mind_units(r: FullReport, work: Path) -> None:
    with r.stage("D4.3", "mind units: risk gate, input sanitising, memory ranking, handler approval enforcement, tracing", "L4") as st:
        from muse.consciousness_loop import (
            MindLoop, MindStepHandler, assess_risk, heuristic_reflection, rank_memories, sanitize_input,
        )
        st.check(assess_risk("writer", "发布到公众号并付款推广")[0] == "high", "publish/pay is high risk")
        st.check(assess_risk("researcher", "collect information sources")[0] == "low", "'information' is not 'format'")
        st.check(assess_risk("operator", "整理")[0] == "medium", "operator role is medium")
        st.check(len(sanitize_input("x" * 20_000)) == 8000, "input bounded to 8000 chars")
        st.check("\x07" not in sanitize_input("a\x07b"), "control chars stripped")
        st.check(heuristic_reflection("你好呀")["needs_plan"] is False, "small talk does not create plans")
        ranked = rank_memories([{"id": "old", "confidence": 0.9, "updated_at": "2000-01-01T00:00:00+00:00"},
                                {"id": "new", "confidence": 0.9, "updated_at": "2100-01-01T00:00:00+00:00"}])
        st.check(ranked[0]["id"] == "new", "recency decay ranks fresh memory first (read-time only)")
        handler = MindStepHandler(work / "packets")
        payload = {"cycle_id": "c" * 32, "step_id": "s1", "role": "writer", "objective": "写提纲"}
        try:
            handler.execute_task(payload, task_id="t1")
            st.check(False, "unapproved mind_step refused")
        except PermissionError:
            st.check(True, "unapproved mind_step refused (defence in depth)")
        done = handler.execute_task({**payload, "_approved": True, "_approval": {"approved_by": "human"}}, task_id="t1")
        st.check(Path(done["packet_path"]).is_file() and done["status"] == "work_packet_prepared", "approved step writes packet")
        try:
            handler.execute_task({**payload, "step_id": "../../escape", "_approved": True, "_approval": {"approved_by": "h"}}, task_id="t2")
            st.check(False, "path traversal step_id refused")
        except ValueError:
            st.check(True, "path traversal step_id refused")

        class Boom:
            def execute_task(self, _payload, task_id):
                raise RuntimeError("boom")

        def failing(db, handlers, task_id):
            return smoke_run_once(db, {"mind_step": Boom()}, task_id)

        loop = MindLoop(work / "mind-fail", runner=failing)
        result = loop.perceive("规划研究资料整理")
        st.check(result["actions"]["failed"] >= 1, f"handler failure observed ({result['actions']['failed']})")
        st.check(len(result["learning"]["failure_traces"]) == result["actions"]["failed"], "each failure gets causal hypotheses")
        st.check(result["learning"]["strategy"]["max_actions"] < 4, "strategy self-corrects after failure")


def smoke_run_once(database, handlers, task_id):
    from muse.worker import DEFAULT_HANDLERS, run_once
    return run_once(database, {**DEFAULT_HANDLERS, **handlers}, task_id=task_id)


# --------------------------------------------------------------------------- L5
class DeterministicMockLLM:
    """Zero-dependency offline model with deterministic, schema-shaped answers.

    ``mode='messy'`` wraps the first reflection in prose with a wrong type so the
    repair path is exercised; ``mode='down'`` raises ConnectionError on every call.
    """

    def __init__(self, mode: str = "clean") -> None:
        self.mode = mode
        self.calls = 0
        self.lock = threading.Lock()

    def chat(self, messages, **_kwargs) -> str:
        with self.lock:
            self.calls += 1
        if self.mode == "down":
            raise ConnectionError("mock model offline")
        system = messages[0].get("content", "")
        last = messages[-1].get("content", "")
        # Repair rounds append a format prompt; the real input is always the first user message.
        first_user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        user = first_user.split("Input:\n", 1)[-1]
        if "reflective layer" in system:
            if self.mode == "messy" and "FORMAT_REPAIR_REQUIRED" not in last:
                return '我想了一下：{"summary": 42, "intent": "plan"}'
            lowered = user.casefold()
            if any(m in user for m in ("规划", "设计", "制作")):
                intent, plan = "plan", True
            elif "研究" in user:
                intent, plan = "research", True
            elif "偏好" in user or "记住" in user:
                intent, plan = "remember", False
            else:
                intent, plan = "chat", False
            candidates = [user.strip()[:200]] if ("偏好" in user or "prefer" in lowered) else []
            return json.dumps({"summary": user[:120], "intent": intent, "needs_plan": plan, "goal": user[:300],
                               "risk_notes": [], "memory_candidates": candidates}, ensure_ascii=False)
        if "Plan the goal" in system:
            return json.dumps({"steps": [
                {"id": "research", "role": "researcher", "objective": "整理相关资料与约束", "depends_on": []},
                {"id": "design", "role": "architect", "objective": "形成方案与验收标准", "depends_on": []},
                {"id": "build", "role": "developer", "objective": "实现最小原型", "depends_on": ["research", "design"]},
                {"id": "review", "role": "reviewer", "objective": "独立核查成果", "depends_on": ["build"]},
            ]}, ensure_ascii=False)
        return json.dumps({"notes": "deterministic work note", "next_actions": ["记录结果"], "open_questions": []})


def stage_brain_loop(r: FullReport, work: Path) -> None:
    with r.stage("D5.1", "offline brain loop: multi-round cycles, restart, human approval, heartbeat, goal closure", "L5") as st:
        from muse.consciousness_loop import MIND_STEP_KIND, MindLoop, read_mind_status
        from muse.memory import MemoryStore
        from muse.task_queue import TaskQueue
        workspace = work / "Muse 全脑 闭环"
        model = DeterministicMockLLM("messy")
        loop = MindLoop(workspace, model=model)
        first = loop.perceive("请帮我规划一个离线灵感实验台")
        st.check(first["reflection"]["source"] == "model", "malformed first reply repaired into a model reflection")
        st.check(first["plan"]["source"] == "model" and first["plan"]["steps"] == 4, "model plan accepted (4 steps)")
        st.check(first["actions"]["auto_approved"] == 3 and len(first["actions"]["waiting_approval"]) == 1,
                 "3 low-risk steps policy-approved, developer step waits for a human")
        st.check(first["actions"]["succeeded"] == 2, "independent research + design executed in parallel wave")
        st.check(first["status"] == "waiting_approval", "cycle reports waiting_approval")
        second = loop.perceive("记住：我偏好用粤语写复盘")
        st.check(second["actions"]["planned"] == 0, "preference turn creates no plan")
        st.check(len(second["learning"]["memory_candidates_pending"]) == 1, "preference staged as pending candidate")
        third = loop.perceive("你好，今天状态如何？")
        st.check(third["status"] == "completed", "chat turn completes")
        with MemoryStore(loop.memory_db) as memory:
            candidates = memory.list_pending(limit=10)
            st.check(len(candidates) == 1, "exactly one pending candidate (dedupe)")
            memory.approve_candidate(int(candidates[0]["id"]), approved_by="human-diag")
        fourth = loop.perceive("复盘应该用什么语言写？")
        st.check(any(m["track"] == "user" for m in fourth["memories"]), "approved preference retrieved in a later cycle")
        counters_before = dict(loop.state.state["counters"])
        pending = loop.status()["pending_approvals"]
        st.check(len(pending) == 1, "one human approval pending")
        with TaskQueue(loop.task_db) as queue:
            queue.approve(pending[0]["task_id"], approved_by="human-diag")
        restarted = MindLoop(workspace, model=DeterministicMockLLM("clean"))
        st.check(restarted.state.state["counters"]["cycles"] == counters_before["cycles"], "cycle counter survives restart")
        st.check(restarted.state.state["counters"]["restarts"] >= 1, "restart recorded")
        tick = restarted.idle_tick()
        st.check(tick["executed_after_approval"]["succeeded"] == 2, "approved developer + dependent reviewer run on heartbeat")
        with TaskQueue(restarted.task_db) as queue:
            statuses = sorted(t["status"] for t in queue.list_tasks(kind=MIND_STEP_KIND))
        st.check(statuses == ["succeeded"] * 4, f"all plan steps succeeded ({statuses})")
        st.check(not [g for g in restarted.state.state["goals"] if g["status"] == "active"], "goal closed after completion")
        tick2 = restarted.idle_tick()
        st.check(tick2["status"] == "rested", f"no re-planning of closed goals ({tick2['status']}/{tick2['reason']})")
        with MemoryStore(restarted.memory_db) as memory:
            episodes = memory.list_memories(project="muse-mind", track="agent", limit=50)
        st.check(len(episodes) == 4, f"one Agent Track episode per cycle ({len(episodes)})")
        status = read_mind_status(workspace)
        st.check(status["inner_state"]["status"] == "ok" and status["pending_approvals"] == 0, "API view consistent")
        st.details = {"model_calls": model.calls, "statuses": statuses, "cycles": len(status["recent_cycles"])}


def stage_degraded_model(r: FullReport, work: Path) -> None:
    with r.stage("D5.2", "degraded model: offline LLM, circuit breaker, deterministic fallback, budget exhaustion", "L5") as st:
        from muse.consciousness_loop import MindLoop
        down = DeterministicMockLLM("down")
        loop = MindLoop(work / "Muse 降级", model=down)
        results = [loop.perceive("规划一个学习计划") for _ in range(3)]
        st.check(all(r_["reflection"]["source"] == "heuristic" for r_ in results), "heuristic reflection when model is down")
        st.check(all(r_["plan"]["source"] == "safe_fallback" for r_ in results), "safe fallback plan when model is down")
        st.check(loop.breaker.state == "open", "circuit opens instead of hammering the model")
        calls_when_open = down.calls
        loop.perceive("再规划一次")
        st.check(down.calls == calls_when_open, "open circuit short-circuits further calls")
        tiny = MindLoop(work / "Muse 预算", model=DeterministicMockLLM("clean"),
                        state_kwargs={"max_calls_per_hour": 1, "max_tokens_per_hour": 100_000})
        result = tiny.perceive("请规划一个工具")
        st.check(result["status"] in {"waiting_approval", "completed"} and "budget_exhausted" in str(result.get("model_error")),
                 f"budget exhaustion degrades gracefully ({result.get('model_error')})")


# --------------------------------------------------------------------------- matrix
STAGE_MODULES = {
    "S3": ["main", "config"], "S4": ["task_queue", "worker"], "S5": ["autonomous_runtime", "task_queue"],
    "S6": ["initiative_engine", "product_builder"], "S7": ["research_state_machine", "research_knowledge"],
    "S8": ["status_api"], "L2": ["config", "main"], "L3.1": ["memory"], "L3.2": ["assistant", "conversations"],
    "L3.3": ["task_queue", "worker"], "L3.4": ["commander_planner"], "L3.5": ["review_gate"], "L3.6": ["status_api"],
    "L4.1": ["assistant", "commander_planner", "conversations", "initiative_engine", "memory", "memory_extraction",
             "product_builder", "review_gate", "task_queue", "worker", "status_api"],
    "L4.2": ["task_queue", "memory_extraction"], "L4.3": ["task_queue", "memory", "worker"],
    "D3.1": ["inner_state", "consciousness_loop"], "D4.1": ["structured_output"], "D4.2": ["inner_state"],
    "D4.3": ["consciousness_loop", "trace", "worker", "task_queue"],
    "D5.1": ["consciousness_loop", "inner_state", "structured_output", "memory", "task_queue", "worker",
             "commander_planner", "skillify", "trace"],
    "D5.2": ["consciousness_loop", "structured_output", "commander_planner"],
}


def build_matrix(stages: list[dict]) -> dict:
    by_stage = {s["stage"]: s for s in stages}
    s2 = by_stage.get("S2", {}).get("details", {})
    import_fail = {item.split(":", 1)[0] for item in s2.get("failures", [])}
    import_optional = {item.split(" ", 1)[0] for item in s2.get("optional_missing", [])}
    l1 = by_stage.get("L1", {}).get("details", {})
    compile_fail = {Path(item.split(":", 1)[0]).stem for item in l1.get("failures", []) if "src" in item}
    test_text = {p.stem: p.read_text(encoding="utf-8", errors="replace") for p in TESTS.glob("test_*.py")}
    import tomllib
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    cli_modules = {target.split(":")[0].split(".")[1]: cmd for cmd, target in scripts.items()}
    cli_results = by_stage.get("L1.2", {}).get("details", {}).get("results", {})
    modules = {}
    for path in sorted((SRC / "muse").glob("*.py")):
        name = path.stem
        if name == "__init__":
            continue
        tests = [t for t, text in test_text.items() if re.search(rf"\bmuse\.{name}\b", text)]
        behaviour = {stage: by_stage[stage]["passed"] for stage, mods in STAGE_MODULES.items()
                     if name in mods and stage in by_stage}
        if name in compile_fail or name in import_fail:
            verdict = "❌"
        elif name in import_optional:
            verdict = "⚠️ optional dependency missing"
        elif behaviour and not all(behaviour.values()):
            verdict = "❌ behaviour check failed"
        elif behaviour or tests:
            verdict = "✅"
        else:
            verdict = "⚠️ no test / no behavioural check"
        modules[name] = {
            "compile": name not in compile_fail, "import": "fail" if name in import_fail else (
                "optional_missing" if name in import_optional else "ok"),
            "unit_test_files": len(tests), "behaviour_stages": behaviour,
            "cli": cli_modules.get(name), "cli_help": cli_results.get(cli_modules.get(name, ""), None),
            "verdict": verdict,
        }
    summary = {}
    for item in modules.values():
        key = item["verdict"].split(" ")[0]
        summary[key] = summary.get(key, 0) + 1
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary, "modules": modules}


# --------------------------------------------------------------------------- main
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse full-depth diagnostic engine")
    parser.add_argument("--full-tests", action="store_true", help="S9 runs the complete unittest suite")
    parser.add_argument("--skip-tests", action="store_true", help="skip unittest stage S9")
    parser.add_argument("--merge-smoke", default=None, help="latest.json of a muse_smoke.py run to merge instead of re-running")
    parser.add_argument("--merge-smoke-log", default=None, help="log of that smoke run, appended to latest.log")
    parser.add_argument("--llm-url", default=None, help="optional loopback model URL for smoke stage L4.1")
    args = parser.parse_args(argv)

    r = FullReport()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    r.log("Muse full-depth diagnostic")
    r.log(f"started  : {started}")
    r.log(f"python   : {sys.version.split()[0]} ({sys.executable})")
    r.log(f"root     : {ROOT}")
    merged_stages: list[dict] = []
    smoke_log = ""
    if args.merge_smoke:
        try:
            merged = json.loads(Path(args.merge_smoke).read_text(encoding="utf-8"))
            merged_stages = merged.get("stages", [])
            r.log(f"merged smoke run: {len(merged_stages)} stages from {args.merge_smoke}")
        except (OSError, ValueError) as exc:
            r.log(f"WARN could not merge smoke run ({type(exc).__name__}); running smoke stages here")
    if args.merge_smoke_log:
        try:
            smoke_log = Path(args.merge_smoke_log).read_text(encoding="utf-8")
        except OSError:
            smoke_log = ""

    with tempfile.TemporaryDirectory(prefix="muse-full-diag-") as temp:
        work = Path(temp)
        if not merged_stages:
            # L1/L2/L3/L4/L6: every original smoke stage, isolated by FullReport
            smoke.stage_compile(r)
            smoke.stage_import(r)
            smoke.stage_config(r)
            smoke.stage_task_flow(r, work)
            smoke.stage_local_workflow(r, work)
            smoke.stage_product_flow(r, work)
            smoke.stage_research(r, work)
            smoke.stage_status_api(r, work)
            smoke.stage_static_all(r)
            smoke.stage_cli_entrypoints(r)
            smoke.stage_config_boundaries(r, work)
            smoke.stage_memory(r, work)
            smoke.stage_context_window(r, work)
            smoke.stage_approval_gate(r, work)
            smoke.stage_planner(r)
            smoke.stage_review_gate(r, work)
            smoke.stage_api_sweep(r, work)
            smoke.stage_mind_loop(r, work, args.llm_url)
            smoke.stage_fault_injection(r, work)
            smoke.stage_concurrency(r, work)
            if not args.skip_tests:
                smoke.stage_tests(r, args.full_tests)
        stage_undefined_names(r)
        stage_import_tests(r)
        stage_persistence(r, work)
        stage_structured_output(r)
        stage_inner_state(r, work)
        stage_mind_units(r, work)
        stage_brain_loop(r, work)
        stage_degraded_model(r, work)

    stages = merged_stages + r.stages
    matrix = build_matrix(stages)
    passed = all(s["passed"] for s in stages)
    checks_ok = sum(s.get("checks_passed", 0) for s in stages)
    checks_total = sum(s.get("checks_total", 0) for s in stages)
    stages_ok = sum(1 for s in stages if s["passed"])
    r.log("\n=== SUMMARY (all layers) ===")
    r.log(f"{'stage':<7}{'layer':<7}{'result':<7}{'checks':<9}title")
    for s in stages:
        r.log(f"{s['stage']:<7}{s.get('layer', ''):<7}{'PASS' if s['passed'] else 'FAIL':<7}"
              f"{str(s.get('checks_passed', 0)) + '/' + str(s.get('checks_total', 0)):<9}{s['title']}")
    rate = round(100.0 * stages_ok / max(len(stages), 1), 1)
    r.log(f"stages {stages_ok}/{len(stages)} ({rate}%), checks {checks_ok}/{checks_total}")
    r.log(f"module matrix: {matrix['summary']}")
    r.log(f"RESULT: {'ALL PASSED' if passed else 'FAILED'}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    text = "\n".join(r.lines) + "\n"
    if smoke_log:
        text += "\n\n==================== ORIGINAL SMOKE RUN (muse_smoke.py) ====================\n" + smoke_log
    _atomic_write(REPORT_DIR / "latest.log", text)
    _atomic_write(REPORT_DIR / f"full-diag-{stamp}.log", text)
    payload = {"started": started, "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "python": sys.version.split()[0],
               "passed": passed, "stage_pass_rate": rate, "checks_passed": checks_ok, "checks_total": checks_total,
               "stages": stages}
    _atomic_write(REPORT_DIR / "latest.json", json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    _atomic_write(REPORT_DIR / "diag_matrix.json", json.dumps({**matrix, "stages": [
        {k: s.get(k) for k in ("stage", "layer", "title", "passed", "checks_passed", "checks_total", "seconds", "error")}
        for s in stages]}, ensure_ascii=False, indent=2, default=str))
    print(f"\nreport: {REPORT_DIR / 'latest.log'}\nmatrix: {REPORT_DIR / 'diag_matrix.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    raise SystemExit(main())
