import json
from pathlib import Path
from muse.research_knowledge import ResearchKnowledgeStore
claim_id = "09e820f1bbe25dfb361fcbcf76363b48"
with ResearchKnowledgeStore(Path("data/research/knowledge.sqlite3")) as store:
    print("DOSSIER", json.dumps(store.claim_dossier(claim_id), ensure_ascii=False, default=str))
    print("EDGES", json.dumps(store.list_edges(node_id=claim_id), ensure_ascii=False, default=str))
    nodes = store.list_nodes(limit=500)
    for node in nodes:
        if node.get("node_type") == "source" and node.get("id") in {
            edge.get("source_id") for edge in store.list_edges(node_id=claim_id)
        }:
            print("SOURCE", json.dumps(node, ensure_ascii=False, default=str))
