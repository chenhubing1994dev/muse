"""Persistent, provenance-aware website catalogue for Muse research.

Bookmarks and discovered URLs are discovery hints, never evidence by themselves.
Page content and claim evidence remain in the research evidence store.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize_public_url(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("URL must be non-empty and at most 4096 characters")
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("only credential-free public HTTP(S) URLs are accepted")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test")):
        raise ValueError("local and reserved hostnames are not accepted")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified):
        raise ValueError("non-public IP addresses are not accepted")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL port") from exc
    if port not in (None, 80, 443):
        raise ValueError("non-standard URL ports are not accepted")
    if ":" in host and address is not None:
        netloc = f"[{host}]"
    else:
        netloc = host
    path = parsed.path or "/"
    canonical = urlunsplit((scheme, netloc, path, parsed.query, ""))
    return canonical, host


def _category(title: str, url: str, folder: str = "") -> str:
    text = f"{title} {url} {folder}".casefold()
    if any(term in text for term in ("易经", "周易", "紫微", "奇门", "六壬", "玄学", "命理", "占星", "术数", "qimen", "ziwei", "astrolog", "divination")):
        return "metaphysics"
    if any(term in text for term in ("国学", "古籍", "四库", "经典", "book", "yijing", "5000yan")):
        return "classical_texts"
    if any(term in text for term in ("nature.com", "pubmed", "pmc.ncbi", "journal", "research", "study", "critical")):
        return "research_and_counter_evidence"
    if any(term in text for term in ("martinfowler", "github.com", "software", "engineering", "architecture")):
        return "software_engineering"
    return "general"


class WebsiteKnowledgeCatalog:
    """A durable directory of creator bookmarks and research-discovered websites."""

    def __init__(self, database: str | Path):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS research_sites(
                id TEXT PRIMARY KEY,
                canonical_url TEXT NOT NULL UNIQUE,
                fetch_url TEXT NOT NULL,
                host TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                discovery_type TEXT NOT NULL,
                epistemic_role TEXT NOT NULL,
                status TEXT NOT NULL,
                relevance_score INTEGER NOT NULL DEFAULT 0,
                bookmark_folder TEXT NOT NULL DEFAULT '',
                discovered_from TEXT NOT NULL DEFAULT '',
                read_count INTEGER NOT NULL DEFAULT 0,
                last_seen TEXT NOT NULL,
                last_read TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_research_sites_category
                ON research_sites(category,status,last_seen);
            CREATE INDEX IF NOT EXISTS idx_research_sites_host
                ON research_sites(host);
            CREATE TABLE IF NOT EXISTS research_site_events(
                id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL REFERENCES research_sites(id),
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_research_site_events_site
                ON research_site_events(site_id,created_at);
        """)
        self.connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _event(self, site_id: str, event_type: str, payload: dict) -> None:
        self.connection.execute(
            "INSERT INTO research_site_events(id,site_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
            (hashlib.sha256(f"{site_id}:{event_type}:{self._now()}".encode()).hexdigest()[:32],
             site_id, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), self._now()),
        )

    def upsert_site(self, title: str, url: str, *, fetch_url: str | None = None,
                    category: str | None = None, discovery_type: str = "web_discovery",
                    epistemic_role: str = "discovery_only", status: str = "candidate",
                    relevance_score: int = 0, bookmark_folder: str = "",
                    discovered_from: str = "", metadata: dict | None = None) -> dict:
        canonical, host = normalize_public_url(url)
        fetch_canonical, _ = normalize_public_url(fetch_url or url)
        if discovery_type not in {"creator_bookmark", "web_discovery", "research_read", "curated_reference"}:
            raise ValueError("unsupported website discovery type")
        if epistemic_role not in {"discovery_only", "support_candidate", "counter_candidate", "context_candidate"}:
            raise ValueError("unsupported epistemic role")
        if status not in {"candidate", "read", "fetch_failed", "watch", "ignored"}:
            raise ValueError("unsupported website status")
        if isinstance(relevance_score, bool) or not isinstance(relevance_score, int) or not 0 <= relevance_score <= 100:
            raise ValueError("relevance_score must be an integer from 0 to 100")
        clean_title = str(title or host).strip()[:500] or host
        clean_category = category or _category(clean_title, canonical, bookmark_folder)
        if not isinstance(metadata or {}, dict):
            raise ValueError("metadata must be an object")
        now = self._now()
        site_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        with self.connection:
            old = self.connection.execute("SELECT * FROM research_sites WHERE canonical_url=?", (canonical,)).fetchone()
            if old:
                meta = json.loads(old["metadata_json"])
                meta.update(metadata or {})
                read_count = old["read_count"] + (1 if status == "read" else 0)
                self.connection.execute(
                    "UPDATE research_sites SET fetch_url=?,host=?,title=?,category=?,discovery_type=?,epistemic_role=?,"
                    "status=?,relevance_score=MAX(relevance_score,?),bookmark_folder=CASE WHEN ?<>'' THEN ? ELSE bookmark_folder END,"
                    "discovered_from=CASE WHEN ?<>'' THEN ? ELSE discovered_from END,read_count=?,"
                    "last_seen=?,last_read=CASE WHEN ?='read' THEN ? ELSE last_read END,metadata_json=? WHERE id=?",
                    (fetch_canonical, host, clean_title, clean_category, discovery_type, epistemic_role,
                     status, relevance_score, bookmark_folder, bookmark_folder, discovered_from, discovered_from,
                     read_count, now, status, now, json.dumps(meta, ensure_ascii=False, sort_keys=True), old["id"]),
                )
                site_id = old["id"]
                event = "site_refreshed"
            else:
                self.connection.execute(
                    "INSERT INTO research_sites(id,canonical_url,fetch_url,host,title,category,discovery_type,"
                    "epistemic_role,status,relevance_score,bookmark_folder,discovered_from,read_count,last_seen,last_read,metadata_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (site_id, canonical, fetch_canonical, host, clean_title, clean_category, discovery_type,
                     epistemic_role, status, relevance_score, bookmark_folder, discovered_from,
                     1 if status == "read" else 0, now, now if status == "read" else None,
                     json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)),
                )
                event = "site_discovered"
            self._event(site_id, event, {"url": canonical, "status": status, "epistemic_role": epistemic_role})
        row = self.connection.execute("SELECT * FROM research_sites WHERE id=?", (site_id,)).fetchone()
        return self._decode(row)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def ingest_bookmarks(self, bookmarks) -> int:
        count = 0
        for item in bookmarks:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            self.upsert_site(
                str(item.get("title", "")), str(item["url"]),
                fetch_url=str(item.get("read_url") or item["url"]),
                discovery_type="creator_bookmark", epistemic_role="discovery_only",
                status="candidate", bookmark_folder=str(item.get("folder", "")),
                metadata={"host_from_bookmark": item.get("host", "")},
            )
            count += 1
        return count

    def ingest_research_sources(self, sources, *, support_query: str = "", counter_query: str = "",
                                topic_key: str = "") -> int:
        count = 0
        for item in sources:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            query = str(item.get("query", ""))
            role = ("counter_candidate" if counter_query and query == counter_query
                    else "support_candidate" if support_query and query == support_query
                    else "context_candidate")
            status = "read" if int(item.get("chars", 0) or 0) > 0 else "candidate"
            self.upsert_site(
                str(item.get("title", "")), str(item["url"]),
                discovery_type="research_read" if status == "read" else "web_discovery",
                epistemic_role=role, status=status,
                relevance_score=min(100, max(0, int(item.get("relevance_score", 0) or 0) * 10)),
                discovered_from=topic_key,
                metadata={
                    "source_kind": item.get("source_kind", "web_source_unspecified"),
                    "query": query[:500], "citation_id": item.get("citation_id"),
                    "sha256": item.get("sha256"), "chars": int(item.get("chars", 0) or 0),
                    "evidence_quality": "unassessed",
                },
            )
            count += 1
        return count

    def list_sites(self, *, category: str | None = None, status: str | None = None,
                   epistemic_role: str | None = None, limit: int = 100) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be 1..500")
        clauses, params = [], []
        for column, value in (("category", category), ("status", status), ("epistemic_role", epistemic_role)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        sql = "SELECT * FROM research_sites"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY last_seen DESC LIMIT ?"
        params.append(limit)
        return [self._decode(row) for row in self.connection.execute(sql, params).fetchall()]

    def domain_hints(self, *, category: str | None = None, limit: int = 12) -> list[str]:
        rows = self.list_sites(category=category, limit=limit)
        return list(dict.fromkeys(item["host"] for item in rows))

    def stats(self) -> dict:
        return {
            "sites_total": self.connection.execute("SELECT COUNT(*) FROM research_sites").fetchone()[0],
            "bookmarks": self.connection.execute("SELECT COUNT(*) FROM research_sites WHERE discovery_type='creator_bookmark'").fetchone()[0],
            "discovered": self.connection.execute("SELECT COUNT(*) FROM research_sites WHERE discovery_type IN ('web_discovery','curated_reference')").fetchone()[0],
            "read": self.connection.execute("SELECT COUNT(*) FROM research_sites WHERE status='read'").fetchone()[0],
            "counter_candidates": self.connection.execute("SELECT COUNT(*) FROM research_sites WHERE epistemic_role='counter_candidate'").fetchone()[0],
            "support_candidates": self.connection.execute("SELECT COUNT(*) FROM research_sites WHERE epistemic_role='support_candidate'").fetchone()[0],
        }

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
