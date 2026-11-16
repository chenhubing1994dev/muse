"""Inspect the persistent Muse capability registry."""

from __future__ import annotations

import argparse
import json
from typing import Sequence

from muse.capabilities import CapabilityRegistry, sync_media_capabilities
from muse.media import discover_media_tools


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synchronize and inspect verified Muse capabilities")
    parser.add_argument("--db", default="data/capabilities/muse.sqlite3")
    parser.add_argument("--domain")
    parser.add_argument("--status", choices=("ready", "missing", "unverified", "disabled", "needs_approval"))
    parser.add_argument("--no-sync", action="store_true", help="list persisted entries without probing local media tools")
    args = parser.parse_args(argv)
    with CapabilityRegistry(args.db) as registry:
        sync = None if args.no_sync else sync_media_capabilities(registry, discover_media_tools())
        items = registry.list_all(domain=args.domain, status=args.status)
    print(json.dumps({"sync": sync, "count": len(items), "capabilities": items}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
