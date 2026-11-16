"""CLI for previewing a safe Muse execution plan from a natural-language request."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from muse.reminders import ReminderStore, parse_capture_text
from typing import Sequence

from muse.capabilities import CapabilityRegistry, sync_media_capabilities
from muse.intent import build_execution_plan, classify_request
from muse.media import discover_media_tools


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify a request and preview a Muse execution plan")
    parser.add_argument("request", help="natural-language task request")
    parser.add_argument("--capability-db", default="data/capabilities/muse.sqlite3")
    parser.add_argument("--create-reminder", action="store_true", help="explicitly save a parsed reminder request locally")
    parser.add_argument("--reminder-db", default="data/reminders/muse.sqlite3")
    parser.add_argument("--timezone", help="IANA timezone for reminder parsing")
    args = parser.parse_args(argv)
    inventory = discover_media_tools()
    with CapabilityRegistry(args.capability_db) as registry:
        sync = sync_media_capabilities(registry, inventory)
        classification = classify_request(args.request, inventory=inventory, registry=registry)
    plan = build_execution_plan(classification)
    reminder = None
    if args.create_reminder:
        if classification.get("capability") != "reminder_capture":
            parser.error("--create-reminder requires a recognized reminder request")
        try:
            parsed = parse_capture_text(args.request, timezone_name=args.timezone)
            with ReminderStore(Path(args.reminder_db)) as store:
                reminder = store.add(parsed["title"], parsed["message"], due_at=parsed["due_at"], source="intent_cli")
            reminder["parsed_timezone"] = parsed["timezone"]
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    print(json.dumps({"registry_sync": sync, "classification": classification, "plan": plan,
                      "reminder": reminder}, ensure_ascii=False, indent=2))
    return 0 if plan["status"] not in {"blocked", "needs_clarification"} else 2

if __name__ == "__main__":
    raise SystemExit(main())