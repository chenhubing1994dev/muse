from __future__ import annotations

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from muse.video_qa import inspect_video_file, map_warnings_to_storyboard_scenes


class VideoQATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.video = Path(self.temp.name) / "clip.mp4"
        self.video.write_bytes(b"fixture-mp4")

    def tearDown(self):
        self.temp.cleanup()

    def _probe(self, *, width=1080, height=1920, duration="12.5", fps="24/1", audio=True):
        streams = [{"codec_type": "video", "codec_name": "h264", "width": width,
                    "height": height, "avg_frame_rate": fps, "duration": duration}]
        if audio:
            streams.append({"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"})
        return {"format": {"format_name": "mov,mp4", "duration": duration, "size": str(self.video.stat().st_size)},
                "streams": streams}

    def _runner(self, probe, *, decode_code=0, decode_stderr=None, calls=None):
        def run(args, **kwargs):
            if calls is not None:
                calls.append(list(args))
            if args[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout=json.dumps(probe), stderr="")
            stderr = decode_stderr if decode_stderr is not None else ("decode failed" if decode_code else "")
            return SimpleNamespace(returncode=decode_code, stdout="", stderr=stderr)
        return run

    def test_accepts_valid_vertical_mp4_and_reports_checks(self):
        result = inspect_video_file(self.video, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg",
                                    expected_width=1080, expected_height=1920, expected_fps=24,
                                    runner=self._runner(self._probe()))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["metadata"]["duration_seconds"], 12.5)
        self.assertTrue(result["checks"]["full_decode"]["passed"])
        self.assertEqual(len(result["sha256"]), 64)

    def test_fails_closed_on_wrong_dimensions_and_decode_error(self):
        result = inspect_video_file(self.video, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg",
                                    expected_width=1920, expected_height=1080,
                                    runner=self._runner(self._probe(), decode_code=1))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["checks"]["dimensions"]["passed"])
        self.assertFalse(result["checks"]["full_decode"]["passed"])
        self.assertEqual(result["quality_signals"]["status"], "partial")

    def test_reports_black_freeze_and_silence_as_review_warnings_not_false_failure(self):
        calls = []
        stderr = (
            "[blackdetect] black_start:2 black_end:3.2 black_duration:1.2\n"
            "[freezedetect] freeze_start:4 freeze_duration:1.5 freeze_end:5.5\n"
            "[silencedetect] silence_start:6\n"
            "[silencedetect] silence_end:8 | silence_duration:2\n"
        )
        result = inspect_video_file(
            self.video, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg",
            runner=self._runner(self._probe(), decode_stderr=stderr, calls=calls),
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual({item["code"] for item in result["warnings"]},
                         {"sustained_black", "frozen_frames", "sustained_silence", "audio_metrics_incomplete"})
        self.assertEqual(result["quality_signals"]["status"], "observed")
        decode_call = next(call for call in calls if "-vf" in call)
        self.assertIn("blackdetect", decode_call[decode_call.index("-vf") + 1])
        self.assertIn("freezedetect", decode_call[decode_call.index("-vf") + 1])
        self.assertIn("silencedetect", decode_call[decode_call.index("-af") + 1])
    def test_custom_warning_thresholds_lower_detector_durations(self):
        calls = []
        inspect_video_file(
            self.video, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg",
            black_warning_seconds=0.2, freeze_warning_seconds=0.4,
            silence_warning_seconds=0.3,
            runner=self._runner(self._probe(), calls=calls),
        )
        decode_call = next(call for call in calls if "-vf" in call)
        self.assertIn("blackdetect=d=0.2", decode_call[decode_call.index("-vf") + 1])
        self.assertIn("freezedetect=n=-50dB:d=0.4", decode_call[decode_call.index("-vf") + 1])
        self.assertIn("silencedetect=n=-50dB:d=0.3", decode_call[decode_call.index("-af") + 1])

    def test_does_not_run_silence_detector_when_video_has_no_audio(self):
        calls = []
        result = inspect_video_file(
            self.video, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg",
            runner=self._runner(self._probe(audio=False), calls=calls),
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["quality_signals"]["silent_intervals"], [])
        decode_call = next(call for call in calls if "-vf" in call)
        self.assertNotIn("-af", decode_call)

    def test_rejects_missing_audio_when_required(self):
        result = inspect_video_file(self.video, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg",
                                    require_audio=True, runner=self._runner(self._probe(audio=False)))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["checks"]["audio_stream"]["passed"])

    def test_missing_tool_is_blocked_not_passed(self):
        result = inspect_video_file(self.video, ffprobe_path=None, ffmpeg_path=None)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("ffprobe", result["missing_tools"])
        self.assertEqual(result["quality_signals"]["status"], "unavailable")
        self.assertEqual(result["warnings"], [])

    def test_rejects_non_mp4_or_missing_file(self):
        other = Path(self.temp.name) / "clip.mov"
        other.write_bytes(b"fixture")
        with self.assertRaises(ValueError):
            inspect_video_file(other, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg")
        with self.assertRaises(FileNotFoundError):
            inspect_video_file(Path(self.temp.name) / "missing.mp4", ffprobe_path="ffprobe", ffmpeg_path="ffmpeg")


    def test_reports_summary_loudness_true_peak_and_suspected_clipping_from_ffmpeg_stats(self):
        stderr = """[Parsed_ebur128_0] t: 0.1 I: -70.0 LUFS
[Parsed_ebur128_0] Summary:

  Integrated loudness:
    I:         -12.5 LUFS
  True peak:
    Peak:      -0.2 dBFS
[Parsed_astats_1] Overall
[Parsed_astats_1] Peak level dB: 0.000000
[Parsed_astats_1] RMS level dB: -15.4
"""
        result = inspect_video_file(
            self.video, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg",
            runner=self._runner(self._probe(), decode_stderr=stderr),
        )
        audio = result["audio_quality"]
        self.assertEqual(audio["status"], "observed")
        self.assertAlmostEqual(audio["integrated_lufs"], -12.5)
        self.assertAlmostEqual(audio["true_peak_dbfs"], -0.2)
        self.assertTrue(audio["clipping_suspected"])
        self.assertAlmostEqual(audio["peak_level_dbfs"], 0.0)
        self.assertEqual(audio["rms_dbfs"], -15.4)
        self.assertEqual({item["code"] for item in result["warnings"]},
                         {"audio_loudness_high", "audio_true_peak_high", "audio_clipping_suspected"})

    def test_audio_quality_is_unavailable_when_ffmpeg_does_not_emit_measurements(self):
        result = inspect_video_file(
            self.video, ffprobe_path="ffprobe", ffmpeg_path="ffmpeg",
            runner=self._runner(self._probe(), decode_stderr="decode completed"),
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["audio_quality"]["status"], "unavailable")
        self.assertEqual(result["audio_quality"]["missing_metrics"],
                         ["integrated_lufs", "true_peak_dbfs", "peak_level_dbfs"])


    def test_maps_qa_warning_intervals_to_overlapping_storyboard_scenes(self):
        warnings = [
            {"code": "frozen_frames", "start_seconds": 3.5, "duration_seconds": 1.0},
            {"code": "frozen_frames", "start_seconds": 4.9, "duration_seconds": 0.5},
            {"code": "frozen_frames", "start_seconds": 5.0, "duration_seconds": 0.25},
        ]
        storyboard = {
            "scenes": [
                {"id": "shot-01", "source_file": "shot-01.mp4", "source_duration_seconds": 2.0},
                {"id": "shot-02", "source_file": "shot-02.mp4", "source_duration_seconds": 3.0},
            ]
        }

        mapped = map_warnings_to_storyboard_scenes(
            warnings, storyboard, timeline_offset_seconds=3.0,
        )

        self.assertEqual([item["scene_id"] for item in mapped[0]["storyboard_matches"]], ["shot-01"])
        self.assertEqual([item["scene_id"] for item in mapped[1]["storyboard_matches"]], ["shot-01", "shot-02"])
        self.assertEqual([item["scene_id"] for item in mapped[2]["storyboard_matches"]], ["shot-02"])
        self.assertAlmostEqual(mapped[1]["storyboard_matches"][0]["overlap_seconds"], 0.1)
        self.assertAlmostEqual(mapped[1]["storyboard_matches"][1]["overlap_seconds"], 0.4)
        self.assertEqual(warnings[0]["start_seconds"], 3.5)

    def test_storyboard_mapping_keeps_unmatched_intro_and_outro_warnings_explicit(self):
        warnings = [
            {"code": "frozen_frames", "start_seconds": 0.5, "duration_seconds": 1.0},
            {"code": "frozen_frames", "start_seconds": 9.5, "duration_seconds": 1.0},
        ]
        storyboard = {
            "scenes": [
                {"id": "shot-01", "source_file": "shot-01.mp4", "duration_seconds": 2.0},
            ]
        }

        mapped = map_warnings_to_storyboard_scenes(
            warnings, storyboard, timeline_offset_seconds=3.0,
        )

        self.assertEqual(mapped[0]["storyboard_matches"], [])
        self.assertEqual(mapped[1]["storyboard_matches"], [])
        self.assertEqual(mapped[0]["mapping_status"], "outside_storyboard")
        self.assertEqual(mapped[1]["mapping_status"], "outside_storyboard")

    def test_storyboard_mapping_rejects_invalid_scene_duration_and_warning_timing(self):
        with self.assertRaisesRegex(ValueError, "scene duration"):
            map_warnings_to_storyboard_scenes(
                [], {"scenes": [{"id": "shot-01", "source_duration_seconds": 0}]},
            )
        with self.assertRaisesRegex(ValueError, "warning interval"):
            map_warnings_to_storyboard_scenes(
                [{"code": "frozen_frames", "start_seconds": -1, "duration_seconds": 1}],
                {"scenes": []},
            )


    def test_cli_attaches_storyboard_mapping_when_requested(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from muse.video_qa import main

        storyboard_path = Path(self.temp.name) / "storyboard.json"
        storyboard_path.write_text(json.dumps({
            "scenes": [
                {"id": "shot-01", "source_file": "shot-01.mp4", "duration_seconds": 2.0},
            ]
        }), encoding="utf-8")
        qa_report = {
            "status": "passed",
            "warnings": [{"code": "frozen_frames", "start_seconds": 3.5, "duration_seconds": 0.5}],
        }
        output = io.StringIO()
        with patch("muse.video_qa.discover_media_tools", return_value=SimpleNamespace(tools=[])), \
             patch("muse.video_qa.inspect_video_file", return_value=qa_report), \
             redirect_stdout(output):
            exit_code = main([
                str(self.video), "--storyboard", str(storyboard_path),
                "--storyboard-offset-seconds", "3",
            ])

        self.assertEqual(exit_code, 0)
        document = json.loads(output.getvalue())
        match = document["storyboard_mapping"][0]["storyboard_matches"][0]
        self.assertEqual(match["scene_id"], "shot-01")
        self.assertEqual(match["source_file"], "shot-01.mp4")
        self.assertEqual(document["storyboard_mapping"][0]["mapping_status"], "mapped")


    def test_storyboard_mapping_ignores_sub_frame_boundary_noise(self):
        mapped = map_warnings_to_storyboard_scenes(
            [{"code": "frozen_frames", "start_seconds": 0.0, "duration_seconds": 1.000001}],
            {"scenes": [
                {"id": "shot-01", "duration_seconds": 1.0},
                {"id": "shot-02", "duration_seconds": 1.0},
            ]},
        )
        self.assertEqual(
            [item["scene_id"] for item in mapped[0]["storyboard_matches"]],
            ["shot-01"],
        )


    def test_storyboard_mapping_leaves_non_temporal_audio_warnings_unmapped(self):
        mapped = map_warnings_to_storyboard_scenes(
            [{"code": "audio_loudness_high", "value": -12.5, "unit": "LUFS"}],
            {"scenes": [{"id": "shot-01", "duration_seconds": 2.0}]},
        )
        self.assertEqual(mapped[0]["mapping_status"], "not_time_based")
        self.assertEqual(mapped[0]["storyboard_matches"], [])


    def test_maps_using_explicit_overlapping_scene_timeline_intervals(self):
        warnings = [{"code": "frozen_frames", "start_seconds": 3.8, "duration_seconds": 0.4}]
        storyboard = {"scenes": [
            {"id": "shot-01", "timeline_start_seconds": 0.0, "timeline_end_seconds": 4.0},
            {"id": "shot-02", "timeline_start_seconds": 3.6, "timeline_end_seconds": 6.6},
        ]}
        mapped = map_warnings_to_storyboard_scenes(warnings, storyboard)
        matches = mapped[0]["storyboard_matches"]
        self.assertEqual([item["scene_id"] for item in matches], ["shot-01", "shot-02"])
        self.assertAlmostEqual(matches[0]["overlap_seconds"], 0.2)
        self.assertAlmostEqual(matches[1]["overlap_seconds"], 0.4)

    def test_rejects_offset_when_explicit_scene_timeline_is_provided(self):
        with self.assertRaisesRegex(ValueError, "must be zero when explicit scene timeline"):
            map_warnings_to_storyboard_scenes(
                [],
                {"scenes": [{"id": "shot-01", "timeline_start_seconds": 3.0,
                             "timeline_end_seconds": 5.0}]},
                timeline_offset_seconds=3.0,
            )

    def test_rejects_partial_explicit_scene_timeline(self):
        with self.assertRaisesRegex(ValueError, "both timeline_start_seconds and timeline_end_seconds"):
            map_warnings_to_storyboard_scenes(
                [], {"scenes": [{"id": "shot-01", "timeline_start_seconds": 1.0, "duration_seconds": 2.0}]},
            )

    def test_cli_adds_advisory_motion_report_without_changing_qa_verdict(self):
        from muse.video_qa import main

        qa_report = {"status": "passed", "warnings": []}
        motion_report = {"status": "observed", "sampled_frames": 12,
                         "summary": {"median_mean_flow_px": 0.3}}
        output = io.StringIO()
        with patch("muse.video_qa.discover_media_tools", return_value=SimpleNamespace(tools=[])), \
             patch("muse.video_qa.inspect_video_file", return_value=qa_report), \
             patch("muse.video_motion_diagnostics.inspect_video_motion", return_value=motion_report) as motion, \
             redirect_stdout(output):
            exit_code = main([str(self.video), "--motion-diagnostics", "--motion-max-frames", "24"])

        self.assertEqual(exit_code, 0)
        motion.assert_called_once_with(str(self.video), max_frames=24)
        document = json.loads(output.getvalue())
        self.assertEqual(document["status"], "passed")
        self.assertEqual(document["motion_diagnostics"], motion_report)


if __name__ == "__main__":
    unittest.main()
