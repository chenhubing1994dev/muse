"""Re-fetch reviewed source citations and append fail-closed freshness observations."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
from typing import Any

from muse.research_fetch import PublicSourceReader, SourceReadError


def _safe_header(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    return cleaned[:256] or None


def refresh_dossier_citations(
    research_store: Any,
    dossier: Mapping[str, Any],
    *,
    reader: Any = None,
    max_checks: int = 24,
) -> dict[str, int]:
    """Re-fetch at most max_checks exact reviewed citation anchors.

    A fresh timestamp is not freshness. Only an unchanged complete text snapshot,
    unchanged URL and exact excerpt/offset/hash match yields source_current=True.
    Fetch failures and truncated pages remain unknown; any detected change is stale.
    """
    if isinstance(max_checks, bool) or not isinstance(max_checks, int) or not 1 <= max_checks <= 100:
        raise ValueError("max_checks must be between 1 and 100")
    record = getattr(research_store, "record_citation_freshness_check", None)
    if not callable(record):
        return {"checked": 0, "current": 0, "stale": 0, "unknown": 0, "skipped": 0}
    if not isinstance(dossier, Mapping):
        raise ValueError("dossier must be a mapping")
    reader = reader or PublicSourceReader(timeout=8.0)
    counts = {"checked": 0, "current": 0, "stale": 0, "unknown": 0, "skipped": 0}
    for evidence in dossier.get("evidence", []):
        if not isinstance(evidence, Mapping):
            continue
        review = evidence.get("latest_review")
        if not isinstance(review, Mapping):
            reviews = evidence.get("reviews", [])
            review = reviews[-1] if isinstance(reviews, list) and reviews else None
        if not isinstance(review, Mapping):
            continue
        citations = review.get("citations", [])
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if counts["checked"] >= max_checks:
                counts["skipped"] += 1
                continue
            if not isinstance(citation, Mapping):
                counts["skipped"] += 1
                continue
            citation_id = citation.get("id")
            url = citation.get("source_url")
            excerpt = citation.get("excerpt")
            start, end = citation.get("start_char"), citation.get("end_char")
            source_hash = citation.get("source_sha256")
            excerpt_hash = citation.get("excerpt_sha256")
            if (not isinstance(citation_id, str) or not citation_id
                    or not isinstance(url, str) or not url
                    or not isinstance(excerpt, str)
                    or isinstance(start, bool) or not isinstance(start, int)
                    or isinstance(end, bool) or not isinstance(end, int)
                    or start < 0 or end <= start or end - start != len(excerpt)
                    or not isinstance(source_hash, str) or len(source_hash) != 64
                    or not isinstance(excerpt_hash, str) or len(excerpt_hash) != 64):
                counts["skipped"] += 1
                continue
            outcome = "fetch_failed"
            current: bool | None = None
            final_url = None
            status_code = None
            content_hash = None
            current_excerpt_hash = None
            etag = None
            last_modified = None
            detail_code = None
            fetched_at = None
            try:
                page = reader.read(url, max_bytes=2_000_000, max_chars=20_000)
                final_url = page.get("url") if isinstance(page, Mapping) else None
                status_code = page.get("status_code") if isinstance(page, Mapping) else None
                fetched_at = page.get("fetched_at") if isinstance(page, Mapping) else None
                content_hash = page.get("sha256") if isinstance(page, Mapping) else None
                etag = _safe_header(page.get("etag")) if isinstance(page, Mapping) else None
                last_modified = _safe_header(page.get("last_modified")) if isinstance(page, Mapping) else None
                text = page.get("text") if isinstance(page, Mapping) else None
                if not isinstance(text, str) or page.get("truncated") is True:
                    outcome = "incomplete_snapshot"
                elif final_url != url:
                    outcome = "redirect_changed"
                    current = False
                elif content_hash != source_hash:
                    outcome = "content_changed"
                    current = False
                else:
                    current_excerpt = text[start:end]
                    current_excerpt_hash = hashlib.sha256(current_excerpt.encode("utf-8")).hexdigest()
                    if current_excerpt != excerpt or current_excerpt_hash != excerpt_hash:
                        outcome = "anchor_mismatch"
                        current = False
                    else:
                        outcome = "unchanged"
                        current = True
            except SourceReadError as exc:
                status_code = getattr(exc, "status_code", None)
                detail_code = type(exc).__name__
            except (OSError, TimeoutError, ValueError) as exc:
                detail_code = type(exc).__name__
            record(
                str(evidence.get("id", "")), citation_id,
                outcome=outcome, source_current=current,
                final_url=final_url, http_status=status_code, fetched_at=fetched_at,
                content_sha256=content_hash, excerpt_sha256=current_excerpt_hash,
                etag=etag, last_modified=last_modified, detail_code=detail_code,
            )
            counts["checked"] += 1
            counts["current" if current is True else "stale" if current is False else "unknown"] += 1
    return counts
