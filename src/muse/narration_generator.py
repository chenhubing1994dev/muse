"""Local, deterministic Cantonese narration generation for Muse Phase 2.

No paid API, no network, no model download. A caller may inject a planner
callable (for example ``LocalModelCommanderPlanner``); its output is treated as
untrusted and validated. Invalid or missing planners fall back to a bounded,
deterministic Cantonese template pipeline.
"""
from __future__ import annotations

from collections.abc import Sequence

import re
from enum import Enum
from typing import Any, Callable
from muse.error_journal import record_suppressed_error

_MAX_TOPIC_CHARS = 200
_MAX_INSPIRATION_CHARS = 200
_MAX_RESEARCH_CHARS = 12_000
_MAX_SEGMENT_CHARS = 280
_MAX_SEGMENTS = 16
_CLAIM_ID = re.compile(r"^[0-9a-f]{32}$")
_MIN_SEGMENTS = 3
_MAX_PLANNER_TEXT_CHARS = 600
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")
_CLAUSE_SPLIT = re.compile(r"(?<=[，,、：:])")


class NarrationStyle(str, Enum):
    YUJIE_CANTO = "yujie_canto"
    CUTE_CANTO = "cute_canto"
    NEWS_CANTO = "news_canto"


# Voice profiles. The yujie profile is pinned by the Phase 2 contract.
_VOICE_PROFILES: dict[NarrationStyle, dict[str, object]] = {
    NarrationStyle.YUJIE_CANTO: {
        "speed_scale": 0.85,
        "pitch_semitones": -2,
        "breath_hint": True,
    },
    NarrationStyle.CUTE_CANTO: {
        "speed_scale": 1.12,
        "pitch_semitones": 3,
        "breath_hint": False,
    },
    NarrationStyle.NEWS_CANTO: {
        "speed_scale": 1.15,
        "pitch_semitones": 0,
        "breath_hint": False,
    },
}

_OPENERS: dict[NarrationStyle, tuple[str, ...]] = {
    NarrationStyle.YUJIE_CANTO: ("係㗎，", "講返", "慢慢嚟，", "呢part係", ">–< ",),
    NarrationStyle.CUTE_CANTO: ("✨ ", "喂喂～", "叮！", "估唔到呀，", "嘻嘻，"),
    NarrationStyle.NEWS_CANTO: ("本台報道：", "接下來，", "據悉，", "與此同時，", "此外，"),
}
_CONNECTORS: dict[NarrationStyle, tuple[str, ...]] = {
    NarrationStyle.YUJIE_CANTO: ("不过呢，", "再加上，", "讲真，", "顺便一提，"),
    NarrationStyle.CUTE_CANTO: ("然後呀～", "还有哦！", "噔噔～", "而且呢，"),
    NarrationStyle.NEWS_CANTO: ("同時，", "另一方面，", "此外，", "再者，"),
}
_ENDINGS: dict[NarrationStyle, tuple[str, ...]] = {
    NarrationStyle.YUJIE_CANTO: ("…係咁㗎啦。", "，你懂㗎。", "，慢慢消化。"),
    NarrationStyle.CUTE_CANTO: ("～✨", "呀～", "超可愛㗎啦～"),
    NarrationStyle.NEWS_CANTO: ("。", "。以上為本段報道。", "。"),
}
_EMOTIONS: dict[NarrationStyle, tuple[str, ...]] = {
    NarrationStyle.YUJIE_CANTO: ("calm", "warm", "thoughtful", "confident"),
    NarrationStyle.CUTE_CANTO: ("bright", "playful", "excited", "curious"),
    NarrationStyle.NEWS_CANTO: ("neutral", "formal", "measured", "neutral"),
}


def synth_params(style: NarrationStyle | str = NarrationStyle.YUJIE_CANTO) -> dict[str, object]:
    """Return a copy of the voice profile for ``style`` (yujie is pinned)."""
    resolved = _resolve_style(style)
    return dict(_VOICE_PROFILES[resolved])


def _resolve_style(style: NarrationStyle | str) -> NarrationStyle:
    if isinstance(style, NarrationStyle):
        return style
    if isinstance(style, str):
        for candidate in NarrationStyle:
            if style == candidate.value or style.casefold() == candidate.name.casefold():
                return candidate
    raise ValueError("style must be a NarrationStyle or one of its names/values")


def _bounded_text(value: Any, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} must not be empty")
    return text[:limit]


def _split_sentences(text: str) -> list[str]:
    pieces = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part and part.strip()]
    return pieces or [text.strip()]


def _split_to_chunks(text: str, target: int) -> list[str]:
    sentences = _split_sentences(text)
    if len(sentences) >= target:
        return sentences
    chunks: list[str] = []
    for sentence in sentences:
        clauses = [part.strip() for part in _CLAUSE_SPLIT.split(sentence) if part and part.strip()]
        if len(clauses) > 1 and len(chunks) + len(clauses) <= max(target * 2, 8):
            chunks.extend(clauses)
        else:
            chunks.append(sentence)
    return chunks


def _distribute(items: list[str], count: int) -> list[list[str]]:
    """Split items into contiguous, balanced buckets without reordering or duplication."""
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")
    if not items:
        return [[] for _ in range(count)]

    bucket_count = min(count, len(items))
    base_size, remainder = divmod(len(items), bucket_count)
    buckets: list[list[str]] = []
    offset = 0
    for index in range(bucket_count):
        size = base_size + (1 if index < remainder else 0)
        buckets.append(items[offset:offset + size])
        offset += size
    buckets.extend([] for _ in range(count - bucket_count))
    return buckets


def _truncate(text: str, limit: int = _MAX_SEGMENT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class NarrationGenerator:
    """Generate bounded, structured Cantonese narration segments offline."""

    def __init__(self, planner: Callable[[str], Any] | None = None) -> None:
        if planner is not None and not callable(planner):
            raise ValueError("planner must be callable or None")
        self.planner = planner

    def generate(
        self,
        topic: str,
        research_text: str,
        inspiration: str,
        style: NarrationStyle | str,
        *,
        segments: int = 3,
        claim_ids_by_segment: Sequence[Sequence[str]] | None = None,
        factual_by_segment: Sequence[bool] | None = None,
    ) -> dict[str, Any]:
        resolved_style = _resolve_style(style)
        clean_topic = _bounded_text(topic, _MAX_TOPIC_CHARS, "topic")
        clean_inspiration = _bounded_text(inspiration, _MAX_INSPIRATION_CHARS, "inspiration")
        if not isinstance(research_text, str) or not research_text.strip():
            raise ValueError("research_text must not be empty")
        if isinstance(segments, bool) or not isinstance(segments, int) or not _MIN_SEGMENTS <= segments <= _MAX_SEGMENTS:
            raise ValueError(f"segments must be between {_MIN_SEGMENTS} and {_MAX_SEGMENTS}")
        normalized_claim_ids: list[list[str]] | None = None
        if claim_ids_by_segment is not None:
            if (not isinstance(claim_ids_by_segment, Sequence)
                    or isinstance(claim_ids_by_segment, (str, bytes))
                    or len(claim_ids_by_segment) != segments):
                raise ValueError("claim_ids_by_segment must match the generated segment count")
            normalized_claim_ids = []
            for values in claim_ids_by_segment:
                if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                    raise ValueError("each segment claim mapping must be a sequence")
                unique_ids: list[str] = []
                for value in values:
                    if not isinstance(value, str) or not _CLAIM_ID.fullmatch(value):
                        raise ValueError("claim IDs must be 32-character lowercase hexadecimal strings")
                    if value not in unique_ids:
                        unique_ids.append(value)
                normalized_claim_ids.append(unique_ids)
        normalized_factual: list[bool] | None = None
        if factual_by_segment is not None:
            if (not isinstance(factual_by_segment, Sequence)
                    or isinstance(factual_by_segment, (str, bytes))
                    or len(factual_by_segment) != segments
                    or any(not isinstance(value, bool) for value in factual_by_segment)):
                raise ValueError("factual_by_segment must contain one boolean per generated segment")
            normalized_factual = list(factual_by_segment)
        research = " ".join(research_text.split())[:_MAX_RESEARCH_CHARS]

        planned = self._plan(topic=clean_topic, inspiration=clean_inspiration, style=resolved_style, segments=segments)
        if planned is not None:
            texts, emotions, source = planned
        else:
            texts, emotions = self._fallback(research, clean_topic, clean_inspiration, resolved_style, segments)
            source = "deterministic_template"

        result_segments = []
        for index, (text, emotion) in enumerate(zip(texts, emotions), 1):
            result_segments.append({
                "index": index,
                "text": _truncate(text),
                "estimated_duration_seconds": self._estimate_duration(text),
                "emotion": emotion,
            })
            if normalized_claim_ids is not None or normalized_factual is not None:
                result_segments[-1]["claim_ids"] = (
                    normalized_claim_ids[index - 1] if normalized_claim_ids is not None else []
                )
                result_segments[-1]["factual"] = (
                    normalized_factual[index - 1] if normalized_factual is not None else True
                )
        return {
            "topic": clean_topic,
            "inspiration": clean_inspiration,
            "style": resolved_style.value,
            "source": source,
            "segments": result_segments,
        }
    @staticmethod
    def _estimate_duration(text: str) -> float:
        # Cantonese speech is roughly 4-5 spoken characters per second; keep a
        # short minimum so every segment owns a non-zero slice of the timeline.
        return round(max(1.0, min(60.0, len(text.strip()) / 4.5)), 3)

    def _plan(self, *, topic: str, inspiration: str, style: NarrationStyle, segments: int) -> tuple[list[str], list[str], str] | None:
        if self.planner is None:
            return None
        try:
            payload = self.planner(
                "\n".join([
                    "Generate spoken Cantonese narration segments only.",
                    f"topic: {topic}",
                    f"inspiration: {inspiration}",
                    f"style: {style.value}",
                    f"count: {segments}",
                    "Return a JSON array of objects with text and emotion fields.",
                ])
            )
        except Exception as _muse_suppressed:
            record_suppressed_error("narration_generator.py:246", _muse_suppressed)
            return None
        if not isinstance(payload, dict):
            payload = {"segments": payload}
        raw = payload.get("segments", payload.get("steps")) if isinstance(payload, dict) else None
        if not isinstance(raw, list) or len(raw) < segments:
            return None
        texts: list[str] = []
        emotions: list[str] = []
        for item in raw[:segments]:
            if not isinstance(item, dict):
                return None
            text = item.get("text", item.get("text_zh", item.get("content")))
            emotion = item.get("emotion", "neutral")
            if (not isinstance(text, str) or not text.strip()
                    or len(text.strip()) > _MAX_PLANNER_TEXT_CHARS
                    or not isinstance(emotion, str) or not emotion.strip() or len(emotion) > 40):
                return None
            texts.append(text.strip())
            emotions.append(emotion.strip())
        return texts, emotions, "planner"

    @staticmethod
    def _fallback(
        research: str, topic: str, inspiration: str, style: NarrationStyle, count: int,
    ) -> tuple[list[str], list[str]]:
        buckets = _distribute(_split_to_chunks(research, count), count)
        openers = _OPENERS[style]
        connectors = _CONNECTORS[style]
        endings = _ENDINGS[style]
        emotions = _EMOTIONS[style]
        texts: list[str] = []
        for index, bucket in enumerate(buckets):
            body = " ".join(bucket).strip() or f"關於{topic}嘅內容"
            if index == 0:
                prefix = openers[0]
                context = f"今日講吓「{topic}」：{inspiration}。" if style != NarrationStyle.NEWS_CANTO else f"本台報道「{topic}」。"
                text = prefix + context + body + endings[index % len(endings)]
            else:
                text = connectors[(index - 1) % len(connectors)] + body + endings[index % len(endings)]
            texts.append(text)
        return texts, [emotions[index % len(emotions)] for index in range(count)]


def apply_voice_style(segment: dict[str, Any], style: NarrationStyle | str) -> dict[str, Any]:
    """Pure function: return a new segment with a copied ``voice`` profile."""
    if not isinstance(segment, dict) or not isinstance(segment.get("text"), str) or not segment["text"].strip():
        raise ValueError("segment must be a mapping with non-empty text")
    result = dict(segment)
    result["voice"] = synth_params(style)
    return result


def synthesize_cantonese_styled(
    text: str,
    output_path: str | Any,
    *,
    model_dir: str | Any,
    style: NarrationStyle | str = NarrationStyle.YUJIE_CANTO,
    quality: str = "duration_filter",
    synthesize_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Wrap the existing local TTS adapter with voice-style metadata only.

    The existing ``synthesize_cantonese`` signature is unchanged. This wrapper
    never forwards style/synthesize_fn into it; the caller may inject a local
    synthesis callable for offline testing.
    """
    resolved = _resolve_style(style)
    if synthesize_fn is None:
        from muse.cantonese_tts import synthesize_cantonese as synthesize_fn  # type: ignore[assignment]
    result = synthesize_fn(
        text, output_path, model_dir=model_dir, quality=quality,
    )
    if not isinstance(result, dict):
        raise ValueError("synthesis callable must return a dict")
    enriched = dict(result)
    enriched["style"] = resolved.value
    enriched["voice_style"] = synth_params(resolved)
    return enriched
