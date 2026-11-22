from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LinuxSystemdDeploymentArtifactTests(unittest.TestCase):
    def test_systemd_unit_runs_as_dedicated_user_with_hardening(self) -> None:
        unit = (ROOT / "deploy" / "systemd" / "muse-web-portal.service").read_text(encoding="utf-8")
        for directive in (
            "User=muse", "Group=muse", "NoNewPrivileges=true", "PrivateTmp=true",
            "ProtectSystem=strict", "ProtectHome=true", "ProtectKernelTunables=true",
            "ProtectKernelModules=true", "ProtectControlGroups=true", "RestrictSUIDSGID=true",
            "UMask=0077", "KillSignal=SIGINT", "Restart=on-failure",
        ):
            with self.subTest(directive=directive):
                self.assertIn(directive, unit)

    def test_systemd_unit_defaults_to_loopback_and_existing_muse_databases(self) -> None:
        unit = (ROOT / "deploy" / "systemd" / "muse-web-portal.service").read_text(encoding="utf-8")
        self.assertIn("--bind 127.0.0.1 --port 8443", unit)
        self.assertNotIn("--bind 0.0.0.0", unit)
        for path in (
            "/opt/muse/data/tasks/muse.sqlite3", "/opt/muse/data/progress/muse.sqlite3",
            "/opt/muse/data/capabilities/muse.sqlite3", "/opt/muse/data/plugins/registry.sqlite3",
        ):
            with self.subTest(path=path):
                self.assertIn(path, unit)
                self.assertIn(f"ExecStartPre=/usr/bin/test -r {path}", unit)

    def test_secret_example_contains_only_a_placeholder(self) -> None:
        example = (ROOT / "deploy" / "systemd" / "muse-web-portal.env.example").read_text(encoding="utf-8")
        self.assertIn("MUSE_WEB_PASSWORD=REPLACE_WITH_A_RANDOM_64_CHARACTER_ALPHANUMERIC_SECRET", example)
        self.assertNotIn("muse_web_password=actual", example.lower())
        self.assertIn("mode 0600", example)


if __name__ == "__main__":
    unittest.main()
