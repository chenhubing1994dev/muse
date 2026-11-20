from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.agent_registry import AgentRegistry
from muse.orchestration import AgentRoleRegistry, MultiAgentOrchestrator, OrchestrationStore
from muse.specialist_router import AgentAdapterDescriptor, SpecialistProfile, SpecialistRegistry, SpecialistRouter, install_builtin_profiles


class SpecialistRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.roles = AgentRoleRegistry(root / "roles.sqlite3")
        self.roles.register("research", allowed_capabilities=())
        self.roles.register("developer", allowed_capabilities=())
        self.roles.register("architecture", allowed_capabilities=())
        self.roles.register("reviewer", allowed_capabilities=())
        self.store = OrchestrationStore(root / "runs.sqlite3")
        self.registry = SpecialistRegistry(root / "specialists.sqlite3")
        self.agents = AgentRegistry(root / "agents.sqlite3")
        self.router = SpecialistRouter(self.registry, MultiAgentOrchestrator(self.store, role_registry=self.roles), agent_registry=self.agents)

    def tearDown(self) -> None:
        self.registry.close()
        self.agents.close()
        self.store.close()
        self.roles.close()
        self.temp.cleanup()

    def test_builtin_profiles_install_idempotently_without_execution_adapters(self) -> None:
        profiles = install_builtin_profiles(self.registry)
        self.assertEqual(len(profiles), 8)
        self.assertEqual(len(self.registry.list_all(enabled_only=True)), 8)
        install_builtin_profiles(self.registry)
        self.assertEqual(len(self.registry.list_all(enabled_only=True)), 8)
        self.assertEqual(self.router.route("请帮我审查代码安全漏洞").selected, ())

    def test_routes_by_specialty_and_explains_matches(self) -> None:
        self.router.register(SpecialistProfile("researcher", "research", ("research", "研究"), ("资料", "调研")), lambda p: "sources:" + p)
        self.router.register(SpecialistProfile("coder", "developer", ("coding", "开发"), ("代码", "编程")), lambda p: "patch:" + p)
        decision = self.router.route("请研究 Muse 的架构并收集资料", max_agents=2)
        self.assertEqual([item.specialist_id for item in decision.selected], ["researcher"])
        self.assertIn("研究", decision.selected[0].matched_domains)
        self.assertGreater(decision.selected[0].score, 0)

    def test_dispatches_multiple_matching_specialists(self) -> None:
        self.router.register(SpecialistProfile("researcher", "research", ("研究",), ("资料",)), lambda p: "research done")
        self.router.register(SpecialistProfile("coder", "developer", ("开发",), ("代码",)), lambda p: "code done")
        result = self.router.dispatch("研究并开发代码", max_agents=2)
        self.assertEqual(result.orchestration.status, "succeeded")
        self.assertEqual({x.specialist_id for x in result.decision.selected}, {"researcher", "coder"})
        self.assertEqual(len(result.orchestration.outcomes), 2)

    def test_provider_profile_adapts_prompt_only_when_declared(self) -> None:
        observed = []
        profile = SpecialistProfile("claude-coder", "developer", ("开发",), ("代码",))
        descriptor = AgentAdapterDescriptor("claude-coder", frozenset(), model_family="claude")
        self.router.register_adapter(profile, descriptor, lambda prompt: observed.append(prompt) or "done")
        result = self.router.dispatch("开发代码")
        self.assertEqual(result.orchestration.status, "succeeded")
        self.assertEqual(len(observed), 1)
        self.assertIn("=== MUSE SYSTEM GUIDANCE ===", observed[0])
        self.assertIn("## 输出格式", observed[0])
        self.assertIn("## 验收标准", observed[0])
        self.assertIn("=== MUSE TASK ===", observed[0])

    def test_default_router_fanout_selects_32_online_specialists(self) -> None:
        for index in range(32):
            agent_id = f"specialist-{index:02d}"
            self.router.register(
                SpecialistProfile(agent_id, "developer", ("开发",), ("代码",)),
                lambda prompt, name=agent_id: f"{name}:{prompt}",
            )
        decision = self.router.route("开发代码")
        self.assertEqual(len(decision.selected), 32)
        self.assertEqual(len({item.specialist_id for item in decision.selected}), 32)

    def test_default_dispatch_runs_32_registered_specialists_concurrently(self) -> None:
        from threading import Barrier
        barrier = Barrier(32)
        for index in range(32):
            agent_id = f"parallel-{index:02d}"
            def invoke(prompt: str, name: str = agent_id) -> str:
                barrier.wait(timeout=8)
                return f"{name}:{prompt}"
            self.router.register(
                SpecialistProfile(agent_id, "developer", ("开发",), ("代码",)),
                invoke,
            )
        # Stress the executor hard limit independently of host-aware production budgeting.
        self.router.orchestrator.resource_policy = None
        result = self.router.dispatch("开发代码")
        self.assertEqual(result.orchestration.status, "succeeded")
        self.assertEqual(len(result.decision.selected), 32)
        self.assertEqual(len(result.orchestration.outcomes), 32)

    def test_disabled_profile_is_not_selected(self) -> None:
        self.router.register(SpecialistProfile("coder", "developer", ("开发",), ("代码",), enabled=False), lambda p: "no")
        self.assertEqual(self.router.route("开发代码").selected, ())

    def test_unregistered_callable_is_not_dispatched_after_restart(self) -> None:
        self.registry.register(SpecialistProfile("coder", "developer", ("开发",), ("代码",)))
        self.assertEqual(self.router.route("开发代码").selected, ())
        with self.assertRaises(LookupError):
            self.router.dispatch("开发代码")

    def test_capability_policy_is_checked_before_any_agent_runs(self) -> None:
        self.router.register(SpecialistProfile("coder", "developer", ("开发",), ("代码",), capabilities=("shell.execute",)), lambda p: "should not run")
        with self.assertRaises(PermissionError):
            self.router.dispatch("开发代码")

    def test_adapter_catalog_filters_missing_capabilities_and_can_be_disabled(self) -> None:
        profile = SpecialistProfile("coder", "developer", ("开发",), ("代码",), capabilities=("coding", "review"))
        self.router.register_adapter(profile, AgentAdapterDescriptor("coder", frozenset({"coding"}), source="local:test"), lambda p: "must not run")
        self.assertEqual(self.router.adapter_catalog()[0].source, "local:test")
        self.assertEqual(self.router.route("开发代码").selected, ())
        self.router.register_adapter(profile, AgentAdapterDescriptor("coder", frozenset({"coding", "review"}), source="local:test"), lambda p: "done")
        self.assertEqual(len(self.router.route("开发代码").selected), 1)
        self.router.set_adapter_enabled("coder", False)
        self.assertEqual(self.router.route("开发代码").selected, ())

    def test_dispatch_records_heartbeat_and_real_outcome_metrics(self) -> None:
        observed = []
        def call(_prompt: str) -> str:
            observed.append(self.agents.get("coder")["status"])
            return "done"
        self.router.register(SpecialistProfile("coder", "developer", ("开发",), ("代码",)), call)
        self.router.dispatch("开发代码")
        self.assertEqual(observed, ["busy"])
        self.assertEqual(self.agents.get("coder")["status"], "online")
        metrics = self.agents.effectiveness("coder")
        self.assertEqual(metrics["completed"], 1)
        self.assertEqual(metrics["success_rate"], 1.0)

    def test_commander_plans_parallel_work_and_dispatches_to_best_live_agents(self) -> None:
        from muse.commander_planner import CommanderPlanner
        executed = []
        registrations = (
            ("researcher", "research", ("research",)),
            ("architect", "architecture", ("design",)),
            ("developer", "developer", ("coding", "testing")),
            ("reviewer", "reviewer", ("review",)),
        )
        for agent_id, role, capabilities in registrations:
            self.router.register_adapter(
                SpecialistProfile(agent_id, role, (role,), (role,), capabilities=()),                AgentAdapterDescriptor(agent_id, frozenset(capabilities)),
                lambda prompt, name=agent_id: executed.append(name) or name + " completed",
            )
        result = self.router.dispatch_commander("Build Muse capability", CommanderPlanner())
        self.assertEqual(result.dispatch.orchestration.status, "succeeded")
        self.assertEqual(result.plan.parallel_waves[0], ("research", "architecture"))
        self.assertEqual(set(executed), {"researcher", "architect", "developer", "reviewer"})
        self.assertEqual(len(result.dispatch.orchestration.outcomes), 4)

    def test_commander_refuses_missing_or_offline_role_instead_of_fake_dispatch(self) -> None:
        from muse.commander_planner import CommanderPlanner
        with self.assertRaisesRegex(LookupError, "no live compatible Agent"):
            self.router.dispatch_commander("Build Muse capability", CommanderPlanner())

    def test_dispatch_pipeline_runs_research_architecture_development_review_in_order(self) -> None:
        executed = []
        steps = (
            ("researcher", "research", ("研究",), ("资料",)),
            ("architect", "architecture", ("架构",), ("设计",)),
            ("developer", "developer", ("开发",), ("实现",)),
            ("reviewer", "reviewer", ("审查",), ("质量",)),
        )
        for agent_id, role, domains, keywords in steps:
            self.router.register(
                SpecialistProfile(agent_id, role, domains, keywords),
                lambda _prompt, role=role: executed.append(role) or f"{role} result",
            )
        result = self.router.dispatch_pipeline("Build and review Muse")
        self.assertEqual(executed, ["research", "architecture", "developer", "reviewer"])
        self.assertEqual(result.orchestration.status, "succeeded")
        self.assertEqual([item.specialist_id for item in result.decision.selected], [step[0] for step in steps])

    def test_review_pass_rate_breaks_ties_between_equally_successful_agents(self) -> None:
        for agent_id in ("alpha", "beta"):
            self.router.register(SpecialistProfile(agent_id, "developer", ("开发",), ("代码",)), lambda _p: "done")
            self.agents.record_outcome(agent_id, task_kind="coding", succeeded=True)
        self.agents.record_review_outcome("alpha", task_kind="coding", approved=False)
        self.agents.record_review_outcome("beta", task_kind="coding", approved=True)
        decision = self.router.route("开发代码", max_agents=2)
        self.assertEqual([item.specialist_id for item in decision.selected], ["beta", "alpha"])

    def test_local_http_adapter_is_invoked_through_trusted_router_registration(self) -> None:
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                observed.append(payload["prompt"])
                body = json.dumps({"agent_id": "coder", "output": "patch ready"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            profile = SpecialistProfile("coder", "developer", ("开发",), ("代码",))
            self.router.register_local_http_agent(profile, f"http://127.0.0.1:{server.server_port}/api/v1/invoke")
            result = self.router.dispatch("开发代码")
            self.assertEqual(result.orchestration.status, "succeeded")
            self.assertEqual(observed, ["开发代码"])
            self.assertEqual(self.agents.effectiveness("coder")["success_rate"], 1.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_no_match_is_explicit_not_random_fallback(self) -> None:
        self.router.register(SpecialistProfile("coder", "developer", ("开发",), ("代码",)), lambda p: "no")
        with self.assertRaises(LookupError):
            self.router.dispatch("帮我安排一次生日聚会")


if __name__ == "__main__":
    unittest.main()