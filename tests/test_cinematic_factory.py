"""B397: cinematic video factory engines + orchestrator."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

try:
    import gc
except ImportError:  # restricted sandboxes
    gc = None

from muse.cinematic_factory import (
    REQUIRED_FREE_VRAM_MIB,
    CinematicFactory,
    StoryBibleLock,
    align_subtitles,
    analyze_visual_rhythm,
    build_ai_upscale_workflow,
    build_wan21_workflow,
    classify_upscale,
    direct_shots,
    generate_hook_matrix,
    gpu_preflight,
    import_retention_curve,
    integrated_loudness,
    loudness_qc,
    quality_gate_720p,
    request_from_payload,
    retention_to_agent_track,
    review_shots,
    validate_comfy_graph,
)
from muse.memory import MemoryStore


def sine(amplitude: float, seconds: float = 3.0, fs: int = 48000, freq: float = 997.0):
    return [amplitude * math.sin(2 * math.pi * freq * n / fs) for n in range(int(seconds * fs))]


class HookRhythmDirectorTests(unittest.TestCase):
    def test_hook_matrix_covers_patterns_and_fits_three_seconds(self):
        matrix = generate_hook_matrix("粤语短片剪辑")
        self.assertEqual(len({h["pattern"] for h in matrix}), 6)
        self.assertEqual(len(matrix), 12)
        self.assertTrue(all(len(h["beats"]) == 3 for h in matrix))
        self.assertTrue(matrix[0]["fits_3s"])
        self.assertEqual(matrix, generate_hook_matrix("粤语短片剪辑"))  # deterministic
        with self.assertRaises(ValueError):
            generate_hook_matrix("")

    def test_rhythm_density_finds_dead_air_and_hook_window(self):
        slow = analyze_visual_rhythm([{"start": 0, "end": 7, "motion": 0.1}, {"start": 7, "end": 9, "motion": 0.2}])
        self.assertFalse(slow["hook_window_ok"])
        self.assertEqual(slow["dead_air_windows"], [0.0, 3.0])
        fast = analyze_visual_rhythm([{"start": 0, "end": 1.2, "motion": 0.9, "text_overlay": True},
                                      {"start": 1.2, "end": 2.5}, {"start": 2.5, "end": 6, "motion": 0.7}])
        self.assertTrue(fast["hook_window_ok"])
        self.assertEqual(fast["dead_air_windows"], [])

    def test_story_bible_lock_detects_drift_unless_declared(self):
        bible = StoryBibleLock({"ah-ming": {"wardrobe": "red raincoat", "look": "round glasses", "props": ["film camera"]}})
        report = bible.check([
            {"shot_id": "s1", "characters": {"ah-ming": {"wardrobe": "red raincoat", "props": ["film camera"]}}},
            {"shot_id": "s2", "characters": {"ah-ming": {"wardrobe": "blue hoodie"}}},
            {"shot_id": "s3", "characters": {"ah-ming": {"props": []}}},
            {"shot_id": "s4", "characters": {"ah-ming": {"wardrobe": "blue hoodie"}}, "change_events": ["ah-ming"]},
            {"shot_id": "s5", "characters": {"ghost": {}}}])
        self.assertEqual([v["issue"] for v in report["violations"]], ["wardrobe_drift", "prop_missing", "unknown_character"])

    def test_shot_director_never_repeats_size_and_explains(self):
        plan = direct_shots([{"purpose": "hook", "emotion": "energy"}, {"purpose": "hook", "emotion": "tension"},
                             {"purpose": "reveal", "emotion": "reveal"}])
        self.assertEqual(plan[0]["size"], "ECU")
        self.assertNotEqual(plan[0]["size"], plan[1]["size"])
        self.assertTrue(all(p["rationale"] and p["focal_length_mm"] for p in plan))


class GpuWorkflowUpscaleTests(unittest.TestCase):
    def test_gpu_preflight_threshold(self):
        self.assertEqual(REQUIRED_FREE_VRAM_MIB, 7885)
        self.assertTrue(gpu_preflight(lambda: {"free_mib": 7885})["ok"])
        self.assertFalse(gpu_preflight(lambda: {"free_mib": 7884})["ok"])
        self.assertEqual(gpu_preflight(None)["reason"], "no_gpu_probe")
        self.assertTrue(gpu_preflight(lambda: 1 / 0)["reason"].startswith("probe_failed"))

    def test_wan21_workflow_is_valid_graph(self):
        workflow = build_wan21_workflow("neon alley, ECU", seed=7)
        self.assertEqual(validate_comfy_graph(workflow), [])
        self.assertEqual(workflow["7"]["inputs"]["height"], 480)
        with self.assertRaises(ValueError):
            build_wan21_workflow("x", frames=80)
        broken = dict(workflow)
        broken["9"] = {"class_type": "VAEDecode", "inputs": {"samples": ["99", 0], "vae": ["3", 0]}}
        self.assertTrue(validate_comfy_graph(broken))

    def test_real_ai_upscale_vs_plain_resize_gate(self):
        ai = build_ai_upscale_workflow(video_frames_dir="frames")
        self.assertEqual(classify_upscale(ai), "ai_model")
        plain = {"1": {"class_type": "LoadImagesFromDirectory", "inputs": {"directory": "f"}},
                 "2": {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "width": 1920, "height": 1080}},
                 "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}}}
        self.assertEqual(classify_upscale(plain), "plain_resize")
        detached = dict(ai)
        detached["4"] = {"class_type": "ImageScale", "inputs": {"image": ["1", 0], "width": 1920, "height": 1080}}
        self.assertEqual(classify_upscale(detached), "plain_resize")   # model node not on the output path
        self.assertTrue(quality_gate_720p(source_height=480, output_height=1080, upscale_kind="ai_model")["pass"])
        self.assertFalse(quality_gate_720p(source_height=480, output_height=1080, upscale_kind="plain_resize")["pass"])
        self.assertTrue(quality_gate_720p(source_height=720, output_height=720, upscale_kind="plain_resize")["pass"])


class AudioSubtitleReviewTests(unittest.TestCase):
    def test_bs1770_reference_tones(self):
        self.assertAlmostEqual(integrated_loudness([sine(1.0)], 48000), -3.01, delta=0.1)
        self.assertAlmostEqual(integrated_loudness([sine(0.1)], 48000), -23.01, delta=0.1)
        qc = loudness_qc([sine(0.1)], 48000)
        self.assertTrue(qc["pass"])
        loud = loudness_qc([sine(0.9)], 48000)
        self.assertFalse(loud["pass"])
        self.assertLess(loud["gain_to_target_db"], -15)
        self.assertEqual(integrated_loudness([[0.0] * 48000], 48000), -math.inf)

    def test_cantonese_first_subtitles_reading_speed_overlap_and_drift(self):
        good = align_subtitles([{"start": 0, "end": 2, "yue": "今晚一齐睇", "cmn": "今晚一起看"},
                                {"start": 2.1, "end": 4, "yue": "呢个镜头", "cmn": "这个镜头"}],
                               voice=[{"start": 0.05, "end": 1.98}, {"start": 2.12, "end": 4.05}])
        self.assertTrue(good["ok"], good["issues"])
        self.assertIn("00:00:02,100 --> 00:00:04,000", good["srt_yue"])
        bad = align_subtitles([{"start": 0, "end": 1, "yue": "呢句字幕实在太长太长太长太长", "cmn": ""},
                               {"start": 0.5, "end": 2, "yue": "", "cmn": "普通话"}],
                              voice=[{"start": 0.4, "end": 1.0}])
        issues = {i["issue"] for i in bad["issues"]}
        self.assertTrue({"yue_reading_speed", "overlap", "missing_cantonese_primary", "av_drift", "no_voice_segment"} <= issues)

    def test_jyutping_romanisation_is_validated(self):
        ok = align_subtitles([{"start": 0, "end": 2, "yue": "你好", "cmn": "你好", "jyutping": "nei5 hou2"}])
        self.assertTrue(ok["ok"], ok["issues"])
        bad = align_subtitles([{"start": 0, "end": 2, "yue": "你好", "jyutping": "nei5"},
                               {"start": 2, "end": 4, "yue": "早晨", "jyutping": "zou9x san4"}])
        self.assertEqual([i["issue"] for i in bad["issues"]], ["jyutping_syllable_mismatch", "invalid_jyutping"])

    def test_vlm_review_falls_back_to_human(self):
        shots = [{"shot_id": "s01"}, {"shot_id": "s02"}, {"shot_id": "s03"}]
        replies = {"s01": '{"verdict":"pass","issues":[]}', "s02": '{"verdict":"fail","issues":["six fingers"]}', "s03": "garbage"}
        result = review_shots(shots, lambda shot: replies[shot["shot_id"]])
        self.assertEqual([q["shot_id"] for q in result["human_review_queue"]], ["s02", "s03"])
        self.assertTrue(result["release_requires_human"])
        self.assertEqual(len(review_shots(shots, None)["human_review_queue"]), 3)


class RetentionAndOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self):
        if gc:
            gc.collect()
        self.tmp.cleanup()

    def test_retention_import_and_agent_track_candidates(self):
        csv_text = "\ufeffVideo position (seconds),Audience retention (%)\n0,100\n3,72\n10,55\n20,51\n30,38\n"
        curve = import_retention_curve(csv_text)
        self.assertEqual((curve["hold_3s"], curve["completion_rate"]), (0.72, 0.38))
        self.assertEqual(curve["biggest_drops"][0]["from_s"], 0.0)
        ratio = import_retention_curve("时间,留存\n0,1\n0.5,0.6\n1,0.4\n", duration_seconds=20)
        self.assertEqual(ratio["completion_rate"], 0.4)
        with MemoryStore(Path(self.tmp.name) / "m.sqlite3") as store:
            proposals = retention_to_agent_track(curve, hook_id="hook-contradiction-1", video_id="v1", memory_store=store)
            self.assertEqual(len(store.list_pending()), len(proposals))
            self.assertTrue(all(p["track"] == "agent" for p in proposals))
        with self.assertRaises(ValueError):
            import_retention_curve("a,b\n1,2\n")

    def test_orchestrator_full_dag_blocks_without_gpu_and_submits_with_gpu(self):
        request = request_from_payload({"topic": "港式夜景", "subtitles": [{"start": 0, "end": 2, "yue": "夜景", "cmn": "夜景"}]})
        blocked = CinematicFactory().plan(request, audio=([sine(0.1, seconds=1.0)], 48000))
        self.assertEqual(list(blocked["nodes"]), list(CinematicFactory.DAG))
        self.assertEqual(blocked["nodes"]["wan21_workflow"]["status"], "blocked_gpu")
        self.assertFalse(blocked["ready_for_render"])
        self.assertEqual(blocked["nodes"]["upscale_1080p_bridge"]["output"]["upscale_kind"], "ai_model")
        self.assertTrue(blocked["nodes"]["loudness_qc"]["output"]["pass"])
        submitted = []
        ready = CinematicFactory(gpu_probe=lambda: {"free_mib": 9000},
                                 comfy_submit=lambda wf: submitted.append(wf) or f"p{len(submitted)}").plan(request)
        self.assertEqual(ready["nodes"]["wan21_workflow"]["status"], "submitted")
        self.assertEqual(len(submitted), 6)
        self.assertTrue(ready["ready_for_render"])
        self.assertEqual(len(ready["manifest_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
