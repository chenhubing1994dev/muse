"""Read-only citation anchor verifier for Research Wiki source snapshots.

The verifier never fetches URLs or modifies either SQLite database. It checks that
an exact, unique excerpt belongs to the stored snapshot identified by URL + SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


def verify_citation(
    database: str | Path, *, source_url: str, expected_sha256: str, excerpt: str,
) -> dict[str, Any]:
    if not isinstance(source_url, str) or not source_url.strip() or len(source_url) > 4096:
        raise ValueError("source_url must be non-empty text up to 4096 characters")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("expected_sha256 must be a lowercase SHA-256 hex digest")
    if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 2000:
        raise ValueError("excerpt must be non-empty text up to 2000 characters")
    path = Path(database).expanduser()
    if not path.is_file():
        raise FileNotFoundError("research evidence database does not exist")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT url, content_type, text, sha256, fetched_at, truncated "
            "FROM research_evidence WHERE url=?", (source_url,)
        ).fetchone()
        if row is None:
            raise ValueError("source snapshot not found for exact URL")
        body = str(row["text"])
        actual_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        stored_hash = str(row["sha256"])
        if actual_hash != stored_hash:
            raise ValueError("stored source snapshot failed integrity verification")
        if stored_hash != expected_sha256:
            raise ValueError("source snapshot SHA-256 does not match expected value")
        if bool(row["truncated"]):
            raise ValueError("source snapshot is truncated; citation cannot establish full-source context")
        first = body.find(excerpt)
        if first < 0:
            raise ValueError("exact excerpt was not found in verified source snapshot")
        if body.find(excerpt, first + 1) >= 0:
            raise ValueError("excerpt occurs more than once; provide a more specific excerpt")
        return {
            "schema": "muse.research_wiki.citation_anchor.v1",
            "verified": True,
            "source_url": str(row["url"]),
            "content_type": str(row["content_type"]),
            "source_sha256": stored_hash,
            "fetched_at": str(row["fetched_at"]),
            "excerpt": excerpt,
            "start_char": first,
            "end_char": first + len(excerpt),
            "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "snapshot_truncated": False,
            "read_only": True,
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an exact excerpt against a stored source snapshot (read-only)")
    parser.add_argument("--db", default="data/research/evidence.sqlite3")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--excerpt", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = verify_citation(
        args.db, source_url=args.source_url,
        expected_sha256=args.source_sha256, excerpt=args.excerpt,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).expanduser().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
