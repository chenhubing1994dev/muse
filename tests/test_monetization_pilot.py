from __future__ import annotations

import base64
import tempfile
import json
import subprocess
from unittest.mock import patch
import unittest
from pathlib import Path

from muse.monetization_pilot import (
    build_asr_review_gate,
    build_platform_drafts,
    build_release_checklist,
    resolve_workspace_file,
    _resolve_output_target,
    _run_local_asr,
)


class MonetizationPilotTests(unittest.TestCase):
    def test_platform_drafts_are_original_and_never_claim_published(self):
        drafts = build_platform_drafts(
            title="AI講得好肯定，就一定係真？",
            source_title="NIST Generative AI Profile",
            narration="先拆主張，再查原文，最後主動搵反證。",
        )
        self.assertEqual(set(drafts), {"youtube_shorts", "tiktok", "instagram_reels", "bilibili"})
        for draft in drafts.values():
            self.assertEqual(draft["release_state"], "draft_only")
            self.assertFalse(draft["publish_api_called"])
            self.assertIn("AI-generated", draft["disclosure"])
            self.assertIn("source-notes.json", draft["description"])

    def test_asr_mismatch_keeps_review_draft_but_never_unlocks_publication(self):
        gate = build_asr_review_gate({
            "status": "needs_review",
            "transcript": "完全唔同內容",
            "comparison": {
                "status": "needs_review", "cer": 0.344828,
                "encoding_integrity": "passed",
            },
        })
        self.assertEqual(gate["status"], "needs_review")
        self.assertEqual(gate["character_error_rate"], 0.344828)
        self.assertTrue(gate["human_subtitle_review_required"])
        self.assertEqual(gate["encoding_integrity"], "passed")
        self.assertFalse(gate["ready_to_publish"])

    def test_asr_pass_still_requires_human_subtitle_review_before_publication(self):
        gate = build_asr_review_gate({
            "status": "passed",
            "transcript": "人工智能可以答得好快",
            "comparison": {
                "status": "passed", "cer": 0.0625,
                "encoding_integrity": "passed",
            },
        })
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["encoding_integrity"], "passed")
        self.assertTrue(gate["human_subtitle_review_required"])
        self.assertFalse(gate["ready_to_publish"])

    def test_corrupted_asr_text_cannot_pass_with_a_low_reported_cer(self):
        gate = build_asr_review_gate({
            "status": "passed",
            "transcript": "人工智能\ufffd˹ܿ",
            "comparison": {
                "status": "passed", "cer": 0.0625,
                "encoding_integrity": "failed",
            },
        })
        self.assertEqual(gate["status"], "needs_review")
        self.assertEqual(gate["encoding_integrity"], "failed")
        self.assertTrue(gate["human_subtitle_review_required"])
        self.assertFalse(gate["ready_to_publish"])

    def test_asr_wrapper_uses_base64_utf8_reference_and_ascii_safe_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio = root / "voice.wav"
            audio.write_bytes(b"audio")
            subtitles = root / "subtitles.srt"
            reference = "人工智能可以答得好快，但未必答得啱。"
            payload = {"status": "passed", "transcript": reference, "subtitles": {"path": str(subtitles)}}

            def fake_run(command, *, cwd, timeout, env=None):
                reference_b64 = command[8]
                self.assertTrue(reference_b64.isascii())
                self.assertEqual(base64.b64decode(reference_b64, validate=True).decode("utf-8"), reference)
                self.assertIn("base64.b64decode", command[3])
                self.assertIn("validate=True", command[3])
                self.assertIn(".decode('utf-8')", command[3])
                self.assertIn("ensure_ascii=True", command[3])
                self.assertTrue(env["PYTHONPATH"].startswith(str(root / "src")))
                self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
                subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n測試\n", encoding="utf-8")
                return subprocess.CompletedProcess(
                    command, 0,
                    stdout="MUSE_ASR_RESULT=" + json.dumps(payload, ensure_ascii=True),
                    stderr="",
                )

            with patch("muse.monetization_pilot.shutil.which", return_value="py"):
                with patch("muse.monetization_pilot._run", side_effect=fake_run):
                    result = _run_local_asr(
                        root, audio=audio, model_dir=root / "model", packages_dir=root / "packages",
                        subtitles=subtitles, reference_text=reference,
                    )
            self.assertEqual(result["transcript"], reference)
            self.assertEqual(result["subtitles"]["path"], "subtitles.srt")
            self.assertFalse((root / "asr-reference.txt").exists())

    def test_release_checklist_fails_closed_until_rights_and_human_review(self):
        checklist = build_release_checklist()
        self.assertFalse(checklist["can_publish"])
        self.assertEqual(checklist["release_state"], "draft_only")
        self.assertEqual(checklist["account_authorization"], "not_connected")
        self.assertEqual(checklist["visual_and_model_rights_review"], "pending")
        self.assertEqual(checklist["human_editorial_review"], "pending")

    def test_workspace_file_resolver_rejects_traversal_absolute_and_windows_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inside = root / "inside.txt"
            inside.write_text("safe", encoding="utf-8")
            self.assertEqual(resolve_workspace_file(root, "inside.txt"), root.resolve(strict=True) / "inside.txt")
            for unsafe in ("../outside.txt", "C:/outside.txt", r"folder\escape.txt"):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(ValueError):
                        resolve_workspace_file(root, unsafe)

    def test_output_target_is_new_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = _resolve_output_target(root, "new-package")
            self.assertEqual(target, root / "new-package")
            target.mkdir()
            with self.assertRaises(FileExistsError):
                _resolve_output_target(root, "new-package")

if __name__ == "__main__":
    unittest.main()
