from __future__ import annotations

import unittest

from muse.chat_cli import is_loopback_endpoint


class MemoryExtractionPrivacyTests(unittest.TestCase):
    def test_accepts_localhost_and_loopback_ip_endpoints(self) -> None:
        self.assertTrue(is_loopback_endpoint("http://127.0.0.1:1234/v1"))
        self.assertTrue(is_loopback_endpoint("http://localhost:1234/v1"))
        self.assertTrue(is_loopback_endpoint("http://[::1]:1234/v1"))

    def test_rejects_remote_and_non_http_endpoints_for_memory_extraction(self) -> None:
        self.assertFalse(is_loopback_endpoint("https://api.example.com/v1"))
        self.assertFalse(is_loopback_endpoint("http://192.168.1.10:1234/v1"))
        self.assertFalse(is_loopback_endpoint("file:///tmp/model"))
        self.assertFalse(is_loopback_endpoint("not-a-url"))


if __name__ == "__main__":
    unittest.main()
