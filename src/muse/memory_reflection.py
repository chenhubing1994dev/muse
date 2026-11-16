"""Conservative cross-memory reflection; all generated insights remain pending."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import re
from itertools import combinations
from typing import Any

from muse.memory import MemoryStore


class MemoryReflection:
    """Find repeated concepts across memories and stage auditable candidate insights."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    @staticmethod
    def _terms(value: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]{2,}", value.casefold())
        terms: set[str] = set()
        for token in tokens:
            if any("\u3400" <= char <= "\u9fff" for char in token):
                terms.update(token[i:i + 2] for i in range(len(token) - 1))
            elif len(token) >= 3:
                terms.add(token)
        return terms

    def scan(self, *, project: str | None = None, limit: int = 100,
             min_shared_terms: int = 2, max_candidates: int = 10) -> dict[str, Any]:
        if not 2 <= min_shared_terms <= 20:
            raise ValueError("min_shared_terms must be between 2 and 20")
        if not 1 <= max_candidates <= 50:
            raise ValueError("max_candidates must be between 1 and 50")
        memories = self.store.list_memories(project=project, limit=min(limit, 200))
        pairs = []
        for left, right in combinations(memories, 2):
            if left["id"] == right["id"]:
                continue
            shared = self._terms(left["content"]) & self._terms(right["content"])
            if len(shared) >= min_shared_terms:
                pairs.append((len(shared), left, right, sorted(shared)[:12]))
        pairs.sort(key=lambda item: (-item[0], item[1]["id"], item[2]["id"]))
        candidates = []
        seen = set()
        for _, left, right, shared in pairs:
            key = tuple(sorted((left["id"], right["id"])))
            if key in seen:
                continue
            seen.add(key)
            content = (
                "待人工核实的跨记忆关联：两条记忆共享概念 "
                + "、".join(shared)
                + f"。来源记忆 ID：{left['id']}、{right['id']}。此关联不是事实结论。"
            )
            candidate = self.store.propose(
                content, project=project or left["project"],
                source=f"memory-reflection:{left['id']}:{right['id']}",
                confidence=0.45, kind="foresight", track="agent",
            )
            candidates.append({"candidate_id": candidate["id"], "source_memory_ids": list(key), "shared_terms": shared})
            if len(candidates) >= max_candidates:
                break
        return {"memories_scanned": len(memories), "related_pairs": len(pairs),
                "candidates_staged": len(candidates), "candidates": candidates,
                "approval_required": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Find cross-memory associations and stage review candidates")
    parser.add_argument("--db", default="data/memory/muse.sqlite3")
    parser.add_argument("--project")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-shared-terms", type=int, default=2)
    parser.add_argument("--max-candidates", type=int, default=10)
    args = parser.parse_args()
    with MemoryStore(Path(args.db)) as store:
        result = MemoryReflection(store).scan(
            project=args.project, limit=args.limit, min_shared_terms=args.min_shared_terms,
            max_candidates=args.max_candidates,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
