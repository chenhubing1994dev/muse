"""Pinned, local-only Cantonese TTS adapter for Muse narration.

The adapter never downloads models during synthesis. Provision the ONNX bundle
separately from the pinned Hugging Face revision before enabling this backend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any
from muse.process_utils import no_console_window_kwargs

_TEMP_DIR_LOCK = threading.RLock()
_PREFLIGHT_CACHE: dict[tuple[str, int, int, str], dict[str, object]] = {}

MODEL_REPO = "typangaa/canto-tts-nano"
MODEL_REVISION = "fe17418a293d9be85685ddf750b0cfef9c88abb4"
MODEL_LICENSE = "Apache-2.0"
SAMPLE_RATE = 48_000
_MAX_TEXT_CHARS = 12_000


class CantoneseTTSError(RuntimeError):
    """Cantonese speech synthesis failed or its output did not pass validation."""


def synthesize_cantonese(
    text: str,
    output_path: str | Path,
    *,
    model_dir: str | Path,
    quality: str = "duration_filter",
) -> dict[str, object]:
    """Synthesize Hong Kong Cantonese locally to a validated 48 kHz WAV.

    model_dir must already contain the pinned ONNX bundle. No network
    download is performed here, so video rendering can run offline.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT_CHARS:
        raise ValueError(f"text must contain 1-{_MAX_TEXT_CHARS} characters")
    if quality not in {"single", "duration_filter"}:
        raise ValueError("quality must be single or duration_filter")

    model = Path(model_dir).expanduser()
    if (any(part.is_symlink() for part in (model, *model.parents))
            or not model.is_dir()
            or not (model / "browser_poc_manifest.json").is_file()):
        raise CantoneseTTSError("pinned Cantonese ONNX model directory is missing or unsafe")

    output = Path(output_path).expanduser()
    if output.suffix.casefold() != ".wav":
        raise ValueError("Cantonese TTS output must use a .wav extension")
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise CantoneseTTSError("output parent must be an existing regular directory")
    if output.is_symlink():
        raise CantoneseTTSError("refusing to overwrite a symlink output")

    fd, temporary_name = tempfile.mkstemp(prefix=".muse-canto-", suffix=".wav", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        try:
            from canto_tts import CantoTTS
        except ImportError as exc:
            raise CantoneseTTSError(
                "canto-tts and its ONNX dependencies are not installed in this Python environment"
            ) from exc
        try:
            engine = CantoTTS(checkpoint=str(model.resolve(strict=True)), backend="onnx")
            if quality == "duration_filter":
                # canto-tts creates candidate WAVs in tempfile.gettempdir() and
                # atomically moves the winner. Keep those candidates on the same
                # volume as output_path (Windows cannot os.replace across drives).
                with _TEMP_DIR_LOCK:
                    previous_tempdir = tempfile.tempdir
                    tempfile.tempdir = str(output.parent.resolve())
                    try:
                        engine.synthesize(text.strip(), str(temporary), quality="duration_filter", max_attempts=2)
                    finally:
                        tempfile.tempdir = previous_tempdir
            else:
                engine.synthesize(text.strip(), str(temporary))
        except Exception as exc:
            raise CantoneseTTSError(f"local Cantonese synthesis failed: {type(exc).__name__}: {exc}") from exc

        if not temporary.is_file() or temporary.is_symlink() or temporary.stat().st_size < 44:
            raise CantoneseTTSError("TTS engine did not produce a valid WAV file")
        try:
            with wave.open(str(temporary), "rb") as audio:
                channels = audio.getnchannels()
                sample_rate = audio.getframerate()
                frames = audio.getnframes()
                sample_width = audio.getsampwidth()
        except (wave.Error, OSError) as exc:
            raise CantoneseTTSError("TTS output is not a readable PCM WAV") from exc
        if sample_rate != SAMPLE_RATE or channels not in {1, 2} or frames <= 0 or sample_width not in {2, 3, 4}:
            raise CantoneseTTSError(
                f"unexpected WAV format: {sample_rate} Hz, {channels} channels, {sample_width * 8}-bit"
            )

        os.replace(temporary, output)
        return {
            "status": "completed",
            "engine": "canto-tts-onnx",
            "model_repo": MODEL_REPO,
            "model_revision": MODEL_REVISION,
            "license": MODEL_LICENSE,
            "language": "zh-HK",
            "sample_rate": sample_rate,
            "channels": channels,
            "frames": frames,
            "duration_seconds": frames / sample_rate,
            "bytes": output.stat().st_size,
            "output": str(output),
        }
    finally:
        if temporary.exists():
            temporary.unlink()



def split_cantonese_narration(
    text: str, *, max_segment_chars: int = 180,
    sentence_pause_ms: int = 320, clause_pause_ms: int = 160,
) -> list[dict[str, object]]:
    """Split narration at natural punctuation and encode real inter-segment silence."""
    if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT_CHARS:
        raise ValueError(f"text must contain 1-{_MAX_TEXT_CHARS} characters")
    if isinstance(max_segment_chars, bool) or not isinstance(max_segment_chars, int) or not 20 <= max_segment_chars <= 280:
        raise ValueError("max_segment_chars must be an integer from 20 to 280")
    for name, value in (("sentence_pause_ms", sentence_pause_ms), ("clause_pause_ms", clause_pause_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3000:
            raise ValueError(f"{name} must be an integer from 0 to 3000")
    sentence_marks = set("。！？!?")
    clause_marks = set("，,；;：:")
    segments: list[dict[str, object]] = []
    current = ""
    for char in text.strip():
        current += char
        pause = sentence_pause_ms if char in sentence_marks else clause_pause_ms if char in clause_marks else 0
        if pause or len(current) >= max_segment_chars:
            segments.append({"text": current.strip(), "pause_after_ms": pause})
            current = ""
    if current.strip():
        segments.append({"text": current.strip(), "pause_after_ms": 0})
    if not segments:
        raise ValueError("narration did not contain any speakable text")
    if len(segments) > 64:
        raise ValueError("narration exceeds the 64-segment synthesis limit")
    return segments


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_cantonese_segment_payload(
    segments: Any, *, max_segments: int = 64,
) -> list[dict[str, object]]:
    if not isinstance(segments, list) or not 1 <= len(segments) <= max_segments:
        raise ValueError(f"segments must contain 1-{max_segments} items")
    normalized: list[dict[str, object]] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or set(segment) - {"text", "pause_after_ms"}:
            raise ValueError(f"segments[{index}] may contain only text and pause_after_ms")
        text = segment.get("text")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 280:
            raise ValueError(f"segments[{index}].text must contain 1-280 characters")
        pause_ms = segment.get("pause_after_ms", 0)
        if isinstance(pause_ms, bool) or not isinstance(pause_ms, int) or not 0 <= pause_ms <= 3000:
            raise ValueError(f"segments[{index}].pause_after_ms must be an integer from 0 to 3000")
        item: dict[str, object] = {"text": text.strip()}
        if "pause_after_ms" in segment:
            item["pause_after_ms"] = pause_ms
        normalized.append(item)
    return normalized


def _synthesize_segments_with_engine(
    segments: list[dict[str, object]], output_path: str | Path, *,
    engine: Any, quality: str = "duration_filter",
) -> dict[str, object]:
    """Synthesize ordered text segments and insert verified trailing PCM silence."""
    normalized = _normalize_cantonese_segment_payload(segments, max_segments=64)
    if quality not in {"single", "duration_filter"}:
        raise ValueError("quality must be single or duration_filter")
    output = Path(output_path).expanduser()
    if (output.suffix.casefold() != ".wav"
            or any(parent.is_symlink() for parent in (output.parent, *output.parent.parents))
            or not output.parent.is_dir()):
        raise CantoneseTTSError("segmented TTS output must be a WAV in an existing regular directory")
    if output.is_symlink():
        raise CantoneseTTSError("refusing to overwrite a symlink output")
    with tempfile.TemporaryDirectory(prefix=".muse-canto-segments-", dir=output.parent) as temp_name:
        staging = Path(temp_name)
        combined_frames: list[bytes] = []
        reference: tuple[int, int, int] | None = None
        total_pause_frames = 0
        segment_metadata: list[dict[str, object]] = []
        for index, segment in enumerate(normalized):
            text = str(segment["text"])
            pause_ms = int(segment.get("pause_after_ms", 0))
            clip = staging / f"segment-{index:03d}.wav"
            try:
                if quality == "duration_filter":
                    with _TEMP_DIR_LOCK:
                        previous_tempdir = tempfile.tempdir
                        tempfile.tempdir = str(staging.resolve())
                        try:
                            engine.synthesize(text, str(clip), quality="duration_filter", max_attempts=2)
                        finally:
                            tempfile.tempdir = previous_tempdir
                else:
                    engine.synthesize(text, str(clip))
            except Exception as exc:
                raise CantoneseTTSError(
                    f"segmented Cantonese synthesis failed at segment {index}: {type(exc).__name__}"
                ) from exc
            try:
                with wave.open(str(clip), "rb") as audio:
                    params = audio.getparams()
                    frames = audio.readframes(audio.getnframes())
            except (wave.Error, OSError) as exc:
                raise CantoneseTTSError(f"segment {index} did not produce a readable PCM WAV") from exc
            signature = (params.nchannels, params.sampwidth, params.framerate)
            if reference is not None and signature != reference:
                raise CantoneseTTSError("segmented TTS returned inconsistent WAV formats")
            if (params.framerate != SAMPLE_RATE or params.nchannels not in {1, 2}
                    or params.sampwidth not in {2, 3, 4} or params.nframes <= 0):
                raise CantoneseTTSError(f"segment {index} has an unsupported WAV format")
            if reference is None:
                reference = signature
            combined_frames.append(frames)
            silence_frames = params.framerate * pause_ms // 1000
            if silence_frames:
                combined_frames.append(b"\x00" * silence_frames * params.nchannels * params.sampwidth)
                total_pause_frames += silence_frames
            segment_metadata.append({
                "index": index,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "pause_after_ms": pause_ms,
                "inserted_pause_frames": silence_frames,
            })
        temporary = staging / "combined.wav"
        channels, sample_width, sample_rate = reference
        try:
            with wave.open(str(temporary), "wb") as audio:
                audio.setnchannels(channels)
                audio.setsampwidth(sample_width)
                audio.setframerate(sample_rate)
                for frames in combined_frames:
                    audio.writeframesraw(frames)
        except (wave.Error, OSError) as exc:
            raise CantoneseTTSError("could not assemble segmented Cantonese WAV") from exc
        os.replace(temporary, output)
    with wave.open(str(output), "rb") as audio:
        frame_count = audio.getnframes()
    return {
        "status": "completed", "engine": "canto-tts-onnx-segmented",
        "model_repo": MODEL_REPO, "model_revision": MODEL_REVISION,
        "license": MODEL_LICENSE, "language": "zh-HK",
        "sample_rate": SAMPLE_RATE, "channels": channels,
        "segment_count": len(normalized), "segment_metadata": segment_metadata,
        "inserted_pause_frames": total_pause_frames,
        "inserted_pause_seconds": total_pause_frames / SAMPLE_RATE,
        "frames": frame_count, "duration_seconds": frame_count / SAMPLE_RATE,
        "bytes": output.stat().st_size, "output": str(output),
        "audio_sha256": _sha256_file(output),
        "applied_controls": ["pause_after"] if total_pause_frames else [],
        "backend_capabilities": ["pause_after"],
        "unsupported_controls": ["emotion", "rate_scale", "pitch_semitones", "breath_hint", "pause_before_ms"],
    }


def _synthesize_cantonese_segments_local(
    segments: list[dict[str, object]], output_path: str | Path, *,
    packages_dir: str | Path, model_dir: str | Path, quality: str,
) -> dict[str, object]:
    packages, model = _validate_runtime_paths(packages_dir, model_dir)
    CantoTTS = _load_canto_engine(packages)
    engine = CantoTTS(checkpoint=str(model.resolve(strict=True)), backend="onnx")
    return _synthesize_segments_with_engine(segments, output_path, engine=engine, quality=quality)


def _synthesize_cantonese_segments_payload_with_runtime(
    segments: list[dict[str, object]], output_path: str | Path, *,
    model_dir: str | Path, packages_dir: str | Path, quality: str,
) -> dict[str, object]:
    normalized = _normalize_cantonese_segment_payload(segments, max_segments=64)
    if quality not in {"single", "duration_filter"}:
        raise ValueError("quality must be single or duration_filter")
    packages, model = _validate_runtime_paths(packages_dir, model_dir)
    preflight_cantonese_tts_runtime(packages, model)
    output = Path(output_path).expanduser()
    if (output.suffix.casefold() != ".wav"
            or any(parent.is_symlink() for parent in (output.parent, *output.parent.parents))
            or not output.parent.is_dir() or output.is_symlink()):
        raise CantoneseTTSError("segmented TTS output must be a WAV in an existing regular directory")

    with tempfile.TemporaryDirectory(prefix=".muse-canto-structured-", dir=output.parent) as temp_name:
        staging = Path(temp_name)
        temporary_output = staging / "voiceover.wav"
        if sys.version_info[:2] == (3, 14):
            result = _synthesize_cantonese_segments_local(
                normalized, temporary_output, packages_dir=packages, model_dir=model, quality=quality,
            )
        else:
            segments_path = staging / "segments.json"
            segments_path.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
            result = _run_python314_cli([
                "--synthesize-segments", "--packages-dir", str(packages), "--model-dir", str(model),
                "--segments-file", str(segments_path), "--output", str(temporary_output), "--quality", quality,
            ], timeout=1800)

        if (not isinstance(result, dict) or result.get("status") != "completed"
                or not temporary_output.is_file() or temporary_output.is_symlink()):
            raise CantoneseTTSError("isolated segmented Cantonese TTS did not produce a validated WAV")
        try:
            with wave.open(str(temporary_output), "rb") as audio:
                channels = audio.getnchannels()
                sample_rate = audio.getframerate()
                frames = audio.getnframes()
                sample_width = audio.getsampwidth()
        except (wave.Error, OSError) as exc:
            raise CantoneseTTSError("segmented TTS output is not a readable PCM WAV") from exc
        if (sample_rate != SAMPLE_RATE or channels not in {1, 2}
                or sample_width not in {2, 3, 4} or frames <= 0):
            raise CantoneseTTSError("segmented TTS output has an unsupported WAV format")

        expected_segments = [
            {
                "index": index,
                "text_sha256": hashlib.sha256(str(segment["text"]).encode("utf-8")).hexdigest(),
                "pause_after_ms": int(segment.get("pause_after_ms", 0)),
                "inserted_pause_frames": SAMPLE_RATE * int(segment.get("pause_after_ms", 0)) // 1000,
            }
            for index, segment in enumerate(normalized)
        ]
        expected_pause_frames = sum(int(item["inserted_pause_frames"]) for item in expected_segments)
        audio_sha256 = _sha256_file(temporary_output)
        expected_applied = ["pause_after"] if expected_pause_frames else []
        if (result.get("segment_count") != len(normalized)
                or result.get("segment_metadata") != expected_segments
                or result.get("inserted_pause_frames") != expected_pause_frames
                or result.get("applied_controls") != expected_applied
                or result.get("backend_capabilities") != ["pause_after"]
                or result.get("audio_sha256") != audio_sha256
                or result.get("sample_rate") != sample_rate
                or result.get("channels") != channels
                or result.get("frames") != frames):
            raise CantoneseTTSError("segmented TTS runtime metadata does not match the produced WAV and requested segments")

        os.replace(temporary_output, output)
        verified = dict(result)
        verified["output"] = str(output)
        verified["audio_sha256"] = audio_sha256
        return verified


def synthesize_cantonese_segment_payload_with_runtime(
    segments: list[dict[str, object]], output_path: str | Path, *,
    model_dir: str | Path, packages_dir: str | Path, quality: str = "duration_filter",
) -> dict[str, object]:
    """Synthesize explicit ordered Cantonese segments without re-splitting their text.

    Only text and pause_after_ms are accepted. Emotion, rate, pitch, breath and
    leading-pause requests remain unsupported and must stay visible in the
    caller's prosody plan rather than being silently treated as applied.
    """
    normalized = _normalize_cantonese_segment_payload(segments, max_segments=16)
    return _synthesize_cantonese_segments_payload_with_runtime(
        normalized, output_path, model_dir=model_dir, packages_dir=packages_dir, quality=quality,
    )


def synthesize_cantonese_segments_with_runtime(
    text: str, output_path: str | Path, *, model_dir: str | Path,
    packages_dir: str | Path, quality: str = "duration_filter",
    max_segment_chars: int = 180, sentence_pause_ms: int = 320,
    clause_pause_ms: int = 160,
) -> dict[str, object]:
    """Synthesize punctuation-aware Cantonese text and insert real PCM pauses."""
    segments = split_cantonese_narration(
        text, max_segment_chars=max_segment_chars,
        sentence_pause_ms=sentence_pause_ms, clause_pause_ms=clause_pause_ms,
    )
    return _synthesize_cantonese_segments_payload_with_runtime(
        segments, output_path, model_dir=model_dir, packages_dir=packages_dir, quality=quality,
    )



_RESULT_MARKER = "MUSE_CANTO_TTS_RESULT="


def _validate_runtime_paths(packages_dir: str | Path, model_dir: str | Path) -> tuple[Path, Path]:
    packages = Path(packages_dir).expanduser()
    model = Path(model_dir).expanduser()
    if (any(part.is_symlink() for part in (packages, *packages.parents))
            or not packages.is_dir() or not (packages / "canto_tts").is_dir()):
        raise CantoneseTTSError("project-local canto-tts package directory is missing or unsafe")
    if (any(part.is_symlink() for part in (model, *model.parents))
            or not model.is_dir() or not (model / "browser_poc_manifest.json").is_file()):
        raise CantoneseTTSError("pinned Cantonese ONNX model directory is missing or unsafe")
    return packages.resolve(strict=True), model.resolve(strict=True)


def _python314_command() -> list[str]:
    """Resolve the isolated Python 3.14 runtime without changing Muse's interpreter."""
    if sys.version_info[:2] == (3, 14):
        return [sys.executable]
    override = os.environ.get("MUSE_PYTHON314", "").strip()
    if override:
        executable = Path(override).expanduser()
        if executable.is_file() and not executable.is_symlink():
            return [str(executable.resolve())]
        raise CantoneseTTSError("MUSE_PYTHON314 does not point to a regular Python executable")
    if os.name == "nt":
        launcher = shutil.which("py") or shutil.which("py.exe")
        if launcher:
            return [launcher, "-3.14"]
    executable = shutil.which("python3.14") or shutil.which("python3.14.exe")
    if executable:
        return [executable]
    raise CantoneseTTSError("Python 3.14 is required for the pinned canto-tts binary environment")


def _child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1])
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(part for part in (source_root, existing) if part)
    return environment


def _load_canto_engine(packages_dir: Path, model_dir: Path | None = None):
    package_path = str(packages_dir.resolve(strict=True))
    if package_path not in sys.path:
        sys.path.insert(0, package_path)
    try:
        from canto_tts import CantoTTS
    except ImportError as exc:
        raise CantoneseTTSError(
            "canto-tts and its ONNX dependencies are not importable in the Python 3.14 environment"
        ) from exc
    if model_dir is not None:
        try:
            CantoTTS(checkpoint=str(model_dir.resolve(strict=True)), backend="onnx")
        except Exception as exc:
            raise CantoneseTTSError(
                f"pinned Cantonese ONNX model failed runtime preflight: {type(exc).__name__}: {exc}"
            ) from exc
    return CantoTTS


def _run_python314_cli(arguments: list[str], *, timeout: int) -> dict[str, object]:
    command = [*_python314_command(), "-m", "muse.cantonese_tts", *arguments]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, env=_child_environment(),
            **no_console_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CantoneseTTSError(f"isolated Python 3.14 TTS helper could not complete: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "")[-1200:]
        raise CantoneseTTSError(f"isolated Python 3.14 TTS helper failed: {detail}")
    payload = next((line[len(_RESULT_MARKER):] for line in reversed((completed.stdout or "").splitlines())
                    if line.startswith(_RESULT_MARKER)), None)
    if payload is None:
        raise CantoneseTTSError("isolated Python 3.14 TTS helper returned no structured result")
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CantoneseTTSError("isolated Python 3.14 TTS helper returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise CantoneseTTSError("isolated Python 3.14 TTS helper returned an invalid result")
    return result


def preflight_cantonese_tts_runtime(
    packages_dir: str | Path, model_dir: str | Path,
) -> dict[str, object]:
    """Verify the pinned Python 3.14 package and model before any GPU render starts."""
    packages, model = _validate_runtime_paths(packages_dir, model_dir)
    package_stat = packages.stat()
    manifest_digest = hashlib.sha256((model / "browser_poc_manifest.json").read_bytes()).hexdigest()
    cache_key = (str(packages), package_stat.st_mtime_ns, package_stat.st_size, manifest_digest)
    with _TEMP_DIR_LOCK:
        cached = _PREFLIGHT_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    if sys.version_info[:2] == (3, 14):
        _load_canto_engine(packages, model)
        result = {"status": "ready", "python_version": f"{sys.version_info.major}.{sys.version_info.minor}", "model_loaded": True}
    else:
        result = _run_python314_cli([
            "--preflight", "--packages-dir", str(packages), "--model-dir", str(model),
        ], timeout=90)
    if result.get("status") != "ready":
        raise CantoneseTTSError("isolated Python 3.14 TTS runtime did not pass preflight")
    with _TEMP_DIR_LOCK:
        _PREFLIGHT_CACHE[cache_key] = dict(result)
    return result


def synthesize_cantonese_with_runtime(
    text: str, output_path: str | Path, *, model_dir: str | Path,
    packages_dir: str | Path, quality: str = "duration_filter",
) -> dict[str, object]:
    """Use Python 3.14 for TTS while leaving the main Muse runtime untouched."""
    packages, model = _validate_runtime_paths(packages_dir, model_dir)
    preflight_cantonese_tts_runtime(packages, model)
    if sys.version_info[:2] == (3, 14):
        _load_canto_engine(packages)
        return synthesize_cantonese(text, output_path, model_dir=model, quality=quality)
    output = Path(output_path).expanduser()
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise CantoneseTTSError("output parent must be an existing regular directory")
    descriptor, script_name = tempfile.mkstemp(prefix=".muse-canto-script-", suffix=".txt", dir=output.parent)
    script_path = Path(script_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        result = _run_python314_cli([
            "--synthesize", "--packages-dir", str(packages), "--model-dir", str(model),
            "--script-file", str(script_path), "--output", str(output), "--quality", quality,
        ], timeout=900)
    finally:
        if script_path.exists() and not script_path.is_symlink():
            script_path.unlink()
    if result.get("status") != "completed" or not output.is_file() or output.is_symlink():
        raise CantoneseTTSError("isolated Python 3.14 TTS did not produce a validated WAV")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated Python 3.14 Cantonese TTS helper")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--synthesize", action="store_true")
    mode.add_argument("--synthesize-segments", action="store_true")
    parser.add_argument("--packages-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--script-file")
    parser.add_argument("--segments-file")
    parser.add_argument("--output")
    parser.add_argument("--quality", choices=("single", "duration_filter"), default="duration_filter")
    args = parser.parse_args(argv)
    try:
        packages, model = _validate_runtime_paths(args.packages_dir, args.model_dir)
        if args.preflight:
            result = preflight_cantonese_tts_runtime(packages, model)
        elif args.synthesize_segments:
            if not args.segments_file or not args.output:
                parser.error("--synthesize-segments requires --segments-file and --output")
            segments_path = Path(args.segments_file)
            if segments_path.is_symlink() or not segments_path.is_file():
                raise CantoneseTTSError("segmented narration JSON is missing or unsafe")
            segments = json.loads(segments_path.read_text(encoding="utf-8"))
            if not isinstance(segments, list) or any(not isinstance(item, dict) for item in segments):
                raise CantoneseTTSError("segmented narration JSON must contain a list of segment objects")
            result = _synthesize_cantonese_segments_local(
                segments, args.output, packages_dir=packages, model_dir=model, quality=args.quality,
            )
        else:
            if not args.script_file or not args.output:
                parser.error("--synthesize requires --script-file and --output")
            script = Path(args.script_file)
            if script.is_symlink() or not script.is_file():
                raise CantoneseTTSError("narration script file is missing or unsafe")
            text = script.read_text(encoding="utf-8")
            if sys.version_info[:2] == (3, 14):
                _load_canto_engine(packages)
                result = synthesize_cantonese(text, args.output, model_dir=model, quality=args.quality)
            else:
                result = synthesize_cantonese_with_runtime(
                    text, args.output, model_dir=model, packages_dir=packages, quality=args.quality,
                )
        print(_RESULT_MARKER + json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
