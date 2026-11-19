from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from muse.subtitle_review import apply_human_subtitle_review, render_approved_subtitles


_CONFIRMATION = "APPROVE-HUMAN-REVIEW"


def _safe_input(path: str, label: str) -> Path:
    candidate = Path(path).expanduser().absolute()
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents)):
        raise ValueError(f"{label} must not be inside a symlink path")
    if not candidate.is_file():
        raise ValueError(f"{label} must be an existing regular file")
    return candidate.resolve(strict=True)


def _write_new_file(path: Path, content: str) -> None:
    path = path.expanduser().absolute()
    if not path.parent.is_dir() or any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("output path must use an existing regular directory")
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".muse-review-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"refusing to overwrite existing output: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply an explicitly human-completed Muse Cantonese subtitle review."
    )
    parser.add_argument("--review", required=True, help="subtitle-review.json from the video delivery bundle")
    parser.add_argument("--answers", required=True, help="JSON with reviewed_segment_indexes and corrections")
    parser.add_argument("--reviewer", required=True, help="human reviewer identity or initials")
    parser.add_argument("--output-review", help="new review JSON path; defaults to <review>.reviewed.json")
    parser.add_argument("--srt-output", help="approved SRT path; defaults beside output review")
    parser.add_argument("--approve", action="store_true", help="approve only after every segment has been listened to and reviewed")
    parser.add_argument("--confirm", help=f"must equal {_CONFIRMATION} when --approve is supplied")
    parser.add_argument("--workspace", default=".", help="Muse workspace for an optional approved-product revision")
    parser.add_argument("--remux-approved-product", action="store_true", help="create a new Creator product version with the approved SRT muxed as a selectable MP4 subtitle track")
    parser.add_argument("--product-id", help="video product initiative_id; required with --remux-approved-product")
    parser.add_argument("--version-id", help="video product version_id; required with --remux-approved-product")
    args = parser.parse_args()

    try:
        if args.approve and args.confirm != _CONFIRMATION:
            raise ValueError(f"--approve requires --confirm {_CONFIRMATION}")
        if not args.approve and args.confirm is not None:
            raise ValueError("--confirm is only valid together with --approve")
        if args.remux_approved_product and (not args.approve or not args.product_id or not args.version_id):
            raise ValueError("--remux-approved-product requires --approve, --product-id and --version-id")
        if not args.remux_approved_product and (args.product_id is not None or args.version_id is not None):
            raise ValueError("--product-id and --version-id are only valid with --remux-approved-product")
        review_path = _safe_input(args.review, "review JSON")
        answers_path = _safe_input(args.answers, "review answers JSON")
        review = json.loads(review_path.read_text(encoding="utf-8-sig"))
        answers = json.loads(answers_path.read_text(encoding="utf-8-sig"))
        if not isinstance(answers, dict) or set(answers) != {"reviewed_segment_indexes", "corrections"}:
            raise ValueError("answers JSON must contain exactly reviewed_segment_indexes and corrections")
        updated = apply_human_subtitle_review(
            review,
            reviewed_segment_indexes=answers["reviewed_segment_indexes"],
            corrections=answers["corrections"],
            reviewer=args.reviewer,
            approve=args.approve,
        )
        output_review = Path(args.output_review).expanduser() if args.output_review else review_path.with_name(
            review_path.stem + ".reviewed.json"
        )
        _write_new_file(output_review, json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
        print(f"Review saved: {output_review}")
        print(f"Review status: {updated['review_status']}")
        if updated["human_approved"]:
            output_srt = Path(args.srt_output).expanduser() if args.srt_output else output_review.with_name(
                "reviewed-subtitles.srt"
            )
            _write_new_file(output_srt, render_approved_subtitles(updated))
            print(f"Approved SRT saved: {output_srt}")
            if args.remux_approved_product:
                from muse.video_product_builder import VideoProductBuilder
                product = VideoProductBuilder(args.workspace).package_approved_subtitle_revision(
                    args.product_id, args.version_id, updated,
                )
                print(f"Creator product revision: {product['initiative_id']}/{product['version_id']}")
                print(f"Creator initiative: {product['creator_initiative_id']} (status remains testing)")
                print(f"Ready to publish: {str(bool(product.get('quality_gate', {}).get('ready_to_publish'))).lower()}")
        else:
            print("No approved SRT was generated; review every segment and explicitly approve to release captions.")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"subtitle review failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
