import hashlib
import json
import os
from pathlib import Path

from muse.research_fetch import PublicSourceReader, ResearchEvidenceStore
from muse.research_knowledge import ResearchKnowledgeStore
from muse.research_pipeline import ResearchPipeline

CLAIM_ID = "09e820f1bbe25dfb361fcbcf76363b48"
REVIEWER_ID = "chatgpt_web_audit_followup_2026-11-20"
QUERIES = (
    "YouTube official title A/B testing watch time share documentation",
    "False discovery in A/B testing 2766 experiments FDR",
    "Online controlled experiments A/B testing review randomization SUTVA",
)

ITEMS = {
    QUERIES[0]: [{
        "title": "A/B test titles & thumbnails",
        "url": "https://support.google.com/youtube/answer/16391400?hl=en",
        "snippet": "Creators can test and compare up to 3 different titles and thumbnails. Results use watch time share; native YouTube tests are concurrent and may be inconclusive.",
        "engine": "curated_external_web",
        "published_at": "",
        "source_kind": "official_platform_documentation",
    }],
    QUERIES[1]: [{
        "title": "False Discovery in A/B Testing",
        "url": "https://ideas.repec.org/a/inm/ormnsc/v68y2022i9p6762-6782.html",
        "snippet": "Berman and Van den Bulte analyze 4,964 effects from 2,766 experiments and estimate false discovery rates of 18% to 25% at 5% significance.",
        "engine": "curated_external_web",
        "published_at": "2022-09-01",
        "source_kind": "journal_article",
    }],
    QUERIES[2]: [{
        "title": "Statistical Challenges in Online Controlled Experiments: A Review of A/B Testing Methodology",
        "url": "https://arxiv.org/html/2212.11366",
        "snippet": "Review of online controlled experiments covering randomization, power, heterogeneous treatment effects, optional stopping, and interference.",
        "engine": "curated_external_web",
        "published_at": "2023-10-18",
        "source_kind": "author_preprint_fulltext_of_published_methodology_review",
    }],
}

class CuratedSearch:
    def search(self, query, *, limit=8):
        return ITEMS.get(query, [])[:limit]

class BoundedReader(PublicSourceReader):
    def read(self, url, *, max_bytes=2_000_000, max_chars=50_000):
        return super().read(url, max_bytes=max_bytes, max_chars=max_chars)

def citation_anchor(source, needles):
    text = source["text"]
    for needle in needles:
        start = text.find(needle)
        if start >= 0:
            excerpt = needle
            break
    else:
        lowered = text.casefold()
        found = next((lowered.find(needle.casefold()) for needle in needles
                      if lowered.find(needle.casefold()) >= 0), -1)
        if found < 0:
            return None
        start = max(0, found - 100)
        end = min(len(text), found + 180)
        excerpt = text[start:end]
    end = start + len(excerpt)
    if text[start:end] != excerpt:
        raise AssertionError("citation excerpt offsets do not match fetched source text")
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if source_hash != source["sha256"]:
        raise AssertionError("fetched source hash does not match ResearchPipeline result")
    return {
        "verified": True,
        "source_url": source["url"],
        "source_sha256": source_hash,
        "excerpt": excerpt,
        "start_char": start,
        "end_char": end,
        "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "fetched_at": source["fetched_at"],
        "content_type": source["content_type"],
    }

for proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[proxy_key] = "http://127.0.0.1:10808"
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)

root = Path.cwd()
with ResearchKnowledgeStore(root / "data/research/knowledge.sqlite3") as knowledge, \
     ResearchEvidenceStore(root / "data/research/evidence.sqlite3") as evidence:
    claim = knowledge.get_node(CLAIM_ID)
    if claim is None or claim["status"] != "proposed":
        raise RuntimeError("claim is missing or its lifecycle status changed")
    result = ResearchPipeline(
        CuratedSearch(), BoundedReader(timeout=25),
        evidence, max_workers=2, resource_aware=False,
        knowledge_store=knowledge,
    ).run(QUERIES, max_sources=3, task_id="creator-claim-context-research-2026-11-20")
    summaries = []
    evidence_refs = []
    source_nodes = {
        node.get("external_ref"): node
        for node in knowledge.list_nodes(node_type="source", limit=500)
        if node.get("external_ref")
    }
    for source in result["sources"]:
        node = source_nodes.get(source["url"])
        if node is None:
            continue
        evidence_item = knowledge.record_claim_evidence(
            CLAIM_ID, node["id"], direction="context_candidate",
            evidence_type=str(source.get("source_kind", "web_source"))[:80],
            quality="unassessed", independence="unassessed",
            search_query=str(source.get("query", ""))[:500],
            notes="Methodological/platform context only; this source does not establish that a specific title framework wins.",
        )
        evidence_refs.append(node["id"])
        if source["url"].startswith("https://support.google.com/youtube/"):
            needles = [
                "Creators can test and compare up to 3 different titles and thumbnails.",
                "At the end of the experiment, you will see one of following results based on watch time share:",
            ]
            rationale = (
                "Official YouTube documentation confirms the product supports testing up to three title/thumbnail variants, "
                "uses watch-time share, and can return Winner, Performed Same, or Inconclusive. This establishes platform "
                "capability and outcome semantics only; it does not show that either proposed title framework is superior."
            )
            quality = "high"
        elif "ideas.repec.org" in source["url"]:
            needles = [
                "Our data consist of 4,964 effects from 2,766 experiments conducted on a commercial A/B testing platform.",
                "between 18% and 25% for tests at 5% significance",
            ]
            rationale = (
                "This Management Science study analyzes 4,964 effects from 2,766 website experiments and estimates a "
                "non-trivial false-discovery rate. It is relevant as a caution against treating isolated A/B winners as "
                "portable creator rules, but it is not a YouTube title-framework experiment."
            )
            quality = "moderate"
        elif "tandfonline.com" in source["url"] or "arxiv.org" in source["url"]:
            needles = [
                "randomized in real time to one of the variants",
                "p-value misinterpretation and problematic practices regularly lead to increased false-positive rates",
                "we review challenges that require new statistical methodologies",
            ]
            rationale = (
                "This methodological review concerns online controlled experiments and discusses randomization and "
                "statistical challenges. It provides general design context, not direct evidence that a particular "
                "YouTube title framework changes watch-time share."
            )
            quality = "moderate"
        else:
            continue
        anchor = citation_anchor(source, needles)
        existing = next(
            row for row in knowledge.list_claim_evidence(CLAIM_ID, limit=100)
            if row["id"] == evidence_item["id"]
        )
        if not any(row.get("reviewer_id") == REVIEWER_ID for row in existing.get("reviews", [])):
            review = knowledge.record_claim_evidence_review(
                evidence_item["id"], reviewer_id=REVIEWER_ID, verdict="mentions",
                rationale=rationale, quality=quality, independence="unassessed",
                citation_anchor=anchor,
            )
        else:
            review = {"verdict": "mentions", "id": "already-reviewed"}
        summaries.append({
            "source_id": node["id"], "title": source["title"], "url": source["url"],
            "sha256": source["sha256"], "chars": source["chars"],
            "citation_anchor_attached": bool(anchor), "review": review,
        })
    if len(summaries) >= 2:
        latest = knowledge.get_node(CLAIM_ID)["metadata"].get("claim_review", {})
        if latest.get("reviewer_id") != REVIEWER_ID:
            knowledge.record_claim_review(
                CLAIM_ID, reviewer_id=REVIEWER_ID,
                assessment={
                    "verdict": "insufficient",
                    "rationale": (
                        "The new official platform documentation and A/B-testing methodology sources clarify how to run "
                        "concurrent title tests and why false discoveries/generalization are risks. They do not provide "
                        "direct repeated experimental results showing that either 'specific promise' or 'curiosity gap' "
                        "wins across creators or videos. The claim therefore remains insufficiently evidenced and proposed."
                    ),
                },
                evidence_refs=evidence_refs, confidence_level="unassessed",
            )
    dossier = knowledge.claim_dossier(CLAIM_ID)
    print("PIPELINE", json.dumps({
        "query_count": len(result["queries"]),
        "search_result_count": result["search_result_count"],
        "read_source_count": result["read_source_count"],
        "failures": result["failures"],
        "knowledge_failures": result["knowledge_failures"],
        "knowledge_run_status": (result.get("knowledge_run") or {}).get("status"),
    }, ensure_ascii=False))
    print("SOURCE_REVIEWS", json.dumps(summaries, ensure_ascii=False))
    print("CLAIM_DOSSIER", json.dumps({
        "claim_status": dossier["claim_status"],
        "review_state": knowledge.get_node(CLAIM_ID)["metadata"].get("review_state"),
        "evidence_counts": dossier["evidence_counts"],
        "review_verdict_counts": dossier["evidence_coverage"]["review_verdict_counts"],
        "reviewed_evidence_count": dossier["evidence_coverage"]["reviewed_evidence_count"],
        "confidence": dossier["confidence"],
    }, ensure_ascii=False))
