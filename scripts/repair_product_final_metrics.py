"""Repair historical video-product metrics to describe the final post-produced MP4."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from muse.video_product_builder import VideoProductBuilder, _finalize_video_metadata


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def repair(workspace: Path, initiative_id: str, version_id: str) -> dict[str, object]:
    builder = VideoProductBuilder(workspace)
    products = workspace / "data" / "products"
    before = builder.verify_path(products, initiative_id, version_id)
    if before.get("validated") is not True:
        raise RuntimeError("refusing to repair an unverified product")
    product = products / initiative_id / version_id
    manifest_path = product / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = manifest.get("video_metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("motion"), dict):
        raise RuntimeError("product does not contain motion metadata")
    qa = json.loads((product / "qa-report.json").read_text(encoding="utf-8"))
    final_metadata = _finalize_video_metadata(metadata, qa, product / "video.mp4", motion=metadata["motion"])
    final_metadata["output"] = "video.mp4"
    manifest["video_metadata"] = final_metadata

    storyboard_path = product / "storyboard.json"
    storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    fields = ("duration_seconds", "frames", "width", "height", "fps", "has_audio", "bytes")
    storyboard["final_output"] = {key: final_metadata[key] for key in fields if key in final_metadata}

    readme_path = product / "README.md"
    lines = readme_path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith("- 帧数：") and not line.startswith("- 时长：")]
    frame_rate_index = next((i for i, line in enumerate(lines) if line.startswith("- 帧率：")), None)
    source_duration = final_metadata.get("source_duration_seconds", metadata.get("duration_seconds", "未知"))
    source_frames = final_metadata.get("source_frames", metadata.get("frames", "未知"))
    insert = [
        f"- AI 原始镜头：约 {source_duration} 秒 / {source_frames} 帧",
        f"- Remotion 后成片：{final_metadata.get('duration_seconds', '未知')} 秒 / {final_metadata.get('frames', '未知')} 帧",
    ]
    if frame_rate_index is None:
        lines.extend(insert)
    else:
        lines[frame_rate_index + 1:frame_rate_index + 1] = insert
    readme = "\n".join(lines) + "\n"

    backup_root = products / ".metadata-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=f"{initiative_id}-{version_id}-", dir=backup_root))
    names = ("README.md", "storyboard.json", "qa-report.json", "video.mp4", "product.zip", "manifest.json")
    for name in names:
        shutil.copy2(product / name, backup / name)

    staging = Path(tempfile.mkdtemp(prefix=".metrics-repair-", dir=product.parent))
    try:
        (staging / "README.md").write_text(readme, encoding="utf-8")
        (staging / "storyboard.json").write_text(json.dumps(storyboard, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        shutil.copy2(product / "qa-report.json", staging / "qa-report.json")
        shutil.copy2(product / "video.mp4", staging / "video.mp4")
        with zipfile.ZipFile(staging / "product.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ("README.md", "storyboard.json", "qa-report.json", "video.mp4"):
                archive.write(staging / name, name)
        manifest["files"] = {name: digest(staging / name) for name in ("README.md", "storyboard.json", "qa-report.json", "video.mp4", "product.zip")}
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for name in names:
            (staging / name).replace(product / name)
        after = builder.verify_path(products, initiative_id, version_id)
        if after.get("validated") is not True:
            raise RuntimeError("repaired product failed verification; restoring backup")
        return {"validated": True, "backup": str(backup), "product": str(product),
                "duration_seconds": final_metadata.get("duration_seconds"),
                "frames": final_metadata.get("frames"), "bytes": final_metadata.get("bytes"),
                "mp4_sha256": after.get("mp4_sha256"), "package_sha256": after.get("package_sha256")}
    except Exception:
        for name in names:
            source = backup / name
            if source.is_file():
                shutil.copy2(source, product / name)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("initiative_id")
    parser.add_argument("version_id")
    parser.add_argument("--workspace", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    result = repair(Path(args.workspace).resolve(), args.initiative_id, args.version_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
