from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from muse.consciousness_loop import (
    MIND_STEP_KIND, MindLoop, assess_risk, heuristic_reflection, rank_memories, read_mind_status, sanitize_input,
)
from muse.inner_state import InnerStateStore, read_snapshot
from muse.memory import MemoryStore
from muse.structured_output import (
    CircuitBreaker, CircuitOpenError, StructuredOutputError, call_with_retry, extract_json, parse_structured,
    request_structured, validate,
)
from muse.task_queue import TaskQueue


class DeterministicMockLLM:
    """Offline model: JSON for reflection/plan/work notes, prose-wrapped on the first reflection."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        system = messages[0]["content"]
        last = messages[-1]["content"]
        if "reflective layer" in system:
            if "FORMAT_REPAIR_REQUIRED" in last:
                return '```json\n{"summary":"研究复盘","intent":"research","needs_plan":true,' \
                       '"goal":"研究复盘工具","risk_notes":[],"memory_candidates":["我偏好简洁报告"],}\n```'
            return '好的：{"summary": 1}'
        if "Plan the goal" in system:
            return json.dumps({"steps": [
                {"id": "r", "role": "researcher", "objective": "整理复盘方法", "depends_on": []},
                {"id": "w", "role": "writer", "objective": "撰写模板", "depends_on": ["r"]},
                {"id": "v", "role": "reviewer", "objective": "独立核查", "depends_on": ["w"]},
            ]}, ensure_ascii=False)
        return '{"notes":"要点已整理","next_actions":["补示例"],"open_questions":[]}'


class StructuredOutputTests(unittest.TestCase):
    def test_extracts_json_from_prose_fences_and_repairs_trailing_commas(self) -> None:
        self.assertEqual(extract_json('结果如下 {"a": 1} 谢谢'), {"a": 1})
        self.assertEqual(extract_json('```json\n{"a": [1, 2,],}\n```'), {"a": [1, 2]})
        self.assertEqual(extract_json("{'x': True}".replace("'", '"')), {"x": True})
        self.assertEqual(extract_json('noise [1,2] then {"k":"v"}', expect=dict), {"k": "v"})
        with self.assertRaises(StructuredOutputError):
            extract_json("no json here")
        with self.assertRaises(StructuredOutputError):
            extract_json('{"a":1,"a":2}')  # duplicate keys are rejected, never silently merged

    def test_schema_validation_and_repair_loop(self) -> None:
        schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer", "minimum": 1}}}
        self.assertEqual(validate({"n": True}, schema), ["$.n: expected integer, found bool"])
        self.assertEqual(parse_structured('{"n": 3}', schema), {"n": 3})
        replies = iter(['{"n": 0}', "oops", '{"n": 2}'])
        self.assertEqual(request_structured(lambda _m: next(replies), [{"role": "user", "content": "x"}], schema), {"n": 2})
        with self.assertRaises(StructuredOutputError) as ctx:
            request_structured(lambda _m: "bad", [{"role": "user", "content": "x"}], schema, max_repairs=1)
        self.assertEqual(len(ctx.exception.attempts), 2)

    def test_circuit_breaker_and_retry(self) -> None:
        now = [0.0]
        breaker = CircuitBreaker(failure_threshold=2, reset_seconds=10, clock=lambda: now[0])

        def fail():
            raise ConnectionError("down")
        for _ in range(2):
            with self.assertRaises(ConnectionError):
                breaker.call(fail)
        with self.assertRaises(CircuitOpenError):
            breaker.call(lambda: 1)
        now[0] = 11.0
        self.assertEqual(breaker.call(lambda: 7), 7)
        self.assertEqual(breaker.state, "closed")
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise TimeoutError()
            return "ok"
        self.assertEqual(call_with_retry(flaky, retries=3, sleep=lambda _s: None), "ok")
        with self.assertRaises(ValueError):
            call_with_retry(lambda: (_ for _ in ()).throw(ValueError("not transient")), retries=3, sleep=lambda _s: None)


class InnerStateTests(unittest.TestCase):
    def test_dynamics_budget_goals_and_strategy(self) -> None:
        now = [1000.0]
        with tempfile.TemporaryDirectory(prefix="Muse 状态 ") as directory:
            store = InnerStateStore(Path(directory) / "state.json", clock=lambda: now[0], max_calls_per_hour=2)
            store.apply_event("task_failed")
            store.apply_event("task_failed")
            self.assertLess(store.state["affect"]["valence"], 0.1)
            now[0] += 6 * 3600
            store.tick()
            self.assertGreater(store.state["affect"]["valence"], -0.2)  # decays back toward baseline
            self.assertEqual([store.try_consume(tokens=1) for _ in range(3)], [True, True, False])
            now[0] += 3601
            self.assertTrue(store.try_consume(tokens=1))  # rolling window resets
            first = store.add_goal("目标 A", priority=0.4)
            store.add_goal("目标 B", priority=0.9)
            self.assertEqual(store.top_goal()["text"], "目标 B")
            self.assertEqual(store.top_goal(exclude={store.top_goal()["id"]})["id"], first["id"])
            self.assertEqual(store.record_cycle_outcome(succeeded=0, failed=1)["max_actions"], 2)
            with self.assertRaises(ValueError):
                store.apply_event("not-an-event")

    def test_atomic_persistence_backup_recovery_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Muse 状态 ") as directory:
            path = Path(directory) / "子目录 含空格" / "state.json"
            store = InnerStateStore(path)
            store.add_goal("持久目标")
            store.save()
            store.apply_event("novelty")
            store.save()
            path.write_text("{corrupt", encoding="utf-8")
            recovered = InnerStateStore(path)
            self.assertEqual(recovered.state["recovered_from"], "backup")
            self.assertEqual(recovered.state["goals"][0]["text"], "持久目标")
            snapshot = read_snapshot(path)
            self.assertEqual(snapshot["status"], "ok")
            readonly = InnerStateStore(path, read_only=True)
            with self.assertRaises(Exception):
                readonly.save()
            errors = []

            def writer():
                try:
                    for _ in range(10):
                        recovered.apply_event("novelty")
                        recovered.save()
                except Exception as exc:  # pragma: no cover - reported below
                    errors.append(repr(exc))
            threads = [threading.Thread(target=writer) for _ in range(4)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(errors, [])
            self.assertIsNone(InnerStateStore(path).state["recovered_from"])
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class MindLoopTests(unittest.TestCase):
    def test_helpers(self) -> None:
        self.assertEqual(assess_risk("researcher", "删除旧文件")[0], "high")
        self.assertEqual(assess_risk("researcher", "integrate information")[0], "low")
        self.assertEqual(assess_risk("developer", "写测试")[0], "medium")
        self.assertEqual(sanitize_input("a\x00b"), "a b")
        with self.assertRaises(ValueError):
            sanitize_input("   ")
        reflection = heuristic_reflection("请规划一个工具，记住我偏好简洁")
        self.assertTrue(reflection["needs_plan"])
        self.assertTrue(reflection["memory_candidates"])
        ranked = rank_memories([
            {"id": 1, "confidence": 0.2, "updated_at": "2020-01-01T00:00:00+00:00"},
            {"id": 2, "confidence": 1.0, "updated_at": "2099-01-01T00:00:00+00:00"},
        ])
        self.assertEqual(ranked[0]["id"], 2)

    def test_full_cycle_gate_restart_and_human_approval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Muse 意识 ") as directory:
            loop = MindLoop(directory)
            result = loop.perceive("请帮我规划一个离线复盘工具，并且记住：我偏好每周一做复盘。")
            self.assertEqual(result["status"], "waiting_approval")
            self.assertEqual(result["actions"]["planned"], 4)
            self.assertEqual(result["actions"]["succeeded"], 2)  # researcher + architect (low risk)
            self.assertEqual([w["risk"] for w in result["actions"]["waiting_approval"]], ["medium"])
            self.assertTrue(all(c["packet_verified"] for c in result["verification"]["checks"]))
            self.assertEqual(len(result["learning"]["memory_candidates_pending"]), 1)
            pending = loop.status()["pending_approvals"]
            with TaskQueue(loop.task_db) as queue:
                for item in pending:
                    queue.approve(item["task_id"], approved_by="human-test")
            restarted = MindLoop(directory)
            self.assertEqual(restarted.state.state["counters"]["restarts"], 1)
            tick = restarted.idle_tick()
            self.assertEqual(tick["executed_after_approval"]["succeeded"], 2)
            with TaskQueue(restarted.task_db) as queue:
                statuses = sorted(t["status"] for t in queue.list_tasks(kind=MIND_STEP_KIND))
            self.assertEqual(statuses, ["succeeded"] * 4)
            self.assertEqual([g for g in restarted.state.state["goals"] if g["status"] == "active"], [])
            with MemoryStore(restarted.memory_db) as memory:
                self.assertEqual(len(memory.list_memories(project="muse-mind", track="agent")), 1)
                self.assertEqual(memory.search("每周一 复盘", track="user"), [])  # candidates stay pending
            status = read_mind_status(directory)
            self.assertEqual(status["inner_state"]["status"], "ok")
            self.assertEqual(status["pending_approvals"], 0)

    def test_model_mode_repairs_output_and_proposes_skill(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Muse 意识 ") as directory:
            model = DeterministicMockLLM()
            loop = MindLoop(directory, model=model)
            result = loop.perceive("帮我研究一下复盘工具")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["reflection"]["source"], "model")
            self.assertEqual(result["plan"]["source"], "model")
            self.assertEqual(result["actions"]["succeeded"], 3)
            self.assertEqual(result["learning"]["skill_candidate"]["status"], "pending_evaluation")

    def test_timeout_cascades_and_rejection_mode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Muse 意识 ") as directory:
            loop = MindLoop(directory, approval_timeout_hours=0.0)
            loop.perceive("规划一个开发任务")
            self.assertEqual(len(loop.expire_stale_approvals()), 2)  # developer + dependent reviewer
            with TaskQueue(loop.task_db) as queue:
                self.assertNotIn("queued", [t["status"] for t in queue.list_tasks(kind=MIND_STEP_KIND)])
        with tempfile.TemporaryDirectory(prefix="Muse 意识 ") as directory:
            strict = MindLoop(directory, auto_approve="none")
            result = strict.perceive("规划一个研究")
            self.assertEqual(result["actions"]["auto_approved"], 0)
            self.assertEqual(result["actions"]["succeeded"], 0)

    def test_cycle_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Muse 意识 ") as directory:
            loop = MindLoop(directory, max_cycles_per_hour=1)
            self.assertEqual(loop.perceive("你好")["status"], "completed")
            self.assertEqual(loop.perceive("你好")["status"], "throttled")


if __name__ == "__main__":
    unittest.main()
