"""Structural guardrails for the local PROMETHEUS master-film plan."""
from __future__ import annotations

import runpy
import unittest
from pathlib import Path


MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "muse-prometheus-film.py"))
SCENES = MODULE["SCENES"]
ORDER = MODULE["ORDER"]


class PrometheusFilmPlanTests(unittest.TestCase):
    def test_ai_scene_ids_and_seeds_are_unique(self):
        ids = [scene["id"] for scene in SCENES]
        seeds = [scene["seed"] for scene in SCENES]
        self.assertEqual(len(ids), 15)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(seeds), len(set(seeds)))

    def test_final_order_contains_every_ai_scene_and_one_real_dashboard_capture(self):
        expected = {scene["id"] for scene in SCENES} | {"dashboard"}
        self.assertEqual(len(ORDER), 16)
        self.assertEqual(len(ORDER), len(set(ORDER)))
        self.assertEqual(set(ORDER), expected)

    def test_future_vision_is_explicitly_labeled(self):
        future = next(scene for scene in SCENES if scene["id"] == "future")
        self.assertEqual(future["role"], "future_vision")
        self.assertIn("未来愿景", future["caption"])
        self.assertIn("规划中", future["caption"])

    def test_master_film_duration_fits_60_to_90_seconds(self):
        target = len(ORDER) * MODULE["SCENE_SECONDS"]
        self.assertGreaterEqual(target, 60)
        self.assertLessEqual(target, 90)
        self.assertGreater(MODULE["AUDIO_SECONDS"], target)


if __name__ == "__main__":
    unittest.main()
