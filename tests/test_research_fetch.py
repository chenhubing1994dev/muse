from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.research_fetch import PublicSourceReader, ResearchEvidenceStore, SourceReadError


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/html; charset=utf-8"):
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size: int = -1):
        return self.body[:size]


def public_resolver(host, port, type=None):
    return [(None, None, None, None, ("93.184.216.34", port))]


class ResearchFetchTests(unittest.TestCase):
    def test_extracts_readable_text_and_discards_executable_layout_content(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(
                b"<html><nav>menu</nav><h1>Muse</h1><p>Useful evidence</p>"
                b"<script>steal()</script><style>.x{}</style><footer>footer</footer></html>"
            )

        page = PublicSourceReader(resolver=public_resolver, opener=opener).read(
            "https://example.com/docs"
        )
        self.assertEqual(page["text"], "Muse Useful evidence")
        self.assertNotIn("steal", page["text"])
        self.assertNotIn("footer", page["text"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(page["sha256"]), 64)


    def test_large_html_uses_bounded_metadata_prefix_but_keeps_readable_body(self):
        body = (
            b'<html><head><meta name="citation_author" content="Ada Lovelace"></head>'
            b'<body><script>' + (b"large-inline-script;" * 60_000) + b'</script>'
            b'<h1>Experiment report</h1><p>' + (b"Useful research evidence. " * 20)
            + b'</p></body></html>'
        )
        reader = PublicSourceReader(
            resolver=public_resolver,
            opener=lambda *_args, **_kwargs: FakeResponse(body),
        )
        page = reader.read("https://example.com/large-report", max_bytes=2_000_000)
        self.assertTrue(page["source_metadata"]["metadata_input_truncated"])
        self.assertEqual(page["source_metadata"]["authors"], ["Ada Lovelace"])
        self.assertIn("Experiment report", page["text"])
        self.assertIn("Useful research evidence.", page["text"])
        self.assertNotIn("large-inline-script", page["text"])

    def test_extracts_source_identity_metadata_before_discarding_html_markup(self):
        body = (
            b'<html><head><link rel="canonical" href="/original">'
            b'<meta name="citation_author" content="Ada Lovelace">'
            b'<meta name="citation_publisher" content="Example Press"></head>'
            b'<body><p>' + (b"Long enough research text. " * 20) + b"</p></body></html>"
        )
        reader = PublicSourceReader(
            resolver=public_resolver,
            opener=lambda *_args, **_kwargs: FakeResponse(body),
        )
        page = reader.read("https://mirror.example/copy")
        self.assertEqual(page["source_metadata"]["canonical_url"], "https://mirror.example/original")
        self.assertEqual(page["source_metadata"]["authors"], ["Ada Lovelace"])
        self.assertEqual(page["source_metadata"]["publisher"], "Example Press")
        self.assertNotIn("canonical", page["text"].casefold())
    def test_blocks_private_literal_hosts_before_network(self):
        called = []
        reader = PublicSourceReader(opener=lambda *_a, **_k: called.append(True))
        for url in (
            "http://127.0.0.1/admin", "http://10.0.0.2/", "http://localhost/",
            "file:///etc/passwd", "https://user:pass@example.com/",
        ):
            with self.subTest(url=url), self.assertRaises(SourceReadError):
                reader.read(url)
        self.assertEqual(called, [])

    def test_blocks_dns_resolution_to_private_address(self):
        def private_resolver(host, port, type=None):
            return [(None, None, None, None, ("192.168.1.20", port))]
        reader = PublicSourceReader(resolver=private_resolver, opener=lambda *_a, **_k: self.fail("must not fetch"))
        with self.assertRaisesRegex(SourceReadError, "private"):
            reader.read("https://example.com/")

    def test_loopback_proxy_synthetic_dns_uses_doh_and_still_requires_public_ip(self):
        import json
        from unittest.mock import patch

        def synthetic_resolver(host, port, type=None):
            return [(None, None, None, None, ("198.18.0.20", port))]

        def doh_opener(request, timeout):
            if "type=A" in request.full_url:
                payload = {"Status": 0, "Answer": [{"type": 1, "data": "93.184.216.34"}]}
            else:
                payload = {"Status": 0, "Answer": []}
            return FakeResponse(json.dumps(payload).encode(), "application/json")

        reader = PublicSourceReader(
            resolver=synthetic_resolver,
            opener=lambda *_a, **_k: FakeResponse(b"<html><p>Verified public source</p></html>"),
        )
        with patch("muse.research_fetch.getproxies", return_value={"https": "http://127.0.0.1:7890"}), \
             patch("muse.research_fetch.urlopen", side_effect=doh_opener):
            page = reader.read("https://example.com/docs")
        self.assertEqual(page["text"], "Verified public source")

    def test_proxy_doh_does_not_allow_private_destination(self):
        import json
        from unittest.mock import patch

        def synthetic_resolver(host, port, type=None):
            return [(None, None, None, None, ("198.18.0.20", port))]

        def doh_opener(request, timeout):
            payload = {"Status": 0, "Answer": [{"type": 1, "data": "10.0.0.8"}]} if "type=A" in request.full_url else {"Status": 0, "Answer": []}
            return FakeResponse(json.dumps(payload).encode(), "application/json")

        reader = PublicSourceReader(
            resolver=synthetic_resolver,
            opener=lambda *_a, **_k: self.fail("private destination must not be fetched"),
        )
        with patch("muse.research_fetch.getproxies", return_value={"https": "http://127.0.0.1:7890"}), \
             patch("muse.research_fetch.urlopen", side_effect=doh_opener):
            with self.assertRaisesRegex(SourceReadError, "private"):
                reader.read("https://example.com/private")

    def test_response_size_and_content_type_are_bounded(self):
        reader = PublicSourceReader(
            resolver=public_resolver,
            opener=lambda *_a, **_k: FakeResponse(b"x" * 2048, "application/octet-stream"),
        )
        with self.assertRaisesRegex(SourceReadError, "not a readable"):
            reader.read("https://example.com/file")
        reader = PublicSourceReader(
            resolver=public_resolver,
            opener=lambda *_a, **_k: FakeResponse(b"x" * 2048, "text/plain"),
        )
        with self.assertRaisesRegex(SourceReadError, "byte limit"):
            reader.read("https://example.com/file", max_bytes=1024)

    def test_evidence_store_checks_hash_and_keeps_provenance(self):
        from datetime import datetime, timezone
        import hashlib

        url = "https://93.184.216.34/research"
        text = "verified extract"
        page = {
            "url": url, "content_type": "text/plain", "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(), "truncated": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.sqlite3"
            with ResearchEvidenceStore(path) as store:
                store.save(page)
                self.assertEqual(store.list_recent()[0]["chars"], len(text))
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    store.save({**page, "text": "tampered"})
            with ResearchEvidenceStore(path) as store:
                self.assertEqual(store.list_recent()[0]["sha256"], page["sha256"])


    def test_evidence_integrity_audit_detects_database_tampering_without_exposing_body(self):
        from datetime import datetime, timezone
        import hashlib

        page_text = "Integrity-protected source extract."
        page = {
            "url": "https://93.184.216.34/integrity",
            "content_type": "text/plain", "text": page_text,
            "sha256": hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(), "truncated": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.sqlite3"
            with ResearchEvidenceStore(path) as store:
                store.save(page)
                clean = store.audit_integrity()
                self.assertEqual(clean["intact"], 1)
                self.assertEqual(clean["source_bodies_returned"], 0)
                self.assertEqual((clean["writes"], clean["deletes"], clean["repairs"]), (0, 0, 0))
                store.connection.execute(
                    "UPDATE research_evidence SET text=? WHERE url=?",
                    ("tampered stored source", page["url"]),
                )
                store.connection.commit()
                report = store.audit_integrity()
                self.assertEqual(report["hash_mismatch"], 1)
                self.assertEqual(report["items"][0]["status"], "hash_mismatch")
                self.assertNotIn("text", report["items"][0])
                self.assertEqual(store.list_recent()[0]["sha256"], page["sha256"])

    def test_evidence_integrity_audit_bounds_limit_and_reports_more_rows(self):
        from datetime import datetime, timezone
        import hashlib

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.sqlite3"
            with ResearchEvidenceStore(path) as store:
                for index in range(3):
                    text = f"source snapshot {index}"
                    store.save({
                        "url": f"https://93.184.216.34/source-{index}",
                        "content_type": "text/plain", "text": text,
                        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "fetched_at": datetime.now(timezone.utc).isoformat(), "truncated": False,
                    })
                report = store.audit_integrity(limit=2)
                self.assertEqual(report["scanned"], 2)
                self.assertTrue(report["has_more"])
                with self.assertRaises(ValueError):
                    store.audit_integrity(limit=0)

    def test_evidence_database_audit_uses_read_only_connection_and_requires_existing_file(self):
        import hashlib

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.sqlite3"
            with ResearchEvidenceStore(path) as store:
                text = "Read-only audit fixture."
                store.save({
                    "url": "https://93.184.216.34/read-only",
                    "content_type": "text/plain", "text": text,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "fetched_at": "2026-11-09T00:00:00+00:00", "truncated": False,
                })
            before = path.read_bytes()
            report = ResearchEvidenceStore.audit_database(path)
            after = path.read_bytes()
            self.assertEqual(report["intact"], 1)
            self.assertEqual(before, after)
            missing = Path(directory) / "missing.sqlite3"
            with self.assertRaises(FileNotFoundError):
                ResearchEvidenceStore.audit_database(missing)
            self.assertFalse(missing.exists())

    def test_existing_attribution_database_migrates_relevance_columns(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("""CREATE TABLE research_attributions (
                url TEXT NOT NULL, query TEXT NOT NULL, title TEXT NOT NULL,
                snippet TEXT NOT NULL, engine TEXT NOT NULL, published_at TEXT NOT NULL,
                discovered_at TEXT NOT NULL, PRIMARY KEY (url, query)
            )""")
            connection.commit()
            connection.close()
            with ResearchEvidenceStore(path) as store:
                columns = {row["name"] for row in store.connection.execute(
                    "PRAGMA table_info(research_attributions)"
                )}
            self.assertIn("relevance_score", columns)
            self.assertIn("relevance_method", columns)

    def test_search_attribution_is_persisted_and_queryable(self):
        url = "https://93.184.216.34/research"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.sqlite3"
            with ResearchEvidenceStore(path) as store:
                store.save_attribution({
                    "url": url, "query": "Muse research", "title": "Muse docs",
                    "snippet": "Official documentation", "engine": "bing-rss",
                    "published_at": "2026-11-03", "relevance_score": 85,
                    "relevance_method": "title_snippet_token_overlap_v1",
                })
                rows = store.list_recent_attributions()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["query"], "Muse research")
            self.assertEqual(rows[0]["title"], "Muse docs")
            self.assertEqual(rows[0]["engine"], "bing-rss")
            self.assertEqual(rows[0]["relevance_score"], 85)
            self.assertEqual(rows[0]["relevance_method"], "title_snippet_token_overlap_v1")


if __name__ == "__main__":
    unittest.main()
