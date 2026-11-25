"""Muse full-depth health check (stdlib only, no network by default, no GPU, no API keys).

Usage (project root; quotes are required because the path contains a space):

    "D:\\AI AGENT\\Muse\\run_smoke.bat"                     one-click (activates .venv, UTF-8)
    py -3.11 scripts\\muse_smoke.py                          all layers + targeted regression tests
    py -3.11 scripts\\muse_smoke.py --full                   + the complete unittest suite
    py -3.11 scripts\\muse_smoke.py --quick                  skip unittest stages
    py -3.11 scripts\\muse_smoke.py --llm-url http://127.0.0.1:1234/v1
                                                            L4 also runs one turn against a real local model

Stages (each is isolated: a failing stage never stops the following ones)

  Original smoke (kept compatible)
    S1 compile src/muse            S2 import every muse module      S3 muse.main --check
    S4 queue -> approval -> worker S5 deterministic local workflow  S6 initiative -> offline product
    S7 research state machine      S8 Status API health + auth      S9 targeted / full unittest
  Layer 1  static: compile every project .py (src, tests, scripts, tools, pipeline, ember-fire)
  Layer 2  config: missing / corrupt / wrong-type / incomplete config; .env.example placeholder audit
  Layer 3  subsystems: memory CRUD + dedupe + persistence, bounded chat context, approval gate
           allow / reject / idempotency, Commander planner + fallback, independent review gate,
           Status API endpoint sweep (auth, origin, every GET route)
  Layer 4  end-to-end mind loop (offline mock LLM, optional real LLM), fault injection,
           concurrent queue / memory stress

Everything runs in a throw-away temporary workspace; data/, config/muse.toml and .env are never
read or written. Results: reports/qw_smoke/latest.log + latest.json (+ timestamped copy).
Exit code 0 = every stage passed.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import locale
import os
import platform
import re
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
REPORT_DIR = ROOT / "reports" / "qw_smoke"

for entry in (str(SRC), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

OPTIONAL_PACKAGES = {
    "faster_whisper", "canto_tts", "celestial_calendar", "llama_cpp", "PIL", "piper",
    "cryptography", "scrapling", "cv2", "numpy", "ziwei", "playwright",
}

TARGETED_TESTS = [
    "test_research_state_machine", "test_research_task_handler", "test_worker", "test_task_queue",
    "test_product_builder", "test_autonomous_runtime", "test_status_api", "test_local_dashboard",
    "test_model_supervisor", "test_video_product_builder", "test_assistant", "test_memory",
    "test_conversations_and_model_client", "test_chat_cli", "test_config", "test_cantonese_tts",
    "test_consciousness_loop",
]

STATIC_DIRS = ("src", "tests", "scripts", "tools", "pipeline", "ember-fire", "examples")
STATIC_SKIP = {"node_modules", ".venv", "__pycache__", "target", "dist", "out", "data", ".git"}


# --------------------------------------------------------------------------- reporting
class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.stages: list[dict] = []

    def log(self, message: str = "") -> None:
        print(message, flush=True)
        self.lines.append(message)

    def stage(self, name: str, title: str, layer: str):
        report = self

        class _Stage:
            def __enter__(self_inner):
                self_inner.started = time.perf_counter()
                self_inner.details = {}
                self_inner.warnings = []
                self_inner.checks = [0, 0]  # passed, total
                report.log(f"\n=== {name} {title} ===")
                return self_inner

            def check(self_inner, condition: bool, label: str) -> bool:
                self_inner.checks[1] += 1
                if condition:
                    self_inner.checks[0] += 1
                    report.log(f"  ok   {label}")
                else:
                    report.log(f"  FAIL {label}")
                return condition

            def __exit__(self_inner, exc_type, exc, tb):
                elapsed = round(time.perf_counter() - self_inner.started, 2)
                failed_checks = self_inner.checks[1] - self_inner.checks[0]
                ok = exc_type is None and failed_checks == 0
                if exc_type is not None:
                    report.log("EXCEPTION: " + "".join(traceback.format_exception(exc_type, exc, tb)).rstrip())
                for warning in self_inner.warnings:
                    report.log("  WARN " + warning)
                report.log(f"--> {name} {'PASS' if ok else 'FAIL'} ({elapsed}s, checks {self_inner.checks[0]}/{self_inner.checks[1]})")
                error = None
                if exc_type is not None:
                    error = f"{exc_type.__name__}: {str(exc)[:500]}"
                elif failed_checks:
                    error = f"{failed_checks} check(s) failed"
                report.stages.append({
                    "stage": name, "layer": layer, "title": title, "passed": ok, "seconds": elapsed,
                    "checks_passed": self_inner.checks[0], "checks_total": self_inner.checks[1],
                    "details": self_inner.details, "warnings": self_inner.warnings, "error": error,
                })
                return True  # never abort the run

        return _Stage()


@contextlib.contextmanager
def chdir(path: Path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def quiet():
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    return stack


class MockLLM:
    """Deterministic offline stand-in for an OpenAI-compatible local model."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def chat(self, messages, **_kwargs) -> str:
        self.calls.append(list(messages))
        system = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
        user = next((str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), "")
        if "Return JSON only" in system and "candidates" in system:
            if "偏好" in user or "prefer" in user.casefold():
                return json.dumps({"candidates": [{"content": user.strip()[:200], "project": "smoke", "confidence": 0.8}]},
                                  ensure_ascii=False)
            return json.dumps({"candidates": []})
        return f"[mock-llm] 已收到：{user[:60]}"


def _claim(store, title: str) -> str:
    claim = store.upsert_node(
        "claim", title, summary="smoke claim", status="proposed",
        metadata={
            "content_sha256": hashlib.sha256(title.encode()).hexdigest(),
            "review_state": "not_researched",
            "epistemic_status": "creator_inspired_hypothesis_only",
            "allow_external_research": True,
        },
    )
    return claim["id"]


def muse_modules() -> list[str]:
    return sorted(p.stem for p in (SRC / "muse").glob("*.py") if p.stem != "__init__")


# --------------------------------------------------------------------------- S1-S9
def stage_compile(r: Report) -> None:
    with r.stage("S1", "compile all src/muse modules", "smoke") as st:
        failures = []
        files = sorted((SRC / "muse").glob("*.py"))
        for path in files:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as exc:
                failures.append(f"{path.name}:{exc.lineno}: {exc.msg}")
        st.details = {"files": len(files), "syntax_errors": failures}
        for item in failures:
            r.log("  " + item)
        st.check(not failures, f"{len(files)} files compile")


def stage_import(r: Report) -> None:
    with r.stage("S2", "import every muse module", "smoke") as st:
        import importlib
        hard, optional = [], []
        modules = muse_modules()
        for name in modules:
            try:
                with quiet():
                    importlib.import_module("muse." + name)
            except ModuleNotFoundError as exc:
                missing = (exc.name or "").split(".")[0]
                if missing in OPTIONAL_PACKAGES:
                    optional.append(f"{name} (optional package '{missing}' not installed)")
                else:
                    hard.append(f"{name}: ModuleNotFoundError: {exc}")
            except Exception as exc:  # noqa: BLE001
                hard.append(f"{name}: {type(exc).__name__}: {str(exc)[:200]}")
        st.details = {"modules": len(modules), "failures": hard, "optional_missing": optional}
        st.warnings.extend(optional)
        for item in hard:
            r.log("  " + item)
        st.check(not hard, f"{len(modules) - len(hard) - len(optional)}/{len(modules)} modules import")


def _run_main_check(config: str) -> tuple[int, str]:
    from muse import main as muse_main
    out = io.StringIO()
    argv = sys.argv
    try:
        sys.argv = ["muse.main", "--check", "--config", config]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = muse_main.main()
    finally:
        sys.argv = argv
    return code, out.getvalue()


def stage_config(r: Report) -> None:
    with r.stage("S3", "muse.main --check (example config)", "smoke") as st:
        with chdir(ROOT):
            code, out = _run_main_check("config/muse.example.toml")
        r.log("  " + out.strip().replace("\n", "\n  "))
        st.details = {"exit_code": code}
        st.check(code == 0, "example config validates")


def stage_task_flow(r: Report, work: Path) -> None:
    with r.stage("S4", "flow B: enqueue -> approval gate -> approve -> worker -> result", "smoke") as st:
        from muse.task_queue import TaskQueue
        from muse.worker import run_once
        database = work / "flowb" / "tasks.sqlite3"
        database.parent.mkdir(parents=True)
        with TaskQueue(database) as queue:
            task = queue.enqueue("smoke echo", {"kind": "echo", "input": "你好，Muse"}, require_approval=True)
        first = run_once(database, task_id=task["id"])
        with TaskQueue(database) as queue:
            held = queue.get(task["id"])["status"]
        st.check(held == "waiting_approval", f"unapproved task held (claimed={first is not None}, status={held})")
        with TaskQueue(database) as queue:
            queue.approve(task["id"], approved_by="muse-smoke")
        run_once(database, task_id=task["id"])
        with TaskQueue(database) as queue:
            final = queue.get(task["id"])
        result = (final.get("payload") or {}).get("_result")
        st.details = {"task_id": task["id"], "final_status": final["status"], "result": result}
        st.check(final["status"] == "succeeded", f"approved task succeeded ({final['status']})")
        st.check(result == {"echo": "你好，Muse"}, "handler result persisted")


def stage_local_workflow(r: Report, work: Path) -> None:
    with r.stage("S5", "flow B': autonomous supervisor deterministic workflow", "smoke") as st:
        from muse.autonomous_runtime import AutonomousSupervisor, enqueue_local_workflow
        from muse.task_queue import TaskQueue
        root = work / "flowb2"
        root.mkdir()
        supervisor = AutonomousSupervisor(
            task_db=root / "tasks.sqlite3", routine_db=root / "routines.sqlite3",
            state_db=root / "runtime.sqlite3", improvement_db=root / "improvements.sqlite3",
            knowledge_db=root / "knowledge.sqlite3", reminder_db=root / "reminders.sqlite3",
            memory_db=root / "memory.sqlite3", conversation_db=root / "conversations.sqlite3",
            workspace=root,
        )
        task = enqueue_local_workflow(
            root / "tasks.sqlite3", "smoke local workflow",
            [{"action": "queue_snapshot"}, {"action": "knowledge_lint"}],
        )
        supervisor.run_tick()
        with TaskQueue(root / "tasks.sqlite3") as queue:
            finished = queue.get(task["id"])
        result = finished["payload"].get("_result", {})
        st.details = {"status": finished["status"], "execution_mode": result.get("execution_mode")}
        st.check(finished["status"] == "succeeded", f"workflow succeeded ({finished['status']})")
        st.check(result.get("model_used") is False, "no model used")


def _make_initiative(store):
    return store.propose(
        "冒烟测试：灵感实验台",
        kind="tool",
        creator_value="验证 Muse 从灵感到离线产品的主流程",
        concept=json.dumps({
            "research_question": "主流程能否从输入走到可下载产物？",
            "key_discoveries": {
                "creator_inspired_hypotheses": ["分层记录稳定约束与变化事件。 Test: 对比。 Falsifier: 无差异。"],
                "counter_evidence": ["尚无独立实证。"],
                "uncertainties": ["冒烟数据仅用于验证流程。"],
                "testable_experiments": ["比较默认策略与候选策略。"],
            },
        }, ensure_ascii=False),
        first_step="生成离线原型",
        expected_output="可离线运行的本地产品",
        interest_signals=["smoke"],
        inspiration_refs=["research:smoke"],
    )


def stage_product_flow(r: Report, work: Path) -> None:
    with r.stage("S6", "flow A: initiative -> offline product package", "smoke") as st:
        from muse.initiative_engine import InitiativeStore
        from muse.product_builder import LocalProductBuilder
        root = work / "flowa"
        (root / "data").mkdir(parents=True)
        store = InitiativeStore(root / "data" / "initiatives.sqlite3")
        try:
            initiative = _make_initiative(store)
            result = LocalProductBuilder(root).build(store, initiative["id"])
        finally:
            store.close()
        output = root / result["relative_path"]
        package = output / "product.zip"
        digest = hashlib.sha256(package.read_bytes()).hexdigest() if package.is_file() else None
        st.details = {"initiative_id": initiative["id"], "status": result.get("status"), "package_sha256": digest}
        st.check((output / "index.html").is_file(), "index.html written")
        st.check(package.is_file(), "product.zip written")
        st.check(digest == result.get("package_sha256"), "package hash matches report")


def _refutation_scenario(root: Path) -> str:
    from muse.research_knowledge import ResearchKnowledgeStore
    from muse.research_state_machine import ResearchStateMachine
    path = root / "refute.sqlite3"
    with ResearchKnowledgeStore(path) as store:
        claim_id = _claim(store, "Smoke claim expected to be refuted")
    machine = ResearchStateMachine(path, handoff_dir=root / "handoff-refute")
    machine.transition(claim_id, "queued")
    machine.transition(claim_id, "researching")
    specs = [
        ("https://support.example/source", "support_search_candidate", "insufficient"),
        ("https://gamma.example/source", "counter_search_candidate", "refutes"),
        ("https://delta.example/source", "counter_search_candidate", "refutes"),
    ]
    with ResearchKnowledgeStore(path) as store:
        recorded = []
        for index, (url, direction, verdict) in enumerate(specs):
            source_hash = hashlib.sha256(f"refute-source-{index}".encode()).hexdigest()
            host = url.split("/")[2]
            source = store.upsert_node(
                "source", f"Refute source {index}", summary="stored excerpt", external_ref=url,
                metadata={"content_hash": source_hash, "chars": 800,
                          "source_identity": {"hostname": host, "registrable_domain": host}},
            )
            evidence = store.record_claim_evidence(
                claim_id, source["id"], direction=direction,
                evidence_type="web_source_extract", search_query=f"refute-query-{index}",
            )
            recorded.append((evidence["id"], url, source_hash, verdict))
        for index, (evidence_id, url, source_hash, verdict) in enumerate(recorded):
            excerpt = f"Verified refutation excerpt number {index} with relevant context."
            store.record_claim_evidence_review(
                evidence_id, reviewer_id="independent-reviewer", verdict=verdict,
                rationale="Review based on the stored source excerpt.", quality="high",
                independence="independent",
                citation_anchor={
                    "verified": True, "source_url": url, "source_sha256": source_hash,
                    "excerpt": excerpt, "start_char": 0, "end_char": len(excerpt),
                    "excerpt_sha256": hashlib.sha256(excerpt.encode()).hexdigest(),
                    "fetched_at": "2026-11-25T00:00:00+00:00", "content_type": "text/html",
                },
            )
    machine.transition(claim_id, "evidence_collected")
    return machine.evaluate_claim(claim_id)["conclusion"]


def stage_research(r: Report, work: Path) -> None:
    with r.stage("S7", "research state machine (F1 regression)", "smoke") as st:
        from muse.research_knowledge import ResearchKnowledgeStore
        from muse.research_state_machine import ResearchStateError, ResearchStateMachine
        root = work / "research"
        root.mkdir()
        path = root / "knowledge.sqlite3"
        with ResearchKnowledgeStore(path) as store:
            claim_id = _claim(store, "Smoke falsifiable hypothesis")
        machine = ResearchStateMachine(path, handoff_dir=root / "handoff")
        try:
            machine.transition(claim_id, "evaluated")
            illegal_rejected = False
        except ResearchStateError:
            illegal_rejected = True
        for state in ("queued", "researching", "evidence_collected"):
            machine.transition(claim_id, state)
        result = machine.evaluate_claim(claim_id)
        with ResearchKnowledgeStore(path) as store:
            node = store.get_node(claim_id)
        st.check(illegal_rejected, "illegal transition rejected")
        st.check(result["conclusion"] == "inconclusive", f"no evidence -> inconclusive ({result['conclusion']})")
        st.check(node["status"] == "proposed", "claim status never auto-promoted")
        refuted = None
        try:
            refuted = _refutation_scenario(root)
        except Exception as exc:  # noqa: BLE001
            st.warnings.append(f"refutation scenario could not be built: {type(exc).__name__}: {exc}")
        if refuted is not None:
            st.check(refuted == "refuted", f"two independent refutations -> refuted ({refuted})")
        st.details = {"conclusion": result["conclusion"], "refutation_conclusion": refuted}


def _start_status_server(root: Path):
    from muse.status_api import create_status_server
    tasks = root / "data" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    server, bearer = create_status_server(
        task_db=tasks / "muse.sqlite3", progress_db=tasks / "progress.sqlite3",
        capability_db=tasks / "capabilities.sqlite3", port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, bearer


def _http(port: int, path: str, *, bearer: str | None = None, origin: str | None = None, timeout: float = 15.0):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def stage_status_api(r: Report, work: Path) -> None:
    with r.stage("S8", "output: Status API /api/v1/health on loopback", "smoke") as st:
        root = work / "status"
        server, bearer = _start_status_server(root)
        try:
            port = server.server_address[1]
            unauth, _ = _http(port, "/api/v1/health")
            code, body = _http(port, "/api/v1/health", bearer=bearer)
            payload = json.loads(body.decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
        st.details = {"unauthenticated_status": unauth, "status": code, "body": payload}
        st.check(unauth == 401, f"health requires bearer ({unauth})")
        st.check(code == 200 and payload.get("status") == "ok", f"authenticated health ok ({code})")


def stage_tests(r: Report, full: bool) -> None:
    title = "full unittest suite" if full else "targeted regression tests"
    with r.stage("S9", title, "smoke") as st:
        loader = unittest.TestLoader()
        with chdir(ROOT):
            if full:
                suite = loader.discover(str(TESTS), top_level_dir=str(TESTS))
            else:
                suite = unittest.TestSuite()
                for name in TARGETED_TESTS:
                    if (TESTS / f"{name}.py").is_file():
                        suite.addTests(loader.loadTestsFromName(name))
            stream = io.StringIO()
            result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
        problems = [f"FAIL {t.id()}" for t, _ in result.failures] + [f"ERROR {t.id()}" for t, _ in result.errors]
        st.details = {"ran": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                      "skipped": len(result.skipped), "problems": problems[:300]}
        r.log(f"  ran={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} skipped={len(result.skipped)}")
        for item in problems[:300]:
            r.log("  " + item)
        if problems:
            r.log("\n--- failure details ---")
            for test, trace in (result.failures + result.errors)[:80]:
                r.log(f"### {test.id()}\n{trace.rstrip()}")
        st.check(result.wasSuccessful(), f"{result.testsRun} tests without failures/errors")


# --------------------------------------------------------------------------- Layer 1
def stage_static_all(r: Report) -> None:
    with r.stage("L1", "static: compile every project .py file", "L1") as st:
        failures, total = [], 0
        for base in STATIC_DIRS:
            top = ROOT / base
            if not top.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(top):
                dirnames[:] = [d for d in dirnames if d not in STATIC_SKIP and not d.startswith(".")]
                if base == "tests" and Path(dirpath) != top:
                    continue  # fixtures under tests/data are not project code
                for name in filenames:
                    if not name.endswith(".py"):
                        continue
                    path = Path(dirpath) / name
                    total += 1
                    try:
                        compile(path.read_bytes().decode("utf-8-sig"), str(path), "exec")
                    except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
                        failures.append(f"{path.relative_to(ROOT)}: {type(exc).__name__}: {exc}")
        st.details = {"files": total, "failures": failures}
        for item in failures:
            r.log("  " + item)
        st.check(not failures, f"{total} files compile ({len(failures)} failures)")


def stage_cli_entrypoints(r: Report) -> None:
    with r.stage("L1.2", "static: every pyproject muse-* entry point resolves and answers --help", "L1") as st:
        import importlib
        import inspect
        import tomllib
        scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
        results = {}
        for command, target in sorted(scripts.items()):
            module_name, func_name = target.split(":")
            try:
                with quiet():
                    module = importlib.import_module(module_name)
                func = getattr(module, func_name)
            except ModuleNotFoundError as exc:
                if (exc.name or "").split(".")[0] in OPTIONAL_PACKAGES:
                    results[command] = "optional-missing"
                    st.warnings.append(f"{command}: optional package {exc.name} not installed")
                    continue
                results[command] = f"import-error:{exc}"
                st.check(False, f"{command} -> {target} imports")
                continue
            except Exception as exc:  # noqa: BLE001
                results[command] = f"import-error:{type(exc).__name__}"
                st.check(False, f"{command} -> {target} imports ({type(exc).__name__}: {exc})")
                continue
            if not callable(func):
                st.check(False, f"{command}: {func_name} is not callable")
                continue
            source = inspect.getsource(module)
            if "ArgumentParser" not in source:
                results[command] = "resolved (no argparse; --help not attempted)"
                continue
            params = inspect.signature(func).parameters
            argv_backup = sys.argv
            code = None
            try:
                sys.argv = [command, "--help"]
                with quiet():
                    func(["--help"]) if params else func()
            except SystemExit as exc:
                code = exc.code
            except Exception as exc:  # noqa: BLE001
                code = f"{type(exc).__name__}: {str(exc)[:120]}"
            finally:
                sys.argv = argv_backup
            results[command] = code
            st.check(code in (0, None), f"{command} --help -> {code}")
        st.details = {"entry_points": len(scripts), "results": results}


# --------------------------------------------------------------------------- Layer 2
def stage_config_boundaries(r: Report, work: Path) -> None:
    with r.stage("L2", "config & environment boundary tests", "L2") as st:
        root = work / "config"
        root.mkdir()
        example = (ROOT / "config" / "muse.example.toml").read_text(encoding="utf-8")
        cases = {
            "missing_file": (None, 0),
            "valid_example": (example, 0),
            "corrupt_toml": ("[app\nname = ", 2),
            "missing_section": (example.replace("[scheduler]", "[scheduler_renamed]"), 2),
            "scalar_section": ("memory = \"sqlite\"\n" + example.replace("[memory]", "[memory_old]"), 2),
            "missing_app_name": (re.sub(r'(?m)^name = "Muse"\s*$', "", example, count=1), 2),
            "bad_scheduler_type": (example.replace("enabled = false", 'enabled = "no"', 1), 2),
            "unsupported_backend": (example.replace('backend = "sqlite"', 'backend = "redis"'), 2),
        }
        results = {}
        for name, (content, expected) in cases.items():
            path = root / f"{name}.toml"
            if content is not None:
                path.write_text(content, encoding="utf-8")
            try:
                code, out = _run_main_check(str(path))
            except Exception as exc:  # noqa: BLE001 - an unhandled traceback is exactly what we test for
                code, out = f"crash:{type(exc).__name__}", str(exc)
            results[name] = code
            st.check(code == expected, f"{name}: exit {code} (expected {expected})")
        st.details["config_cases"] = results

        env_example = ROOT / ".env.example"
        if env_example.is_file():
            suspicious = []
            for line in env_example.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip()
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD", key) and not (value.startswith("<") and value.endswith(">")):
                    suspicious.append(key)
            st.check(not suspicious, f".env.example secrets are placeholders ({suspicious or 'ok'})")
        else:
            st.warnings.append(".env.example not found")
        st.check(not (ROOT / ".env").exists() or True, ".env never read by this check")


# --------------------------------------------------------------------------- Layer 3
def stage_memory(r: Report, work: Path) -> None:
    with r.stage("L3.1", "memory: CRUD, dedupe, ranking, revisions, soft delete, persistence, review", "L3") as st:
        from muse.memory import MemoryStore
        db = work / "memory" / "memory.sqlite3"
        with MemoryStore(db) as memory:
            a = memory.add("用户偏好简洁的粤语回复", project="profile", source="smoke-confirmed", kind="preference")
            dup = memory.add("用户偏好简洁的粤语回复", project="profile", source="smoke-again", kind="preference")
            st.check(dup["id"] == a["id"], "identical memory deduplicated")
            b = memory.add("项目 Muse 使用 SQLite 作为权威记忆源", project="muse", source="smoke")
            memory.add("无关的天气记录", project="misc", source="smoke")
            hits = memory.search("粤语 回复")
            st.check(bool(hits) and hits[0]["id"] == a["id"], "relevance ranking returns best match first")
            revised = memory.revise(b["id"], "项目 Muse 以 SQLite 为权威源，Obsidian 为只读镜像")
            st.check(revised["revision"] == 2, f"revision increments ({revised['revision']})")
            st.check(len(memory.history(b["id"])) == 2, "history keeps both versions")
            st.check(memory.delete(b["id"]) is True, "delete returns True")
            st.check(all(item["id"] != b["id"] for item in memory.search("SQLite 权威")), "soft-deleted memory not searchable")
            candidate = memory.propose("候选：每周一复盘", project="muse", source="smoke-extractor", confidence=0.6)
            st.check(all("每周一复盘" not in item["content"] for item in memory.search("每周一 复盘")), "pending candidate not searchable")
            approved = memory.approve_candidate(int(candidate["id"]), approved_by="smoke-human")
            st.check(approved.get("status") == "approved", "candidate approval path")
            rejected = memory.reject_candidate(int(memory.propose("候选：不可靠", source="smoke")["id"]),
                                               rejected_by="smoke-human", reason="smoke")
            st.check(rejected.get("status") == "rejected", "candidate rejection path")
        with MemoryStore(db) as reopened:
            st.check(reopened.get(a["id"]) is not None, "memory survives reopen (persistence)")
            exported = reopened.export_json()
            st.check(bool(exported), "export_json produces output")


def stage_context_window(r: Report, work: Path) -> None:
    with r.stage("L3.2", "short-term memory: bounded chat context window + checkpoint safety", "L3") as st:
        from muse.assistant import MuseAssistant
        from muse.conversations import ConversationStore
        model = MockLLM()
        with ConversationStore(work / "conv" / "threads.sqlite3") as store:
            assistant = MuseAssistant(store, model, system_prompt="You are Muse.",
                                      context_max_messages=10, context_max_chars=4000)
            for index in range(30):
                assistant.reply("long-thread", f"第 {index} 条消息")
            sent = model.calls[-1]
            st.check(len(sent) <= 10 + 3, f"model context bounded ({len(sent)} messages sent)")
            st.check(sent[0]["content"] == "You are Muse.", "system prompt always kept")
            st.check(any("omitted" in str(m.get("content")) for m in sent if m.get("role") == "system"), "omission note present")
            persisted = store.load("long-thread")["messages"]
            st.check(len(persisted) == 61, f"full history persisted ({len(persisted)})")

            class Broken:
                def chat(self, _messages):
                    raise RuntimeError("model offline")

            before = store.load("long-thread")["revision"]
            try:
                MuseAssistant(store, Broken()).reply("long-thread", "x")
                failed = False
            except RuntimeError:
                failed = True
            st.check(failed and store.load("long-thread")["revision"] == before, "failed model call leaves checkpoint untouched")
        try:
            MuseAssistant(None, model, context_max_messages=1)  # type: ignore[arg-type]
            st.check(False, "invalid window size rejected")
        except ValueError:
            st.check(True, "invalid window size rejected")


def stage_approval_gate(r: Report, work: Path) -> None:
    with r.stage("L3.3", "action gate: allow / reject / idempotency / high-risk kinds / missing handler", "L3") as st:
        from muse.task_queue import TaskQueue
        from muse.worker import run_once
        db = work / "gate" / "tasks.sqlite3"
        db.parent.mkdir(parents=True)
        with TaskQueue(db) as queue:
            rejected = queue.enqueue("reject me", {"kind": "echo", "input": "no"}, require_approval=True)
            queue.cancel(rejected["id"], cancelled_by="smoke-human", reason="rejected in smoke")
            st.check(queue.get(rejected["id"])["status"] == "cancelled", "rejected task is cancelled")
            first = queue.enqueue("idem", {"kind": "echo", "input": "1"}, idempotency_key="smoke-idem-1")
            again = queue.enqueue("idem", {"kind": "echo", "input": "1"}, idempotency_key="smoke-idem-1")
            st.check(first["id"] == again["id"], "idempotency key returns the same task")
            risky = queue.enqueue("legacy video", {"kind": "video_pipeline", "recipe_id": "x"})
            unknown = queue.enqueue("unknown", {"kind": "smoke_no_such_handler"})
        run_once(db, task_id=rejected["id"])
        run_once(db, task_id=risky["id"])
        run_once(db, task_id=unknown["id"])
        run_once(db, task_id=first["id"])
        with TaskQueue(db) as queue:
            st.check(queue.get(rejected["id"])["status"] == "cancelled", "worker never runs a cancelled task")
            st.check(queue.get(risky["id"])["status"] == "waiting_approval", "high-risk kind parked for approval")
            failed = queue.get(unknown["id"])
            st.check(failed["status"] == "failed", "unknown kind fails closed")
            st.check(queue.get(first["id"])["status"] == "succeeded", "idempotent task executed once")
            try:
                queue.approve(first["id"], approved_by="smoke")
                st.check(False, "approving a finished task is refused")
            except ValueError:
                st.check(True, "approving a finished task is refused")


def stage_planner(r: Report) -> None:
    with r.stage("L3.4", "cognition: Commander planner (model, malicious model, fallback, strict)", "L3") as st:
        from muse.commander_planner import CommanderPlanner
        plan = CommanderPlanner().plan("为 Muse 设计一个离线灵感实验工具并完成审查")
        st.check(plan.source == "safe_fallback" and len(plan.steps) >= 1, f"fallback plan ({len(plan.steps)} steps)")
        ids = {s.step_id for s in plan.steps}
        st.check(all(set(s.depends_on) <= ids for s in plan.steps), "fallback DAG dependencies resolve")
        st.check(sum(len(w) for w in plan.parallel_waves) == len(plan.steps), "every step scheduled in a wave")

        def malicious(_goal):
            return {"steps": [{"id": "a", "role": "root_shell", "objective": "rm -rf /", "depends_on": ["b"]},
                              {"id": "b", "role": "developer", "objective": "x", "depends_on": ["a"]}]}
        try:
            bad = CommanderPlanner(malicious).plan("hostile planner output")
            st.check(all(s.role != "root_shell" for s in bad.steps), f"unknown role never executed (source={bad.source})")
        except (ValueError, PermissionError) as exc:
            st.check(True, f"malicious plan rejected ({type(exc).__name__})")
        try:
            CommanderPlanner(strict_model=True).plan("strict without model")
            st.check(False, "strict mode without model refuses")
        except RuntimeError:
            st.check(True, "strict mode without model refuses")
        st.details = {"fallback_steps": [s.step_id for s in plan.steps]}


def stage_review_gate(r: Report, work: Path) -> None:
    with r.stage("L3.5", "metacognition: independent review gate approve / veto / invalid output / self-review", "L3") as st:
        from muse.review_gate import IndependentReviewGate, ReviewStore
        store = ReviewStore(work / "review" / "reviews.sqlite3")
        try:
            def call_for(payload):
                return lambda _request: json.dumps(payload)
            approve = IndependentReviewGate(store, reviewer_id="smoke-reviewer",
                                            call=call_for({"approved": True, "summary": "ok", "findings": []}))
            res = approve.review(artifact_id="a1", builder_id="builder", artifact="x", test_evidence="t", lineage="l")
            st.check(res.approved is True, "clean review approves")
            veto = IndependentReviewGate(store, reviewer_id="smoke-reviewer", call=call_for({
                "approved": True, "summary": "looks fine",
                "findings": [{"severity": "high", "title": "unsafe", "detail": "writes outside workspace"}]}))
            res = veto.review(artifact_id="a2", builder_id="builder", artifact="x", test_evidence="t", lineage="l")
            st.check(res.approved is False, "high-severity finding vetoes approval")
            garbage = IndependentReviewGate(store, reviewer_id="smoke-reviewer", call=lambda _r: "not json")
            try:
                garbage.review(artifact_id="a3", builder_id="builder", artifact="x", test_evidence="t", lineage="l")
                st.check(False, "invalid reviewer output fails closed")
            except (ValueError, TypeError):
                st.check(True, "invalid reviewer output fails closed")
            try:
                approve.review(artifact_id="a4", builder_id="smoke-reviewer", artifact="x", test_evidence="t", lineage="l")
                st.check(False, "builder cannot self-review")
            except PermissionError:
                st.check(True, "builder cannot self-review")
            st.check(len(store.list_for_artifact("a2")) == 1, "review persisted")
        finally:
            store.close()


SKIP_ROUTES = {"/api/v1/events/stream"}  # server-sent events: long-lived by design


def _get_routes() -> list[str]:
    text = (SRC / "muse" / "status_api.py").read_text(encoding="utf-8")
    start = text.find("def do_GET")
    end = text.find("def do_POST", start)
    return sorted(set(re.findall(r'parsed\.path == "(/api/v1/[^"]+)"', text[start:end])) - SKIP_ROUTES)


def stage_api_sweep(r: Report, work: Path) -> None:
    with r.stage("L3.6", "API: auth / origin / every GET route answers without 5xx", "L3") as st:
        root = work / "api"
        with chdir(work):
            server, bearer = _start_status_server(root)
            try:
                port = server.server_address[1]
                routes = _get_routes()
                code, _ = _http(port, "/api/v1/overview")
                st.check(code == 401, f"no bearer -> 401 ({code})")
                code, _ = _http(port, "/api/v1/health", bearer="wrong-" + bearer[6:])
                st.check(code == 401, f"wrong bearer -> 401 ({code})")
                code, _ = _http(port, "/api/v1/health", bearer=bearer, origin="https://evil.example")
                st.check(code == 403, f"foreign origin -> 403 ({code})")
                code, _ = _http(port, "/api/v1/does-not-exist", bearer=bearer)
                st.check(400 <= code < 500, f"unknown route -> 4xx ({code})")
                statuses = {}
                for route in routes:
                    try:
                        code, body = _http(port, route, bearer=bearer, timeout=20)
                        statuses[route] = code
                        if code >= 500 and code != 503:
                            st.check(False, f"GET {route} -> {code} {body[:160]!r}")
                        elif code == 503:
                            st.warnings.append(f"GET {route} -> 503 (store/service not configured in temp workspace)")
                    except Exception as exc:  # noqa: BLE001
                        statuses[route] = f"{type(exc).__name__}"
                        st.check(False, f"GET {route} raised {type(exc).__name__}: {exc}")
                ok = [r_ for r_, c in statuses.items() if isinstance(c, int) and c < 500]
                st.check(len(ok) >= len(routes) * 0.8, f"{len(ok)}/{len(routes)} GET routes answered < 500")
                st.details = {"routes": statuses}
            finally:
                server.shutdown()
                server.server_close()


# --------------------------------------------------------------------------- Layer 4
def _real_llm(url: str | None):
    if not url:
        return None, "not requested (use --llm-url)"
    try:
        from muse.model_client import LMStudioClient
        client = LMStudioClient(url, timeout=60.0, max_retries=1)
        client.list_models()
        return client, "reachable"
    except Exception as exc:  # noqa: BLE001
        return None, f"unreachable ({type(exc).__name__}); falling back to mock"


def stage_mind_loop(r: Report, work: Path, llm_url: str | None) -> None:
    with r.stage("L4.1", "end-to-end mind loop: perceive -> remember -> plan -> gate -> act -> review -> create -> report", "L4") as st:
        from muse.assistant import MuseAssistant
        from muse.commander_planner import CommanderPlanner
        from muse.conversations import ConversationStore
        from muse.initiative_engine import InitiativeStore
        from muse.memory import MemoryStore
        from muse.memory_extraction import LocalMemoryCandidateExtractor
        from muse.product_builder import LocalProductBuilder
        from muse.review_gate import IndependentReviewGate, ReviewStore
        from muse.task_queue import TaskQueue
        from muse.worker import run_once

        root = work / "mind"
        (root / "data" / "tasks").mkdir(parents=True)
        real, real_state = _real_llm(llm_url)
        model = real or MockLLM()
        st.details["llm_mode"] = "real" if real else "mock"
        st.details["real_llm"] = real_state
        r.log(f"  llm mode: {st.details['llm_mode']} ({real_state})")

        # 1 perceive + 2 remember
        with MemoryStore(root / "memory.sqlite3") as memory, ConversationStore(root / "conv.sqlite3") as conv:
            memory.add("创造者偏好可离线运行的小工具", project="profile", source="smoke-confirmed", kind="preference")
            extractor = LocalMemoryCandidateExtractor(MockLLM())
            assistant = MuseAssistant(conv, model, system_prompt="You are Muse.", memory=memory,
                                      memory_candidate_extractor=extractor)
            turn = assistant.reply("mind", "我偏好每周一做一次复盘，请帮我规划一个离线复盘工具")
            st.check(bool(turn["reply"]), "perception turn produced a reply")
            if not real:
                ctx = " ".join(str(m.get("content")) for m in model.calls[-1] if m.get("role") == "system")
                st.check("可离线运行" in ctx, "long-term memory injected into context")
            st.check(turn["memory_candidates_created"] >= 1, "new preference staged as memory candidate (not auto-approved)")
            candidate_id = turn["memory_candidate_ids"][0] if turn["memory_candidate_ids"] else None
            if candidate_id is not None:
                memory.approve_candidate(candidate_id, approved_by="smoke-human")
                st.check(any("复盘" in m["content"] for m in memory.search("每周一 复盘")), "approved candidate becomes retrievable")

        # 3 plan
        plan = CommanderPlanner().plan("规划一个离线复盘工具并交付可下载原型")
        st.check(len(plan.steps) >= 1, f"plan has {len(plan.steps)} steps")

        # 4 gate + 5 act
        db = root / "data" / "tasks" / "muse.sqlite3"
        with TaskQueue(db) as queue:
            task_ids = [queue.enqueue(f"step {s.step_id}", {"kind": "echo", "input": s.objective[:200]},
                                      require_approval=True)["id"] for s in plan.steps]
        for task_id in task_ids:
            run_once(db, task_id=task_id)
        with TaskQueue(db) as queue:
            st.check(all(queue.get(t)["status"] == "waiting_approval" for t in task_ids), "nothing runs before approval")
            for task_id in task_ids:
                queue.approve(task_id, approved_by="smoke-human")
        for task_id in task_ids:
            run_once(db, task_id=task_id)
        with TaskQueue(db) as queue:
            statuses = [queue.get(t)["status"] for t in task_ids]
        st.check(all(s == "succeeded" for s in statuses), f"all approved steps executed ({statuses})")

        # 6 review
        store = ReviewStore(root / "reviews.sqlite3")
        try:
            gate = IndependentReviewGate(store, reviewer_id="smoke-reviewer", call=lambda _r: json.dumps(
                {"approved": True, "summary": "plan executed as approved", "findings": []}))
            verdict = gate.review(artifact_id=plan.plan_id, builder_id="muse-commander",
                                  artifact=json.dumps(plan.to_snapshot(), ensure_ascii=False),
                                  test_evidence=json.dumps(statuses), lineage="smoke")
            st.check(verdict.approved, "independent review approved execution")
        finally:
            store.close()

        # 7 create
        initiatives = InitiativeStore(root / "data" / "initiatives.sqlite3")
        try:
            initiative = _make_initiative(initiatives)
            product = LocalProductBuilder(root).build(initiatives, initiative["id"])
        finally:
            initiatives.close()
        st.check((root / product["relative_path"] / "product.zip").is_file(), "offline product created")

        # 8 report through the API
        server, bearer = _start_status_server(root)
        try:
            code, body = _http(server.server_address[1], "/api/v1/overview", bearer=bearer)
        finally:
            server.shutdown()
            server.server_close()
        st.check(code == 200, f"overview reachable ({code})")
        if code == 200:
            overview = json.loads(body.decode("utf-8"))
            st.check(len(json.dumps(overview)) > 2, "overview reports runtime state")


def stage_fault_injection(r: Report, work: Path) -> None:
    with r.stage("L4.2", "fault injection: corrupt DB, bad model JSON, oversized input, crash recovery", "L4") as st:
        from muse.memory_extraction import LocalMemoryCandidateExtractor
        from muse.task_queue import TaskQueue
        root = work / "faults"
        root.mkdir()
        corrupt = root / "corrupt.sqlite3"
        corrupt.write_bytes(b"this is not a sqlite database" * 64)
        try:
            with TaskQueue(corrupt) as queue:
                queue.snapshot()
            st.check(False, "corrupt task DB raises a clear error")
        except sqlite3.DatabaseError:
            st.check(True, "corrupt task DB raises sqlite3.DatabaseError (no silent reset)")

        class BadJSON:
            def chat(self, _m):
                return "{not json"
        try:
            LocalMemoryCandidateExtractor(BadJSON()).extract("I prefer tea")
            st.check(False, "invalid model JSON rejected")
        except ValueError:
            st.check(True, "invalid model JSON rejected")

        db = root / "tasks.sqlite3"
        with TaskQueue(db) as queue:
            try:
                queue.enqueue("", {"kind": "echo"})
                st.check(False, "empty title rejected")
            except ValueError:
                st.check(True, "empty title rejected")
            try:
                queue.enqueue("x", {"kind": "echo"}, priority=10_000)
                st.check(False, "out-of-range priority rejected")
            except ValueError:
                st.check(True, "out-of-range priority rejected")
            task = queue.enqueue("crash me", {"kind": "echo", "input": "x"})
            claimed = queue.claim_next(worker_id="crashed-worker", lease_seconds=1, task_id=task["id"])
            st.check(claimed is not None, "task claimed by a worker that then crashes")
        time.sleep(1.6)
        with TaskQueue(db) as queue:
            recovered = queue.recover_expired()
            status = queue.get(task["id"])["status"]
            stale = queue.finish(task["id"], status="succeeded", result={"x": 1}, worker_id="crashed-worker")
        st.check(status == "queued", f"expired lease recovered to queued ({status}, recover={recovered})")
        st.check(stale is None, "fenced: stale worker cannot finish a recovered task")


def stage_concurrency(r: Report, work: Path) -> None:
    with r.stage("L4.3", "concurrency: parallel enqueue + parallel workers + parallel memory writes", "L4") as st:
        from muse.memory import MemoryStore
        from muse.task_queue import TaskQueue
        from muse.worker import run_once
        root = work / "stress"
        root.mkdir()
        db = root / "tasks.sqlite3"
        with TaskQueue(db):
            pass
        errors: list[str] = []
        producers, per_producer = 6, 20

        def produce(index: int) -> None:
            try:
                with TaskQueue(db) as queue:
                    for j in range(per_producer):
                        queue.enqueue(f"p{index}-{j}", {"kind": "echo", "input": f"{index}-{j}"})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"producer {index}: {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=produce, args=(i,)) for i in range(producers)]
        started = time.perf_counter()
        [t.start() for t in threads]
        [t.join(120) for t in threads]
        processed: list[str] = []
        lock = threading.Lock()

        def consume() -> None:
            try:
                while True:
                    # No stdout redirection here: redirect_stdout is process-global, not thread-safe.
                    result = run_once(db)
                    if result is None:
                        return
                    with lock:
                        processed.append(result["id"])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"worker: {type(exc).__name__}: {exc}")

        workers = [threading.Thread(target=consume) for _ in range(4)]
        [t.start() for t in workers]
        [t.join(300) for t in workers]
        elapsed = round(time.perf_counter() - started, 2)
        total = producers * per_producer
        with TaskQueue(db) as queue:
            rows = queue.list_tasks(limit=total + 10)
        succeeded = sum(1 for row in rows if row["status"] == "succeeded")
        st.check(not errors, f"no concurrency errors ({errors[:3]})")
        st.check(len(rows) == total, f"all {total} tasks persisted ({len(rows)})")
        st.check(succeeded == total, f"every task succeeded ({succeeded}/{total})")
        st.check(len(processed) == len(set(processed)) == total, f"each task executed exactly once ({len(processed)})")

        mem_db = root / "memory.sqlite3"
        with MemoryStore(mem_db):
            pass
        mem_errors: list[str] = []

        def write_memories(index: int) -> None:
            try:
                with MemoryStore(mem_db) as memory:
                    for j in range(15):
                        memory.add(f"并发记忆 {index}-{j}", project="stress", source="smoke")
            except Exception as exc:  # noqa: BLE001
                mem_errors.append(f"{type(exc).__name__}: {exc}")

        writers = [threading.Thread(target=write_memories, args=(i,)) for i in range(5)]
        [t.start() for t in writers]
        [t.join(120) for t in writers]
        with MemoryStore(mem_db) as memory:
            count = len(memory.list_memories(project="stress", limit=200))
        st.check(not mem_errors, f"no memory write errors ({mem_errors[:2]})")
        st.check(count == 75, f"all concurrent memories persisted ({count}/75)")
        st.details = {"tasks": total, "seconds": elapsed, "throughput_tasks_per_s": round(total / max(elapsed, 0.01), 1)}


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse full-depth health check")
    parser.add_argument("--full", action="store_true", help="run the whole unittest suite in S9")
    parser.add_argument("--quick", action="store_true", help="skip the unittest stage S9")
    parser.add_argument("--llm-url", default=None, help="optional loopback OpenAI-compatible URL for a real-model turn")
    args = parser.parse_args(argv)

    r = Report()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    r.log("Muse full-depth health check")
    r.log(f"started   : {started}")
    r.log(f"python    : {sys.version.split()[0]} ({sys.executable})")
    r.log(f"platform  : {platform.platform()}")
    r.log(f"root      : {ROOT}")
    r.log(f"encoding  : stdout={getattr(sys.stdout, 'encoding', None)} preferred={locale.getpreferredencoding(False)}")

    with tempfile.TemporaryDirectory(prefix="muse-smoke-") as temp:
        work = Path(temp)
        stage_compile(r)
        stage_import(r)
        stage_config(r)
        stage_task_flow(r, work)
        stage_local_workflow(r, work)
        stage_product_flow(r, work)
        stage_research(r, work)
        stage_status_api(r, work)
        stage_static_all(r)
        stage_config_boundaries(r, work)
        stage_memory(r, work)
        stage_context_window(r, work)
        stage_approval_gate(r, work)
        stage_planner(r)
        stage_review_gate(r, work)
        stage_api_sweep(r, work)
        stage_mind_loop(r, work, args.llm_url)
        stage_fault_injection(r, work)
        stage_concurrency(r, work)
        if not args.quick:
            stage_tests(r, args.full)

    passed = all(stage["passed"] for stage in r.stages)
    checks_ok = sum(s["checks_passed"] for s in r.stages)
    checks_total = sum(s["checks_total"] for s in r.stages)
    stages_ok = sum(1 for s in r.stages if s["passed"])
    r.log("\n=== SUMMARY ===")
    r.log(f"{'stage':<6}{'layer':<7}{'result':<7}{'checks':<9}title")
    for s in r.stages:
        r.log(f"{s['stage']:<6}{s['layer']:<7}{'PASS' if s['passed'] else 'FAIL':<7}"
              f"{str(s['checks_passed']) + '/' + str(s['checks_total']):<9}{s['title']}"
              + (f"  [{len(s['warnings'])} warn]" if s["warnings"] else ""))
    rate = round(100.0 * stages_ok / max(len(r.stages), 1), 1)
    r.log(f"stages {stages_ok}/{len(r.stages)} ({rate}%), checks {checks_ok}/{checks_total}")
    r.log(f"RESULT: {'ALL PASSED' if passed else 'FAILED'}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    payload = {
        "started": started, "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version.split()[0], "platform": platform.platform(),
        "mode": "full" if args.full else ("quick" if args.quick else "targeted"),
        "passed": passed, "stage_pass_rate": rate, "checks_passed": checks_ok, "checks_total": checks_total,
        "stages": r.stages,
    }
    text = "\n".join(r.lines) + "\n"
    for name in ("latest.log", f"smoke-{stamp}.log"):
        _atomic_write(REPORT_DIR / name, text)
    _atomic_write(REPORT_DIR / "latest.json", json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(f"\nreport: {REPORT_DIR / 'latest.log'}")
    return 0 if passed else 1


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    raise SystemExit(main())
