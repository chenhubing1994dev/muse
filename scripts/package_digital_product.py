from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\\\" in value or ":" in value:
        raise ValueError(f"{label} must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must not be absolute or traverse directories")
    return path


def _is_reparse(path: Path) -> bool:
    return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_product(
    workspace: Path,
    *,
    source_relative: str = "outputs/digital_products/ai-fact-check-starter-kit-v1",
    output_relative: str = "outputs/digital_products/ai-fact-check-starter-kit-v1-review.zip",
) -> dict[str, Any]:
    root = workspace.resolve(strict=True)
    source_rel = _safe_relative(source_relative, "source_relative")
    output_rel = _safe_relative(output_relative, "output_relative")
    source = root.joinpath(*source_rel.parts)
    if source.is_symlink() or _is_reparse(source) or not source.is_dir():
        raise ValueError("product source must be a regular directory")
    source = source.resolve(strict=True)
    if not source.is_relative_to(root):
        raise ValueError("product source escapes workspace")

    manifest_path = source / "product-manifest.json"
    if manifest_path.is_symlink() or _is_reparse(manifest_path) or not manifest_path.is_file():
        raise ValueError("product manifest is missing or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "internal_review_draft":
        raise ValueError("only internal_review_draft products may be packaged")
    release = manifest.get("release")
    if not isinstance(release, dict) or release.get("can_publish") is not False:
        raise ValueError("product release gate must remain closed")
    contents = manifest.get("contents")
    if not isinstance(contents, list) or not contents:
        raise ValueError("product manifest must declare a non-empty contents list")

    names: list[str] = []
    seen: set[str] = set()
    for item in [*contents, "product-manifest.json"]:
        relative = _safe_relative(item, "manifest content path")
        name = relative.as_posix()
        if name in seen:
            raise ValueError("product manifest contains duplicate file paths")
        seen.add(name)
        candidate = source.joinpath(*relative.parts)
        if candidate.is_symlink() or _is_reparse(candidate) or not candidate.is_file():
            raise ValueError(f"product file is missing or unsafe: {name}")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(source):
            raise ValueError(f"product file escapes source directory: {name}")
        names.append(name)

    output_parent = root
    for part in output_rel.parts[:-1]:
        output_parent = output_parent / part
        if output_parent.exists():
            if output_parent.is_symlink() or _is_reparse(output_parent) or not output_parent.is_dir():
                raise ValueError("output parent contains an unsafe path component")
        else:
            output_parent.mkdir()
    output_parent = output_parent.resolve(strict=True)
    if not output_parent.is_relative_to(root):
        raise ValueError("output directory escapes workspace")
    output = output_parent / output_rel.parts[-1]
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing package: {output_rel.as_posix()}")

    try:
        with output.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in names:
                    archive.write(source.joinpath(*PurePosixPath(name).parts), name)
        with zipfile.ZipFile(output, "r") as archive:
            if archive.testzip() is not None or archive.namelist() != names:
                raise RuntimeError("package ZIP integrity or file-order verification failed")
        return {
            "status": "internal_review_draft",
            "path": output.relative_to(root).as_posix(),
            "bytes": output.stat().st_size,
            "sha256": _sha256(output),
            "file_count": len(names),
            "can_publish": False,
            "payment_processor": "not_connected",
            "sales": 0,
            "settled_revenue_usd": "0.00",
        }
    except Exception:
        if output.exists() and not output.is_symlink():
            output.unlink()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Package an internal-review Muse digital product.")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--source", default="outputs/digital_products/ai-fact-check-starter-kit-v1")
    parser.add_argument("--output", default="outputs/digital_products/ai-fact-check-starter-kit-v1-review.zip")
    args = parser.parse_args()
    result = package_product(Path(args.workspace), source_relative=args.source, output_relative=args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
