"""Phase 5 Muse consciousness / philosophy evolution engine.

A thin, read-mostly adapter over ``PhilosophyStore``. It turns finished video
creations into durable *experiences* turns active principles into inspiration
briefs and neon quotes, and renders the whole philosophy as a graph snapshot
for the Svelte 3D force view.

Compliance (docs/VISION.md pillar 1 and 5, AGENTS.md):
- Symbolic/metaphysical lenses are hypothesis/symbolic_only seeds. They are
  never facts, evidence, predictions, approvals, or personal truths.
- Agent Track is never read as the creator's personal fact, and nothing is
  auto-written back to User Track.
- New principle candidates always stay ``pending_review``; only a human
  reviewer may promote them to active principles.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping

from muse.philosophy import CORE_PRINCIPLES, PhilosophyStore

#: Only these fields of video metadata are durable; everything else
#: (author email, local paths, viewer ids, ...) is dropped on purpose.
_SAFE_METADATA_FIELDS = ("style", "narration", "asset_count", "duration")

#: Built-in symbolic lenses are explicitly non-factual. IDs match the
#: inspiration module's tradition keys where possible.
_BUILTIN_LENSES: dict[str, dict[str, Any]] = {
    "iching": {"name": "易经", "lens": "变化、时位与关系", "tradition": "中国经典象数传统"},
    "liuyao": {"name": "六爻", "lens": "主客关系与条件变化", "tradition": "中国易学占筮传统"},
    "qimen": {"name": "奇门遁甲", "lens": "时空条件与行动窗口", "tradition": "中国传统术数体系"},
    "ziwei": {"name": "紫微斗数", "lens": "领域结构与角色配置", "tradition": "中国传统命理象征体系"},
    "liu_ren": {"name": "大六壬", "lens": "因果链与先后次序", "tradition": "中国传统三式术数"},
    "astrology": {"name": "西方星相学", "lens": "多因素配置与张力", "tradition": "西方占星象征传统"},
    "tarot": {"name": "塔罗", "lens": "角色视角与情境分支", "tradition": "欧洲纸牌象征传统"},
}


class ConsciousnessEngine:
    """Wrap :class:`PhilosophyStore` with the Phase 5 consciousness surface."""

    def __init__(self, philosophy_store: PhilosophyStore, *, auto_propose_threshold: int = 5) -> None:
        if not isinstance(philosophy_store, PhilosophyStore):
            raise TypeError("philosophy_store must be a PhilosophyStore")
        if isinstance(auto_propose_threshold, bool) or not isinstance(auto_propose_threshold, int) \
                or not 1 <= auto_propose_threshold <= 100:
            raise ValueError("auto_propose_threshold must be an integer in 1..100")
        self.store = philosophy_store
        self.auto_propose_threshold = auto_propose_threshold

    # ------------------------------------------------------------------
    # 1. Creation experience capture
    # ------------------------------------------------------------------
    def record_creation_experience(
        self,
        video_metadata: Mapping[str, Any],
        outcome: str,
        reflection: str,
    ) -> dict[str, Any]:
        """Persist one privacy-minimal creation experience after a video completes.

        ``outcome`` must be success/failure/reflection. The stored experience is
        always ``epistemic_status='experience'``; only whitelisted metadata is
        kept. Once ``auto_propose_threshold`` experiences exist, a *pending*
        principle candidate is proposed from the newest experience.
        """
        if not isinstance(video_metadata, Mapping):
            raise ValueError("video_metadata must be a mapping")
        if outcome not in {"success", "failure", "reflection"}:
            raise ValueError("outcome must be success/failure/reflection")
        metadata = {
            key: video_metadata[key]
            for key in _SAFE_METADATA_FIELDS
            if key in video_metadata
        }
        if not isinstance(metadata.get("style", ""), str) or len(str(metadata.get("style", ""))) > 120:
            raise ValueError("style must be bounded text")
        if not isinstance(metadata.get("narration", ""), str) or len(str(metadata.get("narration", ""))) > 400:
            raise ValueError("narration must be bounded text")
        for key in ("asset_count", "duration"):
            if key in metadata and (
                isinstance(metadata[key], bool)
                or not isinstance(metadata[key], (int, float))
                or metadata[key] < 0
            ):
                raise ValueError(f"{key} must be a non-negative number")

        # Distinct source_ref per completion: PhilosophyStore dedupes on
        # (kind, source_ref), while every finished video is its own experience.
        source_ref = f"muse:creation-experience:{uuid.uuid4().hex}"
        experience = self.store.capture_experience(
            outcome,
            str(reflection),
            source_ref=source_ref,
            epistemic_status="experience",
            metadata=metadata,
        )
        result = dict(experience)
        result["metadata"] = metadata
        proposal = self._maybe_auto_propose(experience)
        if proposal is not None:
            result["auto_proposal"] = proposal
        return result

    def _maybe_auto_propose(self, experience: dict[str, Any]) -> dict[str, Any] | None:
        total = self.store.connection.execute(
            "SELECT COUNT(*) FROM philosophy_experiences"
        ).fetchone()[0]
        if total < self.auto_propose_threshold:
            return None
        # One candidate per crossing of the threshold (and every experience
        # after it that does not yet have a proposal) - always pending_review.
        existing = self.store.connection.execute(
            "SELECT 1 FROM philosophy_principle_proposals WHERE experience_id=? LIMIT 1",
            (experience["id"],),
        ).fetchone()
        if existing is not None:
            return None
        metadata = json.loads(experience.get("metadata_json") or "{}")
        style = metadata.get("style") or "跨题材"
        title = f"创作经验候选：{style}"
        rationale = (
            f"截至第 {total} 条创作经验，观察到与「{style}」相关的可复用做法；"
            "这是从经验归纳的待验证假设，不是已确认事实。"
        )
        practice = "在下一次创作前登记基线，使用小步对比验证该做法是否可复用。"
        return self.store.propose_principle_candidate(
            experience["id"],
            title,
            rationale,
            suggested_practice=practice,
            source_refs=[f"experience:{experience['id']}"],
        )

    # ------------------------------------------------------------------
    # 2. Inspiration brief for the narration script generator
    # ------------------------------------------------------------------
    def inject_inspiration(
        self,
        topic: str,
        *,
        symbolic_lenses: Iterable[Any] = (),
    ) -> dict[str, Any]:
        """Return principles + explicitly labelled symbolic lenses as a brief."""
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 2000:
            raise ValueError("topic must contain 1-2000 characters")

        principles = self._relevant_principles(topic)
        lenses = self._normalize_lenses(symbolic_lenses)
        note = (
            "本 brief 中玄学/象征内容仅为 hypothesis/symbolic_only 灵感假设，非事实、证据或预测；"
            "Agent Track 不被当作用户个人事实读取，也不会自动写回 User Track。"
            "新原则候选需人工审核后才可成为 active 原则。"
        )
        return {
            "topic": topic.strip(),
            "principles": principles,
            "symbolic_lenses": lenses,
            "muse_note": note,
            "agent_track_read": False,
            "agent_track_as_personal_fact": False,
            "automatic_user_track_write": False,
            "is_evidence": False,
            "is_prediction": False,
            "epistemic_status": "symbolic_hypothesis_and_experience_only",
        }

    def _relevant_principles(self, topic: str) -> list[dict[str, Any]]:
        content = topic.casefold()
        ranked: list[tuple[int, str, dict[str, Any]]] = []
        for principle in self.store.list_principles(limit=200):
            if principle.get("status") != "active":
                continue
            signals = CORE_PRINCIPLES.get(principle["principle_key"], ("", "", "", ()))[3]
            score = sum(1 for signal in signals if str(signal).casefold() in content)
            ranked.append((score, principle["title"], principle))
        ranked.sort(key=lambda item: (-item[0], -float(item[2].get("confidence") or 0), item[1]))
        selected = ranked[:5] if ranked else []
        return [
            {
                "id": item["id"],
                "principle_key": item["principle_key"],
                "title": item["title"],
                "practice": item["practice"],
                "description": item["description"],
                "status": item["status"],
                "confidence": item["confidence"],
                "source": f"philosophy_principles:{item['principle_key']}",
                "epistemic_status": "active_principle_experience_grounded",
                "is_evidence": False,
            }
            for _, _, item in selected
        ]

    @staticmethod
    def _normalize_lenses(lenses: Iterable[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, lens in enumerate(lenses):
            if isinstance(lens, str):
                base = dict(_BUILTIN_LENSES.get(lens, {"name": lens, "lens": "象征提示"}))
                lens_id = lens
            elif isinstance(lens, Mapping):
                base = dict(lens)
                lens_id = str(base.get("id") or f"lens-{index + 1}")
            else:
                raise ValueError("symbolic lenses must be tradition ids or mappings")
            name = str(base.get("name") or lens_id)[:120]
            # Whatever the caller claims, the engine downgrades to a hypothesis.
            normalized.append({
                "id": lens_id,
                "name": name,
                "tradition": str(base.get("tradition") or "symbolic tradition"),
                "lens": str(base.get("lens") or "象征视角"),
                "epistemic_status": "symbolic_only",
                "is_evidence": False,
                "is_prediction": False,
                "source": f"symbolic_lens:{lens_id}",
                "question": (
                    f"把「{name}」当作提问框架：哪些可观察变量、替代解释与反证条件"
                    "能检验这个直觉？"
                ),
            })
        return normalized

    # ------------------------------------------------------------------
    # 3. Hong-Kong-neon philosophy quotes
    # ------------------------------------------------------------------
    def philosophy_quotes(self, topic: str, limit: int = 3) -> list[dict[str, Any]]:
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 2000:
            raise ValueError("topic must contain 1-2000 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ValueError("limit must be 1..10")
        principles = self._relevant_principles(topic)
        selected = (principles * ((limit // len(principles)) + 1))[:limit] if principles else []
        quotes: list[dict[str, Any]] = []
        for index, principle in enumerate(selected, start=1):
            text = (
                f"霓虹不保证方向，实践才点亮路——{principle['title']}。"
                f"{principle['practice']}｜第 {index} 条"
            )
            quotes.append({
                "id": f"quote-{principle['principle_key']}-{index}",
                "principle_key": principle["principle_key"],
                "title": principle["title"],
                "text": text,
                "practice": principle["practice"],
                "source": "Muse 哲学 · 假设/经验（active principles，非事实证据）",
                "epistemic_status": "hypothesis_and_experience",
                "status": principle["status"],
                "is_evidence": False,
            })
        return quotes

    # ------------------------------------------------------------------
    # 4. Consciousness star-map snapshot for Svelte 3D force graph
    # ------------------------------------------------------------------
    def evolution_snapshot(self) -> dict[str, Any]:
        connection = self.store.connection
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        principle_rows = connection.execute(
            "SELECT * FROM philosophy_principles ORDER BY created_at,id"
        ).fetchall()
        for row in principle_rows:
            item = dict(row)
            nodes.append({
                "id": f"principle:{item['id']}",
                "type": "principle",
                "label": item["title"],
                "title": item["title"],
                "status": item["status"],
                "confidence": item["confidence"],
                "principle_key": item["principle_key"],
                "created_at": item["created_at"],
            })

        experience_rows = connection.execute(
            "SELECT * FROM philosophy_experiences ORDER BY created_at,id"
        ).fetchall()
        for row in experience_rows:
            item = dict(row)
            nodes.append({
                "id": f"experience:{item['id']}",
                "type": "experience",
                "label": item["summary"][:40],
                "title": item["summary"][:80],
                "status": item["epistemic_status"],
                "confidence": 0.6,
                "kind": item["kind"],
                "created_at": item["created_at"],
            })

        proposal_rows = connection.execute(
            "SELECT * FROM philosophy_principle_proposals ORDER BY created_at,id"
        ).fetchall()
        for row in proposal_rows:
            item = dict(row)
            nodes.append({
                "id": f"proposal:{item['id']}",
                "type": "proposal",
                "label": item["title"][:40],
                "title": item["title"],
                "status": item["status"],
                "confidence": 0.3,
                "epistemic_status": item["epistemic_status"],
                "created_at": item["created_at"],
            })

        for row in connection.execute(
            "SELECT * FROM philosophy_links ORDER BY created_at,experience_id,principle_id"
        ).fetchall():
            item = dict(row)
            edges.append({
                "id": f"link:{item['experience_id']}:{item['principle_id']}:{item['relation']}",
                "source": f"experience:{item['experience_id']}",
                "target": f"principle:{item['principle_id']}",
                "relation": item["relation"],
                "created_at": item["created_at"],
            })
        for row in proposal_rows:
            item = dict(row)
            edges.append({
                "id": f"proposed_from:{item['id']}",
                "source": f"proposal:{item['id']}",
                "target": f"experience:{item['experience_id']}",
                "relation": "proposed_from",
                "created_at": item["created_at"],
            })

        timeline = [
            {
                "id": node["id"],
                "type": node["type"],
                "status": node["status"],
                "created_at": node["created_at"],
            }
            for node in nodes
        ]
        timeline.sort(key=lambda item: (item["created_at"], item["id"]))
        return {
            "nodes": nodes,
            "edges": edges,
            "timeline": timeline,
            "epistemic_status": "experience_and_hypothesis_only",
            "is_evidence": False,
            "auto_propose_threshold": self.auto_propose_threshold,
        }


# ----------------------------------------------------------------------
# Optional CLI (uses a temporary DB by default so real data is untouched)
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Muse's consciousness evolution engine")
    parser.add_argument(
        "--db", default=None,
        help="SQLite database path; omit to use a temporary throwaway database",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("summary")
    quotes = sub.add_parser("quotes")
    quotes.add_argument("--topic", default="创作与复盘")
    quotes.add_argument("--limit", type=int, default=3)
    sub.add_parser("snapshot")
    record = sub.add_parser("record")
    record.add_argument("--style", default="港风霓虹")
    record.add_argument("--narration", default="测试解说")
    record.add_argument("--asset-count", type=int, default=1)
    record.add_argument("--duration", type=float, default=10.0)
    record.add_argument("--outcome", default="success", choices=("success", "failure", "reflection"))
    record.add_argument("--reflection", default="CLI 测试经验")
    args = parser.parse_args(argv)

    temp_dir = None
    db = args.db
    if db is None:
        temp_dir = tempfile.TemporaryDirectory()
        db = str(Path(temp_dir.name) / "consciousness.sqlite3")
    try:
        with PhilosophyStore(db) as store:
            engine = ConsciousnessEngine(store)
            if args.command == "summary":
                result = store.summary()
            elif args.command == "quotes":
                result = engine.philosophy_quotes(args.topic, limit=args.limit)
            elif args.command == "snapshot":
                result = engine.evolution_snapshot()
            else:
                result = engine.record_creation_experience(
                    {
                        "style": args.style,
                        "narration": args.narration,
                        "asset_count": args.asset_count,
                        "duration": args.duration,
                    },
                    args.outcome,
                    args.reflection,
                )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
