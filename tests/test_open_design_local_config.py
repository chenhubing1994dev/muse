from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from urllib.parse import urlsplit


class OpenDesignLocalConfigTests(unittest.TestCase):
    def test_open_design_uses_project_local_loopback_provider_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        muse_config = tomllib.loads((root / "config" / "muse.toml").read_text(encoding="utf-8"))
        open_code_path = Path(muse_config["open_design"]["opencode_config_path"])
        self.assertEqual(open_code_path.name, "opencode.local.jsonc")
        self.assertEqual(muse_config["open_design"]["allowed_agents"], [])

        local_config = json.loads((root / "config" / "opencode.local.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(local_config["model"], "muse-local/deep")
        provider = local_config["provider"]["muse-local"]
        base_url = provider["options"]["baseURL"]
        self.assertEqual(base_url, "http://127.0.0.1:1234/v1")
        self.assertIn(urlsplit(base_url).hostname, {"127.0.0.1", "localhost", "::1"})
        self.assertIn("deep", provider["models"])
        self.assertTrue(provider["models"]["deep"]["tool_call"])
        self.assertFalse(any(key.lower() in {"apikey", "api_key", "token", "secret"} for key in local_config))
        self.assertEqual(muse_config["models"]["base_url"], base_url)
        self.assertEqual(muse_config["models"]["model"], "deep")


if __name__ == "__main__":
    unittest.main()
