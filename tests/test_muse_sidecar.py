"""Tests for the desktop sidecar gateway (``src/muse_sidecar_main.py``)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import muse_sidecar_main as sidecar  # noqa: E402


class DataDirTests(unittest.TestCase):
    def test_platform_defaults(self) -> None:
        home = Path("/h")
        with tempfile.TemporaryDirectory() as exe_dir:
            exe = Path(exe_dir)
            self.assertEqual(sidecar.default_data_dir(platform="win32", env={"LOCALAPPDATA": "/la"}, exe_dir=exe,
                                                      home=home), Path("/la") / "Muse")
            self.assertEqual(sidecar.default_data_dir(platform="darwin", env={}, exe_dir=exe, home=home),
                             home / "Library" / "Application Support" / "com.muse.consciousness")
            self.assertEqual(sidecar.default_data_dir(platform="linux", env={}, exe_dir=exe, home=home),
                             home / ".local" / "share" / "muse")
            self.assertEqual(sidecar.default_data_dir(platform="linux", env={"XDG_DATA_HOME": "/x"}, exe_dir=exe,
                                                      home=home), Path("/x") / "muse")

    def test_portable_marker_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as exe_dir:
            exe = Path(exe_dir)
            (exe / sidecar.PORTABLE_MARKER).write_text("portable", encoding="utf-8")
            self.assertEqual(sidecar.default_data_dir(platform="win32", env={}, exe_dir=exe), exe / "muse-data")
            self.assertEqual(sidecar.default_data_dir(platform="win32", env={"MUSE_DATA_DIR": "/o"}, exe_dir=exe),
                             Path("/o"))


class StaticPathTests(unittest.TestCase):
    def test_traversal_rejected_and_pages_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            (dist / "assets").mkdir(parents=True)
            (dist / "index.html").write_text("<html></html>", encoding="utf-8")
            (dist / "review-desk.html").write_text("<html></html>", encoding="utf-8")
            (dist / "assets" / "a.js").write_text("1", encoding="utf-8")
            (Path(tmp) / "secret.txt").write_text("x", encoding="utf-8")
            self.assertEqual(sidecar.safe_static_path(dist, "/"), (dist / "index.html").resolve())
            self.assertEqual(sidecar.safe_static_path(dist, "/review-desk"), (dist / "review-desk.html").resolve())
            self.assertEqual(sidecar.safe_static_path(dist, "/assets/a.js?v=1"), (dist / "assets" / "a.js").resolve())
            self.assertIsNone(sidecar.safe_static_path(dist, "/../secret.txt"))
            self.assertIsNone(sidecar.safe_static_path(dist, "/..%2fsecret.txt"))
            self.assertIsNone(sidecar.safe_static_path(dist, "/missing.js"))


class NonceTests(unittest.TestCase):
    def test_launch_nonce_is_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = sidecar.MuseEngine(data_root=Path(tmp), launch_nonce="n" * 32)
            self.assertFalse(engine.consume_nonce("wrong"))
            self.assertTrue(engine.consume_nonce("n" * 32))
            self.assertFalse(engine.consume_nonce("n" * 32))


class SelfTestIntegration(unittest.TestCase):
    def test_isolated_self_test_passes(self) -> None:
        lines: list[str] = []
        self.assertEqual(sidecar.run_self_test(require_ui=False, emit=lines.append), 0, lines)
        self.assertIn('"self_test": "passed"', lines[-1])

if __name__ == "__main__":
    unittest.main()
