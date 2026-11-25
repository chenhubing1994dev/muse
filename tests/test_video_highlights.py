from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from muse.video_highlights import (
    analyze_video_audio,
    analyze_video_with_cantonese_asr,
    build_vertical_clip_command,
    render_vertical_clip,
    score_audio_windows,
    select_highlight_candidates,
    write_candidate_srt,
)

class AudioHighlightScoringTests(unittest.TestCase):
    def test_loudness_jump_scores_against_trailing_local_baseline(self):
        levels = [-30.0] * 20 + [-10.0, -9.0, -30.0]
        scored = score_audio_windows(levels, baseline_seconds=10)
        self.assertGreater(scored[20]["score"], 0.8)
        self.assertEqual(scored[0]["score"], 0.0)
        self.assertEqual(scored[22]["score"], 0.0)

    def test_optional_shout_signal_is_validated_and_combined(self):
        scored = score_audio_windows([-30.0] * 5, shout_scores=[0.0, 0.0, 0.0, 0.0, 1.0])
        self.assertEqual(scored[-1]["score"], 0.2)
        with self.assertRaisesRegex(ValueError, "match"):
            score_audio_windows([-30.0] * 5, shout_scores=[0.1])
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            score_audio_windows([-30.0] * 5, shout_scores=[0, 0, 0, 0, 2])

    def test_optional_2_4khz_prominence_is_normalized_and_weighted(self):
        levels = [-30.0] * 8
        band = [-80.0] * 7 + [-30.0]
        baseline = score_audio_windows(levels)
        enriched = score_audio_windows(levels, band_energy_db=band)
        self.assertEqual(baseline[-1]["score"], 0.0)
        self.assertNotIn("band_energy_2_4khz_db", baseline[-1])
        self.assertIn("band_energy_2_4khz_db", enriched[-1])

        self.assertAlmostEqual(enriched[-1]["band_prominence_2_4khz"], 1.0)
        self.assertAlmostEqual(enriched[-1]["score"], 0.15)
        self.assertEqual(enriched[0]["band_prominence_2_4khz"], 0.0)
        with self.assertRaisesRegex(ValueError, "match"):
            score_audio_windows(levels, band_energy_db=[-50.0])

    def test_candidate_selection_is_non_overlapping_and_sentence_snapped(self):
        windows = [
            {"second": 50.0, "score": 0.95, "loudness_jump": 0.9},
            {"second": 53.0, "score": 0.90, "loudness_jump": 0.8},
            {"second": 110.0, "score": 0.80, "loudness_jump": 0.7},
        ]
        words = [
            {"start": 38.0, "end": 39.0, "text": "精彩內容。"},
            {"start": 67.0, "end": 68.0, "text": "下一段。"},
            {"start": 98.0, "end": 99.0, "text": "結束。"},
        ]
        result = select_highlight_candidates(
            windows, 140.0, count=2, min_length_seconds=20,
            max_length_seconds=60, words=words,
        )
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["end"] <= result[1]["start"] or result[1]["end"] <= result[0]["start"])
        self.assertEqual(result[0]["start"], 38.0)
        self.assertEqual(result[0]["end"], 68.0)

    def test_invalid_transcript_timestamps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid timestamps"):
            select_highlight_candidates(
                [{"second": 10.0, "score": 1.0}], 60.0,
                words=[{"start": 2.0, "end": 1.0, "text": "bad"}],
            )

    def test_candidate_bounds_are_validated(self):
        with self.assertRaises(ValueError):
            select_highlight_candidates([], 0)
        with self.assertRaises(ValueError):
            score_audio_windows([])


class CantoneseHighlightIntegrationTests(unittest.TestCase):
    def test_candidate_srt_uses_clip_relative_word_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "clip.srt"
            words = [
                {"start": 9.5, "end": 10.0, "text": "開場"},
                {"start": 10.1, "end": 10.6, "text": "精彩。"},
                {"start": 11.0, "end": 11.4, "text": "下一句"},
                {"start": 15.0, "end": 15.5, "text": "完。"},
            ]
            result = write_candidate_srt(words, 10.0, 15.0, target)
            text = target.read_text(encoding="utf-8-sig")
            self.assertEqual(result["word_count"], 2)
            self.assertIn("00:00:00,100 --> 00:00:00,600", text)
            self.assertNotIn("開場", text)

    def test_rank_pipeline_reuses_local_cantonese_word_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"source")
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                if command[0] == "ffprobe":
                    return SimpleNamespace(returncode=0, stdout=json.dumps({
                        "format": {"duration": "50.0"},
                        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
                    }), stderr="")
                if "-af" in command:
                    levels = [-30.0] * 20 + [-10.0] + [-30.0] * 29
                    stderr = "".join(
                        f"frame:{index} lavfi.astats.Overall.RMS_level={level}\\n"
                        for index, level in enumerate(levels)
                    )
                    return SimpleNamespace(returncode=0, stdout="", stderr=stderr)
                Path(command[-1]).write_bytes(b"wav")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            def transcriber(audio_path, **kwargs):
                self.assertTrue(Path(audio_path).is_file())
                return {
                    "engine": "faster-whisper-local",
                    "transcript": "粵語測試精彩內容。",
                    "segments": [{
                        "start": 8.0, "end": 44.0, "text": "粵語測試精彩內容。",
                        "words": [
                            {"start": 8.0, "end": 9.0, "text": "開頭"},
                            {"start": 12.0, "end": 13.0, "text": "精彩"},
                            {"start": 20.0, "end": 21.0, "text": "內容。"},
                            {"start": 40.0, "end": 41.0, "text": "收尾。"},
                        ],
                    }],
                }

            result = analyze_video_with_cantonese_asr(
                source, model_dir=Path(directory) / "model", runner=runner,
                transcriber=transcriber,
            )
            self.assertEqual(result["analysis_mode"], "audio_plus_local_cantonese_asr")
            self.assertEqual(result["transcript_engine"], "faster-whisper-local")
            self.assertTrue(result["candidates"])
            self.assertTrue(all(item["subtitle_timing"] == "word" for item in result["candidates"]))
            self.assertTrue(any("-ac" in command for command in calls))


class VerticalClipCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source")
        self.output = self.root / "clip.mp4"
        self.subtitles = self.root / "captions.srt"
        self.subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n測試\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_builds_9x16_command_with_optional_burned_in_subtitles_and_amf(self):
        command = build_vertical_clip_command(
            self.source, self.output, 10, 40, subtitles=self.subtitles,
            available_encoders=("h264_amf",),
        )
        self.assertIn("h264_amf", command)
        self.assertIn("-n", command)
        self.assertIn("1080:1920", ",".join(command))
        self.assertIn("subtitles=filename=", ",".join(command))
        self.assertEqual(command[-1], str(self.output))

    def test_optional_face_center_uses_a_clamped_crop_expression(self):
        command = build_vertical_clip_command(
            self.source, self.output, 0, 30, crop_center_x=0.75,
        )
        filters = command[command.index("-vf") + 1]
        self.assertIn("crop=1080:1920:max(0\\,min(iw-1080\\,iw*0.750000-540)):0", filters)
        with self.assertRaisesRegex(ValueError, "crop_center_x"):
            build_vertical_clip_command(self.source, self.output, 0, 30, crop_center_x=1.5)

    def test_dynamic_face_keyframes_generate_per_frame_crop_expression(self):
        command = build_vertical_clip_command(
            self.source, self.output, 0, 4,
            crop_center_keyframes=[
                {"time_seconds": 0.0, "center_x": 0.25},
                {"time_seconds": 2.0, "center_x": 0.75},
                {"time_seconds": 4.0, "center_x": 0.4},
            ],
        )
        filters = command[command.index("-vf") + 1]
        self.assertIn("crop=1080:1920:'max(0,min(iw-1080,iw*", filters)
        self.assertIn("if(lt(t,2.000000)", filters)
        self.assertIn("0.250000+(0.500000)*(t-0.000000)/2.000000", filters)
        self.assertIn("0.750000+(-0.350000)*(t-2.000000)/2.000000", filters)
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            build_vertical_clip_command(
                self.source, self.output, 0, 4, crop_center_x=0.5,
                crop_center_keyframes=[
                    {"time_seconds": 0.0, "center_x": 0.5},
                    {"time_seconds": 4.0, "center_x": 0.5},
                ],
            )

    def test_uses_software_fallback_and_rejects_overwrite(self):
        command = build_vertical_clip_command(self.source, self.output, 0, 30)
        self.assertIn("libx264", command)
        self.output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            build_vertical_clip_command(self.source, self.output, 0, 30)

    def test_rejects_bad_ranges_and_non_srt_subtitles(self):
        with self.assertRaises(ValueError):
            build_vertical_clip_command(self.source, self.output, 20, 10)
        bad = self.root / "captions.txt"
        bad.write_text("caption", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SRT"):
            build_vertical_clip_command(self.source, self.output, 0, 30, subtitles=bad)

    def test_audio_analysis_uses_ffprobe_then_audio_only_ffmpeg(self):
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            if command[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "format": {"duration": "5.0"},
                    "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
                }), stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr=(
                "frame:0 lavfi.astats.Overall.RMS_level=-inf\\n"
                "frame:1 lavfi.astats.Overall.RMS_level=-28.0\\n"
                "frame:2 lavfi.astats.Overall.RMS_level=-10.0\\n"
                "frame:3 lavfi.astats.Overall.RMS_level=-11.0\\n"
                "frame:4 lavfi.astats.Overall.RMS_level=-30.0\\n"
            ))
        result = analyze_video_audio(self.source, runner=runner)
        self.assertEqual(result["analysis_mode"], "audio_only")
        self.assertEqual(result["window_count"], 5)
        self.assertIn("-vn", calls[1])
        self.assertIn("astats=metadata=1", calls[1][calls[1].index("-af") + 1])




    def test_optional_spectral_analysis_runs_a_second_audio_only_bandpass(self):
        calls = []
        rms_output = " ".join(
            f"lavfi.astats.Overall.RMS_level={value}"
            for value in ([-30.0] * 4 + [-12.0])
        )
        band_output = " ".join(
            f"lavfi.astats.Overall.RMS_level={value}"
            for value in ([-80.0] * 4 + [-25.0])
        )

        def runner(command, **kwargs):
            calls.append(command)
            if command[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    "format": {"duration": "5.0"},
                    "streams": [{"codec_type": "audio"}],
                }), stderr="")
            output = band_output if "highpass=f=2000" in command[command.index("-af") + 1] else rms_output
            return SimpleNamespace(returncode=0, stdout="", stderr=output)

        result = analyze_video_audio(self.source, spectral_2_4khz=True, runner=runner)
        self.assertTrue(result["spectral_feature_2_4khz"])
        self.assertEqual(len(calls), 3)
        self.assertIn("-vn", calls[2])
        self.assertIn("lowpass=f=4000", calls[2][calls[2].index("-af") + 1])
        self.assertGreater(result["windows"][-1]["band_prominence_2_4khz"], 0.9)

    def test_render_preflights_source_and_verifies_output_probe(self):
        calls = []
        source_metadata = {
            "format": {"duration": "60", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                 "avg_frame_rate": "30/1", "r_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                 "channels": 2, "channel_layout": "stereo"},
            ],
        }
        output_metadata = {
            "format": {"duration": "30", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
                 "avg_frame_rate": "30/1", "r_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
                 "channels": 2, "channel_layout": "stereo"},
            ],
        }

        def runner(command, **kwargs):
            calls.append(command)
            if command[0] == "ffprobe":
                metadata = source_metadata if Path(command[-1]) == self.source else output_metadata
                return SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr="")
            if command[0] == "ffmpeg":
                self.output.write_bytes(b"rendered")
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            raise AssertionError("unexpected executable")

        result = render_vertical_clip(
            self.source, self.output, 0, 30, ffmpeg_path="ffmpeg",
            ffprobe_path="ffprobe", crop_center_x=0.75, runner=runner,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source_probe"]["duration_seconds"], 60.0)
        self.assertEqual(result["output_probe"]["video_codec"], "h264")
        self.assertEqual(result["framing"]["mode"], "face_aware_clip_center")
        self.assertIn("iw*0.750000-540", calls[1][calls[1].index("-vf") + 1])
        self.assertEqual([command[0] for command in calls], ["ffprobe", "ffmpeg", "ffprobe"])

    def test_render_amf_requires_real_encoder_probe_and_shared_gpu_lease(self):
        from muse.gpu_resources import GpuResourceCoordinator
        database = self.root / "gpu-resources.sqlite3"
        source_metadata = {
            "format": {"duration": "60", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                         "avg_frame_rate": "30/1", "r_frame_rate": "30/1"}],
        }
        output_metadata = {
            "format": {"duration": "30", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
                         "avg_frame_rate": "30/1", "r_frame_rate": "30/1"}],
        }
        def runner(command, **kwargs):
            if command[0] == "ffprobe":
                metadata = source_metadata if Path(command[-1]) == self.source else output_metadata
                return SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr="")
            lease = GpuResourceCoordinator(database).active_lease()
            self.assertEqual(lease["owner_kind"], "video_encoding")
            self.output.write_bytes(b"rendered")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("muse.video_capabilities.probe_ffmpeg_encoder", return_value={
            "health": {"status": "healthy"}, "capability": {"status": "available", "encoder": "h264_amf"},
            "ready": True,
        }), patch("muse.gpu_resources.probe_gpu_workloads", return_value={"status": "unavailable"}):
            result = render_vertical_clip(
                self.source, self.output, 0, 30, available_encoders=("h264_amf",),
                runner=runner, gpu_resource_db=database,
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["encoder"], "h264_amf")
        self.assertEqual(result["encoder_capability"]["capability"]["status"], "available")
        self.assertIsNone(GpuResourceCoordinator(database).active_lease())

    def test_render_refuses_clip_range_beyond_probed_source_without_running_ffmpeg(self):
        calls = []
        metadata = {
            "format": {"duration": "25", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}],
        }

        def runner(command, **kwargs):
            calls.append(command)
            return SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr="")

        result = render_vertical_clip(
            self.source, self.output, 0, 30, ffmpeg_path="ffmpeg",
            ffprobe_path="ffprobe", runner=runner,
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "ffprobe")
        self.assertFalse(self.output.exists())


    def test_render_cleans_partial_output_after_timeout(self):
        metadata = {
            "format": {"duration": "40"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}],
        }

        def runner(command, **kwargs):
            if command[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr="")
            if command[0] == "ffmpeg":
                self.output.write_bytes(b"partial")
                raise subprocess.TimeoutExpired(command, 1)
            raise AssertionError("unexpected executable")

        result = render_vertical_clip(
            self.source, self.output, 0, 30, ffmpeg_path="ffmpeg",
            ffprobe_path="ffprobe", runner=runner,
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.output.exists())
if __name__ == "__main__":
    unittest.main()
