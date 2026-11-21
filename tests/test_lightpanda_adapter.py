from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from muse.lightpanda_adapter import LightpandaBrowserAdapter, LightpandaUnavailableError


class LightpandaBrowserAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_doctor_is_disabled_when_no_local_cdp_endpoint_is_configured(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            adapter = LightpandaBrowserAdapter(workspace=self.workspace, cdp_url=None)
        result = adapter.doctor()
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["provider"], "lightpanda")

    def test_rejects_non_loopback_cdp_endpoint(self) -> None:
        for endpoint in (
            "http://example.com:9222",
            "http://192.168.1.10:9222",
            "http://127.0.0.1:9222@evil.example",
            "file:///tmp/cdp",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                LightpandaBrowserAdapter(workspace=self.workspace, cdp_url=endpoint)

    def test_requires_explicit_authorization_before_connecting(self) -> None:
        adapter = LightpandaBrowserAdapter(workspace=self.workspace, cdp_url="http://127.0.0.1:9222")
        with patch("muse.lightpanda_adapter._load_playwright") as load:
            with self.assertRaises(PermissionError):
                adapter.capture_url(
                    "https://example.com", output_directory="data/lightpanda",
                    authorization_confirmed=True, approved=False,
                )
        load.assert_not_called()

    def test_missing_optional_playwright_dependency_is_reported_without_import_failure(self) -> None:
        adapter = LightpandaBrowserAdapter(workspace=self.workspace, cdp_url="http://127.0.0.1:9222")
        with patch("muse.lightpanda_adapter._load_playwright", side_effect=LightpandaUnavailableError("missing")):
            result = adapter.doctor()
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "dependency_missing")

    def test_capture_uses_cdp_and_persists_bounded_provenance_without_screenshot(self) -> None:
        adapter = LightpandaBrowserAdapter(workspace=self.workspace, cdp_url="http://127.0.0.1:9222")
        page = Mock()
        page.url = "https://example.com/article"
        page.title.return_value = "Example article"
        body = Mock()
        body.inner_text.return_value = "A" * 1200
        links = Mock()
        links.count.return_value = 2
        links.nth.side_effect = [
            Mock(evaluate=Mock(return_value={"text": "Docs", "url": "https://example.com/docs"})),
            Mock(evaluate=Mock(return_value={"text": "Local admin", "url": "http://127.0.0.1/admin"})),
        ]
        page.locator.side_effect = lambda selector: body if selector == "body" else links
        context = Mock()
        context.new_page.return_value = page
        browser = Mock()
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)

        output = self.workspace / "data" / "lightpanda"
        with patch(
            "muse.lightpanda_adapter._load_playwright",
            return_value=Mock(sync_playwright=Mock(return_value=manager)),
        ), patch(
            "muse.lightpanda_adapter._validate_public_url",
            side_effect=lambda value: (_ for _ in ()).throw(ValueError("private target"))
            if value.startswith("http://127.") else None,
        ) as validate:
            browser.new_context.return_value = context
            result = adapter.capture_url(
                "https://example.com/article", output_directory=output,
                authorization_confirmed=True, approved=True,
            )

        self.assertEqual(result["provider"], "lightpanda")
        self.assertEqual(result["capture_method"], "lightpanda-cdp")
        self.assertIsNone(result["screenshot_path"])
        self.assertTrue((output / "capture.json").is_file())
        self.assertTrue((output / "capture.md").is_file())
        stored = json.loads((output / "capture.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["source"]["url"], "https://example.com/article")
        self.assertEqual(stored["source"]["title"], "Example article")
        self.assertEqual(len(stored["source"]["page_sha256"]), 64)
        self.assertEqual(stored["links"], [{"text": "Docs", "url": "https://example.com/docs"}])
        self.assertTrue(any(call.args[0] == "http://127.0.0.1/admin" for call in validate.call_args_list))
        self.assertGreaterEqual(validate.call_count, 3)
        playwright.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:9222", timeout=30000,
        )
        route_handler = context.route.call_args.args[1]
        unsafe_route = Mock()
        unsafe_route.request.url = "http://127.0.0.1/admin"
        with patch(
            "muse.lightpanda_adapter._validate_public_url",
            side_effect=ValueError("private target"),
        ):
            route_handler(unsafe_route)
        unsafe_route.abort.assert_called_once()
        context.close.assert_called_once()
        browser.close.assert_called_once()

    def test_output_directory_must_be_new_and_inside_workspace(self) -> None:
        adapter = LightpandaBrowserAdapter(workspace=self.workspace, cdp_url="http://127.0.0.1:9222")
        outside = self.workspace.parent / "outside-lightpanda"
        existing = self.workspace / "existing"
        existing.mkdir()
        # Output-boundary rejection must happen before public URL validation/DNS.
        with patch("muse.lightpanda_adapter._validate_public_url") as validate:
            with self.assertRaises(ValueError):
                adapter.capture_url(
                    "https://example.com", output_directory=outside,
                    authorization_confirmed=True, approved=True,
                )
            with self.assertRaises(FileExistsError):
                adapter.capture_url(
                    "https://example.com", output_directory=existing,
                    authorization_confirmed=True, approved=True,
                )
        validate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
