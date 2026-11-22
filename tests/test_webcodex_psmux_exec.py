from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import webcodex_psmux_exec as adapter


@unittest.skipUnless(sys.platform == "win32", "WebCodex PSMUX executes Windows PowerShell jobs")
class PsmuxExecAdapterTests(unittest.TestCase):
    def test_build_job_script_redirects_all_streams_and_records_exit_code(self):
        output = Path("C:/tmp/output.log")
        result = Path("C:/tmp/result.txt")
        script = adapter._build_job_script("Write-Output 'hello'\ncmd.exe /c exit 7", output, result)
        self.assertIn("Write-Output 'hello'", script)
        self.assertIn("cmd.exe /c exit 7", script)
        self.assertIn("*> 'C:/tmp/output.log'", script)
        self.assertIn("[string]$museExitCode", script)
        self.assertIn("exit $museExitCode", script)

    def test_inline_powershell_is_isolated_in_a_child_process(self):
        wrapper = adapter._build_job_script(
            "exit 7", Path("C:/tmp/output.log"), Path("C:/tmp/result.txt"),
            language="powershell", payload_path=Path("C:/tmp/payload.ps1"),
        )
        self.assertIn("powershell.exe' -NoLogo -NoProfile", wrapper)
        self.assertIn("-File 'C:/tmp/payload.ps1'", wrapper)
        self.assertIn("[string]$museExitCode", wrapper)

    def test_existing_powershell_file_is_invoked_by_path(self):
        wrapper = adapter._build_job_script(
            "", Path("C:/tmp/output.log"), Path("C:/tmp/result.txt"),
            language="powershell", payload_path=Path("D:/AI AGENT/Muse/scripts/check.ps1"),
        )
        self.assertIn("-NoProfile -File 'D:/AI AGENT/Muse/scripts/check.ps1'", wrapper)

    def test_existing_python_file_is_resolved_inside_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            script_path = Path(directory) / "check.py"
            script_path.write_text("print('ok')", encoding="utf-8")
            source, language, resolved = adapter._resolve_script_request(
                {"script_path": "check.py"}, directory
            )
        self.assertEqual(source, "")
        self.assertEqual(language, "python")
        self.assertEqual(resolved, script_path.resolve())

    def test_native_job_wrapper_keeps_stderr_from_overriding_exit_code(self):
        wrapper = adapter._build_job_script(
            "print('hello')", Path("C:/tmp/output.log"), Path("C:/tmp/result.txt"),
            language="python", payload_path=Path("C:/tmp/payload.py"),
        )
        self.assertIn("$ErrorActionPreference = 'Continue'", wrapper)

    def test_build_python_job_script_uses_current_interpreter_and_payload_file(self):
        output = Path("C:/tmp/output.log")
        result = Path("C:/tmp/result.txt")
        payload = Path("C:/tmp/payload.py")
        wrapper = adapter._build_job_script(
            "print('hello')", output, result, language="python", payload_path=payload
        )
        self.assertIn(adapter._ps_quote(adapter.sys.executable), wrapper)
        self.assertIn("$env:PYTHONPATH", wrapper)
        self.assertIn("D:/AI AGENT/Muse/src", wrapper)
        self.assertIn(adapter._ps_quote(payload.as_posix()), wrapper)
        self.assertIn("*> 'C:/tmp/output.log'", wrapper)

    def test_build_bash_job_script_uses_resolved_bash_executable(self):
        output = Path("C:/tmp/output.log")
        result = Path("C:/tmp/result.txt")
        payload = Path("C:/tmp/payload.sh")
        with patch.object(adapter, "_git_bash_executable", return_value="C:/Program Files/Git/bin/bash.exe"):
            wrapper = adapter._build_job_script(
                "echo hello", output, result, language="bash", payload_path=payload
            )
        self.assertIn("'C:/Program Files/Git/bin/bash.exe'", wrapper)
        self.assertIn("'C:/tmp/payload.sh'", wrapper)

    def test_build_bash_job_script_falls_back_to_wsl_with_project_cwd(self):
        output = Path("C:/tmp/output.log")
        result = Path("C:/tmp/result.txt")
        payload = Path("C:/tmp/payload.sh")
        with patch.object(adapter, "_git_bash_executable", return_value=None), patch.object(
            adapter, "_wsl_executable", return_value="C:/Windows/System32/wsl.exe"
        ):
            wrapper = adapter._build_job_script(
                "echo hello", output, result, language="bash", payload_path=payload,
                working_directory=r"D:\AI AGENT\Muse",
            )
        self.assertIn("--exec bash -lc", wrapper)
        self.assertIn("/mnt/d/AI AGENT/Muse", wrapper)
        self.assertIn("/mnt/c/tmp/payload.sh", wrapper)

    def test_windows_to_wsl_path_preserves_drive_and_spaces(self):
        self.assertEqual(adapter._windows_to_wsl_path(r"D:\AI AGENT\Muse"), "/mnt/d/AI AGENT/Muse")

    def test_build_batch_job_script_uses_cmd_without_autorun(self):
        output = Path("C:/tmp/output.log")
        result = Path("C:/tmp/result.txt")
        payload = Path("C:/tmp/payload.bat")
        with patch.dict("os.environ", {"ComSpec": "C:/Windows/System32/cmd.exe"}):
            wrapper = adapter._build_job_script(
                "@echo off & echo hello", output, result, language="batch", payload_path=payload
            )
        self.assertIn("'C:/Windows/System32/cmd.exe'", wrapper)
        self.assertIn("/d /c", wrapper)
        self.assertIn("call", wrapper)
        self.assertIn("'C:/tmp/payload.bat'", wrapper)

    def test_script_path_infers_language_and_resolves_relative_to_project(self):
        with tempfile.TemporaryDirectory() as directory:
            script_path = Path(directory) / "check.py"
            script_path.write_text("print('ok')", encoding="utf-8")
            source, language, resolved = adapter._resolve_script_request(
                {"script_path": "check.py"}, directory
            )
        self.assertEqual(source, "")
        self.assertEqual(language, "python")
        self.assertEqual(resolved, script_path.resolve())

    def test_script_path_infers_all_supported_extensions_case_insensitively(self):
        expected = {
            ".ps1": "powershell", ".py": "python", ".sh": "bash",
            ".bat": "batch", ".cmd": "batch",
        }
        with tempfile.TemporaryDirectory() as directory:
            for suffix, language in expected.items():
                script_path = Path(directory) / f"check{suffix.upper()}"
                script_path.write_text("# probe", encoding="utf-8")
                _, inferred, resolved = adapter._resolve_script_request(
                    {"script_path": script_path.name}, directory
                )
                self.assertEqual(inferred, language)
                self.assertEqual(resolved, script_path.resolve())

    def test_script_path_cannot_escape_project_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            script_path = Path(outside) / "outside.py"
            script_path.write_text("print('no')", encoding="utf-8")
            with self.assertRaisesRegex(adapter.PsmuxError, "script_path_outside_project_root"):
                adapter._resolve_script_request({"script_path": str(script_path)}, root)

    def test_script_and_script_path_cannot_be_combined(self):
        with tempfile.TemporaryDirectory() as root:
            script_path = Path(root) / "check.py"
            script_path.write_text("print('ok')", encoding="utf-8")
            with self.assertRaisesRegex(adapter.PsmuxError, "mutually_exclusive"):
                adapter._resolve_script_request(
                    {"script": "print('inline')", "script_path": str(script_path)}, root
                )

    def test_script_language_aliases_are_accepted_and_unknown_languages_rejected(self):
        result = adapter.execute_request({"script": "echo hello", "language": "ruby"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "unsupported_language")


    def test_ps_quote_escapes_single_quotes(self):
        self.assertEqual(adapter._ps_quote("C:/a'b/file.ps1"), "'C:/a''b/file.ps1'")

    def test_build_psmux_argv_uses_detached_window_and_direct_argv(self):
        argv = adapter.build_psmux_argv(
            "psmux.exe",
            "muse-dev",
            "musejob_123",
            r"D:\AI AGENT\Muse",
            r"C:\Users\Administrator\AppData\Local\Muse\psmux-jobs\job.ps1",
        )
        self.assertEqual(argv[1:9], [
            "new-window", "-d", "-t", "muse-dev", "-n", "musejob_123",
            "-c", r"D:\AI AGENT\Muse",
        ])
        self.assertIn("--", argv)
        self.assertIn("-File", argv)
        self.assertNotIn("powershell.exe -File", argv)

    def test_read_output_decodes_windows_powershell_utf16le_redirection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output.log"
            path.write_bytes(b"\xff\xfe" + "Muse 中文".encode("utf-16-le"))
            output, truncated = adapter._read_output(path)
        self.assertEqual(output, "Muse 中文")
        self.assertFalse(truncated)

    def test_recover_clears_active_job_record_after_completion(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOCALAPPDATA": directory}):
            job_id = "a" * 32
            job_dir = adapter._job_root() / job_id
            job_dir.mkdir(parents=True)
            (job_dir / "result.txt").write_text("0", encoding="utf-8")
            (job_dir / "payload.py").write_text("print('ok')", encoding="utf-8")
            (job_dir / "payload.ps1").write_text("exit 7", encoding="utf-8-sig")
            adapter._write_active_job("muse-dev", job_id, "musejob_aaaaaaaaaaaa")
            result = adapter.recover_job(job_id)
            self.assertEqual(result["status"], "completed")
            self.assertFalse((job_dir / "payload.py").exists())
            self.assertFalse((job_dir / "payload.ps1").exists())
            self.assertIsNone(adapter._active_job_id("muse-dev"))

    def test_list_jobs_reports_completed_active_and_untracked_without_mutating(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOCALAPPDATA": directory}):
            root = adapter._job_root()
            completed_id = "a" * 32
            active_id = "b" * 32
            untracked_id = "c" * 32
            for job_id in (completed_id, active_id, untracked_id):
                (root / job_id).mkdir(parents=True)
            (root / completed_id / "result.txt").write_text("7", encoding="utf-8")
            (root / completed_id / "output.log").write_text("completed output", encoding="utf-8")
            active_path = adapter._active_path("muse-dev")
            adapter._write_active_job("muse-dev", active_id, "musejob_bbbbbbbbbbbb")
            lock_path = root / "muse-dev.lock"
            lock_path.write_bytes(b"lock sentinel")

            result = adapter.list_jobs({"session": "muse-dev", "limit": 10})
            by_id = {item["job_id"]: item for item in result["jobs"]}
            self.assertEqual(result["status"], "completed")
            self.assertEqual(by_id[completed_id]["status"], "completed")
            self.assertEqual(by_id[completed_id]["exit_code"], 7)
            self.assertEqual(by_id[active_id]["status"], "outcome_unknown")
            self.assertTrue(by_id[active_id]["active"])
            self.assertEqual(by_id[untracked_id]["status"], "incomplete_untracked")
            self.assertEqual(lock_path.read_bytes(), b"lock sentinel")
            self.assertEqual(result["concurrency"]["policy"], "one_in_flight_job_per_session")
            self.assertEqual(result["concurrency"]["lock_probe"], "not_performed_read_only")
            self.assertEqual(lock_path.read_bytes(), b"lock sentinel")
            self.assertTrue(active_path.is_file())

    def test_list_jobs_rejects_invalid_limit(self):
        result = adapter.list_jobs({"limit": 0})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "limit_must_be_1_to_100")

    def test_job_manifest_is_written_and_listed_with_recovery_guidance(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOCALAPPDATA": directory}):
            job_id = "d" * 32
            job_dir = adapter._job_root() / job_id
            job_dir.mkdir(parents=True)
            adapter._write_job_manifest(job_id, {
                "job_id": job_id, "session": "muse-dev", "language": "python",
                "working_directory": r"D:\\AI AGENT\\Muse", "status": "launching", "created_at": 123.0,
            })
            result = adapter.list_jobs({"session": "muse-dev", "limit": 10})
            item = next(row for row in result["jobs"] if row["job_id"] == job_id)
            self.assertEqual(item["session"], "muse-dev")
            self.assertEqual(item["language"], "python")
            self.assertEqual(item["next_action"], "inspect_before_retry; process_state_unknown")

    def test_recover_marks_manifest_completed_with_real_exit_code(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOCALAPPDATA": directory}):
            job_id = "e" * 32
            job_dir = adapter._job_root() / job_id
            job_dir.mkdir(parents=True)
            (job_dir / "result.txt").write_text("9", encoding="utf-8")
            adapter._write_job_manifest(job_id, {"job_id": job_id, "session": "muse-dev", "status": "launching"})
            result = adapter.recover_job(job_id)
            manifest = adapter._read_job_manifest(job_id)
            self.assertEqual(result["exit_code"], 9)
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["exit_code"], 9)
            self.assertIn("finished_at", manifest)

    def test_working_directory_is_confined_to_project_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            nested = Path(root) / "src"
            nested.mkdir()
            self.assertEqual(Path(adapter._resolve_working_directory("src", root)), nested.resolve())
            with self.assertRaisesRegex(adapter.PsmuxError, "working_directory_outside_project_root"):
                adapter._resolve_working_directory(outside, root)

    def test_working_directory_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(adapter.PsmuxError, "working_directory_not_found"):
                adapter._resolve_working_directory("missing", root)

    def test_script_path_is_project_relative_even_when_cwd_is_a_subdirectory(self):
        with tempfile.TemporaryDirectory() as root:
            scripts = Path(root) / "scripts"
            nested = Path(root) / "src"
            scripts.mkdir()
            nested.mkdir()
            script_path = scripts / "check.py"
            script_path.write_text("print('ok')", encoding="utf-8")
            source, language, resolved = adapter._resolve_script_request(
                {"script_path": "scripts/check.py"}, str(nested), root
            )
        self.assertEqual(source, "")
        self.assertEqual(language, "python")
        self.assertEqual(resolved, script_path.resolve())

    def test_script_path_still_rejects_escape_from_project_root_with_nested_cwd(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            script_path = Path(outside) / "outside.py"
            script_path.write_text("print('no')", encoding="utf-8")
            nested = Path(root) / "src"
            nested.mkdir()
            with self.assertRaisesRegex(adapter.PsmuxError, "script_path_outside_project_root"):
                adapter._resolve_script_request(
                    {"script_path": str(script_path)}, str(nested), root
                )

    def test_lock_uses_low_level_open_and_preserves_exclusive_session_lease(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"LOCALAPPDATA": directory}):
            lock_path = adapter._job_root() / "muse-dev.lock"
            with patch.object(Path, "open", side_effect=PermissionError("pathlib open denied")):
                first = adapter._acquire_lock(lock_path)
                self.assertIsNotNone(first)
                self.assertIsNone(adapter._acquire_lock(lock_path))
                adapter._release_lock(first)
                second = adapter._acquire_lock(lock_path)
                self.assertIsNotNone(second)
                adapter._release_lock(second)

    def test_invalid_request_is_rejected_before_touching_psmux(self):
        result = adapter.execute_request({"script": "", "timeout_secs": 30})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "script_required")

    def test_invalid_timeout_is_rejected(self):
        result = adapter.execute_request({"script": "Write-Output ok", "timeout_secs": 0})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "timeout_secs_must_be_1_to_7200")


if __name__ == "__main__":
    unittest.main()
