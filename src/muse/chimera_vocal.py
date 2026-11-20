"""Optional local adapter for the MIT-licensed CHIMERA physical voice synthesizer.

CHIMERA is not a Cantonese TTS replacement. This opt-in adapter supports its
English/IPA speech paths and custom melody-note rendering; it never downloads,
installs, or updates the upstream project.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import unicodedata
import wave
from pathlib import Path
from typing import Callable

from muse.process_utils import no_console_window_kwargs


class ChimeraVocalError(RuntimeError):
    """CHIMERA could not safely render a validated speech WAV."""


_BODIES = {"male", "female", "child"}
_MODES = {"text", "ipa"}
_MAX_TEXT_CHARS = 12_000
_DEFAULT_TIMEOUT = 300
_MAX_MELODY_NOTES = 2048
_MAX_MELODY_DURATION_SECONDS = 1800
_MAX_MELODY_FILE_BYTES = 1024 * 1024


def _has_symlink_component(path: Path) -> bool:
    return any(part.is_symlink() for part in (path, *path.parents))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_cli(cli_path: str | Path | None) -> Path:
    supplied = cli_path if cli_path is not None else os.environ.get("MUSE_CHIMERA_CLI")
    if not supplied:
        raise ChimeraVocalError(
            "CHIMERA CLI is not configured; set MUSE_CHIMERA_CLI to an audited local executable"
        )
    raw = Path(supplied).expanduser()
    if _has_symlink_component(raw) or not raw.is_file():
        raise ChimeraVocalError("CHIMERA CLI must be an existing regular file without symlink components")
    return raw.resolve(strict=True)


def _validate_output_path(output_path: str | Path) -> tuple[Path, Path]:
    output = Path(output_path).expanduser()
    if output.suffix.casefold() != ".wav":
        raise ValueError("CHIMERA output path must end in .wav")
    if not output.is_absolute():
        output = Path.cwd() / output
    parent = output.parent
    if _has_symlink_component(parent) or not parent.is_dir():
        raise ChimeraVocalError("output parent must be an existing regular directory without symlink components")
    if output.is_symlink() or output.exists():
        raise ChimeraVocalError("refusing to overwrite an existing or symlink output")
    return output, output.with_suffix("")


def _wav_facts(path: Path) -> dict[str, int | float]:
    try:
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            if channels not in {1, 2} or sample_width not in {2, 4} or sample_rate != 48_000 or frames <= 0:
                raise ChimeraVocalError(
                    "CHIMERA WAV must be non-empty 48 kHz mono/stereo PCM (16- or 32-bit)"
                )
            return {
                "channels": channels,
                "sample_width_bytes": sample_width,
                "sample_rate": sample_rate,
                "frames": frames,
                "duration_seconds": round(frames / sample_rate, 6),
            }
    except (OSError, wave.Error) as exc:
        raise ChimeraVocalError(f"CHIMERA did not produce a readable PCM WAV: {type(exc).__name__}") from exc


def render_chimera_voice(
    text: str,
    output_path: str | Path,
    *,
    mode: str = "text",
    body: str = "female",
    cli_path: str | Path | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    runner: Callable = subprocess.run,
) -> dict[str, object]:
    """Render English text or caller-supplied IPA to a new, validated WAV.

    mode="text" uses CHIMERA's English G2P. mode="ipa" passes explicit IPA
    to its speech planner; it does not add Cantonese tone rules or Cantonese G2P.
    """
    if mode not in _MODES:
        raise ValueError("mode must be text or ipa")
    if body not in _BODIES:
        raise ValueError("body must be male, female, or child")
    if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT_CHARS:
        raise ValueError(f"text must contain 1-{_MAX_TEXT_CHARS} characters")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 1800:
        raise ValueError("timeout must be an integer from 1 to 1800 seconds")

    executable = _resolve_cli(cli_path)
    output, stem = _validate_output_path(output_path)
    kind = "text" if mode == "text" else "speech"
    command = [str(executable), "render", kind, str(stem), text.strip(), body]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **no_console_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChimeraVocalError(f"CHIMERA CLI could not complete: {type(exc).__name__}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "")[-1200:]
        if output.is_file() and not output.is_symlink():
            output.unlink()
        raise ChimeraVocalError(f"CHIMERA render failed: {detail}")
    if not output.is_file() or output.is_symlink():
        raise ChimeraVocalError("CHIMERA CLI returned success without creating the requested WAV")
    warnings = [line.strip() for line in (completed.stderr or "").splitlines()
                if line.lstrip().casefold().startswith("warning:")]
    if warnings:
        output.unlink()
        raise ChimeraVocalError("CHIMERA reported skipped or unsupported phonemes: " + " | ".join(warnings)[-1200:])

    try:
        facts = _wav_facts(output)
        digest = _sha256_file(output)
    except Exception:
        if output.is_file() and not output.is_symlink():
            output.unlink()
        raise
    return {
        "status": "completed",
        "engine": "chimera-vocal-synth",
        "engine_license": "MIT",
        "engine_license_notice_required": True,
        "mode": mode,
        "language": "en" if mode == "text" else "caller_supplied_ipa_unclassified",
        "body": body,
        "path": str(output),
        "bytes": output.stat().st_size,
        "sha256": digest,
        "sample_rate": facts["sample_rate"],
        "channels": facts["channels"],
        "sample_width_bytes": facts["sample_width_bytes"],
        "frames": facts["frames"],
        "duration_seconds": facts["duration_seconds"],
        "cantonese_supported": False,
        "human_naturalness_accepted": False,
        "requires_creator_listening_review": True,
        "stdout_tail": (completed.stdout or "")[-1200:],
        "stderr_tail": (completed.stderr or "")[-1200:],
    }


def _validate_song_lyrics(lyrics: object) -> str:
    if not isinstance(lyrics, str) or not lyrics.strip() or len(lyrics) > _MAX_TEXT_CHARS:
        raise ValueError(f"lyrics must contain 1-{_MAX_TEXT_CHARS} characters")
    for char in lyrics:
        if char.isspace() or char == "~" or unicodedata.category(char).startswith(("P", "M")):
            continue
        if not unicodedata.category(char).startswith("L"):
            raise ValueError("lyrics contain a character outside CHIMERA's supported script front ends")
        name = unicodedata.name(char, "")
        if not any(script in name for script in (
            "LATIN", "CYRILLIC", "HANGUL", "CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH"
        )):
            raise ValueError("lyrics contain a script CHIMERA does not currently route")
    return lyrics.strip()


def _validate_song_melody(melody: object) -> dict[str, object]:
    if not isinstance(melody, dict):
        raise ValueError("melody must be a JSON object")
    allowed = {"bpm", "lyrics", "notes", "vibrato", "t0"}
    if set(melody) - allowed:
        raise ValueError("melody contains unsupported fields")
    lyrics = _validate_song_lyrics(melody.get("lyrics"))
    bpm = melody.get("bpm")
    if isinstance(bpm, bool) or not isinstance(bpm, (int, float)) or not math.isfinite(bpm) or not 30 <= bpm <= 300:
        raise ValueError("bpm must be finite and between 30 and 300")
    notes = melody.get("notes")
    if not isinstance(notes, list) or not 1 <= len(notes) <= _MAX_MELODY_NOTES:
        raise ValueError(f"notes must contain 1-{_MAX_MELODY_NOTES} entries")
    normalized_notes: list[list[float]] = []
    sounding = 0
    total_beats = 0.0
    for index, note in enumerate(notes):
        if not isinstance(note, (list, tuple)) or len(note) != 2:
            raise ValueError(f"notes[{index}] must be [midi, beats]")
        midi, beats = note
        if (isinstance(midi, bool) or not isinstance(midi, (int, float))
                or not math.isfinite(midi) or not (midi == 0 or 1 <= midi <= 127)):
            raise ValueError(f"notes[{index}].midi must be 0 (rest) or between 1 and 127")
        if (isinstance(beats, bool) or not isinstance(beats, (int, float))
                or not math.isfinite(beats) or not 0.05 <= beats <= 32):
            raise ValueError(f"notes[{index}].beats must be between 0.05 and 32")
        normalized_notes.append([float(midi), float(beats)])
        sounding += int(midi > 0)
        total_beats += float(beats)
    if sounding == 0:
        raise ValueError("melody must contain at least one sounding note")
    vibrato = melody.get("vibrato", 35.0)
    if (isinstance(vibrato, bool) or not isinstance(vibrato, (int, float))
            or not math.isfinite(vibrato) or not 0 <= vibrato <= 100):
        raise ValueError("vibrato must be finite and between 0 and 100")
    t0 = melody.get("t0", 0.3)
    if isinstance(t0, bool) or not isinstance(t0, (int, float)) or not math.isfinite(t0) or not 0 <= t0 <= 10:
        raise ValueError("t0 must be finite and between 0 and 10 seconds")
    if total_beats * 60.0 / float(bpm) + float(t0) > _MAX_MELODY_DURATION_SECONDS:
        raise ValueError("melody duration including its start offset exceeds 30 minutes")
    return {
        "lyrics": lyrics, "bpm": float(bpm), "notes": normalized_notes,
        "sounding_notes": sounding, "note_count": len(normalized_notes),
        "rest_count": len(normalized_notes) - sounding,
        "vibrato": float(vibrato), "t0": float(t0),
    }


def _run_chimera(executable: Path, args: list[str], *, timeout: int, runner: Callable):
    try:
        return runner(
            [str(executable), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **no_console_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChimeraVocalError(f"CHIMERA CLI could not complete: {type(exc).__name__}") from exc


def _publish_new_file(source: Path, destination: Path, expected_sha256: str) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
    except FileExistsError as exc:
        raise ChimeraVocalError("refusing to overwrite an output created during rendering") from exc
    try:
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as original:
            shutil.copyfileobj(original, target)
            target.flush()
            os.fsync(target.fileno())
        if _sha256_file(destination) != expected_sha256:
            raise ChimeraVocalError("published CHIMERA WAV failed its SHA-256 copy check")
    except Exception:
        try:
            destination.unlink()
        except OSError:
            pass
        raise


def render_chimera_song(
    melody: object,
    output_path: str | Path,
    *,
    body: str = "female",
    cli_path: str | Path | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    runner: Callable = subprocess.run,
) -> dict[str, object]:
    """Render a custom melody with CHIMERA's physical voice engine to a new WAV.

    The adapter asks CHIMERA's own syllable router to preflight lyrics, then
    freezes those syllables into the notes JSON. Han text routes as Mandarin,
    not Cantonese; Cantonese is deliberately not advertised as supported.
    """
    if body not in _BODIES:
        raise ValueError("body must be male, female, or child")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 1800:
        raise ValueError("timeout must be an integer from 1 to 1800 seconds")
    normalized = _validate_song_melody(melody)
    executable = _resolve_cli(cli_path)
    output, _ = _validate_output_path(output_path)
    lyrics = str(normalized["lyrics"])

    with tempfile.TemporaryDirectory(prefix=".muse-chimera-", dir=output.parent) as temp_name:
        work = Path(temp_name)
        syllable_result = _run_chimera(executable, ["syllables", lyrics], timeout=timeout, runner=runner)
        if syllable_result.returncode != 0:
            detail = (syllable_result.stderr or syllable_result.stdout or "")[-1200:]
            raise ChimeraVocalError(f"CHIMERA lyric preflight failed: {detail}")
        syllable_json = syllable_result.stdout or ""
        if len(syllable_json.encode("utf-8")) > _MAX_MELODY_FILE_BYTES:
            raise ChimeraVocalError("CHIMERA lyric preflight JSON exceeds 1 MiB")
        try:
            syllables = json.loads(syllable_json)
        except json.JSONDecodeError as exc:
            raise ChimeraVocalError("CHIMERA lyric preflight did not return valid JSON") from exc
        if (not isinstance(syllables, list) or len(syllables) != normalized["sounding_notes"]
                or any(not isinstance(item, dict) or not isinstance(item.get("text"), str)
                       or not item["text"] for item in syllables)):
            raise ChimeraVocalError(
                f"CHIMERA parsed {len(syllables) if isinstance(syllables, list) else 'invalid'} syllables; "
                f"melody has {normalized['sounding_notes']} sounding notes"
            )

        melody_path = work / "melody.json"
        payload = {
            "bpm": normalized["bpm"],
            "notes": normalized["notes"],
            "syllables": syllables,
            "vibrato": normalized["vibrato"],
            "t0": normalized["t0"],
        }
        payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload_bytes) > _MAX_MELODY_FILE_BYTES:
            raise ChimeraVocalError("normalized CHIMERA melody JSON exceeds 1 MiB")
        melody_path.write_bytes(payload_bytes)
        output_stem = work / "rendered"
        completed = _run_chimera(
            executable,
            ["render", "notes", str(melody_path), str(output_stem), body],
            timeout=timeout,
            runner=runner,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "")[-1200:]
            raise ChimeraVocalError(f"CHIMERA song render failed: {detail}")
        warnings = [line.strip() for line in (completed.stderr or "").splitlines()
                    if line.lstrip().casefold().startswith("warning:")]
        if warnings:
            raise ChimeraVocalError("CHIMERA reported unsupported song phonemes: " + " | ".join(warnings)[-1200:])
        staged_wav = output_stem.with_suffix(".wav")
        if not staged_wav.is_file() or staged_wav.is_symlink():
            raise ChimeraVocalError("CHIMERA did not create a regular staged song WAV")
        facts = _wav_facts(staged_wav)
        digest = _sha256_file(staged_wav)
        _publish_new_file(staged_wav, output, digest)

    return {
        "status": "completed",
        "engine": "chimera-vocal-synth",
        "engine_license": "MIT",
        "engine_license_notice_required": True,
        "mode": "song",
        "language_front_end": "upstream_auto_router",
        "body": body,
        "path": str(output),
        "bytes": output.stat().st_size,
        "sha256": digest,
        "sample_rate": facts["sample_rate"],
        "channels": facts["channels"],
        "sample_width_bytes": facts["sample_width_bytes"],
        "frames": facts["frames"],
        "duration_seconds": facts["duration_seconds"],
        "bpm": normalized["bpm"],
        "note_count": normalized["note_count"],
        "sounding_notes": normalized["sounding_notes"],
        "rest_count": normalized["rest_count"],
        "syllable_count": len(syllables),
        "lyrics_sha256": hashlib.sha256(lyrics.encode("utf-8")).hexdigest(),
        "cantonese_supported": False,
        "human_naturalness_accepted": False,
        "requires_creator_listening_review": True,
        "stdout_tail": (completed.stdout or "")[-1200:],
        "stderr_tail": (completed.stderr or "")[-1200:],
    }


def _load_melody_file(path: str | Path) -> dict[str, object]:
    melody_path = Path(path).expanduser()
    if (_has_symlink_component(melody_path) or not melody_path.is_file()
            or melody_path.stat().st_size > _MAX_MELODY_FILE_BYTES):
        raise ChimeraVocalError("melody JSON must be a regular non-symlink file no larger than 1 MiB")
    try:
        value = json.loads(melody_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChimeraVocalError(f"melody JSON could not be read: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ChimeraVocalError("melody JSON must contain an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optional local CHIMERA voice and song renderer")
    parser.add_argument("--cli", help="Audited local CHIMERA CLI executable; defaults to MUSE_CHIMERA_CLI")
    parser.add_argument("--mode", choices=sorted(_MODES | {"song"}), default="text")
    parser.add_argument("--body", choices=sorted(_BODIES), default="female")
    parser.add_argument("--text", help="English text or explicit IPA, depending on --mode")
    parser.add_argument("--melody-json", help="Custom song JSON: bpm, lyrics and [[midi, beats], ...] notes")
    parser.add_argument("--output", required=True, help="New WAV output path; existing files are never overwritten")
    parser.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    try:
        if args.mode == "song":
            if not args.melody_json or args.text is not None:
                parser.error("--mode song requires --melody-json and does not accept --text")
            result = render_chimera_song(
                _load_melody_file(args.melody_json), args.output, body=args.body,
                cli_path=args.cli, timeout=args.timeout,
            )
        else:
            if args.text is None or args.melody_json is not None:
                parser.error("--mode text/ipa requires --text and does not accept --melody-json")
            result = render_chimera_voice(
                args.text, args.output, mode=args.mode, body=args.body,
                cli_path=args.cli, timeout=args.timeout,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
