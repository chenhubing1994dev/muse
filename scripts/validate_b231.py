from __future__ import annotations

import unittest


def main() -> int:
    suite = unittest.TestSuite()
    loader = unittest.defaultTestLoader
    for module in (
        "tests.test_subtitle_review",
        "tests.test_video_product_builder",
        "tests.test_status_api",
    ):
        suite.addTests(loader.loadTestsFromName(module))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
