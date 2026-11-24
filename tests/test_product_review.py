"""Human review promotion for offline products (draft -> creator testing).

Builds are intact drafts (BUILDER_VERSION 5). Only a named human review bound to the
exact artifact hashes may promote a product to `testing`; nothing here publishes.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from muse.initiative_engine import InitiativeStore
from muse.product_builder import LocalProductBuilder, ProductBuildError


def _concept(*, with_experiments: bool = True) -> str:
    discoveries = {
        "creator_inspired_hypotheses": ["结构化记录可减少关键步骤遗漏。"],
        "testable_experiments": ["交替使用两种记录格式，比较遗漏数量。"] if with_experiments else [],
        "counter_evidence": [],
        "sources": [],
        "uncertainties": [],
    }
    return json.dumps({
        "research_question": "结构化记录能否提高可审计性？",
        "creator_hypothesis_summary": "假设：结构化记录减少遗漏。",
        "key_discoveries": discoveries,
    }, ensure_ascii=False)


class ProductReviewPromotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = InitiativeStore(self.root / "data" / "initiatives" / "muse.sqlite3")
        self.builder = LocalProductBuilder(self.root)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _initiative(self, *, with_experiments: bool = True) -> dict:
        return self.store.propose(
            "Review fixture" + ("" if with_experiments else " incomplete"), kind="tool",
            creator_value="Creator review test", concept=_concept(with_experiments=with_experiments),
            first_step="Run a reversible experiment", expected_output="An offline worksheet",
            capability_gap="Review gate", priority=50, autonomy_level="draft",
        )

    def test_build_is_draft_and_approval_promotes_to_testing(self):
        item = self._initiative()
        draft = self.builder.build(self.store, item["id"])
        self.assertFalse(draft["validated"])
        self.assertEqual(draft["status"], "draft")
        self.assertNotEqual(self.store.get_by_id(item["id"])["status"], "testing")

        approved = self.builder.approve_for_testing(self.store, item["id"], reviewed_by="creator")
        self.assertTrue(approved["validated"])
        self.assertEqual(approved["status"], "testing")
        self.assertFalse(approved["publishable"])
        self.assertEqual(approved["human_review"]["reviewed_by"], "creator")
        self.assertEqual(approved["package_sha256"], draft["package_sha256"])
        self.assertEqual(self.store.get_by_id(item["id"])["status"], "testing")
        # verify() and a repeated build() both see the bound review.
        self.assertEqual(self.builder.verify(self.store, item["id"])["status"], "testing")
        self.assertEqual(self.builder.build(self.store, item["id"])["status"], "testing")

    def test_approval_is_idempotent(self):
        item = self._initiative()
        self.builder.build(self.store, item["id"])
        first = self.builder.approve_for_testing(self.store, item["id"], reviewed_by="creator")
        second = self.builder.approve_for_testing(self.store, item["id"], reviewed_by="creator")
        self.assertEqual(first["human_review"], second["human_review"])

    def test_approval_requires_named_reviewer(self):
        item = self._initiative()
        self.builder.build(self.store, item["id"])
        for reviewer in ("", "   ", "x" * 201, "a\x00b"):
            with self.subTest(reviewer=repr(reviewer)):
                with self.assertRaises(ValueError):
                    self.builder.approve_for_testing(self.store, item["id"], reviewed_by=reviewer)
        self.assertEqual(self.builder.verify(self.store, item["id"])["status"], "draft")

    def test_approval_refuses_incomplete_content(self):
        item = self._initiative(with_experiments=False)
        draft = self.builder.build(self.store, item["id"])
        self.assertFalse(draft["content_readiness"]["ready_for_human_review"])
        with self.assertRaises(ProductBuildError):
            self.builder.approve_for_testing(self.store, item["id"], reviewed_by="creator")
        self.assertEqual(self.builder.verify(self.store, item["id"])["status"], "draft")

    def test_approval_requires_built_product(self):
        item = self._initiative()
        with self.assertRaises(ProductBuildError):
            self.builder.approve_for_testing(self.store, item["id"], reviewed_by="creator")

    def test_tampered_review_record_fails_closed(self):
        item = self._initiative()
        self.builder.build(self.store, item["id"])
        approved = self.builder.approve_for_testing(self.store, item["id"], reviewed_by="creator")
        review_path = self.root / approved["relative_path"] / "review.json"
        record = json.loads(review_path.read_text(encoding="utf-8"))
        for field, value in (("package_sha256", "0" * 64), ("publishable", True),
                             ("decision", "approved_for_release"), ("reviewed_by", "")):
            with self.subTest(field=field):
                tampered = dict(record, **{field: value})
                review_path.write_text(json.dumps(tampered), encoding="utf-8")
                with self.assertRaises(ProductBuildError):
                    self.builder.verify(self.store, item["id"])
        review_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ProductBuildError):
            self.builder.verify(self.store, item["id"])

    def test_modified_artifact_invalidates_review(self):
        item = self._initiative()
        self.builder.build(self.store, item["id"])
        approved = self.builder.approve_for_testing(self.store, item["id"], reviewed_by="creator")
        html_path = self.root / approved["relative_path"] / "index.html"
        html_path.write_bytes(html_path.read_bytes() + b"<!-- tampered -->")
        with self.assertRaises(ProductBuildError):
            self.builder.verify(self.store, item["id"])


if __name__ == "__main__":
    unittest.main()
