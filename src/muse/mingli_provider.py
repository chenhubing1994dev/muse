"""Optional local Mingli-AI (真一命理) OpenAI-compatible interpretation adapter.

The adapter accepts an already-resolved Muse four-pillar chart. It never computes
calendar facts, never stores birth details, and refuses non-loopback endpoints so
birth-derived chart data cannot silently leave the local machine.
"""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from typing import Any

from muse.sexagenary import STEMS, BRANCHES

_VALID_GANZHI = frozenset(STEMS[i % 10] + BRANCHES[i % 12] for i in range(60))

ENGINE_ID = "mingli-ai"
UPSTREAM_REPOSITORY = "https://github.com/ZHENYI-ORG/Mingli-AI"
UPSTREAM_LICENSE = "Apache-2.0 (model weights/fine-tune); MIT (source code; verify exact files)"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CHART_BYTES = 64 * 1024


class MingliUnavailable(RuntimeError):
    """Raised when the configured local Mingli endpoint cannot be reached."""


class MingliExecutionError(RuntimeError):
    """Raised when Mingli returns an invalid or incompatible response."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _loopback_base_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("base_url must be a local HTTP URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.port is None
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Mingli endpoint must be explicit loopback HTTP with a port")
    return value.rstrip("/")


def _validate_chart(chart: object) -> dict[str, Any]:
    if not isinstance(chart, dict) or set(chart) - {"year", "month", "day", "hour", "gender", "calendar_context_fingerprint", "calendar_policy", "source_refs", "zhenyi_chart"}:
        raise ValueError("chart must be a bounded Muse four-pillar chart object")
    pillars = {key: chart.get(key) for key in ("year", "month", "day", "hour")}
    if any(not isinstance(value, str) or len(value) != 2 for value in pillars.values()):
        raise ValueError("chart must contain four explicit two-character pillars")
    if any(not isinstance(value, str) or value not in _VALID_GANZHI for value in pillars.values()):
        raise ValueError("chart contains an invalid Heavenly-Stem/Earthly-Branch pillar")
    if "gender" in chart and chart["gender"] not in {"男", "女", "male", "female"}:
        raise ValueError("gender must be explicitly male/female when supplied")
    if "zhenyi_chart" in chart and not isinstance(chart["zhenyi_chart"], dict):
        raise ValueError("zhenyi_chart must be a bounded object")
    encoded = json.dumps(chart, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_CHART_BYTES:
        raise ValueError("chart exceeds the local adapter size limit")
    return chart


class MingliAIProvider:
    """Call a user-configured loopback Mingli-AI OpenAI-compatible server."""

    def __init__(
        self, base_url: str = "http://127.0.0.1:8787",
        *, model: str, timeout_seconds: float = 185.0,
        opener: Any | None = None,
    ) -> None:
        self.base_url = _loopback_base_url(base_url)
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("model must be explicitly configured")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 240:
            raise ValueError("timeout_seconds must be between 1 and 240")
        self.model = model.strip()
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener or build_opener(_NoRedirect(), ProxyHandler({}))

    def list_models(self) -> list[str]:
        """Read the configured local Mingli API model catalogue."""
        request = Request(
            self.base_url + "/v1/models",
            headers={"Accept": "application/json"}, method="GET",
        )
        try:
            with self._opener.open(request, timeout=min(self.timeout_seconds, 15.0)) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise MingliUnavailable("local Mingli model catalogue is unavailable") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise MingliExecutionError("Mingli model catalogue exceeded the size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
            data = payload["data"]
            names = [item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise MingliExecutionError("Mingli returned an invalid model catalogue") from error
        if not isinstance(data, list):
            raise MingliExecutionError("Mingli model catalogue data must be a list")
        return names

    def model_available(self) -> bool:
        return self.model in self.list_models()

    def _system_message(self) -> dict[str, str]:
        return {
            "role": "system",
            "content": (
                "你是真一命理八字分析引擎。只按给定四柱作传统命理框架内的分析。"
                "不得补造出生资料、历法事实或缺失柱；区分流派规则、盘面结构、推断与猜测；"
                "列出采用的方法、关键推理依据、至少一种替代解释和局限。"
                "不得声称科学验证或确定预测；健康、投资、法律和重大人生决策不得给出确定性指令。"
                "输出简体中文，明确标注：传统框架解释／灵感假设，非事实证据。"
            ),
        }

    @staticmethod
    def _user_message(chart: dict[str, Any], question: str) -> dict[str, str]:
        pillars = " ".join(chart[key] for key in ("year", "month", "day", "hour"))
        gender_line = f"性别：{chart['gender']}\n" if chart.get("gender") else ""
        structured = json.dumps(
            {"four_pillars": {k: chart[k] for k in ("year", "month", "day", "hour")},
             "calendar_policy": chart.get("calendar_policy"),
             "source_refs": chart.get("source_refs", []),
             "zhenyi_chart": chart.get("zhenyi_chart"),
             "question": question},
            ensure_ascii=False, sort_keys=True,
        )
        # Mingli parses the latest user message for a chart; retain its documented
        # natural-language four-pillar form alongside Muse's structured provenance.
        return {
            "role": "user",
            "content": f"四柱为{pillars}。\n{gender_line}命盘结构化资料：\n{structured}\n问题：{question}",
        }

    def _complete(self, messages: list[dict[str, str]]) -> str:
        body = json.dumps({
            "model": self.model,
            "temperature": 0.2,
            "messages": messages,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + "/v1/chat/completions", data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise MingliUnavailable("local Mingli endpoint request failed") from error
        if len(raw) > MAX_RESPONSE_BYTES:
            raise MingliExecutionError("Mingli response exceeded the size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise MingliExecutionError("Mingli returned an invalid chat-completion response") from error
        if not isinstance(content, str) or not content.strip() or len(content) > 64_000:
            raise MingliExecutionError("Mingli returned empty or oversized interpretation text")
        return content.strip()

    def _result(self, chart: dict[str, Any], content: str) -> dict[str, Any]:
        return {
            "engine": ENGINE_ID,
            "upstream_repository": UPSTREAM_REPOSITORY,
            "model": self.model,
            "interpretation": content,
            "input_chart_fields": ["year", "month", "day", "hour"],
            "gender_supplied": bool(chart.get("gender")),
            "bazi_engine": "zhenyi-bazi" if chart.get("zhenyi_chart") else "muse-calendar-only",
            "calendar_context_fingerprint": chart.get("calendar_context_fingerprint"),
            "epistemic_status": "traditional_framework_interpretation_unverified",
            "is_evidence": False,
            "is_prediction": False,
            "requires_independent_review": True,
            "remote_data_transfer": False,
            "limitations": [
                "Only loopback HTTP endpoints are accepted; remote endpoints and redirects are refused.",
                "Mingli interpretation is a traditional-framework hypothesis, not empirical evidence.",
                "Model output requires independent review and reproducible benchmark cases.",
            ],
        }

    def interpret_bazi(self, chart: dict[str, Any], *, question: str) -> dict[str, Any]:
        chart = _validate_chart(chart)
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ValueError("question must contain 1-2000 characters")
        messages = [self._system_message(), self._user_message(chart, question)]
        return self._result(chart, self._complete(messages))

    def follow_up_bazi(
        self, chart: dict[str, Any], *, question: str, follow_up_question: str,
    ) -> dict[str, Any]:
        """Run the upstream-recommended two-turn protocol with full message history."""
        chart = _validate_chart(chart)
        if not isinstance(follow_up_question, str) or not follow_up_question.strip() or len(follow_up_question) > 2000:
            raise ValueError("follow_up_question must contain 1-2000 characters")
        first = self.interpret_bazi(chart, question=question)
        messages = [
            self._system_message(),
            self._user_message(chart, question),
            {"role": "assistant", "content": first["interpretation"]},
            {"role": "user", "content": follow_up_question.strip()},
        ]
        second = self._complete(messages)
        result = self._result(chart, second)
        result.update({
            "first_interpretation": first["interpretation"],
            "follow_up_interpretation": second,
            "evaluation_protocol": "same_session_two_turn_chart_then_followup",
        })
        return result
