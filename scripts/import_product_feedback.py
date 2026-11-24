"""Import an exported Muse offline product worksheet into the Research Wiki."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from muse.product_feedback_import import (  # noqa: E402
    ProductFeedbackImportError,
    import_offline_product_feedback,
    parse_product_feedback_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT, help="Muse workspace root")
    parser.add_argument("--file", type=Path, required=True, help="JSON exported by the offline Muse product")
    args = parser.parse_args()
    path = args.file
    if path.is_symlink() or not path.is_file():
        parser.error("--file must be a regular, non-symlink file")
    try:
        raw = path.read_bytes()
        payload = parse_product_feedback_json(raw)
        result = import_offline_product_feedback(args.workspace, payload)
    except (ProductFeedbackImportError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
