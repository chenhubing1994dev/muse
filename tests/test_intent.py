from __future__ import annotations

import unittest
from types import SimpleNamespace

from muse.intent import classify_request, build_execution_plan


class IntentRoutingTests(unittest.TestCase):
    def test_routes_video_request_to_existing_media_pipeline(self):
        inventory = SimpleNamespace(tools=(SimpleNamespace(tool_id="ffmpeg"), SimpleNamespace(tool_id="obs")))
        result = classify_request("幫我將呢批素材剪成一條宣傳片", inventory=inventory)
        self.assertEqual(result["domain"], "video")
        self.assertEqual(result["capability"], "video_edit")
        plan = build_execution_plan(result)
        self.assertEqual(plan["steps"][0]["tool"], "ffmpeg")
        self.assertEqual(plan["status"], "needs_review")

    def test_routes_copywriting_report_and_web_tasks(self):
        for request, domain, capability in (
            ("幫我寫一份商業報告", "documents", "report"),
            ("幫我寫宣傳文案", "writing", "copywriting"),
            ("幫我設計一個網站", "web", "website"),
        ):
            with self.subTest(request=request):
                result = classify_request(request)
                self.assertEqual((result["domain"], result["capability"]), (domain, capability))

    def test_ambiguous_request_is_not_guessed(self):
        result = classify_request("幫我搞掂佢")
        self.assertEqual(result["domain"], "unknown")
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(build_execution_plan(result)["status"], "needs_clarification")

    def test_missing_tool_is_reported_not_invented(self):
        inventory = SimpleNamespace(tools=())
        result = classify_request("幫我剪影片", inventory=inventory)
        plan = build_execution_plan(result)
        self.assertEqual(plan["status"], "blocked")
        self.assertIn("not confirmed", plan["blockers"][0])

    def test_routes_against_persisted_capability_registry(self):
        import tempfile
        from pathlib import Path
        from muse.capabilities import CapabilityRegistry

        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "capabilities.sqlite3") as registry:
                registry.register(
                    capability_id="media.ffmpeg", domain="video", name="FFmpeg",
                    description="Verified local media tool", executable="D:/tools/ffmpeg.exe",
                    capabilities=("video_transcode", "video_filter"), status="ready",
                    trust="local_path_discovered", license="not_applicable",
                )
                result = classify_request("幫我剪影片", registry=registry)
                self.assertEqual(result["tool"], "ffmpeg")
                self.assertEqual(result["tool_status"], "ready")

    def test_registry_does_not_infer_unregistered_capabilities(self):
        import tempfile
        from pathlib import Path
        from muse.capabilities import CapabilityRegistry

        with tempfile.TemporaryDirectory() as directory:
            with CapabilityRegistry(Path(directory) / "capabilities.sqlite3") as registry:
                result = classify_request("幫我剪影片", registry=registry)
                self.assertEqual(result["tool_status"], "unverified")

    def test_reminder_request_routes_to_explicit_local_capture_not_execution(self):
        result = classify_request("提醒我半小时后检查报告")
        plan = build_execution_plan(result)
        self.assertEqual(result["domain"], "reminders")
        self.assertEqual(result["capability"], "reminder_capture")
        self.assertEqual(plan["status"], "ready_for_local_capture")
        self.assertFalse(plan["execution_started"])
        self.assertFalse(plan["approval_required"])

    def test_sensitive_external_actions_require_approval(self):
        result = classify_request("幫我將呢份報告發送畀客戶")
        plan = build_execution_plan(result)
        self.assertEqual(plan["status"], "waiting_approval")
        self.assertTrue(plan["approval_required"])


    def test_optional_gemma_is_not_called_for_clear_deterministic_intent(self):
        from unittest.mock import patch
        with patch("muse.gemma_intent.assist_unknown_request") as assist:
            result = classify_request("幫我寫代碼")
        self.assertEqual(result["capability"], "coding")
        assist.assert_not_called()

    def test_optional_gemma_is_consulted_only_for_unknown_and_keeps_approval_gate(self):
        import os
        from unittest.mock import patch
        suggestion = {
            "domain": "software", "capability": "coding", "confidence": .9,
            "classifier_source": "optional_local_gemma", "tool_status": "unverified",
            "approval_required": True,
        }
        with patch.dict(os.environ, {"MUSE_GEMMA_ENABLED": "1"}):
            with patch("muse.gemma_intent.assist_unknown_request", return_value=suggestion) as assist:
                result = classify_request("delete this item after review")
        assist.assert_called_once()
        self.assertEqual(result["capability"], "coding")
        self.assertTrue(result["approval_required"])


if __name__ == "__main__":
    unittest.main()
