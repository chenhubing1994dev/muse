"""Command-line interface for inspecting and managing Muse memories."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from muse.memory import MEMORY_KINDS, MEMORY_TRACKS, MemoryStore
from muse.obsidian_cli import ObsidianCLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse local memory store")
    parser.add_argument(
        "--db",
        default="data/memory/muse.sqlite3",
        help="SQLite database path (default: data/memory/muse.sqlite3)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="save an already-approved memory")
    add.add_argument("content")
    add.add_argument("--project", default="general")
    add.add_argument("--source", required=True)
    add.add_argument("--confidence", type=float, default=1.0)
    add.add_argument("--kind", choices=sorted(MEMORY_KINDS), default="fact")
    add.add_argument("--track", choices=sorted(MEMORY_TRACKS), default="user")

    search = commands.add_parser("search", help="search saved memories")
    search.add_argument("query")
    search.add_argument("--project")
    search.add_argument("--kind", choices=sorted(MEMORY_KINDS))
    search.add_argument("--track", choices=sorted(MEMORY_TRACKS))
    search.add_argument("--limit", type=int, default=20)

    list_command = commands.add_parser("list", help="list active memories")
    list_command.add_argument("--project")
    list_command.add_argument("--kind", choices=sorted(MEMORY_KINDS))
    list_command.add_argument("--track", choices=sorted(MEMORY_TRACKS))
    list_command.add_argument("--limit", type=int, default=50)

    history = commands.add_parser("history", help="show previous versions")
    history.add_argument("memory_id", type=int)

    revise = commands.add_parser("revise", help="revise an existing memory")
    revise.add_argument("memory_id", type=int)
    revise.add_argument("content")
    revise.add_argument("--source")
    revise.add_argument("--confidence", type=float)
    revise.add_argument("--kind", choices=sorted(MEMORY_KINDS))
    revise.add_argument("--track", choices=sorted(MEMORY_TRACKS))

    delete = commands.add_parser("delete", help="soft-delete a memory")
    delete.add_argument("memory_id", type=int)

    propose = commands.add_parser("propose", help="stage a memory for explicit approval")
    propose.add_argument("content")
    propose.add_argument("--project", default="general")
    propose.add_argument("--source", required=True)
    propose.add_argument("--confidence", type=float, default=1.0)
    propose.add_argument("--kind", choices=sorted(MEMORY_KINDS), default="fact")
    propose.add_argument("--track", choices=sorted(MEMORY_TRACKS), default="user")

    pending = commands.add_parser("list-pending", help="list memory candidates awaiting approval")
    pending.add_argument("--limit", type=int, default=100)

    approve = commands.add_parser("approve", help="approve a memory candidate")
    approve.add_argument("candidate_id", type=int)
    approve.add_argument("--by", required=True)

    reject = commands.add_parser("reject", help="reject a memory candidate")
    reject.add_argument("candidate_id", type=int)
    reject.add_argument("--by", required=True)
    reject.add_argument("--reason", default="")

    obsidian = commands.add_parser(
        "export-obsidian", help="publish approved memories as Markdown notes in an Obsidian Vault"
    )
    obsidian.add_argument("--vault", required=True, help="path to an existing or new Obsidian Vault directory")
    obsidian.add_argument("--limit", type=int, default=200)

    obsidian_search = commands.add_parser("obsidian-search", help="search notes in a named Obsidian Vault (read-only)")
    obsidian_search.add_argument("query")
    obsidian_search.add_argument("--vault-name", required=True)
    obsidian_search.add_argument("--cli", help="path to Obsidian.com/obsidian executable")
    obsidian_search.add_argument("--limit", type=int, default=50)

    obsidian_read = commands.add_parser("obsidian-read", help="read a note from a named Obsidian Vault (read-only)")
    obsidian_read.add_argument("note_path")
    obsidian_read.add_argument("--vault-name", required=True)
    obsidian_read.add_argument("--cli", help="path to Obsidian.com/obsidian executable")

    obsidian_audit = commands.add_parser("audit-obsidian", help="audit Muse-managed Vault notes without modifying them")
    obsidian_audit.add_argument("--vault", required=True, help="path to an existing Obsidian Vault directory")

    research_wiki = commands.add_parser(
        "export-research-wiki", help="export explicitly selected ResearchKnowledgeStore nodes as create-only Obsidian notes"
    )
    research_wiki.add_argument("--knowledge-db", default="data/research/knowledge.sqlite3")
    research_wiki.add_argument("--vault", required=True)
    research_wiki.add_argument("--node-id", action="append", required=True,
                               help="explicit research node ID to export; repeat for each selected node")

    research_wiki_audit = commands.add_parser(
        "audit-research-wiki", help="read-only audit of Research Wiki mirror hashes and source freshness"
    )
    research_wiki_audit.add_argument("--knowledge-db", default="data/research/knowledge.sqlite3")
    research_wiki_audit.add_argument("--vault", required=True, help="path to an existing Obsidian Vault directory")

    research_evidence_audit = commands.add_parser(
        "audit-research-evidence", help="read-only hash audit of stored research source snapshots"
    )
    research_evidence_audit.add_argument("--evidence-db", default="data/research/evidence.sqlite3")
    research_evidence_audit.add_argument("--limit", type=int, default=100)

    commands.add_parser("export", help="export active memories as JSON")
    args = parser.parse_args(argv)

    if args.command == "obsidian-search":
        client = ObsidianCLI(args.vault_name, executable=args.cli)
        print(json.dumps(client.search(args.query, limit=args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.command == "obsidian-read":
        client = ObsidianCLI(args.vault_name, executable=args.cli)
        print(client.read(args.note_path), end="")
        return 0

    if args.command == "audit-obsidian":
        with MemoryStore(Path(args.db)) as memory:
            from muse.obsidian_vault import audit_vault
            print(json.dumps(audit_vault(memory, args.vault), ensure_ascii=False, indent=2))
        return 0

    if args.command == "export-research-wiki":
        from muse.research_knowledge import ResearchKnowledgeStore
        from muse.obsidian_vault import export_research_wiki
        with ResearchKnowledgeStore(Path(args.knowledge_db)) as knowledge:
            result = export_research_wiki(knowledge, args.vault, node_ids=args.node_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit-research-wiki":
        from muse.research_knowledge import ResearchKnowledgeStore
        from muse.obsidian_vault import audit_research_wiki
        with ResearchKnowledgeStore(Path(args.knowledge_db)) as knowledge:
            result = audit_research_wiki(knowledge, args.vault)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "audit-research-evidence":
        from muse.research_fetch import ResearchEvidenceStore
        result = ResearchEvidenceStore.audit_database(args.evidence_db, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    with MemoryStore(Path(args.db)) as memory:
        if args.command == "add":
            result = memory.add(
                args.content, project=args.project, source=args.source, confidence=args.confidence,
                kind=args.kind, track=args.track,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "search":
            print(json.dumps(
                memory.search(args.query, project=args.project, kind=args.kind, track=args.track, limit=args.limit),
                ensure_ascii=False, indent=2,
            ))
        elif args.command == "list":
            print(json.dumps(
                memory.list_memories(project=args.project, kind=args.kind, track=args.track, limit=args.limit),
                ensure_ascii=False, indent=2,
            ))
        elif args.command == "history":
            print(json.dumps(memory.history(args.memory_id), ensure_ascii=False, indent=2))
        elif args.command == "revise":
            result = memory.revise(
                args.memory_id, args.content, source=args.source, confidence=args.confidence,
                kind=args.kind, track=args.track,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "delete":
            deleted = memory.delete(args.memory_id)
            print(json.dumps(
                {"memory_id": args.memory_id, "deleted": deleted},
                ensure_ascii=False, indent=2,
            ))
        elif args.command == "propose":
            result = memory.propose(
                args.content, project=args.project, source=args.source, confidence=args.confidence,
                kind=args.kind, track=args.track,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "list-pending":
            print(json.dumps(memory.list_pending(limit=args.limit), ensure_ascii=False, indent=2))
        elif args.command == "approve":
            # A refused approval is an expected outcome, not a crash: report it as JSON on stderr with a
            # distinct exit code (3 = identity refused, 2 = invalid candidate); nothing is written either way.
            try:
                result = memory.approve_candidate(args.candidate_id, approved_by=args.by)
            except (PermissionError, ValueError) as exc:
                code = 3 if isinstance(exc, PermissionError) else 2
                print(json.dumps({"approved": False, "candidate_id": args.candidate_id, "error": str(exc),
                                  "error_type": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
                return code
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "reject":
            result = memory.reject_candidate(
                args.candidate_id, rejected_by=args.by, reason=args.reason
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "export":
            print(memory.export_json())
        elif args.command == "export-obsidian":
            from muse.obsidian_vault import export_approved_memories
            result = export_approved_memories(memory, args.vault, limit=args.limit)
            print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0

if __name__ == "__main__":
    raise SystemExit(main())