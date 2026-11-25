"""B396: research closed loop, Xuanshu fusion labels, feedback upload bridge."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

try:
    import gc
except ImportError:  # restricted sandboxes
    gc = None

from muse.feedback_bridge import FeedbackBridge, FeedbackRejected, parse_upload
from muse.research_closed_loop import (
    ClaimDossierStore,
    ResearchClosedLoop,
    Source,
    audit_independence,
    decide_verdict,
    registrable_domain,
    verify_citation,
)
from muse.xuanshu_fusion import EPISTEMIC_LABELS, XuanshuLabelError, fuse, validate_labels

BODY = ("Short vertical videos with a pattern interrupt in the first three seconds retain viewers longer, "
        "according to a 2026 platform analysis of eleven thousand creator uploads across four markets.")


class ResearchLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = ClaimDossierStore(Path(self.tmp.name) / "claims.sqlite3")

    def tearDown(self):
        self.store.close()
        if gc:
            gc.collect()
        self.tmp.cleanup()

    def test_syndicated_copies_collapse_into_one_independent_source(self):
        sources = [
            Source("https://www.news-a.com/x?utm_source=t", "A", BODY),
            Source("https://mirror-b.net/y", "B", BODY + " 来源：news-a.com"),
            Source("https://portal-c.org/z", "C", BODY.replace("longer", "much longer")),  # near duplicate
            Source("https://lab.example.edu/paper", "Paper", "An independent controlled trial with 300 participants "
                   "found hooks in the first seconds improved completion rate by nine percent."),
            Source("https://blog.news-a.com/again", "A2", "different words entirely about hooks and retention data"),
        ]
        audit = audit_independence(sources)
        self.assertEqual(audit["raw_source_count"], 5)
        self.assertEqual(audit["independent_source_count"], 2)
        self.assertEqual(audit["disguised_syndication_groups"], 1)
        self.assertEqual(registrable_domain("https://a.b.example.co.uk/x"), "example.co.uk")

    def test_citation_anchor_must_literally_exist(self):
        source = Source("https://lab.example.edu/p", "P", BODY)
        ok = verify_citation(source, "pattern   interrupt in the FIRST three seconds")
        self.assertTrue(ok["verified"])
        self.assertEqual(len(ok["passage_sha256"]), 64)
        self.assertFalse(verify_citation(source, "pattern interrupt doubles revenue")["verified"])
        self.assertEqual(verify_citation(source, "short")["reason"], "quote_too_short")

    def test_closed_loop_runs_counter_search_and_writes_versioned_dossier(self):
        queries = []

        def support(query):
            queries.append(("s", query))
            return [Source("https://lab.example.edu/p", "P", BODY, stance="supports"),
                    Source("https://stats.gov.example/r", "R", "Official statistics: hooks improved retention in 2026 "
                           "for creators who changed the opening shot.", stance="supports")]

        def counter(query):
            queries.append(("c", query))
            return []

        loop = ResearchClosedLoop(support_search=support, counter_search=counter, store=self.store, clock=lambda: 7.0)
        dossier = loop.investigate("hook-3s", "3-second hooks improve retention",
                                   citations=[{"url": "https://lab.example.edu/p", "quote": "first three seconds retain viewers longer"}])
        self.assertTrue(any(kind == "c" for kind, _ in queries))
        self.assertEqual(dossier["verdict"], "supported")
        self.assertEqual(dossier["verdict_semantics"], "evidence_balance_not_truth")
        self.assertTrue(dossier["citation_anchors"][0]["verified"])
        again = ResearchClosedLoop(support_search=support, counter_search=lambda q: [
            Source("https://critic.example.org/a", "Critique", "We could not replicate the hook effect at all.", stance="refutes")],
            store=self.store).investigate("hook-3s", "3-second hooks improve retention")
        self.assertEqual((again["version"], again["verdict"]), (2, "contested"))
        with self.assertRaises(sqlite3.DatabaseError):
            self.store.db.execute("DELETE FROM claim_dossiers")

    def test_unverifiable_quote_downgrades_and_retriever_errors_recorded(self):
        def boom(_):
            raise TimeoutError("offline")
        loop = ResearchClosedLoop(support_search=lambda q: [
            Source("https://a.example.com/1", "1", BODY, stance="supports"),
            Source("https://b.example.org/2", "2", "Totally separate study text about retention curves.", stance="supports")],
            counter_search=boom, store=self.store)
        dossier = loop.investigate("c-2", "hooks matter", citations=[{"url": "https://a.example.com/1", "quote": "a fabricated quotation here"}])
        self.assertEqual(dossier["verdict"], "insufficient")
        self.assertTrue(any(q.startswith("!error:") for q in dossier["queries"]["counter"]))
        self.assertEqual(decide_verdict(0, 3), "refuted")
        with self.assertRaises(TypeError):
            ResearchClosedLoop(support_search=lambda q: [], counter_search=None, store=self.store)


class XuanshuFusionTests(unittest.TestCase):
    def test_every_lens_and_prompt_is_labelled_and_missing_providers_are_unavailable(self):
        class Broken:
            def calculate(self, context):
                raise RuntimeError("engine offline")

        class Fake:
            def calculate(self, context):
                return {"palace": "命宫", "star": "紫微", "version": "x"}

        result = fuse("下一支粤语短片的视觉主题", seed="2026-11-25", providers={"ziwei": Fake(), "qimen": Broken()}, context={"d": 1})
        validate_labels(result)
        self.assertEqual(tuple(result["epistemic_labels"]), EPISTEMIC_LABELS)
        self.assertEqual(result["lenses"]["qimen"]["status"], "unavailable")
        self.assertEqual(result["lenses"]["liuren"]["reason"], "provider_not_configured")
        self.assertEqual(result["computed_lenses"], ["yijing", "ziwei"])
        self.assertFalse(result["is_prediction"])
        self.assertEqual(fuse("q2", seed="s")["lenses"]["yijing"], fuse("q3", seed="s")["lenses"]["yijing"])

    def test_stripped_or_upgraded_labels_are_rejected_after_json_roundtrip(self):
        result = json.loads(json.dumps(fuse("灵感", seed="x")))
        validate_labels(result)
        stripped = json.loads(json.dumps(result))
        stripped["prompts"][0]["epistemic_labels"] = ["symbolic_inspiration"]
        with self.assertRaises(XuanshuLabelError):
            validate_labels(stripped)
        upgraded = json.loads(json.dumps(result))
        upgraded["is_evidence"] = True
        with self.assertRaises(XuanshuLabelError):
            validate_labels(upgraded)
        status = json.loads(json.dumps(result))
        status["status"] = "verified prediction"
        with self.assertRaises(XuanshuLabelError):
            validate_labels(status)


class FeedbackBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.claims = ClaimDossierStore(root / "claims.sqlite3")
        self.claims.append({"claim_id": "hook-3s", "claim": "hooks", "verdict": "insufficient"})
        self.bridge = FeedbackBridge(root / "feedback.sqlite3", self.claims, clock=lambda: 9.0)
        self.manifest = {"schema": "muse.feedback.v1", "claim_id": "hook-3s",
                         "results": [{"metric": "retention_3s", "value": 0.71}, {"metric": "note", "value": "粤语版更好"}]}

    def tearDown(self):
        self.bridge.close()
        self.claims.close()
        if gc:
            gc.collect()
        self.tmp.cleanup()

    def _send(self, name, data, sha=None):
        return self.bridge.upload(filename=name, content_base64=base64.b64encode(data).decode(),
                                  declared_sha256=sha or hashlib.sha256(data).hexdigest())

    def test_json_upload_is_idempotent_and_linked_tested_by(self):
        data = json.dumps(self.manifest, ensure_ascii=False).encode()
        first = self._send("worksheet.json", data)
        self.assertEqual((first["status"], first["relation"]), ("accepted", "tested_by"))
        self.assertEqual(self._send("worksheet.json", data)["status"], "duplicate")
        self.assertEqual(self.claims.feedback_for("hook-3s"), [first["feedback_id"]])
        with self.assertRaisesRegex(FeedbackRejected, "SHA-256"):
            self._send("worksheet.json", data + b" ", sha=hashlib.sha256(data).hexdigest())

    def test_html_worksheet_parsed_without_execution(self):
        html = ("<html><body><script>alert(1)</script><script type=\"application/json\" id=\"muse-feedback\">"
                + json.dumps(self.manifest, ensure_ascii=False) + "</script></body></html>").encode()
        self.assertEqual(self._send("表单.html", html)["format"], "html")
        with self.assertRaisesRegex(FeedbackRejected, "exactly one"):
            parse_upload("x.html", b"<html><script>1</script></html>")

    def _zip(self, members, manifest_files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({**self.manifest, "files": manifest_files}))
            for name, data in members.items():
                archive.writestr(name, data)
        return buffer.getvalue()

    def test_zip_verification_zip_slip_bomb_and_unlisted(self):
        curve = b"second,retention\n1,0.9\n3,0.71\n"
        good = self._zip({"curve.csv": curve}, [{"name": "curve.csv", "sha256": hashlib.sha256(curve).hexdigest()}])
        self.assertEqual(self._send("bundle.zip", good)["verified_files"], 1)
        bad_hash = self._zip({"curve.csv": curve}, [{"name": "curve.csv", "sha256": "0" * 64}])
        with self.assertRaisesRegex(FeedbackRejected, "mismatch"):
            parse_upload("b.zip", bad_hash)
        with self.assertRaisesRegex(FeedbackRejected, "unsafe"):
            parse_upload("b.zip", self._zip({"../../evil.py": b"x"}, []))
        with self.assertRaisesRegex(FeedbackRejected, "not listed"):
            parse_upload("b.zip", self._zip({"extra.bin": b"x"}, []))
        with self.assertRaisesRegex(FeedbackRejected, "zip bomb"):
            parse_upload("b.zip", self._zip({"big.txt": b"0" * 2_000_000}, [{"name": "big.txt", "sha256": "1" * 64}]))

    def test_unknown_claim_and_stripped_symbolic_labels_rejected(self):
        with self.assertRaisesRegex(FeedbackRejected, "existing Claim"):
            self._send("w.json", json.dumps({**self.manifest, "claim_id": "nope"}).encode())
        xuanshu = json.loads(json.dumps(fuse("灵感", seed="s")))
        xuanshu["epistemic_labels"] = []
        with self.assertRaisesRegex(FeedbackRejected, "labels"):
            self._send("w.json", json.dumps({**self.manifest, "xuanshu": xuanshu}).encode())
        with self.assertRaisesRegex(FeedbackRejected, "filename"):
            self._send("../evil.json", b"{}")


if __name__ == "__main__":
    unittest.main()
