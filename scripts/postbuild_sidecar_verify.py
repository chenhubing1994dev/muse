"""Verify built Sidecar and desktop runtime consistency by version and SHA256."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r'^SIDECAR_VERSION\s*=\s*["\']([^"\']+)["\']', re.M)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: Path) -> tuple[str, int]:
    if not root.is_dir():
        raise SystemExit(f"[post-build-verify] FAIL: runtime directory missing: {root}")
    h = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"[post-build-verify] FAIL: runtime directory is empty: {root}")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        file_hash = digest(path).encode("ascii")
        h.update(len(relative).to_bytes(4, "big"))
        h.update(relative)
        h.update(file_hash)
    return h.hexdigest(), len(files)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=ROOT / "src-tauri" / "sidecar-runtime")
    parser.add_argument("--desktop", type=Path)
    parser.add_argument("--engine", type=Path, default=ROOT / "src-tauri" / "binaries" / "muse-engine-x86_64-pc-windows-msvc.exe")
    parser.add_argument("--manifest", type=Path, default=ROOT / "build-sidecar" / "sidecar-integrity.json")
    args = parser.parse_args()
    source = ROOT / "src" / "muse_sidecar_main.py"
    built = args.runtime / "app" / "muse_sidecar_main.py"
    if not source.is_file() or not built.is_file():
        raise SystemExit(f"[post-build-verify] FAIL: missing source or built entry: {source} / {built}")
    source_version = VERSION_RE.search(source.read_text(encoding="utf-8"))
    built_version = VERSION_RE.search(built.read_text(encoding="utf-8"))
    if not source_version or not built_version:
        raise SystemExit("[post-build-verify] FAIL: Sidecar version is unknown")
    source_hash, built_hash = digest(source), digest(built)
    if source_version.group(1) != built_version.group(1) or source_hash != built_hash:
        raise SystemExit(
            f"[post-build-verify] FAIL: source/build mismatch; version {source_version.group(1)} != "
            f"{built_version.group(1)}, sha256 {source_hash} != {built_hash}"
        )
    runtime_hash, runtime_files = tree_digest(args.runtime)
    result = {
        "status": "passed",
        "version": source_version.group(1),
        "entry_sha256": source_hash,
        "runtime_tree_sha256": runtime_hash,
        "runtime_file_count": runtime_files,
        "runtime": str(args.runtime.resolve()),
    }
    if args.engine.is_file():
        result["built_engine_sha256"] = digest(args.engine)
    elif args.desktop:
        raise SystemExit(f"[post-build-verify] FAIL: built engine missing: {args.engine}")
    if args.desktop:
        desktop_root = args.desktop / "sidecar-runtime"
        desktop_entry = desktop_root / "app" / "muse_sidecar_main.py"
        desktop_engine = args.desktop / "muse-engine.exe"
        if not desktop_entry.is_file() or not desktop_engine.is_file():
            raise SystemExit(f"[post-build-verify] FAIL: desktop Sidecar or engine missing under {args.desktop}")
        desktop_version = VERSION_RE.search(desktop_entry.read_text(encoding="utf-8"))
        desktop_hash = digest(desktop_entry)
        desktop_tree_hash, desktop_file_count = tree_digest(desktop_root)
        if (not desktop_version or desktop_version.group(1) != source_version.group(1)
                or desktop_hash != source_hash or desktop_tree_hash != runtime_hash
                or desktop_file_count != runtime_files):
            raise SystemExit(
                f"[post-build-verify] FAIL: desktop runtime mismatch; version="
                f"{desktop_version.group(1) if desktop_version else 'unknown'}, "
                f"entry_sha256={desktop_hash}, runtime_sha256={desktop_tree_hash}, "
                f"files={desktop_file_count}; expected version={source_version.group(1)}, "
                f"entry_sha256={source_hash}, runtime_sha256={runtime_hash}, files={runtime_files}"
            )
        if args.engine.is_file() and digest(desktop_engine) != digest(args.engine):
            raise SystemExit(
                f"[post-build-verify] FAIL: desktop engine SHA256 {digest(desktop_engine)} "
                f"!= built engine SHA256 {digest(args.engine)}"
            )
        result["desktop_entry_sha256"] = desktop_hash
        result["desktop_runtime_tree_sha256"] = desktop_tree_hash
        result["desktop_runtime_file_count"] = desktop_file_count
        result["desktop_engine_sha256"] = digest(desktop_engine)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
