"""Conservative HTML metadata extraction and reviewable source-identity hints.

This module never executes page scripts, verifies ownership, or turns identity hints
into evidence verdicts. It extracts bounded metadata from fetched HTML only.
"""
from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


_TRACKING_KEYS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid",
    "ref_src", "ref_url", "igshid",
})
_TRACKING_PREFIXES = ("utm_",)


def normalize_source_url(url: str, *, base_url: str | None = None) -> str:
    """Normalize URL identity without merging distinct paths or meaningful query values."""
    if not isinstance(url, str) or not url.strip() or len(url) > 4096:
        raise ValueError("source URL must be non-empty text up to 4096 characters")
    absolute = urljoin(base_url, url.strip()) if base_url else url.strip()
    parsed = urlsplit(absolute)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source URL must be credential-free HTTP(S)")
    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("source URL has an invalid port") from exc
    default_port = 80 if scheme == "http" else 443
    suffix = f":{port}" if port and port != default_port else ""
    netloc = f"[{host}]{suffix}" if ":" in host else f"{host}{suffix}"
    query = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_KEYS
        and not any(key.casefold().startswith(prefix) for prefix in _TRACKING_PREFIXES)
    ]
    query.sort(key=lambda item: (item[0].casefold(), item[1]))
    return urlunsplit((scheme, netloc, parsed.path or "/", urlencode(query, doseq=True), ""))


class _MetadataParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.meta: dict[str, list[str]] = {}
        self.canonical: list[str] = []
        self.jsonld: list[str] = []
        self._in_jsonld = False
        self._jsonld_parts: list[str] = []
        self._jsonld_size = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs if key and value is not None}
        if tag.casefold() == "meta":
            key = (values.get("name") or values.get("property") or values.get("itemprop") or "").strip().casefold()
            value = (values.get("content") or "").strip()
            if key and value:
                self.meta.setdefault(key, []).append(value[:1000])
        elif tag.casefold() == "link":
            rel = set((values.get("rel") or "").casefold().split())
            href = (values.get("href") or "").strip()
            if href and "canonical" in rel:
                self.canonical.append(urljoin(self.page_url, href)[:4096])
        elif tag.casefold() == "script" and (values.get("type") or "").split(";")[0].strip().casefold() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_parts = []
            self._jsonld_size = 0

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._in_jsonld:
            value = "".join(self._jsonld_parts).strip()
            if value and len(value) <= 256_000 and len(self.jsonld) < 12:
                self.jsonld.append(value)
            self._in_jsonld = False
            self._jsonld_parts = []
            self._jsonld_size = 0

    def handle_data(self, data: str) -> None:
        if self._in_jsonld and self._jsonld_size < 256_000:
            chunk = data[: 256_000 - self._jsonld_size]
            self._jsonld_parts.append(chunk)
            self._jsonld_size += len(chunk)


def _values(value: Any) -> list[str]:
    """Extract human-readable entity names, never mislabel profile URLs as names."""
    if isinstance(value, str):
        text = value.strip()
        return [text[:500]] if text else []
    if isinstance(value, list):
        return [item for child in value for item in _values(child)][:20]
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return [name.strip()[:500]]
    return []


def _url_values(value: Any, page_url: str) -> list[str]:
    """Extract URL-valued fields from citation/lineage objects separately from entity names."""
    def candidates(item: Any) -> list[str]:
        if isinstance(item, str):
            return [item]
        if isinstance(item, list):
            return [child for value_item in item for child in candidates(value_item)][:40]
        if isinstance(item, dict):
            return [
                child for key in ("url", "@id")
                for child in candidates(item.get(key))
            ][:20]
        return []

    found = []
    for item in candidates(value):
        candidate = item.strip()
        if not candidate.startswith(("http://", "https://", "//", "/")):
            continue
        try:
            found.append(normalize_source_url(candidate, base_url=page_url))
        except ValueError:
            continue
    return list(dict.fromkeys(found))[:20]


def extract_html_source_metadata(html: str, page_url: str) -> dict[str, Any]:
    """Extract canonical URL, author/publisher, citation and lineage hints from HTML."""
    if not isinstance(html, str) or len(html.encode("utf-8", errors="replace")) > 1_000_000:
        raise ValueError("HTML metadata input must not exceed 1 MiB")
    normalized_page = normalize_source_url(page_url)
    parser = _MetadataParser(normalized_page)
    parser.feed(html)
    meta = parser.meta

    canonical_candidates = [*parser.canonical, *meta.get("og:url", [])]
    canonical_url = None
    for candidate in canonical_candidates[:4]:
        try:
            canonical_url = normalize_source_url(candidate, base_url=normalized_page)
            break
        except ValueError:
            continue

    authors: list[str] = []
    publishers: list[str] = []
    citations: list[str] = []
    based_on: list[str] = []
    same_as: list[str] = []
    published_at = (meta.get("article:published_time") or meta.get("citation_publication_date") or [None])[0]

    for key in ("citation_author", "author", "article:author"):
        authors.extend(meta.get(key, []))
    publishers.extend(meta.get("citation_publisher", []))
    site_names = meta.get("og:site_name", [])[:4]
    for key in ("citation_reference", "citation"):
        citations.extend(_url_values(meta.get(key, []), normalized_page))

    for raw in parser.jsonld:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        stack = [payload]
        visited = 0
        while stack and visited < 500:
            item = stack.pop()
            visited += 1
            if isinstance(item, list):
                stack.extend(item[:100])
                continue
            if not isinstance(item, dict):
                continue
            if "@graph" in item:
                stack.append(item["@graph"])
            authors.extend(_values(item.get("author")))
            publishers.extend(_values(item.get("publisher")))
            citations.extend(_url_values(item.get("citation"), normalized_page))
            based_on.extend(_url_values(item.get("isBasedOn"), normalized_page))
            same_as.extend(_url_values(item.get("sameAs"), normalized_page))
            published_at = published_at or item.get("datePublished") or item.get("dateCreated")
            for key in ("author", "publisher", "mainEntity", "subjectOf", "about"):
                child = item.get(key)
                if isinstance(child, (dict, list)):
                    stack.append(child)

    def clean_names(items: list[str]) -> list[str]:
        result = []
        for item in items:
            text = re.sub(r"\s+", " ", str(item)).strip()
            if text and text.casefold() not in {value.casefold() for value in result}:
                result.append(text[:200])
        return result[:12]

    return {
        "canonical_url": canonical_url,
        "authors": clean_names(authors),
        "publisher": (clean_names(publishers) or [None])[0],
        "site_name": (clean_names(site_names) or [None])[0],
        "citation_urls": list(dict.fromkeys(citations))[:20],
        "is_based_on": list(dict.fromkeys(based_on))[:20],
        "same_as": list(dict.fromkeys(same_as))[:20],
        "published_at": str(published_at)[:80] if published_at else None,
        "metadata_extraction": {
            "method": "html_meta_jsonld",
            "canonical_declared": bool(canonical_url),
            "jsonld_blocks_read": len(parser.jsonld),
            "is_ownership_verification": False,
        },
    }


def build_source_identity_candidates(
    sources: list[dict[str, Any]], *,
    existing_sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compare new sources with one another and a bounded history; never infer history-to-history."""
    candidates: dict[tuple[str, str, str], dict[str, Any]] = {}

    def prepare(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        prepared = []
        for source in records[:limit]:
            node_id = str(source.get("source_node_id") or source.get("id") or "")
            url = source.get("url") or source.get("external_ref")
            metadata = source.get("metadata") or source.get("source_metadata") or {}
            if not isinstance(metadata, dict):
                continue
            if isinstance(metadata.get("source_identity"), dict):
                metadata = {**metadata, **metadata["source_identity"]}
            if not node_id or not isinstance(url, str):
                continue
            try:
                normalized_url = normalize_source_url(url)
                canonical = normalize_source_url(metadata["canonical_url"]) if metadata.get("canonical_url") else normalized_url
            except (ValueError, TypeError):
                continue

            def normalized_set(values: Any) -> set[str]:
                result = set()
                if isinstance(values, (list, tuple, set)):
                    for value in values:
                        if isinstance(value, str):
                            try:
                                result.add(normalize_source_url(value))
                            except ValueError:
                                continue
                return result

            author_values = metadata.get("authors", [])
            prepared.append({
                "id": node_id,
                "url": normalized_url,
                "canonical": canonical,
                "authors": {item.casefold() for item in author_values if isinstance(item, str) and item.strip()}
                          if isinstance(author_values, (list, tuple, set)) else set(),
                "publisher": str(metadata.get("publisher") or "").strip().casefold(),
                "citations": normalized_set(metadata.get("citation_urls", [])),
                "based_on": normalized_set(metadata.get("is_based_on", [])),
            })
        return prepared

    current = prepare(sources, 32)
    historical = prepare(existing_sources or [], 500)

    def add(left: dict[str, Any], right: dict[str, Any], kind: str, confidence: float, basis: str) -> None:
        if left["id"] == right["id"]:
            return
        if kind in {"canonical_equivalent", "same_publisher", "same_author"} and right["id"] < left["id"]:
            left, right = right, left
        key = (left["id"], right["id"], kind)
        candidates[key] = {
            "source_node_id": left["id"],
            "related_source_node_id": right["id"],
            "candidate_type": kind,
            "confidence": confidence,
            "evidence": {
                "rule": basis,
                "candidate_only": True,
                "normalization": "source_url_v1",
                "identity_is_not_ownership_proof": True,
            },
        }

    def compare(left: dict[str, Any], right: dict[str, Any]) -> None:
        if left["id"] == right["id"]:
            return
        if left["canonical"] == right["canonical"]:
            add(left, right, "canonical_equivalent", 0.95, "canonical_url_match")
        if left["publisher"] and left["publisher"] == right["publisher"]:
            add(left, right, "same_publisher", 0.8, "exact_publisher_name_match")
        if left["authors"] & right["authors"]:
            add(left, right, "same_author", 0.7, "exact_author_name_overlap")
        if right["url"] in left["citations"] or right["canonical"] in left["citations"]:
            add(left, right, "cites", 0.9, "citation_url_matches_source")
        if right["url"] in left["based_on"] or right["canonical"] in left["based_on"]:
            add(left, right, "derived_from", 0.9, "isBasedOn_url_matches_source")
        if left["url"] in right["citations"] or left["canonical"] in right["citations"]:
            add(right, left, "cites", 0.9, "citation_url_matches_source")
        if left["url"] in right["based_on"] or left["canonical"] in right["based_on"]:
            add(right, left, "derived_from", 0.9, "isBasedOn_url_matches_source")

    for index, left in enumerate(current):
        for right in current[index + 1:]:
            compare(left, right)
        for right in historical:
            compare(left, right)
    return list(candidates.values())
