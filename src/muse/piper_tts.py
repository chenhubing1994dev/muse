"""Offline Piper TTS adapter for Muse media workflows.

The adapter never downloads models. Callers must provide an existing ONNX voice
model and its JSON config; missing assets fail closed with an actionable error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path
from typing import Iterable


class PiperTtsError(RuntimeError):
    """Raised when local Piper synthesis cannot safely complete."""


def _pcm_bytes(samples: Iterable[float]) -> bytes:
    pcm = array("h", (int(max(-1.0, min(1.0, float(value))) * 32767) for value in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm.tobytes()


def synthesize_to_wav(
    text: str,
    model_path: str | Path,
    output_path: str | Path,
    *,
    config_path: str | Path | None = None,
    voice_loader=None,
) -> dict[str, object]:
    """Synthesize Chinese text to mono PCM WAV using an existing Piper model."""
    text = text.strip()
    if not text:
        raise ValueError("text must not be empty")
    model = Path(model_path).expanduser().resolve()
    config = Path(config_path).expanduser().resolve() if config_path else Path(f"{model}.json")
    output = Path(output_path).expanduser().resolve()
    if model.suffix.lower() != ".onnx":
        raise ValueError("Piper model must be an .onnx file")
    if not model.is_file() or not config.is_file():
        raise PiperTtsError(
            f"local Piper voice assets are missing; expected model={model} and config={config}. "
            "No model download is attempted by Muse."
        )
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing audio: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if voice_loader is None:
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise PiperTtsError("Piper TTS is not installed in this Python environment") from exc
        voice_loader = PiperVoice.load

    voice = voice_loader(model, config_path=config, use_cuda=False)
    chunks = list(voice.synthesize(text))
    if not chunks:
        raise PiperTtsError("Piper returned no audio chunks")
    sample_rate = int(chunks[0].sample_rate)
    sample_width = int(chunks[0].sample_width)
    channels = int(chunks[0].sample_channels)
    if sample_width != 2 or channels != 1:
        raise PiperTtsError(f"unsupported Piper PCM format: width={sample_width}, channels={channels}")
    if any(int(chunk.sample_rate) != sample_rate for chunk in chunks):
        raise PiperTtsError("Piper returned inconsistent sample rates")

    try:
        with wave.open(str(output), "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            for chunk in chunks:
                wav_file.writeframes(_pcm_bytes(chunk.audio_float_array))
    except Exception:
        output.unlink(missing_ok=True)
        raise
    if output.stat().st_size <= 44:
        output.unlink(missing_ok=True)
        raise PiperTtsError("Piper produced an empty WAV file")
    return {
        "status": "completed", "engine": "Piper TTS", "model": str(model),
        "config": str(config), "output": str(output), "bytes": output.stat().st_size,
        "sample_rate": sample_rate, "channels": channels, "sample_width": sample_width,
        "duration_seconds": round(sum(len(chunk.audio_float_array) for chunk in chunks) / sample_rate, 3),
    }


def synthesize_with_cli(
    text: str,
    model_path: str | Path,
    output_path: str | Path,
    *,
    executable: str | Path,
    config_path: str | Path | None = None,
    timeout_seconds: float = 300,
    runner=subprocess.run,
) -> dict[str, object]:
    """Run an installed Piper CLI with literal argv and validate its WAV output.

    The executable and voice assets are caller-selected local paths. No shell is
    used and this function never downloads or overwrites model/audio files.
    """
    text = text.strip()
    if not text:
        raise ValueError("text must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    model = Path(model_path).expanduser().resolve()
    config = Path(config_path).expanduser().resolve() if config_path else Path(f"{model}.json")
    output = Path(output_path).expanduser().resolve()
    if model.suffix.lower() != ".onnx":
        raise ValueError("Piper model must be an .onnx file")
    if not model.is_file() or not config.is_file():
        raise PiperTtsError(
            f"local Piper voice assets are missing; expected model={model} and config={config}. "
            "No model download is attempted by Muse."
        )
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing audio: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    executable_value = str(Path(executable).expanduser()) if isinstance(executable, Path) else str(executable)
    if not executable_value.strip():
        raise ValueError("Piper executable must not be empty")

    input_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".txt", prefix="muse-piper-", delete=False
        ) as text_file:
            text_file.write(text)
            input_path = Path(text_file.name)
        command = [
            executable_value, "-m", str(model), "-c", str(config),
            "-i", str(input_path), "-f", str(output),
        ]
        try:
            completed = runner(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_seconds, check=False,
            )
        except FileNotFoundError as exc:
            raise PiperTtsError(f"Piper executable was not found: {executable_value}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PiperTtsError(f"Piper CLI timed out after {timeout_seconds:g} seconds") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
            raise PiperTtsError(f"Piper CLI exited with code {completed.returncode}: {detail[-1200:]}")

        if not output.is_file():
            raise PiperTtsError("Piper CLI reported success but did not create the requested WAV")
        try:
            with wave.open(str(output), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                frames = wav_file.getnframes()
                compression = wav_file.getcomptype()
        except (wave.Error, EOFError, OSError) as exc:
            raise PiperTtsError(f"Piper CLI output is not a readable WAV: {exc}") from exc
        if channels != 1 or sample_width != 2 or sample_rate <= 0 or frames <= 0 or compression != "NONE":
            raise PiperTtsError(
                "unsupported or empty Piper WAV: "
                f"channels={channels}, sample_width={sample_width}, sample_rate={sample_rate}, "
                f"frames={frames}, compression={compression}"
            )
        size = output.stat().st_size
        return {
            "status": "completed", "engine": "Piper CLI", "executable": executable_value,
            "model": str(model), "config": str(config), "output": str(output),
            "bytes": size, "sample_rate": sample_rate, "channels": channels,
            "sample_width": sample_width, "frames": frames,
            "duration_seconds": round(frames / sample_rate, 3),
        }
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        if input_path is not None:
            input_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Chinese TTS using an already-installed Piper voice")
    parser.add_argument("--model", required=True, help="Existing Piper .onnx model path")
    parser.add_argument("--config", help="Existing Piper JSON config; defaults to MODEL.onnx.json")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file")
    parser.add_argument("--output", required=True, help="New WAV output path; existing files are never overwritten")
    parser.add_argument("--engine", choices=("python", "cli"), default="python")
    parser.add_argument("--executable", help="Existing Piper CLI executable (required with --engine cli)")
    parser.add_argument("--timeout", type=float, default=300, help="Piper CLI timeout in seconds")
    args = parser.parse_args()
    try:
        text = args.text if args.text is not None else Path(args.text_file).read_text(encoding="utf-8")
        if args.engine == "cli":
            if not args.executable:
                parser.error("--executable is required with --engine cli")
            result = synthesize_with_cli(
                text, args.model, args.output, config_path=args.config,
                executable=args.executable, timeout_seconds=args.timeout,
            )
        else:
            result = synthesize_to_wav(text, args.model, args.output, config_path=args.config)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
