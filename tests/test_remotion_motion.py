from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from muse.remotion_motion import RemotionMotionError, render_motion_package, render_scene_timeline


class RemotionMotionTests(unittest.TestCase):
    def _workspace(self, root: Path) -> tuple[Path, Path, Path]:
        motion = root / "video-motion"
        (motion / "node_modules" / "@remotion" / "cli").mkdir(parents=True)
        (motion / "src").mkdir()
        (motion / "package.json").write_text(json.dumps({"dependencies": {"remotion": "4.0.543"}}))
        (motion / "node_modules" / "@remotion" / "cli" / "remotion-cli.js").write_text("// fixture")
        (motion / "src" / "index.tsx").write_text("// fixture")
        base = root / "base.mp4"
        base.write_bytes(b"base")
        output = root / "final.mp4"
        return root, base, output

    def test_rejects_missing_remotion_install_without_touching_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base.mp4"
            base.write_bytes(b"source")
            with self.assertRaisesRegex(RemotionMotionError, "not installed"):                render_motion_package(
                    root, base, root / "final.mp4", title="Muse", subtitle="Create",
                    width=640, height=360, fps=30, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                )
            self.assertEqual(base.read_bytes(), b"source")

    def test_validates_output_and_canvas_before_launching_node(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, base, output = self._workspace(Path(temporary))
            with patch("muse.remotion_motion.shutil.which", return_value="node"):
                with self.assertRaisesRegex(ValueError, "dimensions"):
                    render_motion_package(
                        root, base, output, title="Muse", subtitle="", width=20, height=360,
                        fps=30, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                    )

    def test_scene_timeline_requires_a_bounded_nonempty_scene_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "between 1 and 40"):
                render_scene_timeline(
                    root, [], root / "audio.mp4", root / "timeline.mp4",
                    width=640, height=360, fps=30,
                    ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                )

    def test_scene_timeline_rejects_symlinked_scene_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scene.mp4"
            source.write_bytes(b"fixture")
            alias = root / "alias.mp4"
            try:
                alias.symlink_to(source)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable in this environment")
            with self.assertRaisesRegex(RemotionMotionError, "non-symlink"):
                render_scene_timeline(
                    root, [alias], root / "audio.mp4", root / "timeline.mp4",
                    width=640, height=360, fps=30,
                    ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                )

    def test_source_and_output_must_be_distinct_and_new(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, base, _ = self._workspace(Path(temporary))
            with patch("muse.remotion_motion.shutil.which", return_value="node"):
                with self.assertRaisesRegex(RemotionMotionError, "new MP4"):
                    render_motion_package(
                        root, base, base, title="Muse", subtitle="", width=640, height=360,
                        fps=30, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe",
                    )


    def test_scene_timeline_validates_per_shot_camera_and_vfx_directives(self):
        from muse.remotion_motion import _validate_shot_directives

        directives = _validate_shot_directives(
            [{"camera_move": "push_in", "vfx": "embers", "accent": "#aa7733"}], 1,
        )
        self.assertEqual(directives[0]["camera_move"], "push_in")
        self.assertEqual(directives[0]["vfx"], "embers")
        self.assertEqual(
            _validate_shot_directives(None, 2),
            [
                {"camera_move": "none", "vfx": "none", "accent": "#d8b46a"},
                {"camera_move": "none", "vfx": "none", "accent": "#d8b46a"},
            ],
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _validate_shot_directives([], 1)
        with self.assertRaisesRegex(ValueError, "camera_move"):
            _validate_shot_directives([{"camera_move": "shake"}], 1)
        with self.assertRaisesRegex(ValueError, "camera_move"):
            _validate_shot_directives([{"camera_move": []}], 1)
        with self.assertRaisesRegex(ValueError, "vfx"):
            _validate_shot_directives([{"vfx": []}], 1)
        with self.assertRaisesRegex(ValueError, "hex color"):
            _validate_shot_directives([{"accent": "gold"}], 1)

if __name__ == "__main__":
    unittest.main()
