from __future__ import annotations

import json
import unittest

from muse.mingli_provider import (
    MingliAIProvider, MingliExecutionError, MingliUnavailable,
)


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload, ensure_ascii=False).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size=-1): return self.payload[:size]


def _chart():
    return {
        "year": "甲辰", "month": "庚午", "day": "庚戌", "hour": "壬午",
        "calendar_context_fingerprint": "a" * 64,
        "calendar_policy": {"timezone": "Asia/Shanghai", "year_boundary": "lichun"},
        "source_refs": ["muse-calendar-fixture-v1"],
    }


class MingliAIProviderTests(unittest.TestCase):
    def test_loopback_request_returns_unverified_interpretation_without_birth_record(self):
        captured = {}
        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            body = json.loads(request.data)
            captured["body"] = body
            return _Response({"choices": [{"message": {"content": "传统框架解释：仅作假设。"}}]})
        provider = MingliAIProvider(model="mingli-test", opener=type("O", (), {"open": staticmethod(opener)})())
        result = provider.interpret_bazi(_chart(), question="分析结构与不同流派解释")
        self.assertEqual(result["engine"], "mingli-ai")
        self.assertFalse(result["is_evidence"])
        self.assertFalse(result["is_prediction"])
        self.assertFalse(result["remote_data_transfer"])
        self.assertEqual(captured["request"].full_url, "http://127.0.0.1:8787/v1/chat/completions")
        prompt = captured["body"]["messages"][1]["content"]
        self.assertNotIn("1990-", prompt)
        self.assertIn("甲辰", prompt)
        self.assertEqual(captured["timeout"], 185.0)

    def test_lists_models_from_local_openai_compatible_api(self):
        captured = {}
        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            return _Response({"data": [{"id": "ZHENYI"}]})
        provider = MingliAIProvider(model="ZHENYI", opener=type("O", (), {"open": staticmethod(opener)})())
        self.assertEqual(provider.list_models(), ["ZHENYI"])
        self.assertTrue(provider.model_available())
        self.assertEqual(captured["url"], "http://127.0.0.1:8787/v1/models")
        self.assertEqual(captured["method"], "GET")

    def test_includes_compact_zhenyi_chart_when_supplied(self):
        captured = {}
        def opener(request, timeout):
            captured["body"] = json.loads(request.data)
            return _Response({"choices": [{"message": {"content": "传统解释。"}}]})
        chart = _chart()
        chart["gender"] = "男"
        chart["zhenyi_chart"] = {"engine_version": "2.0.2", "pillars": {"year": "甲辰"}}
        provider = MingliAIProvider(model="test", opener=type("O", (), {"open": staticmethod(opener)})())
        result = provider.interpret_bazi(chart, question="test")
        prompt = captured["body"]["messages"][1]["content"]
        self.assertIn("zhenyi_chart", prompt)
        self.assertIn("四柱为甲辰 庚午 庚戌 壬午", prompt)
        self.assertIn("性别：男", prompt)
        self.assertEqual(result["bazi_engine"], "zhenyi-bazi")


    def test_two_turn_protocol_replays_full_conversation_history(self):
        calls = []
        answers = iter(("第一轮分析。", "第二轮选项判断。"))
        def opener(request, timeout):
            body = json.loads(request.data)
            calls.append(body)
            return _Response({"choices": [{"message": {"content": next(answers)}}]})
        provider = MingliAIProvider(model="test", opener=type("O", (), {"open": staticmethod(opener)})())
        result = provider.follow_up_bazi(
            _chart(), question="先分析命局结构",
            follow_up_question="现在给出 A/B/C 选项，请结合上一轮判断并说明理由",
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual([m["role"] for m in calls[1]["messages"]], ["system", "user", "assistant", "user"])
        self.assertEqual(calls[1]["messages"][2]["content"], "第一轮分析。")
        self.assertEqual(result["first_interpretation"], "第一轮分析。")
        self.assertEqual(result["follow_up_interpretation"], "第二轮选项判断。")
        self.assertEqual(result["evaluation_protocol"], "same_session_two_turn_chart_then_followup")

    def test_rejects_remote_urls_and_bad_chart_before_network(self):
        for url in ("https://example.com", "http://192.168.1.5:8000", "http://127.0.0.1:8000/path"):
            with self.assertRaises(ValueError):
                MingliAIProvider(url, model="test")
        provider = MingliAIProvider(model="test", opener=type("O", (), {"open": staticmethod(lambda *a, **k: self.fail("must not call"))})())
        invalid_cycle = _chart()
        invalid_cycle["year"] = "甲丑"
        with self.assertRaises(ValueError):
            provider.interpret_bazi(invalid_cycle, question="test")
        with self.assertRaises(ValueError):
            provider.interpret_bazi({"year": "甲辰", "month": "庚午", "day": "庚戌"}, question="test")

    def test_rejects_invalid_or_oversized_model_response(self):
        provider = MingliAIProvider(model="test", opener=type("O", (), {"open": staticmethod(lambda *a, **k: _Response({"choices": []}))})())
        with self.assertRaises(MingliExecutionError):
            provider.interpret_bazi(_chart(), question="test")

    def test_endpoint_failure_is_redacted(self):
        def fail(*args, **kwargs):
            raise OSError("private endpoint detail")
        provider = MingliAIProvider(model="test", opener=type("O", (), {"open": staticmethod(fail)})())
        with self.assertRaisesRegex(MingliUnavailable, "local Mingli endpoint"):
            provider.interpret_bazi(_chart(), question="test")


if __name__ == "__main__":
    unittest.main()
