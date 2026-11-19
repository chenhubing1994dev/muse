from __future__ import annotations

import unittest

from muse.subtitle_review import (
    apply_human_subtitle_review,
    build_subtitle_review,
    render_approved_subtitles,
    parse_srt_cues,
)


class SubtitleReviewTests(unittest.TestCase):
    def _asr(self):
        return {
            "engine": "faster-whisper-local", "model_repo": "local/whisper",
            "model_revision": "abc123", "audio_sha256": "a" * 64,
            "transcript": "人工只能講得流暢。",
            "comparison": {"cer": 0.1, "max_cer": 0.18, "status": "passed"},
            "segments": [
                {"start": 0.0, "end": 1.2, "text": "人工只能", "avg_logprob": -0.2, "no_speech_prob": 0.01},
                {"start": 1.2, "end": 2.5, "text": "講得流暢。", "avg_logprob": -0.1, "no_speech_prob": 0.02},
            ],
        }

    def test_review_keeps_raw_asr_and_script_separate_and_never_auto_approves(self):
        review = build_subtitle_review("人工智能講得流暢。", self._asr())
        self.assertEqual(review["review_status"], "pending_human_review")
        self.assertFalse(review["human_approved"])
        self.assertEqual(review["reference_script"], "人工智能講得流暢。")
        self.assertEqual(review["raw_asr_transcript"], "人工只能講得流暢。")
        self.assertEqual(review["segments"][0]["asr_text"], "人工只能")
        self.assertIsNone(review["segments"][0]["corrected_text"])
        self.assertEqual(review["asr"]["cer"], 0.1)

    def test_rejects_invalid_segment_timing_and_unsafe_subtitle_name(self):
        asr = self._asr()
        asr["segments"][1]["start"] = 0.5
        with self.assertRaisesRegex(ValueError, "invalid timing"):
            build_subtitle_review("reference", asr)
        with self.assertRaisesRegex(ValueError, "simple filename"):
            build_subtitle_review("reference", self._asr(), subtitle_file="../captions.srt")

    def test_rejects_invalid_cer_and_non_finite_confidence(self):
        asr = self._asr()
        asr["comparison"]["cer"] = float("nan")
        with self.assertRaisesRegex(ValueError, "CER must be finite"):
            build_subtitle_review("reference", asr)
        asr = self._asr()
        asr["segments"][0]["avg_logprob"] = float("inf")
        with self.assertRaisesRegex(ValueError, "avg_logprob must be finite"):
            build_subtitle_review("reference", asr)

    def test_partial_review_never_approves_or_renders_srt(self):
        review = build_subtitle_review("人工智能講得流暢。", self._asr())
        partial = apply_human_subtitle_review(
            review, reviewed_segment_indexes=[1], corrections=[{"segment_index": 1, "corrected_text": "人工智能"}],
            reviewer="reviewer-1", approve=False,
        )
        self.assertEqual(partial["review_status"], "partially_reviewed")
        self.assertFalse(partial["human_approved"])
        self.assertEqual(partial["segments"][0]["asr_text"], "人工只能")
        self.assertEqual(partial["segments"][0]["corrected_text"], "人工智能")
        self.assertEqual(review["segments"][0]["review_status"], "pending")
        with self.assertRaisesRegex(ValueError, "human approval"):
            render_approved_subtitles(partial)

    def test_all_segments_require_explicit_approval_and_render_corrected_srt(self):
        review = build_subtitle_review("人工智能講得流暢。", self._asr())
        approved = apply_human_subtitle_review(
            review, reviewed_segment_indexes=[1, 2],
            corrections=[{"segment_index": 1, "corrected_text": "人工智能"}],
            reviewer="human-editor", approve=True,
        )
        self.assertTrue(approved["human_approved"])
        self.assertEqual(approved["review_status"], "approved")
        self.assertEqual(approved["approved_by"], "human-editor")
        self.assertEqual(approved["segments"][0]["asr_text"], "人工只能")
        srt = render_approved_subtitles(approved)
        self.assertIn("00:00:00,000 --> 00:00:01,200\n人工智能", srt)
        self.assertIn("00:00:01,200 --> 00:00:02,500\n講得流暢。", srt)
        with self.assertRaisesRegex(ValueError, "immutable"):
            apply_human_subtitle_review(
                approved, reviewed_segment_indexes=[1, 2], corrections=[], reviewer="another", approve=True,
            )
    def test_reconfirming_asr_text_clears_a_prior_correction_without_erasing_audit(self):
        review = build_subtitle_review("人工智能講得流暢。", self._asr())
        corrected = apply_human_subtitle_review(
            review, reviewed_segment_indexes=[1],
            corrections=[{"segment_index": 1, "corrected_text": "人工智能"}], reviewer="editor",
        )
        reconfirmed = apply_human_subtitle_review(
            corrected, reviewed_segment_indexes=[1], corrections=[], reviewer="editor-2",
        )
        self.assertIsNone(reconfirmed["segments"][0]["corrected_text"])
        self.assertEqual(reconfirmed["segments"][0]["review_status"], "confirmed")
        self.assertEqual(len(reconfirmed["corrections"]), 1)
        self.assertEqual(reconfirmed["segments"][0]["asr_text"], "人工只能")

    def test_approval_requires_every_segment_and_rejects_duplicate_or_unreviewed_corrections(self):
        review = build_subtitle_review("人工智能講得流暢。", self._asr())
        with self.assertRaisesRegex(ValueError, "every subtitle segment"):
            apply_human_subtitle_review(
                review, reviewed_segment_indexes=[1], corrections=[], reviewer="human", approve=True,
            )
        with self.assertRaisesRegex(ValueError, "invalid or duplicate"):
            apply_human_subtitle_review(
                review, reviewed_segment_indexes=[1, 1], corrections=[], reviewer="human",
            )
        with self.assertRaisesRegex(ValueError, "not marked as reviewed"):
            apply_human_subtitle_review(
                review, reviewed_segment_indexes=[1],
                corrections=[{"segment_index": 2, "corrected_text": "unreviewed"}], reviewer="human",
            )

    def test_srt_cues_drive_review_granularity_and_preserve_raw_asr_segments(self):
        srt = (
            "\ufeff1\n00:00:00,000 --> 00:00:01,200\n人工只能\n\n"
            "2\n00:00:01,200 --> 00:00:02,500\n講得流暢。\n"
        )
        cues = parse_srt_cues(srt)
        review = build_subtitle_review("人工智能講得流暢。", self._asr(), subtitle_cues=cues)
        self.assertEqual(review["subtitle_cue_count"], 2)
        self.assertEqual([item["asr_text"] for item in review["segments"]], ["人工只能", "講得流暢。"])
        self.assertEqual(review["segments"][0]["source_asr_segment_indexes"], [1])
        self.assertEqual(review["segments"][1]["source_asr_segment_indexes"], [2])
        self.assertEqual(len(review["asr_segments"]), 2)
        self.assertEqual(review["raw_asr_transcript"], "人工只能講得流暢。")

    def test_srt_parser_rejects_overlapping_and_nonsequential_cues(self):
        overlap = "1\n00:00:00,000 --> 00:00:01,000\nA\n\n2\n00:00:00,500 --> 00:00:01,500\nB"
        with self.assertRaisesRegex(ValueError, "invalid timing"):
            parse_srt_cues(overlap)
        bad_index = "1\n00:00:00,000 --> 00:00:01,000\nA\n\n3\n00:00:01,000 --> 00:00:02,000\nB"
        with self.assertRaisesRegex(ValueError, "sequential"):
            parse_srt_cues(bad_index)


if __name__ == "__main__":
    unittest.main()
