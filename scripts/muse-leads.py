from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from muse.lead_intake import LeadStore, export_leads_csv  # noqa: E402


def project_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(ROOT.resolve(strict=True)):
        raise ValueError("path must remain inside the Muse project directory")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the local Muse inquiry inbox.")
    parser.add_argument("--database", default="data/commerce/leads.sqlite3")
    commands = parser.add_subparsers(dest="action", required=True)
    listing = commands.add_parser("list", help="show recent leads without contact details")
    listing.add_argument("--limit", type=int, default=50)
    detail = commands.add_parser("show", help="show one lead, including its contact details")
    detail.add_argument("lead_reference")
    export = commands.add_parser("export", help="export all non-expired leads to a new CSV file")
    export.add_argument("--output", required=True, help="new CSV path inside the project")
    args = parser.parse_args()
    try:
        store = LeadStore(project_path(args.database))
        if args.action == "list":
            print(json.dumps(store.list_leads(limit=args.limit), ensure_ascii=False, indent=2))
            return 0
        if args.action == "show":
            lead = store.get_lead(args.lead_reference)
            if lead is None:
                print("No matching lead.")
                return 1
            print(json.dumps(lead, ensure_ascii=False, indent=2))
            return 0
        output = export_leads_csv(store.export_rows(), project_path(args.output))
        print(json.dumps({
            "export": str(output.relative_to(ROOT)),
            "records": store.count(),
            "contains_contact_details": True,
        }, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
