from __future__ import annotations

import json
import unittest

from muse.software_recreation import RecreationRequest, build_recreation_plan, main


class SoftwareRecreationPlanTests(unittest.TestCase):
    def test_clean_room_plan_is_default_and_does_not_dispatch(self):
        plan = build_recreation_plan(RecreationRequest(
            target="https://example.com/app", target_kind="website", authorization_confirmed=True,
        ))
        data = plan.to_dict()
        self.assertEqual(plan.mode, "clean_room")
        self.assertFalse(plan.execution_started)
        self.assertTrue(data["authorization_confirmed"])
        self.assertEqual(plan.dispatcher, "muse_commander_only")
        self.assertIn("craft_anything.clean_room_study", data["steps"][0]["required_capabilities"])
        self.assertIn("rea.passive_visible_observation", data["steps"][0]["required_capabilities"])
        self.assertTrue(data["steps"][3]["requires_approval"])

    def test_deep_mode_requires_scope(self):
        with self.assertRaises(PermissionError):
            build_recreation_plan(RecreationRequest(
                target="D:/apps/example", target_kind="desktop_app",
                authorization_confirmed=True, mode="deep",
            ))

    def test_deep_mode_records_scope_and_requires_study_approval(self):
        scope = "Owned local test application; static analysis only"
        plan = build_recreation_plan(RecreationRequest(
            target="D:/apps/example", target_kind="desktop_app", authorization_confirmed=True,
            mode="deep", authorization_scope=scope,
        ))
        self.assertEqual(plan.authorization_scope, scope)
        self.assertTrue(plan.steps[0].requires_approval)
        self.assertIn("rea.deep_analysis", plan.steps[0].required_capabilities)

    def test_rejects_unconfirmed_authorization(self):
        with self.assertRaises(PermissionError):
            build_recreation_plan(RecreationRequest(target="https://example.com", target_kind="website"))

    def test_rejects_credentials_in_url(self):
        with self.assertRaises(ValueError):
            build_recreation_plan(RecreationRequest(
                target="https://user:pass@example.com", target_kind="website", authorization_confirmed=True,
            ))

    def test_rejects_secret_query_parameters(self):
        with self.assertRaises(ValueError):
            build_recreation_plan(RecreationRequest(
                target="https://example.com/?api_key=secret", target_kind="website", authorization_confirmed=True,
            ))

    def test_local_target_requires_absolute_path_and_rejects_parent_traversal(self):
        with self.assertRaises(ValueError):
            build_recreation_plan(RecreationRequest(
                target="relative/app", target_kind="desktop_app", authorization_confirmed=True,
            ))
        with self.assertRaises(ValueError):
            build_recreation_plan(RecreationRequest(
                target="D:/apps/../secret", target_kind="desktop_app", authorization_confirmed=True,
            ))

    def test_refinement_budget_is_bounded(self):
        with self.assertRaises(ValueError):
            build_recreation_plan(RecreationRequest(
                target="D:/apps/example", target_kind="desktop_app",
                authorization_confirmed=True, max_refine_rounds=6,
            ))

    def test_plan_is_deterministic_and_refinement_is_bounded(self):
        request = RecreationRequest(
            target="D:/apps/example", target_kind="desktop_app",
            authorization_confirmed=True, max_refine_rounds=2,
        )
        first = build_recreation_plan(request)
        second = build_recreation_plan(request)
        self.assertEqual(first.plan_id, second.plan_id)
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        refine = next(step for step in first.steps if step.step_id == "refine")
        self.assertEqual(refine.max_attempts, 2)

    def test_cli_is_plan_only_and_prints_json(self):
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["https://example.com", "--kind", "website", "--authorized"])
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["execution_started"])
        self.assertEqual(payload["dispatcher"], "muse_commander_only")


if __name__ == "__main__":
    unittest.main()
