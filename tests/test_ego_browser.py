from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from muse.ego_browser import EgoBrowserAdapter, EgoBrowserError


class EgoBrowserAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.adapter = EgoBrowserAdapter(workspace=self.workspace, executable="ego-browser")

    def tearDown(self) -> None:
        self.temp.cleanup()
    def test_doctor_does_not_claim_unprobed_browser_runtime_is_ready(self) -> None:
        result = self.adapter.doctor()
        self.assertEqual(result["status"], "installed_unverified")
        self.assertTrue(result["runtime_probe_required"])

    def test_runtime_probe_marks_ready_only_after_browser_cli_responds(self) -> None:
        with patch.object(self.adapter, "_run_marked_script", return_value={"runtime_probe": True}):
            result = self.adapter.probe_runtime()
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["runtime_ready"])
        self.assertEqual(result["browser_app_onboarding_status"], "runtime_verified")
        self.assertFalse(result["runtime_probe_required"])

    def test_runtime_probe_reports_not_ready_without_failing_muse(self) -> None:
        with patch.object(self.adapter, "_run_marked_script", side_effect=EgoBrowserError("app closed")):
            result = self.adapter.probe_runtime()
        self.assertEqual(result["status"], "not_ready")
        self.assertFalse(result["runtime_ready"])
        self.assertEqual(result["runtime_error"], "EgoBrowserError")

    def test_marked_script_reads_cli_log_result_from_stderr(self) -> None:
        payload = {"runtime_probe": True}
        completed = subprocess.CompletedProcess(
            args=["ego-browser", "nodejs"], returncode=0,
            stdout="", stderr="MUSE_EGO_RESULT:" + json.dumps(payload) + "\n",
        )
        with patch("muse.ego_browser.no_console_window_kwargs", return_value={"creationflags": 123}), patch(
            "muse.ego_browser.subprocess.run", return_value=completed
        ) as run:
            self.assertEqual(self.adapter._run_marked_script("cliLog('result')"), payload)
        self.assertEqual(run.call_args.kwargs["creationflags"], 123)


    def payload(self) -> dict:
        return {
            "task_space": "Muse research example.com",
            "snapshot": "Page: Example\n@1 [heading] Example",
            "document": {
                "url": "https://example.com/",
                "title": "Example",
                "text": "Example content",
                "links": [{"text": "Docs", "url": "https://example.com/docs"}],
                "assets": [{"kind": "image", "url": "https://example.com/hero.png", "alt": "Hero"}],
                "source_metadata": {"publisher": "Example Publisher", "authors": ["A. Author"]},
            },
            "screenshot": {"saved": True, "path": "page.png"},
        }

    def completed(self, payload: dict | None = None) -> subprocess.CompletedProcess:
        value = json.dumps(payload or self.payload(), ensure_ascii=False)
        return subprocess.CompletedProcess(
            args=["ego-browser", "nodejs"], returncode=0,
            stdout="", stderr="MUSE_EGO_RESULT:" + value + "\n",
        )

    def test_requires_explicit_authorization_and_approval_before_browser_launch(self) -> None:
        with patch("muse.ego_browser.subprocess.run") as run:
            with self.assertRaises(PermissionError):
                self.adapter.capture_url(
                    "https://example.com", output_directory="data/study",
                    authorization_confirmed=True, approved=False,
                )
            run.assert_not_called()

    def test_rejects_embedded_credentials_and_secret_query_parameters(self) -> None:
        for url in (
            "https://user:pass@example.com/",
            "https://example.com/?access_token=secret",
            "file:///C:/Windows/win.ini",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.adapter.capture_url(
                    url, output_directory="data/study",
                    authorization_confirmed=True, approved=True,
                )

    def test_capture_runs_ego_browser_without_shell_and_writes_provenance(self) -> None:
        output = self.workspace / "data" / "study"
        with patch("muse.ego_browser.subprocess.run", return_value=self.completed()) as run:
            result = self.adapter.capture_url(
                "https://example.com", output_directory=output,
                authorization_confirmed=True, approved=True,
            )
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["ego-browser", "nodejs"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(Path(kwargs["cwd"]).resolve(), self.workspace.resolve())
        self.assertIn("taskSpace(", kwargs["input"])
        self.assertIn("page.snapshot()", kwargs["input"])
        self.assertIn("page.screenshot({ path: screenshotPath })", kwargs["input"])
        self.assertIn("task.finish({ keep: [] })", kwargs["input"])
        self.assertEqual(result["provider"], "ego-lite")
        self.assertTrue((output / "capture.json").is_file())
        self.assertTrue((output / "capture.md").is_file())
        stored = json.loads((output / "capture.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["source"]["url"], "https://example.com/")
        self.assertEqual(len(stored["source"]["page_sha256"]), 64)
        self.assertEqual(stored["assets"][0]["kind"], "image")
        self.assertEqual(stored["source_metadata"]["publisher"], "Example Publisher")

    def test_public_only_capture_validates_requested_and_final_urls(self) -> None:
        with patch("muse.research_fetch._validate_public_url") as validate, patch(
            "muse.ego_browser.subprocess.run", return_value=self.completed(),
        ):
            self.adapter.capture_url(
                "https://example.com", output_directory="data/public-study",
                authorization_confirmed=True, approved=True, public_only=True,
            )
        self.assertEqual(validate.call_count, 2)

    def test_capture_does_not_download_or_execute_page_assets(self) -> None:
        with patch("muse.ego_browser.subprocess.run", return_value=self.completed()) as run:
            self.adapter.capture_url(
                "https://example.com", output_directory="data/study",
                authorization_confirmed=True, approved=True,
            )
        script = run.call_args.kwargs["input"]
        self.assertNotIn("fetch(", script)
        self.assertNotIn("httpGet(", script)
        self.assertNotIn("uploadFile(", script)
        self.assertNotIn("click(", script)

    def test_output_directory_must_stay_inside_workspace_and_must_not_exist(self) -> None:
        outside = self.workspace.parent / "outside-study"
        with patch("muse.ego_browser.subprocess.run") as run:
            with self.assertRaises(ValueError):
                self.adapter.capture_url(
                    "https://example.com", output_directory=outside,
                    authorization_confirmed=True, approved=True,
                )
            run.assert_not_called()
        existing = self.workspace / "existing"
        existing.mkdir()
        with patch("muse.ego_browser.subprocess.run") as run:
            with self.assertRaises(FileExistsError):
                self.adapter.capture_url(
                    "https://example.com", output_directory=existing,
                    authorization_confirmed=True, approved=True,
                )
            run.assert_not_called()

    def test_capture_limits_are_bounded(self) -> None:
        with patch("muse.ego_browser.subprocess.run") as run:
            with self.assertRaises(ValueError):
                self.adapter.capture_url(
                    "https://example.com", output_directory="data/study",
                    authorization_confirmed=True, approved=True, max_chars=1000000,
                )
            run.assert_not_called()

    def test_malformed_or_missing_result_marker_fails_closed(self) -> None:
        for stdout in ("no result", "MUSE_EGO_RESULT:not-json"):
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
            with self.subTest(stdout=stdout), patch("muse.ego_browser.subprocess.run", return_value=completed):
                with self.assertRaises(EgoBrowserError):
                    self.adapter.capture_url(
                        "https://example.com", output_directory="data/study",
                        authorization_confirmed=True, approved=True,
                    )

    def test_nonzero_process_exit_reports_bounded_diagnostic(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="browser unavailable")
        with patch("muse.ego_browser.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(EgoBrowserError, "browser unavailable"):
                self.adapter.capture_url(
                    "https://example.com", output_directory="data/study",
                    authorization_confirmed=True, approved=True,
                )

    def test_doctor_reports_missing_runtime_without_launching(self) -> None:
        with patch("muse.ego_browser.shutil.which", return_value=None), patch(
            "muse.ego_browser._discover_ego_browser", return_value=None,
        ):
            adapter = EgoBrowserAdapter(workspace=self.workspace, executable=None)
            result = adapter.doctor()
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "missing")

    def test_doctor_discovers_installed_cli_outside_path(self) -> None:
        cli = self.workspace / "ego-browser.exe"
        cli.write_bytes(b"test")
        with patch("muse.ego_browser.shutil.which", return_value=None), patch(
            "muse.ego_browser._discover_ego_browser", return_value=str(cli),
        ):
            adapter = EgoBrowserAdapter(workspace=self.workspace, executable=None)
            result = adapter.doctor()
        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "installed_unverified")
        self.assertEqual(result["executable"], str(cli))
        self.assertFalse(result["cli_on_path"])

    def test_capture_caps_extracted_links_and_assets_before_persisting(self) -> None:
        payload = self.payload()
        payload["document"]["links"] = [{"text": str(i), "url": f"https://example.com/{i}"} for i in range(10)]
        payload["document"]["assets"] = [{"kind": "image", "url": f"https://example.com/{i}.png"} for i in range(10)]
        with patch("muse.ego_browser.subprocess.run", return_value=self.completed(payload)):
            result = self.adapter.capture_url(
                "https://example.com", output_directory="data/study",
                authorization_confirmed=True, approved=True, max_links=3, max_assets=2,
            )
        self.assertEqual(len(result["links"]), 3)
        self.assertEqual(len(result["assets"]), 2)

    def test_search_web_returns_structured_results_without_opening_result_sites(self) -> None:
        payload = {
            "task_space": "Muse Google search",
            "search_results": [
                {"title": "Research paper", "url": "https://example.com/paper", "snippet": "A useful result"},
            ],
            "snapshot": "Google search results",
        }
        completed = self.completed(payload)
        with patch("muse.research_fetch._validate_public_url"), patch(
            "muse.ego_browser.subprocess.run", return_value=completed,
        ) as run:
            results = self.adapter.search_web(
                "research agents", output_directory="data/search",
                authorization_confirmed=True, approved=True, max_results=5,
            )
        self.assertEqual(results[0]["title"], "Research paper")
        self.assertEqual(results[0]["engine"], "ego-lite-google")
        script = run.call_args.kwargs["input"]
        self.assertIn("google.com/search?q=", script)
        self.assertIn("div.g", script)
        self.assertNotIn("openOrReuseTab(item.url", script)
        self.assertTrue((self.workspace / "data/search/search.json").is_file())

    def test_search_web_requires_explicit_approval(self) -> None:
        with patch("muse.ego_browser.subprocess.run") as run:
            with self.assertRaises(PermissionError):
                self.adapter.search_web(
                    "research agents", output_directory="data/search",
                    authorization_confirmed=True, approved=False,
                )
            run.assert_not_called()

    def test_asset_download_requires_explicit_domain_allowlist(self) -> None:
        with patch("muse.ego_browser.subprocess.run") as run, patch("muse.research_fetch._validate_public_url") as validate_public:
            with self.assertRaises(ValueError):
                self.adapter.download_assets(
                    "https://example.com/page", ["https://cdn.example.com/a.png"],
                    allowed_domains=[], output_directory="data/assets",
                    authorization_confirmed=True, approved=True,
                )
            with self.assertRaises(ValueError):
                self.adapter.download_assets(
                    "https://example.com/page", ["https://other.test/a.png"],
                    allowed_domains=["example.com"], output_directory="data/assets",
                    authorization_confirmed=True, approved=True,
                )
            run.assert_not_called()
            validate_public.assert_not_called()

    def test_asset_download_saves_hash_and_rights_provenance_manifest(self) -> None:
        output = self.workspace / "data" / "assets"
        def fake_run(*args, **kwargs):
            (output / "asset_001.download").write_bytes(b"PNG!")
            payload = {
                "task_space": "Muse asset collection",
                "results": [{
                    "url": "https://example.com/hero.png", "final_url": "https://example.com/hero.png",
                    "ok": True, "status": 200, "content_type": "image/png",
                    "content_length": 4, "saved_path": "asset_001.download",
                }],
            }
            return self.completed(payload)
        with patch("muse.research_fetch._validate_public_url"), patch(
            "muse.ego_browser.subprocess.run", side_effect=fake_run,
        ) as run:
            result = self.adapter.download_assets(
                "https://example.com/page", ["https://example.com/hero.png"],
                allowed_domains=["example.com"], output_directory=output,
                authorization_confirmed=True, approved=True,
            )
        script = run.call_args.kwargs["input"]
        self.assertIn("taskSpace(", script)
        self.assertIn("page.goto(pageUrl", script)
        self.assertIn('method: "HEAD"', script)
        self.assertIn("page.fetch", script)
        self.assertIn("saveAs", script)
        self.assertIn('redirect: "manual"', script)
        self.assertIn("task.finish({ keep: [] })", script)
        manifest = json.loads((output / "assets.json").read_text(encoding="utf-8"))
        self.assertEqual(result["downloaded_count"], 1)
        self.assertEqual(manifest["assets"][0]["sha256"], __import__("hashlib").sha256(b"PNG!").hexdigest())
        self.assertEqual(manifest["assets"][0]["license_status"], "unreviewed")
        self.assertFalse(manifest["assets"][0]["reuse_allowed"])


if __name__ == "__main__":
    unittest.main()
