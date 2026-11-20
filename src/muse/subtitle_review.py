from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timezone
from typing import Any


def _parse_srt_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value)
    if not match:
        raise ValueError("invalid SRT timestamp")
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    if minutes >= 60 or seconds >= 60:
        raise ValueError("invalid SRT timestamp")
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_srt_cues(srt_text: str) -> list[dict[str, Any]]:
    """Parse and validate SRT cues for cue-level human review."""
    if not isinstance(srt_text, str) or not srt_text.strip() or len(srt_text) > 500_000:
        raise ValueError("SRT text must contain 1-500000 characters")
    normalized = srt_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n\s*\n", normalized)
    if not 1 <= len(blocks) <= 500:
        raise ValueError("SRT must contain 1-500 cues")
    cues: list[dict[str, Any]] = []
    previous_end = 0.0
    for expected_index, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.split("\n")]
        if len(lines) < 3 or lines[0].strip() != str(expected_index):
            raise ValueError("SRT cue indexes must be sequential and one-based")
        timing = lines[1].split(" --> ")
        if len(timing) != 2:
            raise ValueError("invalid SRT cue timing line")
        start, end = (_parse_srt_timestamp(part.strip()) for part in timing)
        cue_text = "\n".join(lines[2:]).strip()
        if start < previous_end or end <= start or not cue_text:
            raise ValueError("SRT cue has invalid timing or empty text")
        cues.append({"index": expected_index, "start": start, "end": end, "text": cue_text})
        previous_end = end
    return cues


def build_subtitle_review(
    narration_script: str, narration_asr: dict[str, Any], *,
    subtitle_file: str = "narration-subtitles.srt",
    subtitle_cues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an auditable, explicitly unapproved ASR-vs-script review document.

    When actual SRT cues are supplied, the human workflow is cue-level rather than
    one giant Whisper segment. Original ASR segments remain separately preserved.
    """
    if not isinstance(narration_script, str) or not narration_script.strip() or len(narration_script) > 20000:
        raise ValueError("narration_script must contain 1-20000 characters")
    if not isinstance(narration_asr, dict):
        raise ValueError("narration_asr must be an object")
    transcript = narration_asr.get("transcript")
    asr_segments = narration_asr.get("segments")
    comparison = narration_asr.get("comparison")
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("narration_asr transcript must be non-empty")
    if not isinstance(asr_segments, list) or not asr_segments or len(asr_segments) > 500:
        raise ValueError("narration_asr segments must contain 1-500 entries")
    if not isinstance(comparison, dict):
        raise ValueError("narration_asr comparison is required")
    cer = comparison.get("cer")
    if isinstance(cer, bool) or not isinstance(cer, (int, float)) or not math.isfinite(cer) or not 0 <= cer <= 1:
        raise ValueError("narration_asr CER must be finite and between 0 and 1")
    if not isinstance(subtitle_file, str) or not subtitle_file or subtitle_file.replace("\\", "/").split("/")[-1] != subtitle_file:
        raise ValueError("subtitle_file must be a simple filename")

    validated_asr: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, segment in enumerate(asr_segments, start=1):
        if not isinstance(segment, dict):
            raise ValueError("narration_asr segment must be an object")
        start, end, text = segment.get("start"), segment.get("end"), segment.get("text")
        if (isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start)
                or isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end)
                or start < 0 or end <= start or start < previous_end
                or not isinstance(text, str) or not text.strip()):
            raise ValueError("narration_asr segment has invalid timing or text")
        avg_logprob = segment.get("avg_logprob")
        no_speech_prob = segment.get("no_speech_prob")
        for name, value in (("avg_logprob", avg_logprob), ("no_speech_prob", no_speech_prob)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"narration_asr {name} must be finite when provided")
        validated_asr.append({
            "index": index, "start": float(start), "end": float(end),
            "text": text.strip(), "avg_logprob": avg_logprob,
            "no_speech_prob": no_speech_prob,
        })
        previous_end = float(end)

    if subtitle_cues is not None:
        if not isinstance(subtitle_cues, list) or not 1 <= len(subtitle_cues) <= 500:
            raise ValueError("subtitle_cues must contain 1-500 cues")
        cue_source = subtitle_cues
    else:
        cue_source = [
            {"index": item["index"], "start": item["start"], "end": item["end"], "text": item["text"]}
            for item in validated_asr
        ]

    reviewed_segments: list[dict[str, Any]] = []
    previous_end = 0.0
    for expected_index, cue in enumerate(cue_source, start=1):
        if not isinstance(cue, dict):
            raise ValueError("subtitle cue must be an object")
        cue_index = cue.get("index", expected_index)
        start, end, cue_text = cue.get("start"), cue.get("end"), cue.get("text")
        if (isinstance(cue_index, bool) or cue_index != expected_index
                or isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start)
                or isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end)
                or start < 0 or end <= start or start < previous_end
                or not isinstance(cue_text, str) or not cue_text.strip()):
            raise ValueError("subtitle cue has invalid index, timing or text")
        overlaps = [
            item for item in validated_asr
            if min(float(end), item["end"]) > max(float(start), item["start"])
        ]
        weights = [min(float(end), item["end"]) - max(float(start), item["start"]) for item in overlaps]
        def weighted_confidence(key: str) -> float | None:
            available = [(item[key], weight) for item, weight in zip(overlaps, weights) if item[key] is not None]
            if not available:
                return None
            return sum(float(value) * weight for value, weight in available) / sum(weight for _, weight in available)
        reviewed_segments.append({
            "index": expected_index,
            "start_seconds": round(float(start), 3),
            "end_seconds": round(float(end), 3),
            "asr_text": cue_text.strip(),
            "avg_logprob": weighted_confidence("avg_logprob"),
            "no_speech_prob": weighted_confidence("no_speech_prob"),
            "source_asr_segment_indexes": [item["index"] for item in overlaps],
            "review_status": "pending",
            "corrected_text": None,
        })
        previous_end = float(end)

    return {
        "schema_version": 1,
        "review_status": "pending_human_review",
        "human_approved": False,
        "approval_timestamp": None,
        "subtitle_file": subtitle_file,
        "reference_script": narration_script.strip(),
        "raw_asr_transcript": transcript.strip(),
        "asr_segments": copy.deepcopy(asr_segments),
        "asr": {
            "engine": narration_asr.get("engine"),
            "model_repo": narration_asr.get("model_repo"),
            "model_revision": narration_asr.get("model_revision"),
            "audio_sha256": narration_asr.get("audio_sha256"),
            "cer": float(cer), "max_cer": comparison.get("max_cer"),
            "machine_status": comparison.get("status"),
        },
        "segments": reviewed_segments,
        "subtitle_cue_count": len(reviewed_segments),
        "corrections": [],
        "review_instructions": [
            "Listen to the original narration audio; do not rely on CER alone.",
            "Review every timestamped subtitle cue against the reference script and original audio.",
            "Record corrected_text and reviewer identity for each changed cue.",
            "Only a human reviewer may set human_approved=true and review_status=approved.",
        ],
    }




def apply_human_subtitle_review(
    review: dict[str, Any], *, reviewed_segment_indexes: list[int],
    corrections: list[dict[str, Any]], reviewer: str, approve: bool = False,
    reviewer_id: str | None = None, reviewer_role: str | None = None,
    rationale: str | None = None, evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Record explicit human review without modifying immutable ASR source text."""
    if not isinstance(review, dict) or review.get("schema_version") != 1:
        raise ValueError("unsupported subtitle review document")
    if review.get("human_approved") is True or review.get("review_status") == "approved":
        raise ValueError("approved subtitle review is immutable; create a new review revision")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer.strip()) > 120:
        raise ValueError("reviewer must contain 1-120 characters")
    segments = review.get("segments")
    if not isinstance(segments, list) or not segments or len(segments) > 500:
        raise ValueError("subtitle review segments are invalid")
    if not isinstance(reviewed_segment_indexes, list):
        raise ValueError("reviewed_segment_indexes must be a list")
    if any(not isinstance(item, dict) or isinstance(item.get("index"), bool)
           or not isinstance(item.get("index"), int) for item in segments):
        raise ValueError("subtitle review contains an invalid segment index")
    valid_indexes = {item["index"] for item in segments}
    if valid_indexes != set(range(1, len(segments) + 1)):
        raise ValueError("subtitle review segment indexes must be contiguous and one-based")
    if (any(isinstance(index, bool) or not isinstance(index, int) or index not in valid_indexes
            for index in reviewed_segment_indexes)
            or len(set(reviewed_segment_indexes)) != len(reviewed_segment_indexes)):
        raise ValueError("reviewed_segment_indexes contains an invalid or duplicate index")
    if not isinstance(corrections, list) or len(corrections) > len(segments):
        raise ValueError("corrections must be a bounded list")
    correction_map: dict[int, str] = {}
    for item in corrections:
        if not isinstance(item, dict) or set(item) != {"segment_index", "corrected_text"}:
            raise ValueError("each correction must contain only segment_index and corrected_text")
        index, corrected_text = item["segment_index"], item["corrected_text"]
        if (isinstance(index, bool) or not isinstance(index, int) or index not in valid_indexes
                or index not in reviewed_segment_indexes or index in correction_map
                or not isinstance(corrected_text, str) or not corrected_text.strip() or len(corrected_text) > 500):
            raise ValueError("correction is invalid, duplicated or not marked as reviewed")
        correction_map[index] = corrected_text.strip()
    if not isinstance(approve, bool):
        raise ValueError("approve must be a boolean")
    all_indexes = {item["index"] for item in segments}
    if approve and set(reviewed_segment_indexes) != all_indexes:
        raise ValueError("cannot approve until every subtitle segment has been reviewed")
    if approve:
        if not isinstance(reviewer_id, str) or not reviewer_id.strip() or len(reviewer_id.strip()) > 120:
            raise ValueError("reviewer_id must contain 1-120 characters")
        if not isinstance(reviewer_role, str) or not reviewer_role.strip() or len(reviewer_role.strip()) > 80:
            raise ValueError("reviewer_role must contain 1-80 characters")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale.strip()) > 2000:
            raise ValueError("approval rationale must contain 1-2000 characters")
        if (not isinstance(evidence_refs, list) or not evidence_refs or len(evidence_refs) > 30
                or any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in evidence_refs)):
            raise ValueError("evidence_refs must contain 1-30 non-empty references")
    result = copy.deepcopy(review)
    now = datetime.now(timezone.utc).isoformat()
    existing_corrections = result.get("corrections", [])
    if not isinstance(existing_corrections, list):
        raise ValueError("subtitle review correction audit must be a list")
    correction_audit = list(existing_corrections)
    for segment in result["segments"]:
        index = segment["index"]
        if index not in reviewed_segment_indexes:
            continue
        segment["reviewed_by"] = reviewer.strip()
        segment["reviewed_at"] = now
        if index in correction_map:
            corrected_text = correction_map[index]
            segment["corrected_text"] = corrected_text
            segment["review_status"] = "corrected"
            correction_audit.append({
                "segment_index": index, "asr_text": segment["asr_text"],
                "corrected_text": corrected_text, "reviewer": reviewer.strip(), "timestamp": now,
            })
        else:
            segment["corrected_text"] = None
            segment["review_status"] = "confirmed"
    result["corrections"] = correction_audit
    fully_reviewed = all(segment.get("review_status") in {"confirmed", "corrected"} for segment in result["segments"])
    result["human_approved"] = bool(approve and fully_reviewed)
    result["review_status"] = "approved" if result["human_approved"] else (
        "reviewed_pending_approval" if fully_reviewed else "partially_reviewed"
    )
    result["approval_timestamp"] = now if result["human_approved"] else None
    if result["human_approved"]:
        result["approved_by"] = reviewer.strip()
        result["reviewer_identity"] = {
            "id": reviewer_id.strip(), "role": reviewer_role.strip(),
            "identity_assurance": "self_asserted_local_identity",
        }
        result["approval_rationale"] = rationale.strip()
        result["approval_evidence_refs"] = list(evidence_refs)
    return result


def render_approved_subtitles(review: dict[str, Any]) -> str:
    """Render an SRT only from a fully human-approved review document."""
    if not isinstance(review, dict) or review.get("human_approved") is not True or review.get("review_status") != "approved":
        raise ValueError("human approval is required before rendering reviewed subtitles")
    segments = review.get("segments")
    if not isinstance(segments, list) or not segments or any(
        not isinstance(item, dict) or item.get("review_status") not in {"confirmed", "corrected"}
        for item in segments
    ):
        raise ValueError("every subtitle segment must have a human review record")

    def stamp(seconds: float) -> str:
        milliseconds = int(round(seconds * 1000))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        whole_seconds, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"

    cues = []
    for index, segment in enumerate(segments, start=1):
        start, end = segment.get("start_seconds"), segment.get("end_seconds")
        text = segment.get("corrected_text") or segment.get("asr_text")
        if (isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start)
                or isinstance(end, bool) or not isinstance(end, (int, float)) or not math.isfinite(end)
                or start < 0 or end <= start or not isinstance(text, str) or not text.strip()):
            raise ValueError("approved subtitle segment has invalid timing or text")
        cues.append(f"{index}\n{stamp(float(start))} --> {stamp(float(end))}\n{text.strip()}")
    return "\n\n".join(cues) + "\n"