import unittest

from muse.commander_planner import (
    CommanderPlanner, CommanderPlanConsensus, PlanConsensusBlocked,
)


class CommanderPlannerTests(unittest.TestCase):
    def test_fallback_exposes_parallel_research_and_architecture_wave(self):
        plan = CommanderPlanner().plan("build a feature")
        self.assertEqual(plan.source, "safe_fallback")
        self.assertEqual(plan.parallel_waves[0], ("research", "architecture"))
        self.assertEqual(plan.parallel_waves[-1], ("independent_review",))

    def test_model_plan_validates_and_preserves_dependencies(self):
        raw = {"steps": [
            {"id": "a", "role": "researcher", "objective": "research", "depends_on": [], "required_capabilities": []},
            {"id": "b", "role": "developer", "objective": "build", "depends_on": ["a"], "required_capabilities": ["coding"]},
        ]}
        plan = CommanderPlanner(lambda _goal: raw).plan("goal")
        self.assertEqual(plan.source, "model")
        self.assertEqual(plan.parallel_waves, (("a",), ("b",)))

    def test_unknown_dependency_gets_one_bounded_model_repair(self):
        bad = {"steps": [
            {"id": "research", "role": "researcher", "objective": "research", "depends_on": [], "required_capabilities": ["research"]},
            {"id": "build", "role": "developer", "objective": "build", "depends_on": ["missing"], "required_capabilities": ["coding", "testing"]},
        ]}
        good = {"steps": [
            {"id": "research", "role": "researcher", "objective": "research", "depends_on": [], "required_capabilities": ["research"]},
            {"id": "build", "role": "developer", "objective": "build", "depends_on": ["research"], "required_capabilities": ["coding", "testing"]},
        ]}
        calls = []
        def planner(prompt):
            calls.append(prompt)
            return bad if len(calls) == 1 else good
        plan = CommanderPlanner(planner).plan("build feature")
        self.assertEqual(plan.source, "model")
        self.assertEqual(len(calls), 2)
        self.assertIn("PLAN_REPAIR_REQUIRED", calls[1])
        self.assertEqual(plan.parallel_waves, (("research",), ("build",)))

    def test_invalid_dependency_repair_uses_explicit_safe_fallback(self):
        bad = {"steps": [
            {"id": "research", "role": "researcher", "objective": "research", "depends_on": ["missing"], "required_capabilities": ["research"]},
            {"id": "build", "role": "developer", "objective": "build", "depends_on": [], "required_capabilities": ["coding", "testing"]},
        ]}
        plan = CommanderPlanner(lambda _prompt: bad).plan("build feature")
        self.assertEqual(plan.source, "safe_fallback")
        self.assertEqual(plan.parallel_waves[0], ("research", "architecture"))

    def test_malformed_parallel_dependencies_fall_back_without_type_error(self):
        malformed = {"steps": [
            {"id": "r", "role": "researcher", "objective": "research", "depends_on": [], "required_capabilities": ["research"]},
            {"id": "a", "role": "architect", "objective": "design", "depends_on": [], "required_capabilities": ["design"]},
            {"id": "d", "role": "developer", "objective": "build", "depends_on": "r", "required_capabilities": ["coding"]},
            {"id": "v", "role": "reviewer", "objective": "review", "depends_on": ["d"], "required_capabilities": ["review"]},
        ]}
        goal = "researcher and architect run in parallel; developer depends on both; reviewer depends on developer"
        plan = CommanderPlanner(lambda _prompt: malformed).plan(goal)
        self.assertEqual(plan.source, "safe_fallback")
        self.assertEqual(plan.parallel_waves[0], ("research", "architecture"))

    def test_explicit_parallel_role_contract_repairs_or_uses_safe_fallback(self):
        goal = "researcher and architect run in parallel; developer depends on both; reviewer depends on developer"
        sequential = {"steps": [
            {"id": "r", "role": "researcher", "objective": "research", "depends_on": [], "required_capabilities": ["research"]},
            {"id": "a", "role": "architect", "objective": "design", "depends_on": ["r"], "required_capabilities": ["design"]},
            {"id": "d", "role": "developer", "objective": "synthesize", "depends_on": ["a"], "required_capabilities": ["coding", "testing"]},
            {"id": "v", "role": "reviewer", "objective": "review", "depends_on": ["d"], "required_capabilities": ["review"]},
        ]}
        calls = []
        def planner(prompt):
            calls.append(prompt)
            return sequential
        plan = CommanderPlanner(planner).plan(goal)
        self.assertEqual(len(calls), 2)
        self.assertIn("PARALLEL_ROLE_CONTRACT_REQUIRED", calls[1])
        self.assertEqual(plan.source, "safe_fallback")
        self.assertEqual(plan.parallel_waves[0], ("research", "architecture"))
        self.assertEqual(plan.parallel_waves[1], ("implementation",))
        self.assertEqual(plan.parallel_waves[2], ("independent_review",))

    def test_rejects_cycles_and_unknown_dependencies(self):
        cycle = {"steps": [
            {"id": "a", "role": "researcher", "objective": "a", "depends_on": ["b"]},
            {"id": "b", "role": "reviewer", "objective": "b", "depends_on": ["a"]},
        ]}
        with self.assertRaisesRegex(ValueError, "cycle"):
            CommanderPlanner(lambda _goal: cycle).plan("goal")

    def test_model_failure_uses_safe_fallback(self):
        plan = CommanderPlanner(lambda _goal: 1 / 0).plan("goal")
        self.assertEqual(plan.source, "safe_fallback")



    def test_independent_critic_can_request_one_validated_revision(self):
        calls = []
        first = {"steps": [
            {"id": "research", "role": "researcher", "objective": "research", "depends_on": [], "required_capabilities": ["research"]},
            {"id": "build", "role": "developer", "objective": "build", "depends_on": ["research"], "required_capabilities": ["coding"]},
        ]}
        revised = {"steps": [
            {"id": "research", "role": "researcher", "objective": "research", "depends_on": [], "required_capabilities": ["research"]},
            {"id": "design", "role": "architect", "objective": "design", "depends_on": [], "required_capabilities": ["design"]},
            {"id": "build", "role": "developer", "objective": "build", "depends_on": ["research", "design"], "required_capabilities": ["coding"]},
        ]}

        def planner_call(prompt):
            calls.append(prompt)
            return first if len(calls) == 1 else revised

        critic_calls = []
        def critic(plan, review_round):
            critic_calls.append((plan, review_round))
            if review_round == 1:
                return {"critic_id": "critic-a", "approved": False,
                        "findings": ["Missing independent architecture step"],
                        "revision_feedback": "Add an independent architecture step before implementation."}
            return {"critic_id": "critic-b", "approved": True, "findings": [],
                    "revision_feedback": ""}

        consensus = CommanderPlanConsensus(CommanderPlanner(planner_call), critic)
        result = consensus.plan("Build a feature")
        self.assertEqual(result.revisions, 1)
        self.assertEqual(result.plan.goal, "Build a feature")
        self.assertEqual(result.plan.parallel_waves[0], ("research", "design"))
        self.assertEqual([item.critic_id for item in result.critiques], ["critic-a", "critic-b"])
        self.assertIn("independent architecture step", calls[1])
        self.assertIn("\n\nIndependent Critic", calls[1])
        self.assertNotIn("\\\\n\\\\nIndependent Critic", calls[1])

    def test_consensus_reuses_prevalidated_plan_without_regenerating(self):
        planner_calls = []
        planner = CommanderPlanner(lambda prompt: planner_calls.append(prompt) or [])
        initial = CommanderPlanner().plan("Build a feature")
        critic = lambda _plan, _round: {
            "critic_id": "separate-critic", "approved": True,
            "findings": [], "revision_feedback": "",
        }
        result = CommanderPlanConsensus(planner, critic).plan(
            "Build a feature", initial_plan=initial,
        )
        self.assertEqual(result.plan.plan_id, initial.plan_id)
        self.assertEqual(planner_calls, [])

    def test_consensus_rejects_prevalidated_plan_for_another_goal(self):
        initial = CommanderPlanner().plan("Build feature A")
        critic_calls = []
        critic = lambda *_args: critic_calls.append(True)
        with self.assertRaises(PlanConsensusBlocked):
            CommanderPlanConsensus(CommanderPlanner(), critic).plan(
                "Build feature B", initial_plan=initial,
            )
        self.assertEqual(critic_calls, [])

    def test_consensus_blocks_after_critic_rejects_revised_plan(self):
        critic_ids = iter(("critic-a", "critic-b"))
        critic = lambda _plan, _round: {
            "critic_id": next(critic_ids), "approved": False,
            "findings": ["missing acceptance criteria"], "revision_feedback": "Add acceptance criteria."
        }
        with self.assertRaises(PlanConsensusBlocked):
            CommanderPlanConsensus(CommanderPlanner(), critic).plan("Build a feature")

    def test_consensus_rejects_same_or_non_independent_critic_identity(self):
        with self.assertRaises(PlanConsensusBlocked):
            CommanderPlanConsensus(
                CommanderPlanner(), lambda _plan, _round: {
                    "critic_id": "muse-commander-planner", "approved": True,
                    "findings": [], "revision_feedback": ""
                }
            ).plan("Build a feature")

    def test_consensus_fails_closed_on_malformed_critic_response(self):
        with self.assertRaises(PlanConsensusBlocked):
            CommanderPlanConsensus(CommanderPlanner(), lambda _plan, _round: {"approved": True}).plan("Build a feature")

if __name__ == "__main__": unittest.main()
