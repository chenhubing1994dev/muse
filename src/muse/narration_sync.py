"""SQLite-backed narration runs, timeline alignment and subtitle cue export.

Pure standard library: sqlite3 for persistence and wave for WAV duration.
"""
from __future__ import annotations

from collections.abc import Mapping

import json
import math
import sqlite3
import wave
from datetime import datetime, timezone
from pathlib import Path
import uuid
from typing import Any, Iterable


def wav_duration_seconds(path: str | Path) -> float:
    """Read a PCM WAV duration with the standard library ``wave`` module."""
    with wave.open(str(Path(path).expanduser()), "rb") as audio:
        rate = audio.getframerate()
        frames = audio.getnframes()
    if rate <= 0 or frames < 0:
        raise ValueError("invalid WAV frame rate or frame count")
    return frames / rate


def _srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    whole, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def _ass_time(seconds: float) -> str:
    centis = int(round(seconds * 100))
    hours, rest = divmod(centis, 360_000)
    minutes, rest = divmod(rest, 6_000)
    whole, centi = divmod(rest, 100)
    return f"{hours:02d}:{minutes:02d}:{whole:02d}.{centi:02d}"


class NarrationSyncStore:
    """Build timing artefacts and persist narration runs in SQLite."""

    ASS_STYLES = {
        "hk-yellow-white": (
            "Style: HKCanto,Microsoft YaHei,42,&H0000FFFF&,"
            "&H00FFFFFF&,&H00000000&,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1"
        ),
    }

    def __init__(self, database: str | Path) -> None:
        self.path = Path(database).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS narration_runs(
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                style TEXT NOT NULL,
                segments_json TEXT NOT NULL,
                audio_path TEXT,
                timeline_json TEXT NOT NULL,
                subtitle_path TEXT,
                created_at TEXT NOT NULL,
                evidence_contract_json TEXT
            );
            CREATE INDEX IF NOT EXISTS narration_runs_created_at
                ON narration_runs(created_at DESC);
        """)
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(narration_runs)")
        }
        if "evidence_contract_json" not in columns:
            self.connection.execute(
                "ALTER TABLE narration_runs ADD COLUMN evidence_contract_json TEXT"
            )
        self.connection.commit()
    def __enter__(self) -> NarrationSyncStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def align_segments(segments: Iterable[dict[str, Any]], audio_duration_seconds: float) -> list[dict[str, Any]]:
        """Normalise estimated durations onto a contiguous [0, duration] timeline."""
        items = list(segments)
        if not items:
            raise ValueError("segments must not be empty")
        if (isinstance(audio_duration_seconds, bool)
                or not isinstance(audio_duration_seconds, (int, float))
                or not math.isfinite(audio_duration_seconds)
                or audio_duration_seconds <= 0):
            raise ValueError("audio_duration_seconds must be a finite positive number")
        weights: list[float] = []
        for segment in items:
            if not isinstance(segment, dict) or not isinstance(segment.get("text"), str) or not segment["text"].strip():
                raise ValueError("each segment must have non-empty text")
            weight = segment.get("estimated_duration_seconds", 1.0)
            if (isinstance(weight, bool) or not isinstance(weight, (int, float))
                    or not math.isfinite(weight) or weight <= 0):
                raise ValueError("estimated_duration_seconds must be finite and positive")
            weights.append(float(weight))
        total = sum(weights)
        cursor = 0.0
        timeline: list[dict[str, Any]] = []
        for index, (segment, weight) in enumerate(zip(items, weights)):
            if index == len(items) - 1:
                duration = float(audio_duration_seconds) - cursor
            else:
                duration = float(audio_duration_seconds) * weight / total
            end = cursor + duration
            timeline.append({
                "index": int(segment.get("index", index + 1)),
                "text": segment["text"],
                "start_seconds": cursor,
                "end_seconds": end,
                "duration_seconds": duration,
                "emotion": str(segment.get("emotion", "neutral")),
            })
            cursor = end
        return timeline

    @staticmethod
    def generate_srt(timeline: Iterable[dict[str, Any]]) -> str:
        """Generate subrip cues from an aligned timeline."""
        cues: list[str] = []
        for cue_index, segment in enumerate(timeline, 1):
            start, end = segment.get("start_seconds"), segment.get("end_seconds")
            if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                    or not math.isfinite(start) or not math.isfinite(end) or end <= start):
                raise ValueError("timeline segment has invalid start/end")
            cues.append(
                f"{cue_index}\n{_srt_time(start)} --> {_srt_time(end)}\n{segment['text']}"
            )
        if not cues:
            raise ValueError("timeline must not be empty")
        return "\n\n".join(cues) + "\n"

    @classmethod
    def generate_ass(cls, timeline: Iterable[dict[str, Any]], *, style: str = "hk-yellow-white") -> str:
        """Generate ASS subtitles with the Hong Kong yellow-text/white-outline style."""
        if style not in cls.ASS_STYLES:
            raise ValueError(f"unsupported ASS style: {style}")
        header = (
            "[Script Info]\n"
            "Title: Muse Cantonese Narration\n"
            "ScriptType: v4.00+\n"
            "PlayResX: 1920\n"
            "PlayResY: 1080\n"
            "\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            + cls.ASS_STYLES[style]
            + "\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        lines: list[str] = []
        for segment in timeline:
            start, end = segment.get("start_seconds"), segment.get("end_seconds")
            if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                    or not math.isfinite(start) or not math.isfinite(end) or end <= start):
                raise ValueError("timeline segment has invalid start/end")
            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},HKCanto,,0,0,0,,{segment['text']}"
            )
        if not lines:
            raise ValueError("timeline must not be empty")
        return header + "\n".join(lines) + "\n"

    @staticmethod
    def mouth_cues(timeline: Iterable[dict[str, Any]], fps: int) -> list[dict[str, float | int]]:
        """Produce one mouth-open value per video frame (speech on, silence off)."""
        if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
            raise ValueError("fps must be a positive integer")
        items = list(timeline)
        cues: list[dict[str, float | int]] = []
        # Derive the real upper bound from the last segment.
        total_seconds = max((segment.get("end_seconds", 0.0) for segment in items), default=0.0)
        if (isinstance(total_seconds, bool) or not isinstance(total_seconds, (int, float))
                or not math.isfinite(total_seconds) or total_seconds < 0):
            raise ValueError("timeline has invalid end_seconds")
        frame_count = int(math.ceil(total_seconds * fps))
        for frame in range(frame_count):
            now = (frame + 0.5) / fps
            active = any(
                segment.get("start_seconds", 1e9) <= now < segment.get("end_seconds", -1.0)
                for segment in items
            )
            mouth_open = round(0.5 + 0.25 * math.sin(frame * 1.7), 3) if active else 0.0
            cues.append({"frame": frame, "mouth_open": mouth_open})
        return cues

    def save_run(
        self,
        topic: str,
        style: str,
        segments: Iterable[dict[str, Any]],
        *,
        audio_path: str | Path | None = None,
        timeline: Iterable[dict[str, Any]] | None = None,
        subtitle_path: str | Path | None = None,
        run_id: str | None = None,
        evidence_contract: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a narration run and its optional claim/evidence audit snapshot."""
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 200:
            raise ValueError("topic must be non-empty text of at most 200 characters")
        if not isinstance(style, str) or not style.strip() or len(style) > 64:
            raise ValueError("style must be non-empty text of at most 64 characters")
        segment_list = list(segments)
        if not segment_list:
            raise ValueError("segments must not be empty")
        encoded_segments = json.dumps(segment_list, ensure_ascii=False, sort_keys=True, allow_nan=False)
        timeline_list = list(timeline) if timeline is not None else []
        encoded_timeline = json.dumps(timeline_list, ensure_ascii=False, sort_keys=True, allow_nan=False)
        encoded_evidence_contract = None
        if evidence_contract is not None:
            if not isinstance(evidence_contract, Mapping):
                raise ValueError("evidence_contract must be a mapping")
            if (evidence_contract.get("schema_version") != 1
                    or evidence_contract.get("contract") != "claim_grounded_narration"
                    or not isinstance(evidence_contract.get("publishable"), bool)
                    or evidence_contract.get("status") not in {"ready", "blocked"}
                    or evidence_contract.get("publishable") != (evidence_contract.get("status") == "ready")
                    or not isinstance(evidence_contract.get("segments"), list)):
                raise ValueError("evidence_contract has an invalid claim-grounded narration schema")
            encoded_evidence_contract = json.dumps(
                evidence_contract, ensure_ascii=False, sort_keys=True, allow_nan=False
            )
        if (len(encoded_segments) > 1_000_000 or len(encoded_timeline) > 1_000_000
                or (encoded_evidence_contract is not None and len(encoded_evidence_contract) > 1_000_000)):
            raise ValueError("serialised narration run data is too large")
        run_key = run_id or uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            "INSERT INTO narration_runs(id,topic,style,segments_json,audio_path,timeline_json,"
            "subtitle_path,created_at,evidence_contract_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                run_key, topic.strip(), style.strip(), encoded_segments,
                str(audio_path) if audio_path is not None else None,
                encoded_timeline,
                str(subtitle_path) if subtitle_path is not None else None,
                created_at,
                encoded_evidence_contract,
            ),
        )
        self.connection.commit()
        return self.get_run(run_key)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM narration_runs WHERE id=?", (run_id,)).fetchone()
        return self._decode(row) if row else None

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.connection.execute(
            "SELECT * FROM narration_runs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "topic": row["topic"],
            "style": row["style"],
            "segments": json.loads(row["segments_json"]),
            "audio_path": row["audio_path"],
            "timeline": json.loads(row["timeline_json"]),
            "subtitle_path": row["subtitle_path"],
            "evidence_contract": (
                json.loads(row["evidence_contract_json"])
                if row["evidence_contract_json"] else None
            ),
            "created_at": row["created_at"],
        }
