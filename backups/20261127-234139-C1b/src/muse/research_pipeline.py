"""Concurrent, evidence-preserving research pipeline for Muse."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

from muse.research import BingRSSSearchAdapter, DuckDuckGoHTMLSearchAdapter, SearxngSearchAdapter
from muse.research_fetch import PublicSourceReader, ResearchEvidenceStore, SourceReadError
from muse.resource_governor import ResourcePolicy, probe_host_resources
from muse.progress import ProgressEventStore
from muse.research_knowledge import ResearchKnowledgeStore
from muse.source_identity import build_source_identity_candidates, find_exact_content_matches
from muse.error_journal import record_suppressed_error



_RELEVANCE_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "about", "how", "what",
    "official", "documentation", "docs", "guide", "latest", "project",
})


_MULTI_LABEL_PUBLIC_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "co.nz", "org.nz", "com.br", "com.cn", "com.hk", "co.jp", "co.kr",
})


def _wiki_source_contract(url: str, *, fetch_status: str) -> dict[str, Any]:
    """Research Wiki contract: a search hit is ``pending_fetch``; only a read page may back evidence."""
    from muse.epistemics import stamp
    from muse.research_wiki import _domain
    return {"wiki_object": "source", "fetch_status": fetch_status, "registrable_domain": _domain(url),
            **stamp("observation", source=url, confidence=0.5, origin="web_research").as_dict()}

def _registrable_domain_hint(hostname: str) -> str | None:
    """Return a conservative registrable-domain grouping hint without a PSL dependency.

    This is deliberately a grouping heuristic, not proof of common ownership. Unknown
    multi-label public suffixes may be grouped imperfectly and must remain reviewable.
    """
    host = hostname.casefold().rstrip(".")
    if not host:
        return None
    try:
        import ipaddress
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    labels = [part for part in host.split(".") if part]
    if len(labels) < 2:
        return host
    suffix2 = ".".join(labels[-2:])
    suffix_labels = 2 if suffix2 in _MULTI_LABEL_PUBLIC_SUFFIXES else 1
    if len(labels) <= suffix_labels:
        return None
    return ".".join(labels[-(suffix_labels + 1):])


def _source_identity_signals(url: str) -> dict[str, Any]:
    """Expose URL identity hints without claiming independence, ownership or trust."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    registrable_domain = _registrable_domain_hint(hostname)
    return {
        "scheme": parsed.scheme.casefold(),
        "hostname": hostname,
        "registrable_domain": registrable_domain,
        "domain_grouping": "heuristic_registrable_domain_v1" if registrable_domain else "hostname_only",
        "source_independence": "unassessed",
        "source_credibility": "unassessed",
        "grouping_is_ownership_proof": False,
    }


def _publication_date_assessment(published_at: Any, fetched_at: Any) -> dict[str, Any]:
    """Parse publisher dates conservatively; this is a date signal, not source verification."""
    raw = str(published_at or "").strip()
    if not raw:
        return {"status": "missing", "published_date": None, "age_days": None}
    parsed: datetime | None = None
    try:
        candidate = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            parsed = datetime.combine(date.fromisoformat(raw[:10]), datetime.min.time())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return {"status": "unparseable", "published_date": None, "age_days": None}
    try:
        fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        fetched = fetched.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        fetched = datetime.now(timezone.utc)
    age_days = (fetched.date() - parsed.date()).days
    return {
        "status": "future" if age_days < -1 else "parsed",
        "published_date": parsed.date().isoformat(),
        "age_days": age_days,
    }


def _source_citation_id(url: str, content_hash: str) -> str:
    """Stable opaque citation handle tied to both canonical URL and fetched body hash."""
    return hashlib.sha256((url + "\n" + content_hash).encode("utf-8")).hexdigest()[:20]


def _completed_futures(futures: dict[Any, Any], stop_event: Any | None, *,
                       slice_seconds: float = 0.1) -> Any:
    """Yield completed futures while staying responsive to a cooperative stop request.

    A plain as_completed() call cannot be interrupted, so a supervisor shutdown would
    have to wait for the slowest network fetch. Polling in short slices lets the caller
    abandon the remaining work as soon as stop is requested.
    """
    pending = set(futures)
    while pending:
        if stop_event is not None and stop_event.is_set():
            for future in pending:
                future.cancel()
            return
        done, pending = wait(pending, timeout=slice_seconds, return_when=FIRST_COMPLETED)
        for future in done:
            yield future


def _relevance_score(query: str, title: str, snippet: str) -> int:
    """Return a transparent lexical relevance hint (0-100), not a credibility rating."""
    terms = {
        token for token in re.findall(r"[\w]+", query.casefold())
        if len(token) > 2 and token not in _RELEVANCE_STOP_WORDS
    }
    if not terms:
        return 0
    title_terms = set(re.findall(r"[\w]+", title.casefold()))
    snippet_terms = set(re.findall(r"[\w]+", snippet.casefold()))
    title_overlap = len(terms & title_terms) / len(terms)
    snippet_overlap = len(terms & snippet_terms) / len(terms)
    return max(0, min(100, round(100 * (0.7 * title_overlap + 0.3 * snippet_overlap))))


def _passes_query_relevance_gate(query: str, title: str, snippet: str) -> bool:
    """Reject obvious lexical collisions for YouTube title A/B research without grading credibility."""
    normalized_query = query.casefold()
    if not (
        "youtube" in normalized_query
        and ("title" in normalized_query or "thumbnail" in normalized_query)
        and ("a/b" in normalized_query or "ab test" in normalized_query or "testing" in normalized_query)
    ):
        return True
    content = f"{title} {snippet}".casefold()
    platform_anchors = ("youtube", "video", "creator", "thumbnail", "channel")
    experiment_anchors = (
        "title", "thumbnail", "a/b", "ab test", "experiment", "randomized",
        "watch-time", "watch time", "ctr", "variant", "test and compare",
    )
    return any(anchor in content for anchor in platform_anchors) and any(
        anchor in content for anchor in experiment_anchors
    )


def _freshness_bonus(published_at: Any, *, now: datetime | None = None) -> int:
    """Small ranking bonus for dated recent results; never a credibility score."""
    raw = str(published_at or "").strip()
    if not raw:
        return 0
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_days = (reference.date() - parsed.astimezone(timezone.utc).date()).days
    if age_days < 0:
        return 0
    if age_days <= 7:
        return 25
    if age_days <= 30:
        return 18
    if age_days <= 90:
        return 12
    if age_days <= 180:
        return 6
    return 0


class ResearchPipeline:
    """Search multiple angles, fetch independent sources concurrently, persist extracts."""

    def __init__(
        self,
        search: Any,
        reader: PublicSourceReader,
        evidence: ResearchEvidenceStore,
        *,
        max_workers: int = 8,
        resource_policy: ResourcePolicy | None = None,
        resource_aware: bool = True,
        progress_events: ProgressEventStore | None = None,
        knowledge_store: ResearchKnowledgeStore | None = None,
    ) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be between 1 and 16")
        if not isinstance(resource_aware, bool):
            raise TypeError("resource_aware must be a boolean")
        if resource_policy is not None and not isinstance(resource_policy, ResourcePolicy):
            raise TypeError("resource_policy must be a ResourcePolicy or None")
        if resource_policy is not None and not resource_aware:
            raise ValueError("resource_policy cannot be supplied when resource_aware is false")
        self.search = search
        self.reader = reader
        self.evidence = evidence
        self.max_workers = max_workers
        self.resource_policy = resource_policy or (
            ResourcePolicy(maximum_workers=max_workers) if resource_aware else None
        )
        self.progress_events = progress_events
        if knowledge_store is not None and not isinstance(knowledge_store, ResearchKnowledgeStore):
            raise TypeError("knowledge_store must be a ResearchKnowledgeStore or None")
        self.knowledge_store = knowledge_store

    def _record_progress(self, subject_id: str, event: str, *, summary: str,
                         error_code: str | None = None) -> bool:
        if self.progress_events is None:
            return False
        try:
            self.progress_events.record(
                "research", subject_id, event, summary=summary, error_code=error_code
            )
            return True
        except Exception as _muse_suppressed:
            record_suppressed_error("research_pipeline.py:251", _muse_suppressed)
            # Progress telemetry is best-effort and must not interrupt evidence collection.
            return False

    def _aborted(self, cleaned: list[str], failures: list[dict[str, str]],
                 prior_knowledge: list[dict[str, Any]], progress_count: int,
                 progress_id: str) -> dict[str, Any]:
        """Report a stop-interrupted run without claiming it produced evidence."""
        progress_count += int(self._record_progress(
            progress_id, "cancelled", summary="Research stopped on runtime shutdown request.",
            error_code="research.cancelled",
        ))
        return {
            "queries": cleaned, "search_result_count": 0, "prior_knowledge": prior_knowledge,
            "knowledge_run": None, "progress_event_count": progress_count,
            "unique_source_count": 0, "read_source_count": 0, "independent_domain_count": 0,
            "sources": [], "failures": failures, "knowledge_node_count": 0,
            "knowledge_failures": [], "cancelled": True,
            "verification_note": "研究在运行停止请求下提前结束，未产生新的证据结论。",
        }

    def _worker_slots(self, requested: int) -> int:
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
            raise ValueError("requested worker slots must be a positive integer")
        if self.resource_policy is None:
            return min(self.max_workers, requested)
        decision = self.resource_policy.decide(probe_host_resources())
        if decision.recommended_workers < 1:
            raise RuntimeError("research dispatch paused to protect the configured host resource reserve")
        return min(self.max_workers, requested, decision.recommended_workers)

    def run(self, queries: tuple[str, ...], *, max_sources: int = 8,
            task_id: str | None = None,
            stop_event: Any | None = None) -> dict[str, Any]:
        if not isinstance(queries, tuple) or not 1 <= len(queries) <= 8:
            raise ValueError("queries must be a tuple containing 1-8 search angles")
        cleaned = []
        for query in queries:
            if not isinstance(query, str) or not query.strip() or len(query) > 500:
                raise ValueError("each query must contain 1-500 characters")
            if query.strip() not in cleaned:
                cleaned.append(query.strip())
        if isinstance(max_sources, bool) or not isinstance(max_sources, int) or not 1 <= max_sources <= 16:
            raise ValueError("max_sources must be between 1 and 16")
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 160):
            raise ValueError("task_id must be non-empty text of at most 160 characters")

        progress_id = uuid.uuid4().hex
        prior_knowledge = []
        if self.knowledge_store is not None:
            try:
                prior_knowledge = self.knowledge_store.search_context(" ".join(cleaned), limit=8)
            except Exception as _muse_suppressed:
                record_suppressed_error("research_pipeline.py:303", _muse_suppressed)
                prior_knowledge = []
        progress_count = int(self._record_progress(
            progress_id, "started", summary="Multi-angle research started."
        ))
        results_by_query: dict[str, list[dict[str, Any]]] = {query: [] for query in cleaned}
        failures: list[dict[str, str]] = []
        search_slots = self._worker_slots(len(cleaned))
        pool = ThreadPoolExecutor(max_workers=search_slots)
        try:
            futures = {pool.submit(self.search.search, query, limit=max_sources): query for query in cleaned}
            for future in _completed_futures(futures, stop_event):
                query = futures[future]
                try:
                    results_by_query[query] = [
                        {
                            **item,
                            "query": query,
                            "relevance_score": _relevance_score(
                                query, str(item.get("title", "")), str(item.get("snippet", ""))
                            ),
                            "relevance_gate_passed": _passes_query_relevance_gate(
                                query, str(item.get("title", "")), str(item.get("snippet", ""))
                            ),
                            "relevance_method": "title_snippet_token_overlap_v1_domain_anchor_gate_v1",
                        }
                        for item in future.result()
                    ]
                except Exception as exc:
                    failures.append({"stage": "search", "query": query, "error": type(exc).__name__})
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if stop_event is not None and stop_event.is_set():
            return self._aborted(cleaned, failures, prior_knowledge, progress_count, progress_id)
        drain_search_failures = getattr(self.search, "drain_failures", None)
        if callable(drain_search_failures):
            try:
                failures.extend(
                    item for item in drain_search_failures()
                    if isinstance(item, dict) and isinstance(item.get("stage"), str)
                )
            except Exception as exc:
                failures.append({"stage": "search_diagnostics", "error": type(exc).__name__})

        search_results = [item for query in cleaned for item in results_by_query[query]]
        unique: dict[str, dict[str, Any]] = {}
        per_query: dict[str, list[dict[str, Any]]] = {query: [] for query in cleaned}
        for item in search_results:
            url = item["url"]
            if url in unique:
                continue
            unique[url] = item
            per_query[item["query"]].append(item)

        for queue in per_query.values():
            queue.sort(
                key=lambda item: (
                    item.get("relevance_score", 0) + _freshness_bonus(item.get("published_at")),
                    item.get("relevance_score", 0),
                ),
                reverse=True,
            )

        knowledge_node_ids: set[str] = set()
        knowledge_source_refs: dict[str, str] = {}
        knowledge_failures: list[dict[str, str]] = []
        knowledge_source_refs_read: set[str] = set()  # Research Wiki: never downgrade read -> pending
        for source in search_results:
            try:
                self.evidence.save_attribution(source)
            except Exception as exc:
                failures.append({"stage": "attribution", "url": source["url"], "error": type(exc).__name__})
                continue
            if self.knowledge_store is not None:
                try:
                    source_node = self.knowledge_store.upsert_node(
                        "source", str(source.get("title", source["url"]))[:500],
                        external_ref=source["url"],
                        metadata={**_wiki_source_contract(source["url"], fetch_status=(
                                      "read" if source["url"] in knowledge_source_refs_read else "pending_fetch")),
                                  "source_identity": _source_identity_signals(source["url"]),
                                  "attributions": [{
                            "query": source["query"], "engine": str(source.get("engine", ""))[:100],
                            "published_at": str(source.get("published_at", ""))[:80],
                            "source_kind": str(source.get("source_kind", "web_source_unspecified"))[:120],
                            "relevance_score": source.get("relevance_score", 0),
                            "relevance_gate_passed": source.get("relevance_gate_passed", True),
                            "relevance_method": source.get("relevance_method", "unscored"),
                        }]},
                    )
                    knowledge_node_ids.add(source_node["id"])
                    knowledge_source_refs[source["url"]] = source_node["id"]
                except Exception as exc:
                    knowledge_failures.append({"stage": "knowledge_source", "url": source["url"],
                                               "error": type(exc).__name__})

        # Round-robin across angles and prefer unseen hosts before taking a second
        # result from one domain. This prevents one fast query dominating the evidence set.
        candidates: list[dict[str, Any]] = []
        seen_hosts: set[str] = set()
        while len(candidates) < max_sources:
            progressed = False
            for query in cleaned:
                queue = per_query[query]
                if not queue or len(candidates) >= max_sources:
                    continue
                eligible_indices = [
                    index for index, item in enumerate(queue)
                    if item.get("relevance_gate_passed", True)
                ]
                if not eligible_indices:
                    continue
                selected_index = next(
                    (index for index in eligible_indices
                     if (urlsplit(queue[index]["url"]).hostname or "").casefold() not in seen_hosts),
                    eligible_indices[0],
                )
                item = queue.pop(selected_index)
                candidates.append(item)
                seen_hosts.add((urlsplit(item["url"]).hostname or "").casefold())
                progressed = True
            if not progressed:
                break

        pages: list[dict[str, Any]] = []
        read_slots = self._worker_slots(max(1, len(candidates)))
        pool = ThreadPoolExecutor(max_workers=read_slots)
        try:
            futures = {pool.submit(self.reader.read, item["url"]): item for item in candidates}
            for future in _completed_futures(futures, stop_event):
                source = futures[future]
                try:
                    page = future.result()
                    page_text = str(page.get("text", "")).strip()
                    challenge_markers = (
                        "verify your browser", "complete the check below", "checking your browser",
                        "captcha", "access denied", "please enable javascript",
                    )
                    if len(page_text) < 200:
                        raise SourceReadError("source returned fewer than 200 readable characters")
                    if any(marker in page_text[:2000].casefold() for marker in challenge_markers):
                        raise SourceReadError("source returned an access challenge instead of research content")
                    self.evidence.save(page)
                    if self.knowledge_store is not None:
                        try:
                            page_source_node = self.knowledge_store.upsert_node(
                                "source", str(source.get("title", source["url"]))[:500],
                                external_ref=page["url"],
                                metadata={
                                    "source_identity": {
                                        **_source_identity_signals(page["url"]),
                                        **(page.get("source_metadata") or {}),
                                    },
                                    **_wiki_source_contract(page["url"], fetch_status="read"),
                                    "content_sha256": page["sha256"],
                                    "excerpt": str(page.get("text", ""))[:1500],
                                    "content_hash": page["sha256"],
                                    "fetched_at": page["fetched_at"],
                                    "chars": len(page["text"]),
                                    "truncated": bool(page["truncated"]),
                                    "search_engine": str(source.get("engine", ""))[:100],
                                    "capture_method": str(page.get("capture_method", "http"))[:40],
                                    "browser_evidence_directory": page.get("browser_evidence_directory"),
                                    "browser_screenshot_path": page.get("browser_screenshot_path"),
                                    "asset_manifest_path": page.get("asset_manifest_path"),
                                    "asset_count": page.get("asset_count", 0),
                                    "structured_extract": page.get("structured_extract"),
                                    "structured_extract_error": page.get("structured_extract_error"),
                                },
                            )
                            knowledge_node_ids.add(page_source_node["id"]); knowledge_source_refs[page["url"]] = page_source_node["id"]; knowledge_source_refs_read.add(page["url"])
                        except Exception as exc:
                            knowledge_failures.append({"stage": "knowledge_evidence", "url": source["url"],
                                                       "error": type(exc).__name__})
                    pages.append({
                        "title": source["title"], "url": page["url"], "query": source["query"],
                        "source_kind": str(source.get("source_kind", "web_source_unspecified"))[:120],
                        "snippet": str(source.get("snippet", ""))[:500],
                        "relevance_score": source.get("relevance_score", 0),
                        "relevance_method": source.get("relevance_method", "unscored"),
                        "content_type": page["content_type"], "chars": len(page["text"]),
                        "sha256": page["sha256"], "fetched_at": page["fetched_at"],
                        "truncated": page["truncated"], "text": page["text"],
                        "source_metadata": page.get("source_metadata") or {},
                        "structured_extract": page.get("structured_extract"),
                        "structured_extract_error": page.get("structured_extract_error"),
                        "capture_method": str(page.get("capture_method", "http"))[:40],
                        "browser_evidence_directory": page.get("browser_evidence_directory"),
                        "browser_screenshot_path": page.get("browser_screenshot_path"),
                        "asset_manifest_path": page.get("asset_manifest_path"),
                        "asset_count": page.get("asset_count", 0),
                        "asset_inventory_count": page.get("asset_inventory_count", 0),
                        "search_evidence_path": source.get("search_evidence_path"),
                        "source_identity": {
                            **_source_identity_signals(page["url"]),
                            **(page.get("source_metadata") or {}),
                        },
                        "citation_id": _source_citation_id(page["url"], page["sha256"]),
                        "source_signals": {
                            "https": page["url"].casefold().startswith("https://"),
                            "source_kind": str(source.get("source_kind", "web_source_unspecified"))[:120],
                            "date_type": ("arxiv_submission_date" if str(source.get("source_kind", "")).startswith("arxiv_")
                                          else "publisher_or_search_provider_date"),
                            "snippet_available": bool(source.get("snippet")),
                            "publication_date": _publication_date_assessment(
                                source.get("published_at"), page["fetched_at"]
                            ),
                            "content_nontrivial": len(page["text"].strip()) >= 200,
                            "search_engine": str(source.get("engine", ""))[:100],
                        },
                    })
                except Exception as exc:
                    failures.append({"stage": "read", "url": source["url"], "error": type(exc).__name__})
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if stop_event is not None and stop_event.is_set():
            return self._aborted(cleaned, failures, prior_knowledge, progress_count, progress_id)

        exact_content_match_hints: list[dict[str, Any]] = []
        source_identity_candidate_count = 0
        if self.knowledge_store is not None and pages:
            identity_sources = [
                {
                    "source_node_id": knowledge_source_refs[item["url"]],
                    "url": item["url"],
                    "metadata": {
                        **(item.get("source_metadata") or {}),
                        "content_hash": item.get("sha256"),
                        "truncated": bool(item.get("truncated")),
                    },
                }
                for item in pages if item["url"] in knowledge_source_refs
            ]
            current_source_ids = {item["source_node_id"] for item in identity_sources}
            try:
                historical_sources = [
                    {
                        "source_node_id": node["id"],
                        "url": node.get("external_ref"),
                        "metadata": node.get("metadata") or {},
                    }
                    for node in self.knowledge_store.list_nodes(node_type="source", limit=500)
                    if node["id"] not in current_source_ids and node.get("external_ref")
                ]
                exact_content_match_hints = find_exact_content_matches(
                    identity_sources, existing_sources=historical_sources,
                )
                for candidate in build_source_identity_candidates(
                    identity_sources, existing_sources=historical_sources,
                ):
                    self.knowledge_store.record_source_identity_candidate(
                        candidate["source_node_id"], candidate["related_source_node_id"],
                        candidate_type=candidate["candidate_type"],
                        confidence=candidate["confidence"], evidence=candidate["evidence"],
                    )
                    source_identity_candidate_count += 1
            except Exception as exc:
                knowledge_failures.append({
                    "stage": "source_identity_candidates", "error": type(exc).__name__,
                })

        publisher_groups = {
            item.get("source_identity", {}).get("registrable_domain")
            or item.get("source_identity", {}).get("hostname")
            for item in pages
            if item.get("source_identity", {}).get("registrable_domain")
            or item.get("source_identity", {}).get("hostname")
        }
        content_hash_counts: dict[str, int] = {}
        for item in pages:
            digest = item.get("sha256")
            if isinstance(digest, str) and len(digest) == 64:
                content_hash_counts[digest] = content_hash_counts.get(digest, 0) + 1
        for item in pages:
            item["source_identity"]["publisher_group_count"] = len(publisher_groups)
            item["source_identity"]["exact_content_duplicate_count"] = content_hash_counts.get(item.get("sha256"), 0)
            item["source_identity"]["independence_assessment"] = "unassessed"

        domains = {(urlsplit(item["url"]).hostname or "").casefold() for item in pages}
        if failures and not pages:
            progress_count += int(self._record_progress(
                progress_id, "failed", summary="Research produced no readable sources.",
                error_code="research.no_sources",
            ))
        elif failures:
            progress_count += int(self._record_progress(
                progress_id, "progress",
                summary=f"Research partially completed: {len(pages)} sources read; {len(failures)} errors.",
            ))
        else:
            progress_count += int(self._record_progress(
                progress_id, "completed",
                summary=f"Research completed: {len(pages)} sources read across {len(domains)} domains.",
            ))
        knowledge_run = None
        if self.knowledge_store is not None:
            try:
                knowledge_run = self.knowledge_store.record_research_run(
                    " ".join(cleaned), run_id=progress_id,
                    source_refs=tuple(item["url"] for item in pages if item["url"] in knowledge_source_refs)[:32],
                    status="failed" if not pages else "partial" if failures else "succeeded",
                    failures=failures, task_id=task_id or progress_id,
                )
            except Exception as exc:
                knowledge_failures.append({"stage": "knowledge_run", "error": type(exc).__name__})
        return {
            "queries": cleaned, "search_result_count": len(search_results),
            "prior_knowledge": prior_knowledge,
            "knowledge_run": knowledge_run,
            "progress_event_count": progress_count,
            "unique_source_count": len(unique), "read_source_count": len(pages),
            "independent_domain_count": len(domains),
            "independent_publisher_group_count": len(publisher_groups),
            "source_identity_candidate_count": source_identity_candidate_count,
            "exact_content_match_hints": exact_content_match_hints,

            "exact_duplicate_content_groups": sum(1 for count in content_hash_counts.values() if count > 1),
            "sources": sorted(pages, key=lambda item: item["url"]),
            "failures": failures, "knowledge_node_count": len(knowledge_node_ids),
            "knowledge_failures": knowledge_failures,
            "verification_note": "不同域名仅代表来源多样性线索，不等于事实已被证实；启发式注册域分组也不证明所有权独立或来源可信。",
        }

def _serialize_result(result: dict[str, Any]) -> str:
    """Keep CLI JSON printable under Windows legacy console encodings."""
    return json.dumps(result, ensure_ascii=True, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run concurrent multi-angle Muse web research")
    parser.add_argument("query", help="research goal; avoid private or sensitive data")
    parser.add_argument("--angle", action="append", default=[], help="additional search angle; repeat as needed")
    parser.add_argument("--provider", choices=("searxng", "bing-rss", "duckduckgo-html"), default="duckduckgo-html")
    parser.add_argument("--endpoint", default=os.environ.get("MUSE_SEARXNG_URL"))
    parser.add_argument("--evidence-db", default="data/research/evidence.sqlite3")
    parser.add_argument("--progress-db", default="data/tasks/muse.sqlite3",
                        help="SQLite database shared with Muse's structured progress events")
    parser.add_argument("--knowledge-db", default="data/research/knowledge.sqlite3",
                        help="SQLite typed research knowledge graph (separate from source-body evidence)")
    parser.add_argument("--max-sources", type=int, default=8)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--scrapling", action="store_true",
                        help="enable optional Scrapling extraction on HTML already fetched by Muse")
    parser.add_argument("--scrapling-adaptive-mode", choices=("off", "learn", "reuse"), default="off",
                        help="optional selector learning/reuse; requires --scrapling")
    parser.add_argument("--task-id", help="optional existing Muse Ticket ID for knowledge provenance")
    args = parser.parse_args()
    if args.provider == "searxng" and not args.endpoint:
        parser.error("SearXNG requires --endpoint or MUSE_SEARXNG_URL; alternatively select --provider duckduckgo-html or bing-rss")
    if args.scrapling_adaptive_mode != "off" and not args.scrapling:
        parser.error("--scrapling-adaptive-mode requires --scrapling")
    if args.provider == "searxng":
        search = SearxngSearchAdapter(args.endpoint)
    elif args.provider == "bing-rss":
        search = BingRSSSearchAdapter()
    else:
        search = DuckDuckGoHTMLSearchAdapter()
    queries = tuple([args.query, *args.angle])
    reader: PublicSourceReader
    if args.scrapling:
        try:
            from scrapling import Selector as _ScraplingSelector  # noqa: F401
            from muse.scrapling_adapter import ScraplingHTMLParser
        except ImportError:
            parser.error("Scrapling is optional; install the Muse web-scraping extra first")
        reader = PublicSourceReader(
            structured_parser=ScraplingHTMLParser(Path.cwd()),
            adaptive_mode=args.scrapling_adaptive_mode,
        )
    else:
        reader = PublicSourceReader()
    try:
        with ResearchEvidenceStore(Path(args.evidence_db)) as evidence, ProgressEventStore(
            Path(args.progress_db)
        ) as progress, ResearchKnowledgeStore(Path(args.knowledge_db)) as knowledge:
            result = ResearchPipeline(
                search, reader, evidence,
                max_workers=args.workers, progress_events=progress, knowledge_store=knowledge,
            ).run(queries, max_sources=args.max_sources, task_id=args.task_id)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, "Research pipeline failed: " + type(exc).__name__ + "\\n")
    print(_serialize_result(result))
    return 0


if __name__ == "__main__":
    from muse.research_fetch import PublicSourceReader, ResearchEvidenceStore
    raise SystemExit(main())
