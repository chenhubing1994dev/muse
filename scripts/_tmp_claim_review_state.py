import json, sqlite3
from pathlib import Path
from muse.research_knowledge import ResearchKnowledgeStore
claim_id = "09e820f1bbe25dfb361fcbcf76363b48"
with ResearchKnowledgeStore(Path("data/research/knowledge.sqlite3")) as store:
    claim = store.get_node(claim_id)
    dossier = store.claim_dossier(claim_id)
    print("CLAIM", json.dumps({
        "id": claim["id"], "status": claim["status"],
        "review_state": claim["metadata"].get("review_state"),
        "research_claim_review_status": claim["metadata"].get("research_claim_review_status"),
        "research_claim_review_verdict": claim["metadata"].get("research_claim_review_verdict"),
        "claim_review": claim["metadata"].get("claim_review"),
    }, ensure_ascii=False, default=str))
    print("EVIDENCE", json.dumps([{
        "id": item["id"], "direction": item["direction"], "quality": item["quality"],
        "independence": item["independence"], "source_node_id": item["source_node_id"],
        "source_title": item["source_title"], "source_ref": item["source_ref"],
        "reviews": item.get("reviews", []), "latest_verdict": item.get("latest_verdict"),
        "latest_review": item.get("latest_review"),
    } for item in dossier["evidence"]], ensure_ascii=False, default=str))
    print("COVERAGE", json.dumps(dossier["evidence_coverage"], ensure_ascii=False))
evidence_db = sqlite3.connect(Path("data/research/evidence.sqlite3").as_uri() + "?mode=ro", uri=True)
evidence_db.row_factory = sqlite3.Row
for url in ("https://journalqd.org/article/view/4114", "https://knowledge.bsigroup.com:443/products/non-destructive-testing-aids-to-visual-inspection-selection-of-low-power-magnifiers"):
    row = evidence_db.execute("SELECT url,content_type,sha256,fetched_at,truncated,length(text) chars FROM research_evidence WHERE url=?", (url,)).fetchone()
    print("SNAPSHOT", json.dumps(dict(row) if row else None, ensure_ascii=False))
evidence_db.close()
