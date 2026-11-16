"""Read-only web search adapter for a configured SearXNG JSON endpoint."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit
from xml.etree import ElementTree
from urllib.request import Request, urlopen


class ResearchError(RuntimeError):
    """Raised when a search provider cannot return a safe, valid result set."""


def _validate_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("search endpoint must be an HTTP(S) URL without embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("search endpoint must not contain a query or fragment")
    return endpoint.rstrip("/")


def _safe_result_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 4096:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


class SearxngSearchAdapter:
    """Search the public web through an explicitly configured SearXNG JSON endpoint."""

    def __init__(self, endpoint: str, *, timeout: float = 15.0,
                 opener: Callable[..., Any] = urlopen) -> None:
        self.endpoint = _validate_endpoint(endpoint)
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self._opener = opener

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1-500 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        url = self.endpoint + "/search?" + urlencode({"q": query.strip(), "format": "json"})
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "MuseResearch/0.1"})
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ResearchError(f"search provider request failed: {type(exc).__name__}") from exc
        if len(raw) > 2_000_000:
            raise ResearchError("search provider response exceeded 2 MB")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchError("search provider returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ResearchError("search provider response is missing a results array")
        results: list[dict[str, Any]] = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            result_url = _safe_result_url(item.get("url"))
            title = item.get("title")
            if not result_url or not isinstance(title, str) or not title.strip():
                continue
            results.append({
                "title": title.strip()[:500], "url": result_url,
                "snippet": str(item.get("content") or "")[:2000],
                "engine": str(item.get("engine") or "")[:100],
                "published_at": str(item.get("publishedDate") or "")[:80],
            })
            if len(results) >= limit:
                break
        return results


class _DuckDuckGoHTMLParser(HTMLParser):
    """Extract result links and snippets without executing provider HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._mode: str | None = None
        self._depth = 0
        self._current: dict[str, str] | None = None
        self._snippet_target: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if self._mode is None:
            if tag == "a" and "result__a" in classes:
                self._current = {"url": values.get("href") or "", "title": "", "snippet": ""}
                self._mode = "title"
                self._depth = 1
                return
            if "result__snippet" in classes and self.results:
                self._snippet_target = self.results[-1]
                self._mode = "snippet"
                self._depth = 1
                return
        elif self._mode is not None:
            self._depth += 1

    def handle_data(self, data: str) -> None:
        if self._mode == "title" and self._current is not None:
            self._current["title"] += data
        elif self._mode == "snippet" and self._snippet_target is not None:
            self._snippet_target["snippet"] += data

    def handle_endtag(self, tag: str) -> None:
        if self._mode is None or self._depth <= 0:
            return
        self._depth -= 1
        if self._depth == 0:
            if self._mode == "title" and self._current is not None:
                self.results.append(self._current)
                self._current = None
            self._snippet_target = None
            self._mode = None


def _unwrap_duckduckgo_result_url(value: str) -> str | None:
    if value.startswith("//"):
        value = "https:" + value
    parsed = urlsplit(value)
    if parsed.hostname and parsed.hostname.casefold() in {"duckduckgo.com", "www.duckduckgo.com"}:
        if parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg", [])
            return _safe_result_url(target[0]) if target else None
    return _safe_result_url(value)


class DuckDuckGoHTMLSearchAdapter:
    """No-key public search using DuckDuckGo HTML results and unwrapped destination URLs."""

    ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(self, *, timeout: float = 15.0, opener: Callable[..., Any] = urlopen) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        self.timeout = float(timeout)
        self._opener = opener

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1-500 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        request = Request(
            self.ENDPOINT + "?" + urlencode({"q": query.strip()}),
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ResearchError("DuckDuckGo search request failed: " + type(exc).__name__) from exc
        if len(raw) > 2_000_000:
            raise ResearchError("DuckDuckGo search response exceeded 2 MB")
        parser = _DuckDuckGoHTMLParser()
        try:
            parser.feed(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            raise ResearchError("DuckDuckGo returned malformed HTML") from exc
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in parser.results:
            title = item.get("title", "").strip()
            result_url = _unwrap_duckduckgo_result_url(item.get("url", ""))
            if not title or not result_url or result_url in seen:
                continue
            seen.add(result_url)
            results.append({
                "title": title[:500], "url": result_url,
                "snippet": item.get("snippet", "").strip()[:2000],
                "engine": "duckduckgo-html", "published_at": "",
            })
            if len(results) >= limit:
                break
        return results



class CrossrefSearchAdapter:
    """Discover DOI records through Crossref; metadata is not proof of peer review or evidence."""

    ENDPOINT = "https://api.crossref.org/works"

    def __init__(self, *, timeout: float = 15.0,
                 opener: Callable[..., Any] = urlopen) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        self.timeout = float(timeout)
        self._opener = opener

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1-500 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        url = self.ENDPOINT + "?" + urlencode({
            "query.bibliographic": query.strip(), "rows": limit,
            "select": "title,DOI,published,published-print,type,publisher,abstract",
        })
        request = Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "MuseResearch/0.1 (public scholarly metadata discovery)",
        })
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ResearchError("Crossref search request failed: " + type(exc).__name__) from exc
        if len(raw) > 2_000_000:
            raise ResearchError("Crossref response exceeded 2 MB")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResearchError("Crossref returned invalid JSON") from exc
        items = payload.get("message", {}).get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise ResearchError("Crossref response is missing a valid item list")
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            doi = item.get("DOI")
            titles = item.get("title")
            title = titles[0].strip() if isinstance(titles, list) and titles and isinstance(titles[0], str) else ""
            if not title or not isinstance(doi, str) or not re.fullmatch(r"10\.\d{4,9}/[^\s]+", doi):
                continue
            canonical_doi = doi.casefold()
            if canonical_doi in seen:
                continue
            seen.add(canonical_doi)
            date_record = item.get("published") or item.get("published-print") or {}
            date_parts = date_record.get("date-parts", []) if isinstance(date_record, dict) else []
            parts = date_parts[0] if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) else []
            published_at = str(parts[0]) if parts and isinstance(parts[0], int) else ""
            abstract = item.get("abstract", "")
            if isinstance(abstract, str):
                abstract = re.sub(r"<[^>]*>", " ", abstract)
                abstract = " ".join(html.unescape(abstract).split())[:2000]
            else:
                abstract = ""
            result = {
                "title": title[:500],
                "url": "https://doi.org/" + doi,
                "snippet": abstract,
                "engine": "crossref-rest",
                "published_at": published_at[:80],
                "source_kind": "crossref_metadata_review_status_unverified",
                "publication_type": str(item.get("type", ""))[:80],
                "publisher": str(item.get("publisher", ""))[:300],
            }
            results.append(result)
            if len(results) >= limit:
                break
        return results


class ArxivSearchAdapter:
    """Search recent arXiv preprints through its public Atom API; results remain preprints."""

    ENDPOINT = "https://export.arxiv.org/api/query"

    def __init__(self, *, timeout: float = 15.0,
                 opener: Callable[..., Any] = urlopen) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        self.timeout = float(timeout)
        self._opener = opener

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1-500 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        lowered = query.casefold()
        critical = any(term in lowered for term in ("counter", "replication", "criticism", "limitations", "反证", "复现"))
        if critical:
            search_query = "all:quantum AND (all:experiment OR all:reproducibility OR all:benchmark OR all:noise)"
        elif "measurement" in lowered and "feedback" in lowered:
            search_query = "all:quantum AND all:measurement AND all:feedback"
        elif "foundations" in lowered:
            search_query = "all:quantum AND all:foundations"
        elif "information" in lowered:
            search_query = "all:quantum AND all:information"
        else:
            search_query = "all:quantum AND (all:information OR all:foundations OR all:measurement OR all:computing)"
        url = self.ENDPOINT + "?" + urlencode({
            "search_query": search_query, "start": 0, "max_results": limit,
            "sortBy": "submittedDate", "sortOrder": "descending",
        })
        request = Request(url, headers={
            "Accept": "application/atom+xml,application/xml",
            "User-Agent": "MuseResearch/0.1 (research preprint discovery)",
        })
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ResearchError("arXiv API request failed: " + type(exc).__name__) from exc
        if len(raw) > 1_000_000:
            raise ResearchError("arXiv API response exceeded 1 MB")
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise ResearchError("arXiv API returned invalid Atom XML") from exc
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        results: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", namespace):
            title = " ".join((entry.findtext("atom:title", default="", namespaces=namespace)).split())
            abstract = " ".join((entry.findtext("atom:summary", default="", namespaces=namespace)).split())
            result_url = _safe_result_url(entry.findtext("atom:id", default="", namespaces=namespace).strip())
            published = entry.findtext("atom:published", default="", namespaces=namespace).strip()
            if not title or not abstract or not result_url:
                continue
            results.append({
                "title": title[:500], "url": result_url, "snippet": abstract[:2000],
                "engine": "arxiv-api", "published_at": published[:80],
                "source_kind": "arxiv_preprint_not_peer_review_confirmation",
            })
            if len(results) >= limit:
                break
        return results


class QuantumAwareSearchAdapter:
    """Combine broad public search with recent arXiv preprints for quantum research only."""

    def __init__(self, primary: Any | None = None, arxiv: Any | None = None,
                 crossref: Any | None = None) -> None:
        self.primary = primary or DuckDuckGoHTMLSearchAdapter(timeout=15)
        self.arxiv = arxiv or ArxivSearchAdapter(timeout=15)
        self.crossref = crossref or CrossrefSearchAdapter(timeout=15)

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        errors: list[Exception] = []
        try:
            results.extend(self.primary.search(query, limit=limit))
        except Exception as exc:
            errors.append(exc)
        if not results:
            try:
                results.extend(self.crossref.search(query, limit=limit))
            except Exception as exc:
                errors.append(exc)
        if any(token in query.casefold() for token in ("quantum", "量子")):
            try:
                results.extend(self.arxiv.search(query, limit=limit))
            except Exception as exc:
                errors.append(exc)
        arxiv_metadata: dict[str, dict[str, Any]] = {}
        for item in results:
            if item.get("engine") != "arxiv-api":
                continue
            path = urlsplit(str(item.get("url", ""))).path
            match = re.search(r"/abs/(\d{4}\.\d{4,5})(?:v\d+)?$", path)
            if match:
                arxiv_metadata[match.group(1)] = item
        merged_by_url: dict[str, dict[str, Any]] = {}
        for item in results:
            url = item.get("url")
            if not isinstance(url, str):
                continue
            dedupe_key = url
            if "arxiv.org" in (urlsplit(url).hostname or "").casefold():
                item.setdefault("source_kind", "arxiv_preprint_not_peer_review_confirmation")
                path = urlsplit(url).path
                match = re.search(r"/abs/(\d{4}\.\d{4,5})(?:v\d+)?$", path)
                if match:
                    dedupe_key = "arxiv:" + match.group(1)
                if match and match.group(1) in arxiv_metadata:
                    api_item = arxiv_metadata[match.group(1)]
                    item["published_at"] = api_item.get("published_at", item.get("published_at", ""))
                    item["source_kind"] = api_item.get("source_kind", item["source_kind"])
                    item["arxiv_metadata_url"] = api_item.get("url", url)
            existing = merged_by_url.get(dedupe_key)
            if existing is None:
                merged_by_url[dedupe_key] = item
            elif item.get("engine") == "arxiv-api":
                # Prefer the canonical API record and submission date over a versionless search hit.
                merged_by_url[dedupe_key] = {**existing, **item}
        merged = list(merged_by_url.values())[:limit * 2]
        if not merged and errors:
            raise ResearchError("all configured search providers failed") from errors[0]
        return merged


class BingRSSSearchAdapter:
    """No-key Bing RSS search adapter for explicitly requested public research."""

    ENDPOINT = "https://www.bing.com/search"

    def __init__(self, *, timeout: float = 15.0,
                 opener: Callable[..., Any] = urlopen) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
            raise ValueError("timeout must be between 1 and 30 seconds")
        self.timeout = float(timeout)
        self._opener = opener

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1-500 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        url = self.ENDPOINT + "?" + urlencode({"format": "rss", "q": query.strip()})
        request = Request(
            url,
            headers={
                "Accept": "application/rss+xml,application/xml;q=0.9",
                "User-Agent": "MuseResearch/0.1",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ResearchError("Bing RSS request failed: " + type(exc).__name__) from exc
        if len(raw) > 1_000_000:
            raise ResearchError("Bing RSS response exceeded 1 MB")
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise ResearchError("Bing returned invalid RSS XML") from exc
        if root.tag.casefold() != "rss":
            raise ResearchError("Bing response is not an RSS document")
        channel = root.find("channel")
        if channel is None:
            raise ResearchError("Bing RSS response is missing a channel")
        results: list[dict[str, Any]] = []
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            result_url = _safe_result_url((item.findtext("link") or "").strip())
            if not title or not result_url:
                continue
            results.append({
                "title": title[:500],
                "url": result_url,
                "snippet": (item.findtext("description") or "")[:2000],
                "engine": "bing-rss",
                "published_at": (item.findtext("pubDate") or "")[:80],
            })
            if len(results) >= limit:
                break
        return results


class ResearchNotebook:
    """Persist search runs and source metadata; never stores credentials."""

    def __init__(self, database: str | Path) -> None:
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """CREATE TABLE IF NOT EXISTS research_runs (
                run_id TEXT PRIMARY KEY, query TEXT NOT NULL, searched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS research_sources (
                run_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
                snippet TEXT NOT NULL, engine TEXT NOT NULL, published_at TEXT NOT NULL,
                PRIMARY KEY (run_id, url),
                FOREIGN KEY (run_id) REFERENCES research_runs(run_id)
            );"""
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResearchNotebook":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save(self, query: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        searched_at = datetime.now(timezone.utc).isoformat()
        self.connection.execute("INSERT INTO research_runs VALUES (?, ?, ?)", (run_id, query, searched_at))
        self.connection.executemany(
            "INSERT INTO research_sources VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, item["url"], item["title"], item["snippet"], item["engine"], item["published_at"])
             for item in results],
        )
        self.connection.commit()
        return {"run_id": run_id, "query": query, "searched_at": searched_at,
                "result_count": len(results), "results": results}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search the web through a configured SearXNG JSON endpoint")
    parser.add_argument("query", nargs="?", help="public information to search for; do not include private data")
    parser.add_argument("--endpoint", default=os.environ.get("MUSE_SEARXNG_URL"),
                        help="SearXNG base URL; alternatively set MUSE_SEARXNG_URL")
    parser.add_argument("--db", default="data/research/muse.sqlite3")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)
    if not args.endpoint:
        parser.error("configure --endpoint or MUSE_SEARXNG_URL; no search provider is assumed")
    if not args.query:
        parser.error("a search query is required")
    try:
        results = SearxngSearchAdapter(args.endpoint).search(args.query, limit=args.limit)
        with ResearchNotebook(args.db) as notebook:
            saved = notebook.save(args.query, results)
    except (ResearchError, ValueError, OSError) as exc:
        parser.exit(2, f"Web research failed: {exc}\n")
    print(json.dumps(saved, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
