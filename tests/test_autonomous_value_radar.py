from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from muse.autonomous_value_radar import (
    build_daily_value_radar,
    make_value_radar_routine_handler,
    write_daily_value_radar,
)


def sample_report():
    return {
        "topic_key": "creator-video-workflow",
        "evidence_coverage_score": 72,
        "sources": [
            {"url": "https://example.com/a", "title": "Source A"},
            {"url": "https://example.com/b", "title": "Source B"},
        ],
        "synthesis": {
            "executive_summary": "两项公开来源指出短视频制作流程存在重复劳动 [S1][S2]",
            "counter_evidence": ["小样本不能证明普遍需求 [S2]"],
            "testable_experiments": ["比较人工与模板化剪辑耗时 [S1]"],
        },
    }


class AutonomousValueRadarTests(unittest.TestCase):
    def test_daily_radar_ranks_hypotheses_without_revenue_claims(self):
        result = build_daily_value_radar(
            [{"report_path": "reports/research/sample.json", "report": sample_report()}],
            as_of=date(2026, 11, 24),
        )
        self.assertEqual(result["status"], "ranked_hypotheses")
        self.assertEqual(result["as_of"], "2026-11-24")
        self.assertGreaterEqual(result["summary"]["candidates"], 1)
        self.assertEqual(result["candidates"][0]["evidence_state"], "hypothesis_only")
        self.assertIsNone(result["candidates"][0]["revenue_forecast"])
        self.assertTrue(result["candidates"][0]["requires_human_review"])
        self.assertFalse(result["autonomy_policy"]["may_publish_or_contact_customers"])
        self.assertFalse(result["autonomy_policy"]["may_spend_or_transfer_funds"])

    def test_cycle_id_is_deterministic_and_empty_scan_is_explicit(self):
        item = {"report_path": "reports/research/sample.json", "report": sample_report()}
        first = build_daily_value_radar([item], as_of="2026-11-24")
        second = build_daily_value_radar([item], as_of="2026-11-24")
        self.assertEqual(first["cycle_id"], second["cycle_id"])
        empty = build_daily_value_radar([], as_of="2026-11-24")
        self.assertEqual(empty["status"], "no_new_opportunities")
        self.assertEqual(empty["summary"]["reports_scanned"], 0)

    def test_rejects_oversized_or_malformed_report_batches(self):
        with self.assertRaises(ValueError):
            build_daily_value_radar([{}])
        with self.assertRaises(ValueError):
            build_daily_value_radar(
                [{"report_path": "x", "report": sample_report()}] * 21
            )
        with self.assertRaises(ValueError):
            build_daily_value_radar(
                [{"report_path": "bad" + chr(0) + "path", "report": sample_report()}]
            )

    def test_write_is_idempotent_and_conflict_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            radar = build_daily_value_radar([], as_of="2026-11-24")
            first = write_daily_value_radar(directory, radar)
            second = write_daily_value_radar(directory, radar)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(first.read_text(encoding="utf-8")), radar)
            changed = dict(radar, mission="different")
            with self.assertRaises(FileExistsError):
                write_daily_value_radar(directory, changed)

    def test_write_rejects_symlink_output_directory(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "reports" / "value-radar").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                write_daily_value_radar(root, build_daily_value_radar([], as_of="2026-11-24"))

    def test_routine_handler_uses_existing_routine_context_and_bounded_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            seen = []
            def loader(limit):
                seen.append(limit)
                return [{"report_path": "reports/research/sample.json", "report": sample_report()}]
            handler = make_value_radar_routine_handler(workspace=directory, report_loader=loader)
            self.assertTrue(getattr(handler, "accepts_execution_context", False))
            context = type("Context", (), {"raise_if_cancelled": lambda self: None})()
            result = handler({"max_reports": 3}, execution_context=context)
            self.assertEqual(seen, [3])
            self.assertEqual(result["status"], "ranked_hypotheses")
            self.assertFalse(result["external_actions_performed"])
            self.assertTrue((Path(directory) / result["report_path"]).is_file())

    def test_routine_handler_honors_cancellation_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            called = []
            def loader(_limit):
                called.append(True)
                return []
            handler = make_value_radar_routine_handler(workspace=directory, report_loader=loader)
            class Cancelled:
                def raise_if_cancelled(self):
                    raise RuntimeError("cancelled")
            with self.assertRaises(RuntimeError):
                handler({}, execution_context=Cancelled())
            self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
