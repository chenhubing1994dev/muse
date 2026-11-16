from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.openfilm_adapter import OpenFilmExportError, export_openfilm_project


class OpenFilmAdapterTests(unittest.TestCase):
    def test_exports_editable_multitrack_project_and_hash_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            footage, voice, music = root / 'shot.mp4', root / 'voice.wav', root / 'music.wav'
            footage.write_bytes(b'video-fixture')
            voice.write_bytes(b'voice-fixture')
            music.write_bytes(b'music-fixture')
            output = root / 'openfilm-candidate'
            result = export_openfilm_project(output, title='纸鹤渡命人', width=1080, height=1920, fps=24,
                shots=[{'id':'shot-001','source':footage,'at':0,'duration':4,'title':'第九十九只纸鹤'}],
                audio_tracks=[{'source':voice,'kind':'narration','at':0,'duration':4},
                              {'source':music,'kind':'music','at':0,'duration':4,'volume':0.4}])
            film = (output / 'film.html').read_text(encoding='utf-8')
            manifest = json.loads((output / 'muse-openfilm-manifest.json').read_text(encoding='utf-8'))
            self.assertIn('<video', film)
            self.assertIn('<iframe', film)
            self.assertIn('<audio', film)
            self.assertEqual(result['asset_count'], 3)
            self.assertEqual(result['duration_seconds'], 4.0)
            self.assertEqual(manifest['tracks'], ['titles','footage','narration','music'])
            self.assertEqual(len(result['manifest_sha256']), 64)

    def test_rejects_overlapping_footage_clips_on_same_track(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'shot.mp4'
            source.write_bytes(b'video')
            with self.assertRaisesRegex(OpenFilmExportError, 'overlap'):
                export_openfilm_project(root / 'out', shots=[
                    {'id':'one','source':source,'at':0,'duration':3},
                    {'id':'two','source':source,'at':2,'duration':3}])

    def test_overlapping_audio_clips_are_placed_on_separate_tracks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            footage, voice, music = root / 'shot.mp4', root / 'voice.wav', root / 'music.wav'
            footage.write_bytes(b'video')
            voice.write_bytes(b'voice')
            music.write_bytes(b'music')
            output = root / 'out'
            export_openfilm_project(output, shots=[{'source':footage,'at':0,'duration':4}],
                audio_tracks=[{'source':voice,'kind':'narration','at':0,'duration':4},
                              {'source':music,'kind':'music','at':0,'duration':4}])
            film = (output / 'film.html').read_text(encoding='utf-8')
            self.assertEqual(film.count('<section>'), 3)

    def test_refuses_to_overwrite_existing_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'shot.mp4'
            source.write_bytes(b'video')
            output = root / 'existing'
            output.mkdir()
            (output / 'film.html').write_text('human edit')
            with self.assertRaisesRegex(OpenFilmExportError, 'new or empty'):
                export_openfilm_project(output, shots=[{'source':source,'at':0,'duration':2}])
            self.assertEqual((output / 'film.html').read_text(), 'human edit')

    def test_rejects_invalid_timing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'shot.mp4'
            source.write_bytes(b'video')
            with self.assertRaisesRegex(OpenFilmExportError, 'duration'):
                export_openfilm_project(root / 'out', shots=[{'source':source,'at':0,'duration':-1}])


if __name__ == '__main__':
    unittest.main()