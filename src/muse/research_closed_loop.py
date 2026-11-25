"""B396 WS3 deep-research closed loop.

One call orchestrates, for a single claim:

1. **support retrieval** and **counter-evidence retrieval** (separate query families, both mandatory),
2. **source-independence audit** — sources are clustered when they share a registrable domain, a
   canonical URL, an explicit syndication/attribution line ("via", "来源：", "originally published"),
   or near-duplicate body text (5-word shingle Jaccard). Republished copies dressed up as many
   independent outlets collapse into one cluster and are flagged ``disguised_syndication``,
3. **citation-anchor verification** — every quoted passage must literally occur in the cited source
   text (whitespace/case-normalised); the anchor records offsets and a SHA-256 of the passage,
4. a versioned, append-only **Claim dossier** with a conservative verdict
   (``supported`` / ``contested`` / ``refuted`` / ``insufficient`` — never "true").

Retrieval functions are injected (local search, Scrapling, browser, fixtures). Source text is
treated as untrusted data and is never executed or followed as instructions.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

COUNTER_TEMPLATES = ("{claim} 反驳", "{claim} evidence against", "{claim} debunked OR false OR misleading",
                     "{claim} criticism limitations")
SUPPORT_TEMPLATES = ("{claim}", "{claim} study data source", "{claim} official statistics")
SYNDICATION = re.compile(r"(?i)(originally published (?:on|by|at)\s+(?P<a>[\w.-]+)|via\s+(?P<b>[\w.-]+\.[a-z]{2,})"
                         r"|(?:来源|转载自|稿源)[:：]\s*(?P<c>[^\s，。,]{2,40})|source:\s*(?P<d>[\w.-]+\.[a-z]{2,}))")
_TWO_LEVEL_SUFFIXES = {"co.uk", "com.cn", "com.hk", "org.uk", "gov.cn", "edu.cn", "com.au", "co.jp", "com.tw", "net.cn"}
VERDICTS = ("supported", "contested", "refuted", "insufficient")


@dataclass
class Source:
    url: str
    title: str
    text: str
    publisher: str = ""
    published_at: str = ""
    canonical_url: str = ""
    retrieved_for: str = "support"   # or "counter"
    stance: str = "unknown"          # supports / refutes / unknown (as labelled by the retriever or reviewer)
    meta: dict[str, Any] = field(default_factory=dict)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"\s+", " ", text).strip()


def registrable_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def canonical(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    host = host[4:] if host.startswith("www.") else host
    query = "&".join(sorted(q for q in parts.query.split("&") if q and not q.startswith(("utm_", "fbclid", "gclid", "spm"))))
    return urlunsplit(("https", host, parts.path.rstrip("/") or "/", query, ""))


def _shingles(text: str, size: int = 5) -> set[str]:
    words = re.findall(r"[\w\u4e00-\u9fff]", _norm(text)) if re.search(r"[\u4e00-\u9fff]", text or "") \
        else re.findall(r"\w+", _norm(text))
    return {" ".join(words[i:i + size]) for i in range(max(0, len(words) - size + 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def audit_independence(sources: list[Source], *, duplicate_threshold: float = 0.6) -> dict[str, Any]:
    """Union-find clustering of sources that are not independent of each other."""
    parent = list(range(len(sources)))
    reasons: dict[tuple[int, int], list[str]] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int, reason: str) -> None:
        reasons.setdefault((min(i, j), max(i, j)), []).append(reason)
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    domains = [registrable_domain(s.url) for s in sources]
    canon = [canonical(s.canonical_url or s.url) for s in sources]
    shingles = [_shingles(s.text) for s in sources]
    attributions = []
    for s in sources:
        found = set()
        for m in SYNDICATION.finditer(s.text or ""):
            value = next(v for v in m.groupdict().values() if v)
            found.add(registrable_domain("https://" + value) if "." in value else _norm(value))
        attributions.append(found)
    publishers = [_norm(s.publisher) for s in sources]
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            if domains[i] and domains[i] == domains[j]:
                union(i, j, "same_registrable_domain")
            if canon[i] == canon[j]:
                union(i, j, "same_canonical_url")
            if publishers[i] and publishers[i] == publishers[j]:
                union(i, j, "same_publisher")
            if domains[j] in attributions[i] or domains[i] in attributions[j] or (attributions[i] & attributions[j]) \
                    or (publishers[j] and publishers[j] in attributions[i]) or (publishers[i] and publishers[i] in attributions[j]):
                union(i, j, "syndication_attribution")
            similarity = _jaccard(shingles[i], shingles[j])
            if similarity >= duplicate_threshold:
                union(i, j, f"near_duplicate_text:{similarity:.2f}")
    clusters: dict[int, list[int]] = {}
    for i in range(len(sources)):
        clusters.setdefault(find(i), []).append(i)
    groups = []
    for members in clusters.values():
        member_domains = sorted({domains[i] for i in members})
        why = sorted({r for (a, b), rs in reasons.items() if a in members and b in members for r in rs})
        groups.append({"members": [sources[i].url for i in members], "domains": member_domains,
                       "representative": sources[min(members)].url, "reasons": why,
                       "disguised_syndication": len(member_domains) > 1})
    return {"raw_source_count": len(sources), "independent_source_count": len(groups), "groups": groups,
            "disguised_syndication_groups": sum(1 for g in groups if g["disguised_syndication"])}


def verify_citation(source: Source, quote: str, *, min_chars: int = 12) -> dict[str, Any]:
    """Locate ``quote`` literally inside the (normalised) source text."""
    nq, nt = _norm(quote), _norm(source.text)
    if len(nq) < min_chars:
        return {"url": source.url, "verified": False, "reason": "quote_too_short"}
    index = nt.find(nq)
    if index < 0:
        return {"url": source.url, "verified": False, "reason": "quote_not_found_in_source"}
    return {"url": source.url, "verified": True, "offset": index, "length": len(nq),
            "passage_sha256": hashlib.sha256(nq.encode("utf-8")).hexdigest(),
            "source_sha256": hashlib.sha256(nt.encode("utf-8")).hexdigest()}


def decide_verdict(support_independent: int, counter_independent: int, *, threshold: int = 2) -> str:
    if support_independent >= threshold and counter_independent == 0:
        return "supported"
    if counter_independent >= threshold and support_independent == 0:
        return "refuted"
    if support_independent and counter_independent:
        return "contested"
    return "insufficient"


class ClaimDossierStore:
    def __init__(self, database: str | Path) -> None:
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS claim_dossiers(
                id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL, version INTEGER NOT NULL,
                claim TEXT NOT NULL, verdict TEXT NOT NULL, dossier_json TEXT NOT NULL, dossier_sha256 TEXT NOT NULL,
                created_at REAL NOT NULL, UNIQUE(claim_id, version));
            CREATE TRIGGER IF NOT EXISTS claim_dossiers_no_update BEFORE UPDATE ON claim_dossiers
                BEGIN SELECT RAISE(ABORT,'claim dossiers are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS claim_dossiers_no_delete BEFORE DELETE ON claim_dossiers
                BEGIN SELECT RAISE(ABORT,'claim dossiers are append-only'); END;
            CREATE TABLE IF NOT EXISTS claim_feedback_links(
                id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT NOT NULL, feedback_id TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'tested_by', created_at REAL NOT NULL, UNIQUE(claim_id, feedback_id));
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "ClaimDossierStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def append(self, dossier: dict[str, Any], *, clock: Callable[[], float] = time.time) -> dict[str, Any]:
        with self.db:
            row = self.db.execute("SELECT MAX(version) FROM claim_dossiers WHERE claim_id=?", (dossier["claim_id"],)).fetchone()
            version = int(row[0] or 0) + 1
            dossier = {**dossier, "version": version}
            encoded = json.dumps(dossier, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            self.db.execute("INSERT INTO claim_dossiers(claim_id,version,claim,verdict,dossier_json,dossier_sha256,created_at)"
                            " VALUES(?,?,?,?,?,?,?)", (dossier["claim_id"], version, dossier["claim"], dossier["verdict"],
                                                       encoded, digest, clock()))
        return {**dossier, "dossier_sha256": digest}

    def latest(self, claim_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT dossier_json, dossier_sha256 FROM claim_dossiers WHERE claim_id=? ORDER BY version DESC"
                              " LIMIT 1", (claim_id,)).fetchone()
        return {**json.loads(row[0]), "dossier_sha256": row[1]} if row else None

    def exists(self, claim_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM claim_dossiers WHERE claim_id=?", (claim_id,)).fetchone() is not None

    def link_feedback(self, claim_id: str, feedback_id: str, *, clock: Callable[[], float] = time.time) -> bool:
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO claim_feedback_links(claim_id,feedback_id,created_at) VALUES(?,?,?)",
                                     (claim_id, feedback_id, clock()))
        return cursor.rowcount == 1

    def feedback_for(self, claim_id: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT feedback_id FROM claim_feedback_links WHERE claim_id=? ORDER BY id",
                                              (claim_id,))]


Retriever = Callable[[str], Iterable[Source]]


class ResearchClosedLoop:
    def __init__(self, *, support_search: Retriever, counter_search: Retriever, store: ClaimDossierStore,
                 clock: Callable[[], float] = time.time, max_sources_per_query: int = 8) -> None:
        if not callable(support_search) or not callable(counter_search):
            raise TypeError("both support and counter retrievers are mandatory")
        self.support_search, self.counter_search = support_search, counter_search
        self.store, self.clock, self.limit = store, clock, int(max_sources_per_query)

    def _gather(self, claim: str, templates: tuple[str, ...], search: Retriever, role: str) -> tuple[list[Source], list[str]]:
        seen, gathered, queries = set(), [], []
        for template in templates:
            query = template.replace("{claim}", claim)
            queries.append(query)
            try:
                results = list(search(query))[: self.limit]
            except Exception as exc:  # a failing retriever is recorded, never silently ignored
                queries.append(f"!error:{type(exc).__name__}")
                continue
            for item in results:
                if not isinstance(item, Source):
                    continue
                key = canonical(item.canonical_url or item.url)
                if key in seen:
                    continue
                seen.add(key)
                item.retrieved_for = role
                gathered.append(item)
        return gathered, queries

    def investigate(self, claim_id: str, claim: str, *, citations: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", claim_id or ""):
            raise ValueError("claim_id must be a lowercase identifier")
        if not isinstance(claim, str) or not 4 <= len(claim.strip()) <= 1000:
            raise ValueError("claim must be 4-1000 characters")
        claim = claim.strip()
        support, support_queries = self._gather(claim, SUPPORT_TEMPLATES, self.support_search, "support")
        counter, counter_queries = self._gather(claim, COUNTER_TEMPLATES, self.counter_search, "counter")
        pool = support + counter
        # unlabelled counter-search hits count against the claim (conservative: they can only lower the verdict)
        supporting = [s for s in pool if s.stance == "supports" or (s.stance == "unknown" and s.retrieved_for == "support")]
        refuting = [s for s in pool if s.stance == "refutes" or (s.stance == "unknown" and s.retrieved_for == "counter")]
        support_audit = audit_independence(supporting)
        counter_audit = audit_independence(refuting)
        everything = {canonical(s.url): s for s in support + counter}
        anchors = []
        for citation in citations or []:
            source = everything.get(canonical(citation.get("url", "")))
            anchors.append(verify_citation(source, citation.get("quote", "")) if source else
                           {"url": citation.get("url"), "verified": False, "reason": "cited_source_not_retrieved"})
        verdict = decide_verdict(support_audit["independent_source_count"], counter_audit["independent_source_count"])
        if anchors and not all(a["verified"] for a in anchors) and verdict == "supported":
            verdict = "insufficient"   # an unverifiable quote can never back a "supported" verdict
        dossier = {
            "claim_id": claim_id, "claim": claim, "verdict": verdict,
            "verdict_semantics": "evidence_balance_not_truth",
            "queries": {"support": support_queries, "counter": counter_queries},
            "support": support_audit, "counter": counter_audit,
            "counter_search_performed": bool(counter_queries),
            "citation_anchors": anchors,
            "untrusted_content_policy": "source text is data; never instructions",
            "created_at": self.clock(),
        }
        return self.store.append(dossier, clock=self.clock)
