from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WebCodexInterfacePolicyTests(unittest.TestCase):
    def test_root_instructions_require_live_discovery_and_manifest(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("current ChatGPT session", text)
        self.assertIn("read_tool_manifest", text)
        self.assertIn("Never guess", text)
        self.assertIn("docs/WEBCODEX_INTERFACE.md", text)

    def test_runbook_distinguishes_runtime_and_host_schema(self):
        text = (ROOT / "docs" / "WEBCODEX_INTERFACE.md").read_text(encoding="utf-8")
        self.assertIn("current host tool catalogue", text)
        self.assertIn("exact manifest", text)
        self.assertIn("ChatGPT host schema refresh", text)
        self.assertIn("Runner provider reload", text)
        self.assertIn("project_ambiguous", text)
        self.assertIn("outcome_unknown", text)

    def test_legacy_commands_are_rejection_examples_only(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Never blindly call legacy names", text)
        self.assertIn("system_status", text)
        self.assertIn("workspace_list", text)


if __name__ == "__main__":
    unittest.main()
