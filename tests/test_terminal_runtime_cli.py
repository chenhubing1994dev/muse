from __future__ import annotations

import contextlib
import io
import json
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from scripts import muse_dev


@dataclass(frozen=True)
class FakeProbe:
    provider_id: str = "herdr-core"
    available: bool = True
    executable: str = "hc.exe"
    version: str = "hc 0.1.19"
    schema_valid: bool = True
    schema_methods: tuple[str, ...] = ("terminal.list",)
    error_code: str | None = None


@dataclass(frozen=True)
class FakeListing:
    provider_id: str = "herdr-core"
    available: bool = True
    payload: dict = None
    error_code: str | None = None


class TerminalRuntimeCliTests(unittest.TestCase):
    def _invoke(self, argv: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = muse_dev.main(argv)
        return code, json.loads(output.getvalue())

    def test_terminal_probe_selects_optional_herdr_provider_and_reports_contract(self):
        provider = type("Provider", (), {"probe": lambda self: FakeProbe()})()
        with patch.object(muse_dev, "HerdrCoreProvider", return_value=provider) as constructor:
            code, result = self._invoke(["terminal", "probe", "--provider", "herdr-core"])
        self.assertEqual(code, 0)
        constructor.assert_called_once_with()
        self.assertEqual(result["provider_id"], "herdr-core")
        self.assertEqual(result["schema_methods"], ["terminal.list"])

    def test_terminal_list_defaults_to_existing_psmux_observers(self):
        listing = FakeListing(provider_id="psmux", payload={"psmux": {}, "jobs": []})
        provider = type("Provider", (), {"list_sessions": lambda self: listing})()
        with patch.object(muse_dev, "PsmuxProvider", return_value=provider) as constructor:
            code, result = self._invoke(["terminal", "list"])
        self.assertEqual(code, 0)
        constructor.assert_called_once()
        self.assertEqual(result["provider_id"], "psmux")
        self.assertEqual(result["payload"]["jobs"], [])

    def test_terminal_probe_returns_nonzero_for_unavailable_optional_provider(self):
        unavailable = FakeProbe(available=False, version=None, schema_valid=False, schema_methods=(), error_code="hc_not_found")
        provider = type("Provider", (), {"probe": lambda self: unavailable})()
        with patch.object(muse_dev, "HerdrCoreProvider", return_value=provider):
            code, result = self._invoke(["terminal", "probe", "--provider", "herdr-core"])
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "hc_not_found")


if __name__ == "__main__":
    unittest.main()
