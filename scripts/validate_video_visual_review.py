from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

loader = unittest.TestLoader()
suite = unittest.TestSuite()
suite.addTests(loader.loadTestsFromName("test_video_human_review"))
suite.addTests(loader.loadTestsFromName("test_video_publish_package.VideoPublishPackageTests.test_prior_package_visual_review_is_not_inherited_by_next_release_package"))
suite.addTests(loader.loadTestsFromName("test_video_recipe_trace.VideoRecipeTraceTests.test_succeeded_product_and_publish_pack_are_linked_but_never_authorize_publish"))
suite.addTests(loader.loadTestsFromName("test_status_api.StatusApiTests.test_visual_review_post_and_get_are_authenticated_hash_bound_and_append_only"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
print(f"VISUAL_REVIEW_TESTS_RUN={result.testsRun}")
print(f"VISUAL_REVIEW_TESTS_FAILED={len(result.failures) + len(result.errors)}")
raise SystemExit(0 if result.wasSuccessful() and result.testsRun == 12 else 1)