from __future__ import annotations

import hashlib
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

from muse.herdr_core_release import PINNED_HERDR_CORE, verify_release_archive
from muse.herdr_core_smoke import extract_verified_archive, inspect_release_binary


class HerdrCoreReleaseSmokeTests(unittest.TestCase):
    def _archive(self, root: Path, entries: dict[str, bytes]) -> tuple[Path, str]:
        archive_path = root / "herdr-core-test.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        return archive_path, digest

    def test_release_pin_is_exact_stable_windows_x64_artifact(self):
        pin = PINNED_HERDR_CORE
        self.assertEqual(pin.version, "0.1.21")
        self.assertEqual(pin.source_commit, "1b0393f80c35b1dae34f37934d33d0144a8fc8d7")
        self.assertEqual(pin.protocol_generation, 1)
        self.assertEqual(pin.openrpc_version, "1.3.2")
        self.assertEqual(pin.schema_blob_sha, "2048664078012a88f90e49d1819d44f8f1e18b86")
        self.assertEqual(pin.target, "windows-x86_64")
        self.assertEqual(pin.archive_sha256, "a8984634c35b58ba82d0aec078d5a5e6004da01d5a25b3daf822ab5e31c7312b")

    def test_smoke_cli_entrypoint_is_registered(self):
        project_root = Path(__file__).resolve().parents[1]
        with (project_root / "pyproject.toml").open("rb") as source:
            config = tomllib.load(source)
        self.assertEqual(
            config["project"]["scripts"]["muse-herdr-core-smoke"],
            "muse.herdr_core_smoke:main",
        )

    def test_archive_verification_accepts_matching_digest_and_rejects_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            archive, digest = self._archive(Path(directory), {"sample.txt": b"verified"})
            self.assertEqual(verify_release_archive(archive, expected_sha256=digest), digest)
            with self.assertRaises(ValueError):
                verify_release_archive(archive, expected_sha256="0" * 64)

    def test_verified_extraction_requires_binary_and_bundled_conpty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, digest = self._archive(root, {
                "herdr-core.exe": b"binary",
                "conpty/conpty.dll": b"dll",
            })
            executable = extract_verified_archive(archive, root / "extract", expected_sha256=digest)
            self.assertEqual(executable.read_bytes(), b"binary")
            self.assertEqual((root / "extract" / "conpty" / "conpty.dll").read_bytes(), b"dll")

    def test_extraction_rejects_path_traversal_even_for_hash_matched_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, digest = self._archive(root, {
                "../outside.txt": b"no",
                "herdr-core.exe": b"binary",
                "conpty/conpty.dll": b"dll",
            })
            with self.assertRaises(ValueError):
                extract_verified_archive(archive, root / "extract", expected_sha256=digest)
            self.assertFalse((root / "outside.txt").exists())

    def test_extraction_rejects_missing_runtime_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, digest = self._archive(root, {"herdr-core.exe": b"binary"})
            with self.assertRaises(ValueError):
                extract_verified_archive(archive, root / "extract", expected_sha256=digest)

    def test_local_binary_inspection_uses_only_version_and_schema(self):
        calls = []

        class Completed:
            def __init__(self, stdout):
                self.stdout = stdout
                self.stderr = ""
                self.returncode = 0

        def runner(argv, **kwargs):
            calls.append((argv[1:], kwargs))
            if argv[1:] == ["--version"]:
                return Completed("hc 0.1.21\n")
            if argv[1:] == ["schema", "--json"]:
                return Completed('{"openrpc":"1.3.2","info":{"version":"0.1.21"},"methods":[{"name":"rpc.discover"},{"name":"events.subscribe"}]}')
            raise AssertionError(f"unexpected command: {argv}")

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "herdr-core.exe"
            executable.write_bytes(b"test")
            version, methods = inspect_release_binary(executable, runner=runner)

        self.assertEqual(version, "hc 0.1.21")
        self.assertEqual(set(methods), {"rpc.discover", "events.subscribe"})
        self.assertEqual([call[0] for call in calls], [["--version"], ["schema", "--json"]])
        self.assertTrue(all(call[1]["shell"] is False for call in calls))
    def test_local_binary_inspection_rejects_unpinned_openrpc_version(self):
        class Completed:
            def __init__(self, stdout):
                self.stdout = stdout
                self.stderr = ""
                self.returncode = 0

        def runner(argv, **kwargs):
            if argv[1:] == ["--version"]:
                return Completed("hc 0.1.21\n")
            return Completed('{"openrpc":"1.2.6","info":{"version":"0.1.21"},"methods":[{"name":"rpc.discover"},{"name":"events.subscribe"}]}')

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "herdr-core.exe"
            executable.write_bytes(b"test")
            with self.assertRaisesRegex(ValueError, "schema version"):
                inspect_release_binary(executable, runner=runner)



if __name__ == "__main__":
    unittest.main()
