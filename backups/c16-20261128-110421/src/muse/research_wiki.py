"""Research Wiki — seven first-class research objects over ResearchKnowledgeStore.

Objects: Claim · Source · Evidence · Experiment · Decision · Failure · Alternative.

The storage of record remains :class:`muse.research_knowledge.ResearchKnowledgeStore`
(nodes + typed edges + append-only events). This facade adds the epistemic rules:

* a Source registered from a URL is ``pending_fetch`` — a URL to read is **not** evidence;
* Evidence can only be attached from a Source marked ``read`` with a non-empty excerpt;
* supporting and refuting evidence are stored side by side, never merged;
* syndicated copies / same registrable domain collapse into one independent voice;
* with no independent evidence the verdict is honestly ``insufficient_evidence``;
* every object carries an epistemic stamp (see :mod:`muse.epistemics`).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from muse.epistemics import (
    INSUFFICIENT_EVIDENCE, EpistemicTagError, normalize_tag, research_verdict, stamp,
)
from muse.research_knowledge import ResearchKnowledgeStore

WIKI_OBJECTS = ("claim", "source", "evidence", "experiment", "decision", "failure", "alternative")
_NODE_TYPE = {"claim": "claim", "source": "source", "evidence": "idea", "experiment": "experiment",
              "decision": "decision", "failure": "failure", "alternative": "idea"}


def _node_id(node: dict[str, Any]) -> str:
    for key in ("id", "node_id"):
        if node.get(key):
            return str(node[key])
    raise KeyError("knowledge node has no id field")


def _domain(url: str) -> str:
    try:
        from muse.research_closed_loop import registrable_domain
        value = registrable_domain(url)
        if value:
            return str(value)
    except (ImportError, ValueError, TypeError) as exc:  # fall back to a conservative host split
        from muse.error_journal import record_suppressed_error
        record_suppressed_error("research_wiki._domain", exc, url=url[:200])
    host = (urlsplit(url).hostname or "").casefold()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class ResearchWiki:
    def __init__(self, database: str | Path) -> None:
        self.knowledge = ResearchKnowledgeStore(Path(database).resolve())

    def close(self) -> None:
        self.knowledge.close()

    def __enter__(self) -> "ResearchWiki":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _create(self, obj: str, title: str, *, summary: str = "", external_ref: str | None = None,
                tag: str, source: str, confidence: float, extra: dict[str, Any] | None = None,
                provenance: tuple[str, ...] = ()) -> dict[str, Any]:
        metadata = {"wiki_object": obj, **stamp(tag, source=source, confidence=confidence,
                                                  origin="web_research" if obj in ("source", "evidence") else "agent_inference").as_dict()}
        metadata.update(extra or {})
        node = self.knowledge.upsert_node(_NODE_TYPE[obj], title, summary=summary, external_ref=external_ref,
                                          metadata=metadata, provenance=provenance)
        return node

    # ---- objects ----------------------------------------------------------------------
    def add_claim(self, statement: str, *, tag: str = "hypothesis", source: str = "muse:research", confidence: float = 0.3) -> dict[str, Any]:
        digest = hashlib.sha256(statement.strip().encode("utf-8")).hexdigest()[:24]
        return self._create("claim", statement, external_ref=f"wiki-claim:{digest}", tag=tag, source=source, confidence=confidence)

    def add_source(self, url: str, *, title: str = "", syndicated_from: str = "") -> dict[str, Any]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https", "file") or not (parts.netloc or parts.scheme == "file"):
            raise ValueError("source must be an http(s) or file URL")
        return self._create("source", title or url, external_ref=url, tag="observation", source=url, confidence=0.5,
                            extra={"fetch_status": "pending_fetch", "registrable_domain": _domain(url),
                                   "syndicated_from": syndicated_from})

    def mark_source_read(self, source_node: dict[str, Any], *, excerpt: str, content_sha256: str) -> dict[str, Any]:
        if not excerpt.strip() or len(content_sha256) != 64:
            raise ValueError("a read source needs an excerpt and the sha256 of the fetched content")
        meta = dict(source_node.get("metadata") or {})
        meta.update({"fetch_status": "read", "excerpt": excerpt[:1500], "content_sha256": content_sha256})
        return self.knowledge.upsert_node("source", source_node["title"], summary=excerpt[:2000],
                                          external_ref=source_node.get("external_ref"), metadata=meta)

    def add_evidence(self, claim: dict[str, Any], source_node: dict[str, Any], *, stance: str, excerpt: str) -> dict[str, Any]:
        if stance not in ("supports", "refutes"):
            raise ValueError("stance must be supports or refutes")
        current = self.knowledge.get_node(_node_id(source_node)) or source_node
        meta = current.get("metadata") or {}
        if meta.get("fetch_status") != "read":
            raise EpistemicTagError("a pending URL is not evidence: read the source first")
        if not excerpt.strip():
            raise EpistemicTagError("evidence needs a verbatim excerpt")
        claim_id, source_id = _node_id(claim), _node_id(current)
        self.knowledge.record_claim_evidence(
            claim_id, str(current.get("external_ref") or source_id),
            direction="support_search_candidate" if stance == "supports" else "counter_search_candidate",
            evidence_type="excerpt", notes=excerpt[:1000])
        ev = self._create("evidence", f"[{stance}] {excerpt[:200]}", summary=excerpt[:2000],
                          external_ref=f"wiki-evidence:{claim_id}:{source_id}:{stance}", tag="observation",
                          source=str(current.get("external_ref") or source_id), confidence=0.6,
                          extra={"stance": stance, "claim_id": claim_id, "source_id": source_id,
                                 "registrable_domain": meta.get("registrable_domain", ""),
                                 "syndicated_from": meta.get("syndicated_from", "")})
        self.knowledge.add_edge(_node_id(ev), claim_id, stance, metadata={"source_id": source_id})
        return ev

    def add_experiment(self, claim: dict[str, Any], design: str, *, success_signal: str) -> dict[str, Any]:
        exp = self._create("experiment", design, summary=f"成功信号：{success_signal}", tag="hypothesis",
                           source="muse:experiment_design", confidence=0.3, extra={"success_signal": success_signal, "outcome": "not_run"})
        self.knowledge.add_edge(_node_id(claim), _node_id(exp), "tested_by")
        return exp

    def add_decision(self, claim: dict[str, Any], decision: str, *, rationale: str) -> dict[str, Any]:
        node = self._create("decision", decision, summary=rationale, tag="interpretation",
                            source="muse:decision", confidence=0.5)
        self.knowledge.add_edge(_node_id(node), _node_id(claim), "relates")
        return node

    def add_failure(self, claim: dict[str, Any], what_failed: str, *, lesson: str) -> dict[str, Any]:
        node = self._create("failure", what_failed, summary=lesson, tag="experience", source="muse:failure", confidence=0.7)
        self.knowledge.add_edge(_node_id(node), _node_id(claim), "refutes" if "falsif" in lesson.casefold() else "relates")
        return node

    def add_alternative(self, claim: dict[str, Any], explanation: str) -> dict[str, Any]:
        node = self._create("alternative", explanation, tag="hypothesis", source="muse:counter_research", confidence=0.3)
        self.knowledge.add_edge(_node_id(claim), _node_id(node), "alternative")
        return node

    # ---- verdict ----------------------------------------------------------------------
    def evidence_split(self, claim: dict[str, Any]) -> dict[str, Any]:
        claim_id = _node_id(claim)
        split: dict[str, set[str]] = {"supports": set(), "refutes": set()}
        raw: dict[str, int] = {"supports": 0, "refutes": 0}
        for stance in ("supports", "refutes"):
            for edge in self.knowledge.list_edges(node_id=claim_id, edge_type=stance, limit=500):
                target = str(edge.get("target_id") or edge.get("target") or "")
                if target != claim_id:
                    continue
                ev = self.knowledge.get_node(str(edge.get("source_id") or edge.get("source") or ""))
                if not ev:
                    continue
                meta = ev.get("metadata") or {}
                voice = meta.get("syndicated_from") or meta.get("registrable_domain") or meta.get("source_id")
                raw[stance] += 1
                split[stance].add(str(voice))
        verdict = research_verdict(len(split["supports"]), len(split["refutes"]))
        return {"claim_id": claim_id, "supporting_raw": raw["supports"], "refuting_raw": raw["refutes"],
                "supporting_independent": len(split["supports"]), "refuting_independent": len(split["refutes"]),
                "verdict": verdict, "honest": verdict != "supported" or len(split["supports"]) >= 2}

    def object_census(self) -> dict[str, int]:
        """Count wiki objects with SQL (list_nodes is paged to 500 by design)."""
        counts = {obj: 0 for obj in WIKI_OBJECTS}
        rows = self.knowledge.connection.execute(
            "SELECT json_extract(metadata_json, '$.wiki_object') AS obj, count(*) AS n FROM knowledge_nodes "
            "WHERE deleted_at IS NULL AND json_extract(metadata_json, '$.wiki_object') IS NOT NULL "
            "GROUP BY obj").fetchall()
        for row in rows:
            obj, n = row[0], row[1]
            if obj in counts:
                counts[obj] = int(n)
        return counts


__all__ = ["ResearchWiki", "WIKI_OBJECTS", "INSUFFICIENT_EVIDENCE", "normalize_tag"]
