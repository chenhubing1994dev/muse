"""CLI for inspecting and reviewing source-identity candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from muse.research_knowledge import ResearchKnowledgeStore


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review Muse source identity candidates")
    parser.add_argument("--db", default="data/research/knowledge.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="list detected source relationship candidates")
    listing.add_argument("--status", choices=("pending", "accepted", "rejected", "all"), default="pending")
    listing.add_argument("--limit", type=int, default=100)

    review = commands.add_parser("review", help="record an explicit review of one candidate")
    review.add_argument("candidate_id")
    review.add_argument("--decision", choices=("accepted", "rejected"), required=True)
    review.add_argument("--by", required=True, dest="reviewer_id")
    review.add_argument("--rationale", required=True)

    args = parser.parse_args(argv)
    with ResearchKnowledgeStore(Path(args.db)) as store:
        if args.command == "list":
            _print(store.list_source_identity_candidates(status=args.status, limit=args.limit))
        elif args.command == "review":
            result = store.review_source_identity_candidate(
                args.candidate_id, reviewer_id=args.reviewer_id,
                decision=args.decision, rationale=args.rationale,
            )
            _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
