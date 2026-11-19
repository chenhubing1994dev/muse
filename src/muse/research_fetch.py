"""Bounded public-web source reader for Muse's evidence-backed research workflow.

Only public HTTP(S) pages are accepted. Redirect targets are revalidated, response
sizes are bounded, and executable/layout-only HTML content is discarded.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import socket
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies, urlopen
from muse.source_identity import extract_html_source_metadata



class SourceReadError(RuntimeError):
    """Raised when a source cannot be fetched or safely extracted."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _loopback_proxy_configured() -> bool:
    for value in getproxies().values():
        try:
            parsed = urlparse(value if "://" in value else "http://" + value)
            host = parsed.hostname or ""
            if host.casefold() == "localhost":
                return True
            if ipaddress.ip_address(host).is_loopback:
                return True
        except ValueError:
            continue
    return False


def _resolve_via_dns_over_https(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve public addresses when a loopback proxy maps all DNS names to RFC2544 test IPs."""
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for record_type, expected_type in (("A", 1), ("AAAA", 28)):
        request = Request(
            "https://dns.google/resolve?" + urlencode({"name": host, "type": record_type}),
            headers={"Accept": "application/dns-json", "User-Agent": "MuseResearch/0.2"},
        )
        with urlopen(request, timeout=3.0) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise SourceReadError("public DNS verification response exceeded 64 KiB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("Status") not in {0, 3}:
            raise SourceReadError("public DNS verification returned an invalid response")
        for answer in payload.get("Answer", []):
            if not isinstance(answer, dict) or answer.get("type") != expected_type:
                continue
            try:
                addresses.append(ipaddress.ip_address(answer.get("data", "")))
            except ValueError:
                continue
    return addresses


def _resolve_public_addresses(
    host: str, port: int, resolver: Callable[..., Any]
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    records = resolver(host, port, type=socket.SOCK_STREAM)
    addresses = [ipaddress.ip_address(record[4][0]) for record in records]
    synthetic_dns_range = ipaddress.ip_network("198.18.0.0/15")
    if (addresses and all(address in synthetic_dns_range for address in addresses)
            and _loopback_proxy_configured()):
        addresses = _resolve_via_dns_over_https(host, port)
    return addresses


def _validate_public_url(url: str, resolver: Callable[..., Any] = socket.getaddrinfo) -> None:
    if not isinstance(url, str) or len(url) > 4096:
        raise SourceReadError("source URL is invalid")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SourceReadError("only credential-free HTTP(S) source URLs are allowed")
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise SourceReadError("local and private hostnames are not allowed")
    try:
        try:
            literal = ipaddress.ip_address(host)
            addresses = [literal]
        except ValueError:
            addresses = _resolve_public_addresses(
                host, parsed.port or (443 if parsed.scheme == "https" else 80), resolver
            )
    except (OSError, ValueError, TimeoutError) as exc:
        raise SourceReadError("source hostname could not be resolved safely") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise SourceReadError("source resolves to a private, loopback, link-local or reserved address")


class _RedirectGuard(HTTPRedirectHandler):
    def __init__(self, resolver: Callable[..., Any]) -> None:
        super().__init__()
        self.resolver = resolver

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request:
        _validate_public_url(newurl, self.resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _ReadableText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "canvas", "nav", "footer", "header"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.skip_depth += 1
        elif not self.skip_depth and tag in {"p", "div", "article", "section", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in {"p", "div", "article", "section", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)


class PublicSourceReader:
    def __init__(self, *, timeout: float = 12.0, resolver: Callable[..., Any] = socket.getaddrinfo,
                 opener: Any = None) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.resolver = resolver
        self.opener = opener or build_opener(_RedirectGuard(resolver)).open

    def read(self, url: str, *, max_bytes: int = 1_000_000, max_chars: int = 20_000) -> dict[str, Any]:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1024 <= max_bytes <= 2_000_000:
            raise ValueError("max_bytes must be between 1024 and 2000000")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 100 <= max_chars <= 100_000:
            raise ValueError("max_chars must be between 100 and 100000")
        _validate_public_url(url, self.resolver)
        request = Request(url, headers={
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            "User-Agent": "MuseResearch/0.2",
        })
        try:
            with self.opener(request, timeout=self.timeout) as response:
                content_type = str(response.headers.get("Content-Type", "")).casefold()
                status_code = response.getcode() if callable(getattr(response, "getcode", None)) else getattr(response, "status", 200)
                etag = response.headers.get("ETag")
                last_modified = response.headers.get("Last-Modified")
                if not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain")):
                    raise SourceReadError("source is not a readable text or HTML document")
                raw = response.read(max_bytes + 1)
                final_url = response.geturl() if callable(getattr(response, "geturl", None)) else url
                _validate_public_url(final_url, self.resolver)
        except SourceReadError:
            raise
        except HTTPError as exc:
            raise SourceReadError(
                "source request failed: HTTPError", status_code=exc.code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SourceReadError("source request failed: " + type(exc).__name__) from exc
        if len(raw) > max_bytes:
            raise SourceReadError("source exceeded the configured byte limit")
        decoded = raw.decode("utf-8", errors="replace")
        source_metadata: dict[str, Any] = {}
        if "html" in content_type or "xhtml" in content_type:
            source_metadata = extract_html_source_metadata(decoded, final_url)
            parser = _ReadableText()
            parser.feed(decoded)
            decoded = " ".join("".join(parser.parts).split())
        else:
            decoded = " ".join(decoded.split())
        text = decoded[:max_chars]
        return {
            "url": final_url,
            "content_type": content_type[:120],
            "source_metadata": source_metadata,
            "text": text,
            "truncated": len(decoded) > len(text),
            "bytes_read": len(raw),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "status_code": status_code,
            "etag": str(etag)[:256] if etag is not None else None,
            "last_modified": str(last_modified)[:256] if last_modified is not None else None,
        }

class ResearchEvidenceStore:
    """Persist bounded source extracts and provenance without executing page content."""

    def __init__(self, database: str | Path) -> None:
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS research_evidence (
            url TEXT PRIMARY KEY, content_type TEXT NOT NULL, text TEXT NOT NULL,
            sha256 TEXT NOT NULL, fetched_at TEXT NOT NULL, truncated INTEGER NOT NULL
        )""")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS research_attributions (
            url TEXT NOT NULL, query TEXT NOT NULL, title TEXT NOT NULL,
            snippet TEXT NOT NULL, engine TEXT NOT NULL, published_at TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            relevance_score INTEGER NOT NULL DEFAULT 0 CHECK(relevance_score BETWEEN 0 AND 100),
            relevance_method TEXT NOT NULL DEFAULT 'unscored',
            PRIMARY KEY (url, query)
        )""")
        attribution_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(research_attributions)")
        }
        if "relevance_score" not in attribution_columns:
            self.connection.execute(
                "ALTER TABLE research_attributions ADD COLUMN relevance_score INTEGER NOT NULL DEFAULT 0"
            )
        if "relevance_method" not in attribution_columns:
            self.connection.execute(
                "ALTER TABLE research_attributions ADD COLUMN relevance_method TEXT NOT NULL DEFAULT 'unscored'"
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ResearchEvidenceStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save(self, page: dict[str, Any]) -> None:
        if not isinstance(page, dict) or not isinstance(page.get("url"), str) or not isinstance(page.get("text"), str):
            raise ValueError("invalid research page record")
        _validate_public_url(page["url"])
        if len(page["text"]) > 100_000:
            raise ValueError("research extract exceeds storage limit")
        digest = hashlib.sha256(page["text"].encode("utf-8")).hexdigest()
        if page.get("sha256") != digest:
            raise ValueError("research extract hash mismatch")
        self.connection.execute(
            """INSERT INTO research_evidence VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET content_type=excluded.content_type,
               text=excluded.text, sha256=excluded.sha256, fetched_at=excluded.fetched_at,
               truncated=excluded.truncated""",
            (page["url"], str(page.get("content_type", ""))[:120], page["text"],
             digest, str(page.get("fetched_at", ""))[:80], int(bool(page.get("truncated")))),
        )
        self.connection.commit()

    def save_attribution(self, source: dict[str, Any]) -> None:
        """Persist the search query and result metadata independently of page fetching."""
        if not isinstance(source, dict):
            raise ValueError("invalid research attribution")
        url, query, title = source.get("url"), source.get("query"), source.get("title")
        if not all(isinstance(value, str) and value.strip() for value in (url, query, title)):
            raise ValueError("research attribution requires URL, query and title")
        if len(query) > 500 or len(title) > 500:
            raise ValueError("research attribution query/title exceeds its limit")
        _validate_public_url(url)
        snippet = source.get("snippet", "")
        engine = source.get("engine", "")
        published_at = source.get("published_at", "")
        if not all(isinstance(value, str) for value in (snippet, engine, published_at)):
            raise ValueError("research attribution metadata must be text")
        if len(snippet) > 2000 or len(engine) > 100 or len(published_at) > 80:
            raise ValueError("research attribution metadata exceeds its limit")
        relevance_score = source.get("relevance_score", 0)
        relevance_method = source.get("relevance_method", "unscored")
        if isinstance(relevance_score, bool) or not isinstance(relevance_score, int) or not 0 <= relevance_score <= 100:
            raise ValueError("relevance_score must be an integer between 0 and 100")
        if not isinstance(relevance_method, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", relevance_method):
            raise ValueError("relevance_method must be a bounded lowercase identifier")
        discovered_at = datetime.now(timezone.utc).isoformat()
        self.connection.execute(
            """INSERT INTO research_attributions
               (url, query, title, snippet, engine, published_at, discovered_at, relevance_score, relevance_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(url, query) DO UPDATE SET title=excluded.title,
               snippet=excluded.snippet, engine=excluded.engine,
               published_at=excluded.published_at, discovered_at=excluded.discovered_at,
               relevance_score=excluded.relevance_score, relevance_method=excluded.relevance_method""",
            (url, query.strip(), title.strip(), snippet, engine, published_at, discovered_at,
             relevance_score, relevance_method),
        )
        self.connection.commit()

    def list_recent_attributions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self.connection.execute(
            "SELECT url, query, title, snippet, engine, published_at, discovered_at, "
            "relevance_score, relevance_method FROM research_attributions "
            "ORDER BY discovered_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def audit_integrity(self, *, limit: int = 100) -> dict[str, Any]:
        """Recompute stored extract hashes without returning source bodies or changing rows."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self.connection.execute(
            "SELECT url,sha256,text,fetched_at,truncated FROM research_evidence "
            "ORDER BY fetched_at DESC,url LIMIT ?", (limit + 1,)
        ).fetchall()
        has_more = len(rows) > limit
        counts = {"intact": 0, "hash_mismatch": 0, "invalid_hash": 0}
        items = []
        for row in rows[:limit]:
            stored_hash = str(row["sha256"] or "")
            actual_hash = hashlib.sha256(str(row["text"]).encode("utf-8")).hexdigest()
            if not re.fullmatch(r"[a-f0-9]{64}", stored_hash):
                status = "invalid_hash"
            elif actual_hash != stored_hash:
                status = "hash_mismatch"
            else:
                status = "intact"
            counts[status] += 1
            items.append({
                "url": row["url"], "status": status, "stored_sha256": stored_hash or None,
                "actual_sha256": actual_hash, "chars": len(row["text"]),
                "fetched_at": row["fetched_at"], "truncated": bool(row["truncated"]),
            })
        return {
            "read_only": True, "limit": limit, "scanned": len(items), "has_more": has_more,
            **counts, "items": items, "source_bodies_returned": 0,
            "writes": 0, "deletes": 0, "repairs": 0,
        }

    @classmethod
    def audit_database(cls, database: str | Path, *, limit: int = 100) -> dict[str, Any]:
        """Open an existing evidence database in SQLite read-only mode for audit."""
        path = Path(database).expanduser()
        if not path.is_file():
            raise FileNotFoundError("research evidence database does not exist")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        instance = object.__new__(cls)
        instance.connection = connection
        try:
            return instance.audit_integrity(limit=limit)
        finally:
            connection.close()

    def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self.connection.execute(
            "SELECT url, content_type, sha256, fetched_at, truncated, length(text) AS chars "
            "FROM research_evidence ORDER BY fetched_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read and persist one bounded public web source")
    parser.add_argument("url")
    parser.add_argument("--db", default="data/research/evidence.sqlite3")
    parser.add_argument("--max-chars", type=int, default=20000)
    args = parser.parse_args()
    try:
        page = PublicSourceReader().read(args.url, max_chars=args.max_chars)
        with ResearchEvidenceStore(args.db) as store:
            store.save(page)
    except (SourceReadError, ValueError, OSError) as exc:
        parser.exit(2, "Source research failed: " + str(exc) + "\n")
    print(json.dumps({key: value for key, value in page.items() if key != "text"} | {
        "text_preview": page["text"][:1200], "stored": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
