"""Pinned, local-only Cantonese TTS adapter for Muse narration.

The adapter never downloads models during synthesis. Provision the ONNX bundle
separately from the pinned Hugging Face revision before enabling this backend.
"""
from __future__ import annotations

import os
import tempfile
import threading
import wave
from pathlib import Path

_TEMP_DIR_LOCK = threading.RLock()

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
