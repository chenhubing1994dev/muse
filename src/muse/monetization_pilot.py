"""Build a local Cantonese-narrated, review-only video package without production DB writes."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SOURCE_RELATIVE = "data/prometheus/high-value-short-b133"
OUTPUT_RELATIVE = "data/monetization/first-cantonese-pilot-2026-11-24"
NARRATION = (
    "人工智能可以答得好快，但未必答得啱。"
    "先拆開人名、日期同數字。再查原始來源，睇原文支持唔支持。"
    "最後搵反面證據。冇來源，就標記未證實。"
)
TITLE = "AI 講得快，不代表答得啱｜三步查證法（粵語旁白版）"


def resolve_workspace_file(workspace: Path, relative: str) -> Path:
    """Resolve a regular file strictly inside workspace; reject traversal/reparse points."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("workspace path must be a non-empty relative POSIX path")
    candidate = Path(relative)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("workspace path must not be absolute or traverse directories")
    root = workspace.resolve(strict=True)
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink() or bool(getattr(current.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("workspace path must not contain symlinks or reparse points")
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("workspace path must resolve to a regular file inside the workspace")
    return resolved


def _resolve_output_target(parent: Path, output_name: str) -> Path:
    """Return a new output directory path; never overwrite an existing file or directory."""
    if not output_name or output_name in {".", ".."} or "/" in output_name or "\\" in output_name or ":" in output_name:
        raise ValueError("output_name must be a single safe path component")
    target = parent / output_name
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing output: {output_name}")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_platform_drafts(*, title: str, source_title: str, narration: str) -> dict[str, dict[str, Any]]:
    if not all(isinstance(value, str) and value.strip() for value in (title, source_title, narration)):
        raise ValueError("title, source_title and narration are required")
    description = (
        "AI 講得好肯定，就一定係真？記住三步：拆開主張、查原始來源、主動尋找反證。\n\n"
        f"研究來源：{source_title}。詳情見隨包 source-notes.json。\n"
        "本片三步查證法屬實用編輯建議，不代表來源機構認證本片。\n"
        "本片畫面包含 AI 生成素材；旁白由本地 AI 語音合成。"
    )
    return {
        key: {
            "title": title,
            "description": description,
            "hashtags": tags,
            "release_state": "draft_only",
            "publish_api_called": False,
            "disclosure": "AI-generated visuals and locally synthesized AI voiceover; review platform-specific disclosure rules.",
            "source_reference": "source-notes.json",
        }
        for key, tags in {
            "youtube_shorts": ["#AI", "#人工智能", "#事实核验", "#知识科普"],
            "tiktok": ["#AI", "#人工智能", "#查證", "#知識分享"],
            "instagram_reels": ["#AI", "#FactChecking", "#人工智能"],
            "bilibili": ["人工智能", "事实核验", "信息素养", "知识科普"],
        }.items()
    }


def build_asr_review_gate(asr_result: dict[str, Any]) -> dict[str, Any]:
    status = asr_result.get("status")
    if status not in {"passed", "needs_review", "transcribed"}:
        raise ValueError("ASR result must carry a recognized status")
    transcript = asr_result.get("transcript")
    comparison = asr_result.get("comparison")
    if not isinstance(transcript, str) or not transcript.strip() or not isinstance(comparison, dict):
        raise ValueError("ASR result must include a transcript and structured comparison")
    cer = comparison.get("cer")
    if (
        not isinstance(cer, (int, float)) or isinstance(cer, bool)
        or not 0 <= cer <= 1
    ):
        raise ValueError("ASR comparison must include a valid character error rate")
    encoding_integrity = comparison.get("encoding_integrity")
    comparison_status = comparison.get("status")
    encoding_failed = (
        "\ufffd" in transcript
        or encoding_integrity != "passed"
    )
    comparison_failed = comparison_status != "passed"
    gate_status = (
        "passed"
        if status == "passed" and not encoding_failed and not comparison_failed
        else "needs_review"
    )
    return {
        "status": gate_status,
        "asr_status": status,
        "comparison_status": comparison_status,
        "encoding_integrity": "failed" if encoding_failed else "passed",
        "character_error_rate": cer,
        "human_subtitle_review_required": True,
        "ready_to_publish": False,
    }


def build_release_checklist() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "release_state": "draft_only",
        "can_publish": False,
        "account_authorization": "not_connected",
        "official_publish_api": "not_called",
        "human_editorial_review": "pending",
        "human_voice_listening_review": "pending",
        "visual_and_model_rights_review": "pending",
        "source_and_fact_review": "pending",
        "ai_generated_visual_and_voice_disclosure": "pending_platform_specific_review",
        "privacy_and_person_release_review": "pending",
        "platform_upload_specifications": "manual_account_specific_check_required",
        "real_platform_metrics_and_revenue": "unavailable_until_authorized_data_import",
    }

def _run(
    command: list[str], *, cwd: Path, timeout: int = 900,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown process error")[-3000:]
        raise RuntimeError(f"command failed ({result.returncode}): {detail}")
    return result


def _probe(ffprobe: str, path: Path, workspace: Path) -> dict[str, Any]:
    result = _run([
        ffprobe, "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_name,codec_type,width,height,avg_frame_rate,sample_rate,channels",
        "-of", "json", str(path),
    ], cwd=workspace, timeout=60)
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or not isinstance(value.get("streams"), list):
        raise RuntimeError("FFprobe returned invalid media metadata")
    return value


def _run_local_asr(
    workspace: Path, *, audio: Path, model_dir: Path, packages_dir: Path,
    subtitles: Path, reference_text: str,
) -> dict[str, Any]:
    launcher = shutil.which("py") or shutil.which("py.exe")
    if not launcher:
        raise RuntimeError("Python launcher is required for the pinned Python 3.14 Cantonese ASR runtime")
    child_code = (
        "import base64,json,sys; "
        "sys.path.insert(0,sys.argv[4]); "
        "from muse.cantonese_asr import transcribe_cantonese; "
        "reference=base64.b64decode(sys.argv[5],validate=True).decode('utf-8'); "
        "r=transcribe_cantonese(sys.argv[1],model_dir=sys.argv[2],output_srt=sys.argv[3],"
        "reference_text=reference,max_cer=0.10,device='cpu',compute_type='int8',"
        "condition_on_previous_text=False,vad_filter=False); "
        "print('MUSE_ASR_RESULT='+json.dumps(r,ensure_ascii=True))"
    )
    env = os.environ.copy()
    source_root = str(workspace / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_root, env.get("PYTHONPATH", "")) if item
    )
    env["PYTHONIOENCODING"] = "utf-8"
    reference_b64 = base64.b64encode(reference_text.encode("utf-8")).decode("ascii")
    result = _run([
        launcher, "-3.14", "-c", child_code,
        str(audio), str(model_dir), str(subtitles), str(packages_dir), reference_b64,
    ], cwd=workspace, timeout=1800, env=env)
    marker = "MUSE_ASR_RESULT="
    payload = next((line[len(marker):] for line in reversed(result.stdout.splitlines()) if line.startswith(marker)), None)
    if payload is None:
        raise RuntimeError("Cantonese ASR returned no structured result")
    value = json.loads(payload)
    if not isinstance(value, dict) or not isinstance(value.get("transcript"), str) or not value["transcript"].strip():
        raise RuntimeError("Cantonese ASR returned no usable transcript; refusing to package unreviewable narration")
    if not subtitles.is_file() or subtitles.stat().st_size < 16:
        raise RuntimeError("Cantonese ASR did not produce timestamped subtitles")
    subtitle_metadata = value.get("subtitles")
    if not isinstance(subtitle_metadata, dict):
        raise RuntimeError("Cantonese ASR returned no subtitle metadata")
    subtitle_metadata["path"] = subtitles.name
    return value

def create_cantonese_video_package(
    workspace: Path, *, source_relative: str = SOURCE_RELATIVE,
    output_relative: str = OUTPUT_RELATIVE, title: str = TITLE,
) -> dict[str, Any]:
    """Create a new voiceover video and ZIP in an isolated output folder; never touch DBs."""
    workspace = workspace.resolve(strict=True)
    source_dir = (workspace / source_relative).resolve(strict=True)
    if not source_dir.is_relative_to(workspace):
        raise ValueError("source directory escapes the Muse workspace")
    source_video = resolve_workspace_file(workspace, f"{source_relative}/ai-verification-three-steps-9x16.mp4")
    source_cover = resolve_workspace_file(workspace, f"{source_relative}/cover.jpg")
    source_notes = resolve_workspace_file(workspace, f"{source_relative}/source-notes.json")
    source_storyboard = resolve_workspace_file(workspace, f"{source_relative}/storyboard.json")
    output_parts = output_relative.split("/") if isinstance(output_relative, str) else []
    if (len(output_parts) < 2 or any(part in {"", ".", ".."} for part in output_parts)
            or "\\" in output_relative or ":" in output_relative):
        raise ValueError("output_relative must be a safe workspace-relative POSIX directory")
    output_name = output_parts[-1]
    parent = workspace
    for part in output_parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise ValueError("output parent must not contain symlinks")
        if parent.exists():
            info = parent.lstat()
            if bool(getattr(info, "st_file_attributes", 0) & 0x400) or not parent.is_dir():
                raise ValueError("output parent components must be regular directories")
        else:
            parent.mkdir()
    parent = parent.resolve(strict=True)
    if not parent.is_relative_to(workspace):
        raise ValueError("output parent must remain inside the workspace")
    final_dir = _resolve_output_target(parent, output_name)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_name}.staging-", dir=parent))
    try:
        packages_dir = workspace / ".muse-canto-tts-py314"
        model_dir = workspace / "data" / "models" / "canto-tts-nano"
        asr_model_dir = workspace / "models" / "whisper-small-cantonese"
        for candidate in (packages_dir, model_dir, asr_model_dir):
            if candidate.is_symlink() or not candidate.is_dir():
                raise RuntimeError(f"required local Cantonese model/runtime path is missing or unsafe: {candidate.name}")

        from muse.cantonese_tts import (
            preflight_cantonese_tts_runtime,
            synthesize_cantonese_segments_with_runtime,
        )
        preflight = preflight_cantonese_tts_runtime(packages_dir, model_dir)
        voiceover = staging / "voiceover.wav"
        synthesis = synthesize_cantonese_segments_with_runtime(
            NARRATION, voiceover, model_dir=model_dir, packages_dir=packages_dir,
            quality="duration_filter",
        )
        subtitles = staging / "subtitles.srt"
        asr = _run_local_asr(
            workspace, audio=voiceover, model_dir=asr_model_dir, packages_dir=packages_dir,
            subtitles=subtitles, reference_text=NARRATION,
        )

        from muse.media import discover_media_tools
        inventory = discover_media_tools()
        tools = {item.tool_id: item.executable for item in inventory.tools}
        ffmpeg, ffprobe = tools.get("ffmpeg"), tools.get("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("FFmpeg and FFprobe are required")
        source_meta = _probe(ffprobe, source_video, workspace)
        source_video_stream = next((item for item in source_meta["streams"] if item.get("codec_type") == "video"), None)
        source_audio_stream = next((item for item in source_meta["streams"] if item.get("codec_type") == "audio"), None)
        source_duration = float(source_meta.get("format", {}).get("duration", 0))
        voice_duration = float(synthesis.get("duration_seconds", 0))
        if not source_video_stream or source_duration <= 0 or voice_duration <= 0:
            raise RuntimeError("source video or generated voiceover has invalid duration/streams")
        if voice_duration > source_duration + 0.15:
            raise RuntimeError(
                f"voiceover ({voice_duration:.2f}s) exceeds source video ({source_duration:.2f}s); "
                "refusing to truncate narration"
            )

        output_video = staging / "video.mp4"
        if source_audio_stream:
            filter_complex = (
                "[0:a]volume=0.18[bg];"
                "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
                "[bg][voice]amix=inputs=2:duration=first:dropout_transition=2[a]"
            )
            command = [
                ffmpeg, "-hide_banner", "-y", "-i", str(source_video), "-i", str(voiceover),
                "-filter_complex", filter_complex, "-map", "0:v:0", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-ac", "2", "-movflags", "+faststart", str(output_video),
            ]
        else:
            command = [
                ffmpeg, "-hide_banner", "-y", "-i", str(source_video), "-i", str(voiceover),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
                str(output_video),
            ]
        _run(command, cwd=workspace, timeout=600)
        output_meta = _probe(ffprobe, output_video, workspace)
        video_stream = next((item for item in output_meta["streams"] if item.get("codec_type") == "video"), None)
        audio_stream = next((item for item in output_meta["streams"] if item.get("codec_type") == "audio"), None)
        duration = float(output_meta.get("format", {}).get("duration", 0))
        if (
            not video_stream or not audio_stream
            or video_stream.get("codec_name") != "h264"
            or video_stream.get("width") != 1080 or video_stream.get("height") != 1920
            or audio_stream.get("codec_name") != "aac"
            or abs(duration - source_duration) > 0.35
        ):
            raise RuntimeError("voiceover video failed technical stream/duration verification")
        _run([ffmpeg, "-hide_banner", "-v", "error", "-i", str(output_video), "-f", "null", "-"], cwd=workspace, timeout=600)

        shutil.copy2(source_cover, staging / "cover.jpg")
        shutil.copy2(source_notes, staging / "source-notes.json")
        shutil.copy2(source_storyboard, staging / "storyboard.json")
        (staging / "narration.txt").write_text(NARRATION + "\n", encoding="utf-8")
        (staging / "README.md").write_text(
            f"# {title}\n\n"
            f"- 影片：{duration:.2f}s，1080×1920，H.264 + AAC。\n"
            "- 旁白：本地 Canto-TTS Nano 粤语合成；Whisper Cantonese ASR 已生成时间戳字幕。\n"
            "- ASR CER 未达门槛时，旁白和字幕只保留人工校对草稿，不降低验收标准。\n"
            "- 画面：复用已验证的 Muse Wan 2.1 原创镜头；本轮未重新启动 GPU 生成。\n"
            "- 声音：原片环境声降低音量后与粤语旁白混音。\n"
            "- 字幕：由本地 ASR 时间戳生成；仍需人工逐句校对及听审。\n"
            "- 状态：draft_only；未发布、未连接账号、未产生或确认任何收入。\n",
            encoding="utf-8",
        )
        asr_gate = build_asr_review_gate(asr)
        (staging / "asr-result.json").write_text(
            json.dumps(asr, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (staging / "subtitle-review.json").write_text(
            json.dumps({
                "schema_version": 1, "status": asr_gate["status"],
                "reference_text": NARRATION, "recognized_text": asr["transcript"],
                "character_error_rate": asr_gate["character_error_rate"],
                "human_review_required": True, "publish_blocked": True,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        platform_drafts = build_platform_drafts(
            title=title, source_title="NIST AI 600-1 Generative AI Profile", narration=NARRATION,
        )
        (staging / "platform-drafts.json").write_text(
            json.dumps(platform_drafts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        checklist = build_release_checklist()
        (staging / "release-checklist.json").write_text(
            json.dumps(checklist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        qa = {
            "schema_version": 1, "status": "passed", "full_decode": "passed",
            "source_duration_seconds": round(source_duration, 3),
            "output_duration_seconds": round(duration, 3),
            "video": {key: video_stream.get(key) for key in ("codec_name", "width", "height", "avg_frame_rate")},
            "audio": {key: audio_stream.get(key) for key in ("codec_name", "sample_rate", "channels")},
            "voiceover_asr_cer": asr["comparison"]["cer"],
            "voiceover_asr_gate": asr["status"],
            "subtitle_review_status": asr_gate["status"],
            "ready_to_publish": False,
        }
        (staging / "qa-report.json").write_text(
            json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        file_names = (
            "video.mp4", "voiceover.wav", "subtitles.srt", "cover.jpg",
            "source-notes.json", "storyboard.json", "narration.txt",
            "platform-drafts.json", "release-checklist.json", "qa-report.json",
            "asr-result.json", "subtitle-review.json", "README.md",
        )
        manifest = {
            "schema_version": 1, "status": "completed", "title": title,
            "source_video": {
                "path": f"{source_relative}/ai-verification-three-steps-9x16.mp4",
                "sha256": _sha256(source_video),
            },
            "voiceover": {
                "engine": synthesis.get("engine"), "model_repo": synthesis.get("model_repo"),
                "model_revision": synthesis.get("model_revision"), "license": synthesis.get("license"),
                "profile": "default_local_voice; not represented as a mature female voice",
                "duration_seconds": round(voice_duration, 3), "sha256": _sha256(voiceover),
                "asr_model_revision": asr.get("model_revision"),
                "asr_cer": asr["comparison"]["cer"], "asr_gate": asr["status"],
                "human_listening_review_required": True,
            },
            "video": {
                "path": "video.mp4", "duration_seconds": round(duration, 3),
                "width": 1080, "height": 1920, "codec": "h264", "audio_codec": "aac",
                "sha256": _sha256(output_video), "full_decode": "passed",
            },
            "files": {name: _sha256(staging / name) for name in file_names},
            "quality_gate": asr_gate,
            "release_state": "draft_only", "auto_publish": False, "publish_api_called": False,
            "tts_preflight": preflight,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        package_path = staging / "delivery-package.zip"
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in (*file_names, "manifest.json"):
                archive.write(staging / name, name)
        with zipfile.ZipFile(package_path) as archive:
            if archive.testzip() is not None or len(archive.namelist()) != len(file_names) + 1:
                raise RuntimeError("delivery package ZIP integrity check failed")
        package_sha = _sha256(package_path)
        (staging / "package.sha256").write_text(f"{package_sha}  delivery-package.zip\n", encoding="ascii")
        staging.rename(final_dir)
        final_package = final_dir / "delivery-package.zip"
        return {
            "status": "completed", "output_dir": str(final_dir.relative_to(workspace)).replace("\\", "/"),
            "video": {**manifest["video"], "path": f"{output_relative}/video.mp4"},
            "voiceover": manifest["voiceover"], "qa": qa, "asr_gate": asr_gate,
            "package": {
                "path": f"{output_relative}/delivery-package.zip",
                "bytes": final_package.stat().st_size,
                "sha256": package_sha, "zip_crc_valid": True,
            },
            "release_state": "draft_only", "can_publish": False,
        }
    except Exception:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a local Cantonese-narrated Muse video package.")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--source", default=SOURCE_RELATIVE)
    parser.add_argument("--output", default=OUTPUT_RELATIVE)
    parser.add_argument("--title", default=TITLE)
    args = parser.parse_args(argv)
    try:
        result = create_cantonese_video_package(
            Path(args.workspace), source_relative=args.source,
            output_relative=args.output, title=args.title,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "failed", "error_type": type(exc).__name__, "message": str(exc)[:1200],
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
