"""Bounded async public-source collectors for Muse Global Opportunity Radar.

Collector structure, source-specific parsing, RSS/Atom handling, GitHub README harvesting
and community JSON normalization are independently reimplemented from
ZiChuanLan/freebie-radar (MIT, copyright 2026 ZiChuanLan).

Verified Muse fetch contract: PublicSourceReader.read(url, *, max_bytes=..., max_chars=...)
is synchronous and returns a mapping containing url/text/status_code/etag/last_modified.
It currently has no conditional-request or JSON method. ConditionalResearchReader and
PublicJSONReader below are explicit extension Protocols, not claimed Muse imports; until
a reviewed adapter is injected, conditional GET is best-effort TTL caching and JSON
collectors fail closed. The existing reader remains the only default HTML/text transport.
"""
from __future__ import annotations

import asyncio
import io
import math
import threading
import hashlib
import json
import random
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from muse.research_fetch import PublicSourceReader, SourceReadError

from .models import RadarConfig, utc_now, validate_radar_database_path
from .sanitizer import safe_url_log_identity
from .sanitizer import sanitize_external_payload, sanitize_external_text, validate_public_url


@dataclass(frozen=True, slots=True)
class CollectedOpportunity:
    source_id: str
    source_type: str
    external_id: str
    url: str
    title: str
    summary: str
    content: str
    author: str
    published_at: str | None
    expires_at: str | None
    license_text: str
    evidence_url: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    def __post_init__(self) -> None:
        limits = {
            "source_id": 160, "source_type": 80, "external_id": 500,
            "title": 500, "summary": 4000, "content": 6000,
            "author": 200, "license_text": 5000,
        }
        for name, maximum in limits.items():
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be text")
            normalized = value.strip()
            if name in {"source_id", "source_type", "external_id", "title"} and not normalized:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, normalized[:maximum])
        object.__setattr__(self, "url", validate_public_url(self.url))
        origin = self.evidence_url or self.url
        object.__setattr__(self, "evidence_url", validate_public_url(origin))
        for name in ("published_at", "expires_at"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be an ISO-8601 string or None")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        try:
            encoded = json.dumps(dict(self.metadata), ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > 32_768:
                raise ValueError("metadata exceeds the 32 KiB limit")
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be bounded JSON-serializable data") from exc
        object.__setattr__(self, "metadata", dict(self.metadata))



    def to_mapping(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "source_type": self.source_type,
            "external_id": self.external_id, "url": self.url, "evidence_url": self.evidence_url,
            "title": self.title,
            "summary": self.summary, "content": self.content, "author": self.author,
            "published_at": self.published_at, "expires_at": self.expires_at,
            "license_text": self.license_text, "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CollectResult:
    items: tuple[CollectedOpportunity, ...]
    source_id: str
    fetched_at: str
    not_modified: bool = False
    degraded: tuple[str, ...] = ()


class CollectorError(RuntimeError):
    """A source could not be collected under the configured safety policy."""


class RobotsDenied(CollectorError):
    """robots.txt or source policy does not permit this fetch."""


class CollectorConfigurationError(CollectorError):
    """A collector is missing a required, explicitly authorized transport."""


@runtime_checkable
class ResearchReader(Protocol):
    def read(self, url: str, *, max_bytes: int = 1_000_000, max_chars: int = 20_000) -> dict[str, Any]:
        """Matches the verified synchronous PublicSourceReader.read signature."""
        raise NotImplementedError("ResearchReader adapters must implement read")

@runtime_checkable
class ConditionalResearchReader(Protocol):
    def read_conditional(
        self, url: str, *, etag: str | None, last_modified: str | None,
        max_bytes: int, max_chars: int,
    ) -> Mapping[str, Any]:
        """Optional extension: return status_code=304 on a validated not-modified response."""
        raise NotImplementedError("ConditionalResearchReader adapters must implement read_conditional")

@runtime_checkable
class PublicJSONReader(Protocol):
    def read_json(
        self, url: str, *, headers: Mapping[str, str], max_bytes: int, timeout: float,
    ) -> Mapping[str, Any]:
        """Optional transport; must repeat Muse SSRF, redirect and response-size checks."""
        raise NotImplementedError("PublicJSONReader adapters must implement read_json")

@runtime_checkable
class ConditionalPublicJSONReader(Protocol):
    def read_json_conditional(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        etag: str | None,
        last_modified: str | None,
        max_bytes: int,
        timeout: float,
    ) -> Mapping[str, Any]:
        """Optional conditional JSON transport; must validate final URL and response bounds."""
        raise NotImplementedError("ConditionalPublicJSONReader adapters must implement read_json_conditional")


class RobotsPolicy:
    """Cached robots.txt policy; failures other than a real 404 fail closed."""

    def __init__(self, reader: ResearchReader, *, user_agent: str = "MuseResearch/0.2") -> None:
        self.reader = reader
        self.user_agent = user_agent
        self._cache: dict[str, tuple[float, RobotFileParser | None, bool]] = {}
        self._lock = asyncio.Lock()
        self._ttl_seconds = 1800.0

    async def allowed(self, url: str) -> bool:
        parsed = urlsplit(validate_public_url(url))
        host = parsed.hostname
        if not host:
            return False
        origin = f"{parsed.scheme}://{host}" + (f":{parsed.port}" if parsed.port else "")
        async with self._lock:
            cached = self._cache.get(origin)
            if cached and time.monotonic() - cached[0] < self._ttl_seconds:
                parser, allow = cached[1], cached[2]
                return allow if parser is None else parser.can_fetch(self.user_agent, url)
            robots_url = origin + "/robots.txt"
            parser = RobotFileParser(robots_url)
            try:
                result = await asyncio.to_thread(
                    self.reader.read, robots_url, max_bytes=65_536, max_chars=65_536
                )
                status = int(result.get("status_code", 200))
                if status == 404:
                    self._cache[origin] = (time.monotonic(), None, True)
                    return True
                if status < 200 or status >= 300:
                    self._cache[origin] = (time.monotonic(), None, False)
                    return False
                body = result.get("text")
                if not isinstance(body, str) or len(body) > 65_536 or re.search(r"<!DOCTYPE|<!ENTITY", body, re.IGNORECASE):
                    self._cache[origin] = (time.monotonic(), None, False)
                    return False
                parser.parse(body.splitlines())
                self._cache[origin] = (time.monotonic(), parser, True)
                return parser.can_fetch(self.user_agent, url)
            except SourceReadError as exc:
                allow = exc.status_code == 404
                self._cache[origin] = (time.monotonic(), None, allow)
                return allow
            except (OSError, ValueError, TypeError, TimeoutError):
                self._cache[origin] = (time.monotonic(), None, False)
                return False


@dataclass(frozen=True, slots=True)
class _CachedFetch:
    url: str
    text: str
    etag: str | None
    last_modified: str | None
    fetched_at: str
    content_type: str
    sha256: str


class UnsupportedFetchCacheSchemaVersion(RuntimeError):
    """The fetch cache was created by a newer application version."""


class FetchCache:
    """Versioned SQLite cache; corruption degrades to no-cache, never to stale trust."""

    SCHEMA_VERSION = 1

    def __init__(self, database: str | Path) -> None:
        self.path = validate_radar_database_path(database)
        self.connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._thread_lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS radar_fetch_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            current = connection.execute(
                "SELECT value FROM radar_fetch_meta WHERE key='schema_version'"
            ).fetchone()
            try:
                version = int(current["value"]) if current is not None else 0
            except (TypeError, ValueError) as exc:
                raise sqlite3.DatabaseError("fetch cache schema version is invalid") from exc
            if version > self.SCHEMA_VERSION:
                raise UnsupportedFetchCacheSchemaVersion(
                    f"Fetch cache schema {version} is newer than supported schema {self.SCHEMA_VERSION}"
                )
            migrations = {0: self._migrate_0_to_1}
            while version < self.SCHEMA_VERSION:
                migration = migrations.get(version)
                if migration is None:
                    raise sqlite3.DatabaseError(f"missing fetch cache migration from schema {version}")
                migration(connection)
                version += 1
                connection.execute(
                    "INSERT INTO radar_fetch_meta(key,value) VALUES('schema_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version),)
                )
            connection.commit()
            self.connection = connection
        except UnsupportedFetchCacheSchemaVersion:
            if "connection" in locals():
                connection.rollback()
                connection.close()
            raise
        except (sqlite3.DatabaseError, OSError):
            if "connection" in locals():
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            self.connection = None

    @staticmethod
    def _migrate_0_to_1(connection: sqlite3.Connection) -> None:
        connection.execute("""CREATE TABLE IF NOT EXISTS radar_fetch_cache (
            url TEXT PRIMARY KEY, text_body TEXT NOT NULL, etag TEXT,
            last_modified TEXT, fetched_at TEXT NOT NULL,
            content_type TEXT NOT NULL, sha256 TEXT NOT NULL
        )""")

    def _disable(self) -> None:
        with self._thread_lock:
            connection, self.connection = self.connection, None
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass

    async def get(self, url: str) -> _CachedFetch | None:
        async with self._lock:
            with self._thread_lock:
                if self.connection is None:
                    return None
                try:
                    row = self.connection.execute(
                        "SELECT * FROM radar_fetch_cache WHERE url=?", (url,)
                    ).fetchone()
                    if row is None:
                        return None
                    item = _CachedFetch(*row)
                    digest = hashlib.sha256(item.text.encode("utf-8")).hexdigest()
                    if digest != item.sha256:
                        self._disable()
                        return None
                    return item
                except sqlite3.DatabaseError:
                    self._disable()
                    return None

    async def put(self, item: _CachedFetch) -> None:
        async with self._lock:
            with self._thread_lock:
                if self.connection is None:
                    return
                try:
                    self.connection.execute("BEGIN IMMEDIATE")
                    self.connection.execute(
                        """INSERT INTO radar_fetch_cache
                           (url,text_body,etag,last_modified,fetched_at,content_type,sha256)
                           VALUES(?,?,?,?,?,?,?)
                           ON CONFLICT(url) DO UPDATE SET
                           text_body=excluded.text_body,etag=excluded.etag,
                           last_modified=excluded.last_modified,fetched_at=excluded.fetched_at,
                           content_type=excluded.content_type,sha256=excluded.sha256""",
                        (item.url, item.text, item.etag, item.last_modified, item.fetched_at, item.content_type, item.sha256),
                    )
                    self.connection.commit()
                except sqlite3.DatabaseError:
                    try:
                        self.connection.rollback()
                    except sqlite3.Error:
                        pass
                    self._disable()

    def close(self) -> None:
        self._disable()

def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON response contains duplicate object keys")
        result[key] = value
    return result


def _validate_json_payload(value: Any, *, max_depth: int = 16, max_nodes: int = 10_000) -> None:
    """Bound already-decoded JSON trees before any source-specific traversal."""
    if not isinstance(value, (dict, list)):
        raise CollectorError("JSON source root must be an object or array")
    stack: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if visited > max_nodes:
            raise CollectorError("JSON response exceeds the maximum node count")
        if depth > max_depth:
            raise CollectorError("JSON response exceeds the maximum nesting depth")
        if isinstance(current, dict):
            if len(current) > max_nodes:
                raise CollectorError("JSON object exceeds the maximum member count")
            for key, child in current.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise CollectorError("JSON object keys must be bounded strings")
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            if len(current) > max_nodes:
                raise CollectorError("JSON array exceeds the maximum item count")
            stack.extend((child, depth + 1) for child in current)
        elif current is None or isinstance(current, (str, bool, int)):
            if isinstance(current, str) and len(current) > 100_000:
                raise CollectorError("JSON string value exceeds the maximum length")
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise CollectorError("JSON numeric values must be finite")
        else:
            raise CollectorError("JSON response contains a non-JSON value")


def _validated_json_metadata(response: Mapping[str, Any], target: str) -> tuple[int, str, str | None, str | None, str, str]:
    status = response.get("status_code", 200)
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise CollectorError("JSON reader returned an invalid HTTP status")
    final_url = validate_public_url(response.get("url", target))
    content_type = response.get("content_type", "application/json")
    if not isinstance(content_type, str) or len(content_type) > 120:
        raise CollectorError("JSON reader returned invalid Content-Type metadata")
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise CollectorError("JSON reader did not return an application/json media type")
    etag = response.get("etag")
    last_modified = response.get("last_modified")
    if etag is not None and (not isinstance(etag, str) or len(etag) > 256):
        raise CollectorError("JSON reader returned invalid ETag metadata")
    if last_modified is not None and (not isinstance(last_modified, str) or len(last_modified) > 256):
        raise CollectorError("JSON reader returned invalid Last-Modified metadata")
    fetched_at = response.get("fetched_at") or utc_now().isoformat()
    if not isinstance(fetched_at, str) or len(fetched_at) > 80:
        raise CollectorError("JSON reader returned invalid fetched_at metadata")
    try:
        stamp = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CollectorError("JSON reader returned invalid fetched_at timestamp") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise CollectorError("JSON reader fetched_at must include a timezone")
    return status, final_url, etag, last_modified, content_type, stamp.astimezone(timezone.utc).isoformat()


class AsyncCollector:
    """Shared bounded fetch, robots, host rate limit, retry and cache implementation."""

    source_type = "unknown"

    def __init__(
        self, *, reader: ResearchReader, config: RadarConfig,
        cache: FetchCache | None = None, robots: RobotsPolicy | None = None,
        json_reader: PublicJSONReader | None = None,
    ) -> None:
        if not isinstance(config, RadarConfig):
            raise TypeError("config must be a RadarConfig")
        if not isinstance(reader, ResearchReader):
            raise TypeError("reader must implement the Muse PublicSourceReader contract")
        if json_reader is not None and not isinstance(json_reader, PublicJSONReader):
            raise TypeError("json_reader must implement read_json")
        self.reader = reader
        self.config = config
        self.cache = cache
        self.robots = robots or RobotsPolicy(reader)
        self.json_reader = json_reader
        self._host_locks: dict[str, asyncio.Semaphore] = {}
        self._host_rate_locks: dict[str, asyncio.Lock] = {}
        self._host_last_request: dict[str, float] = {}

    def _cache(self) -> FetchCache:
        if self.cache is None:
            self.cache = FetchCache(self.config.database_path.with_name("fetch_cache.sqlite3"))
        return self.cache

    async def _throttle(self, host: str) -> None:
        lock = self._host_rate_locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._host_last_request.get(host, 0.0)
            delay = self.config.min_host_gap_seconds - (time.monotonic() - last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._host_last_request[host] = time.monotonic()

    async def fetch_text(self, url: str) -> Mapping[str, Any]:
        if not self.config.enabled:
            raise CollectorError("Radar is disabled by MUSE_RADAR_ENABLED")
        target = validate_public_url(url)
        host = urlsplit(target).hostname or ""
        if not host:
            raise CollectorError("source URL has no hostname")
        if not await self.robots.allowed(target):
            raise RobotsDenied(f"robots.txt or source policy disallows {host}")
        cache = self._cache()
        cached = await cache.get(target)
        now = utc_now()
        if cached is not None:
            try:
                age = (now - datetime.fromisoformat(cached.fetched_at.replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                age = self.config.cache_ttl_seconds + 1
            if age < self.config.cache_ttl_seconds:
                return {
                    "url": cached.url, "text": cached.text, "etag": cached.etag,
                    "last_modified": cached.last_modified, "fetched_at": cached.fetched_at,
                    "content_type": cached.content_type, "sha256": cached.sha256,
                    "status_code": 200, "cache_hit": True,
                }
        semaphore = self._host_locks.setdefault(host, asyncio.Semaphore(self.config.host_concurrency))
        async with semaphore:
            last_error: Exception | None = None
            for attempt in range(self.config.max_retries + 1):
                await self._throttle(host)
                try:
                    conditional = getattr(self.reader, "read_conditional", None)
                    if cached is not None and callable(conditional):
                        result = await asyncio.to_thread(
                            conditional, target, etag=cached.etag, last_modified=cached.last_modified,
                            max_bytes=self.config.max_response_bytes, max_chars=self.config.max_text_chars,
                        )
                    else:
                        result = await asyncio.to_thread(
                            self.reader.read, target, max_bytes=self.config.max_response_bytes,
                            max_chars=self.config.max_text_chars,
                        )
                    if not isinstance(result, Mapping):
                        raise CollectorError("research reader returned a non-mapping response")
                    status = int(result.get("status_code", 200))
                    if status == 304 and cached is not None:
                        return {
                            "url": cached.url, "text": cached.text, "etag": cached.etag,
                            "last_modified": cached.last_modified, "fetched_at": cached.fetched_at,
                            "content_type": cached.content_type, "sha256": cached.sha256,
                            "status_code": 304, "not_modified": True,
                        }
                    if status == 429 or status in {500, 502, 503, 504}:
                        raise SourceReadError("source temporarily unavailable", status_code=status)
                    if status < 200 or status >= 300:
                        raise CollectorError(f"source returned HTTP {status}")
                    body = result.get("text")
                    if not isinstance(body, str):
                        raise CollectorError("research reader response has no text body")
                    body = body[: self.config.max_text_chars]
                    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                    page = _CachedFetch(
                        target, body,
                        str(result["etag"])[:256] if result.get("etag") else None,
                        str(result["last_modified"])[:256] if result.get("last_modified") else None,
                        str(result.get("fetched_at") or now.isoformat()),
                        str(result.get("content_type") or "")[:120], digest,
                    )
                    await cache.put(page)
                    return {**dict(result), "url": target, "text": body, "sha256": digest, "cache_hit": False}
                except SourceReadError as exc:
                    last_error = exc
                    retryable = exc.status_code == 429 or exc.status_code in {500, 502, 503, 504}
                    if not retryable or attempt >= self.config.max_retries:
                        raise
                except (TimeoutError, OSError) as exc:
                    last_error = exc
                    if attempt >= self.config.max_retries:
                        raise
                if attempt < self.config.max_retries:
                    backoff = min(30.0, self.config.retry_base_seconds * (2 ** attempt))
                    await asyncio.sleep(backoff + random.uniform(0.0, backoff * 0.2))
            raise CollectorError("source fetch exhausted retries") from last_error

    async def fetch_json(self, url: str, *, headers: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        if not self.config.enabled:
            raise CollectorError("Radar is disabled by MUSE_RADAR_ENABLED")
        target = validate_public_url(url)
        if self.json_reader is None:
            raise CollectorConfigurationError(
                "JSON source requires an injected PublicJSONReader with Muse-equivalent SSRF and redirect validation"
            )
        host = urlsplit(target).hostname or ""
        if not host or not await self.robots.allowed(target):
            raise RobotsDenied("JSON source is disallowed by robots.txt or source policy")
        cache = self._cache()
        cached = await cache.get(target)
        now = utc_now()
        if cached is not None:
            try:
                age = (now - datetime.fromisoformat(cached.fetched_at.replace("Z", "+00:00"))).total_seconds()
            except ValueError:
                age = self.config.cache_ttl_seconds + 1
            if age < self.config.cache_ttl_seconds:
                try:
                    cached_payload = json.loads(cached.text)
                except json.JSONDecodeError:
                    cached = None
                else:
                    return {
                        "status_code": 200, "data": cached_payload, "url": target,
                        "etag": cached.etag, "last_modified": cached.last_modified,
                        "cache_hit": True, "sha256": cached.sha256,
                    }
        semaphore = self._host_locks.setdefault(host, asyncio.Semaphore(self.config.host_concurrency))
        async with semaphore:
            last_error: Exception | None = None
            safe_headers = dict(headers or {})
            if len(safe_headers) > 32 or any(
                not isinstance(key, str) or not isinstance(value, str)
                or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}", key)
                or len(value) > 1024 or "\r" in value or "\n" in value or "\x00" in value
                for key, value in safe_headers.items()
            ):
                raise CollectorError("JSON request headers are invalid or exceed configured limits")
            for attempt in range(self.config.max_retries + 1):
                await self._throttle(host)
                try:
                    conditional = getattr(self.json_reader, "read_json_conditional", None)
                    if cached is not None and callable(conditional):
                        response = await asyncio.to_thread(
                            conditional, target, headers=safe_headers, etag=cached.etag,
                            last_modified=cached.last_modified, max_bytes=self.config.max_response_bytes,
                            timeout=self.config.request_timeout_seconds,
                        )
                    else:
                        response = await asyncio.to_thread(
                            self.json_reader.read_json, target, headers=safe_headers,
                            max_bytes=self.config.max_response_bytes, timeout=self.config.request_timeout_seconds,
                        )
                    if not isinstance(response, Mapping):
                        raise CollectorError("JSON reader returned a non-mapping response")
                    status, final_url, etag, last_modified, content_type, fetched_at = _validated_json_metadata(response, target)
                    if status == 304 and cached is not None:
                        cached_payload = json.loads(cached.text, object_pairs_hook=_json_object_no_duplicates)
                        _validate_json_payload(cached_payload)
                        return {
                            "status_code": 304, "data": cached_payload, "url": final_url,
                            "etag": cached.etag, "last_modified": cached.last_modified,
                            "not_modified": True, "cache_hit": True,
                        }
                    if status == 429 or status in {500, 502, 503, 504}:
                        raise SourceReadError("JSON source temporarily unavailable", status_code=status)
                    if status < 200 or status >= 300:
                        raise CollectorError(f"JSON source returned HTTP {status}")
                    payload = response.get("data")
                    if payload is None:
                        raw_body = response.get("body", "")
                        if isinstance(raw_body, bytes):
                            if len(raw_body) > self.config.max_response_bytes:
                                raise CollectorError("JSON response exceeds configured byte limit")
                            raw_body = raw_body.decode("utf-8", errors="strict")
                        if not isinstance(raw_body, str) or len(raw_body.encode("utf-8")) > self.config.max_response_bytes:
                            raise CollectorError("JSON response body is invalid or exceeds configured byte limit")
                        payload = json.loads(raw_body, object_pairs_hook=_json_object_no_duplicates)
                    _validate_json_payload(payload)
                    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    encoded = serialized.encode("utf-8")
                    if len(encoded) > self.config.max_response_bytes:
                        raise CollectorError("JSON response exceeds configured byte limit after decoding")
                    page = _CachedFetch(
                        final_url, serialized, etag, last_modified, fetched_at, content_type,
                        hashlib.sha256(encoded).hexdigest(),
                    )
                    await cache.put(page)
                    return {
                        "status_code": status, "data": payload, "url": final_url,
                        "etag": page.etag, "last_modified": page.last_modified,
                        "cache_hit": False, "sha256": page.sha256,
                    }
                except SourceReadError as exc:
                    last_error = exc
                    retryable = exc.status_code == 429 or exc.status_code in {500, 502, 503, 504}
                    if not retryable or attempt >= self.config.max_retries:
                        raise
                except (TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                    last_error = exc
                    if attempt >= self.config.max_retries:
                        raise
                if attempt < self.config.max_retries:
                    backoff = min(30.0, self.config.retry_base_seconds * (2 ** attempt))
                    await asyncio.sleep(backoff + random.uniform(0.0, backoff * 0.2))
            raise CollectorError("JSON source fetch exhausted retries") from last_error


    async def collect(self, source_id: str, settings: Mapping[str, Any] | None = None) -> CollectResult:
        if not self.config.enabled:
            return CollectResult((), source_id, utc_now().isoformat(), degraded=("radar_disabled",))
        raise CollectorError("Concrete collector must implement collect")

_GITHUB_SECTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"generative ai|apis, data, and ml|ai|llm|chatbot|image|video|audio|writing|agent", re.IGNORECASE), "ai"),
    (re.compile(r"domain|dns|web hosting", re.IGNORECASE), "domain"),
    (re.compile(r"education", re.IGNORECASE), "edu"),
    (re.compile(r"source code|artifact|code generation|code quality|code search|ci and cd|testing|issue tracking|management system|low-code|forms|cms|translation|feature toggle|crash|monitoring|log management|messaging|security|authentication|mobile app|tools for teams|tunneling|font|email|search", re.IGNORECASE), "software"),
    (re.compile(r"cloud|paas|baas|iaas|cdn|storage|managed data", re.IGNORECASE), "cloud"),
)


def _github_section_category(section: str) -> str:
    for pattern, category in _GITHUB_SECTION_RULES:
        if pattern.search(section):
            return category
    return "cloud"


def _parse_github_markdown_catalog(markdown: str, limit: int) -> list[dict[str, str]]:
    """Extract link entries and their nearest section from free-for-dev-style Markdown."""
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    section = ""
    bullet = re.compile(r"^\s*[-*+]\s+\[(.+?)\]\((https?://[^)\s]+)\)\s*[-–—:]?\s*(.*)$")
    table = re.compile(r"^\|\s*\[(.+?)\]\((https?://[^)\s]+)\)\s*\|\s*([^|]*)")
    for line in markdown.splitlines():
        heading = re.match(r"^#{2,3}\s+(.+)$", line)
        if heading:
            section = heading.group(1).strip()
            continue
        if len(output) >= limit:
            break
        match = bullet.search(line) or table.search(line)
        if not match:
            continue
        title = re.sub(r"\*\*", "", match.group(1)).strip()
        url = match.group(2).strip()
        key = url.casefold().rstrip("/")
        if not title or key in seen or re.search(r"shields\.io|badge", url, re.IGNORECASE):
            continue
        seen.add(key)
        description = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", match.group(3)).strip()
        output.append({
            "title": title[:500],
            "url": url[:4096],
            "description": description[:2000],
            "section": section[:200],
            "category_hint": _github_section_category(section),
        })
    return output


class GitHubRepositoryCollector(AsyncCollector):
    """Collect GitHub project READMEs and curated free-for-dev-style Markdown catalogs."""

    source_type = "github_repository"

    async def collect(self, source_id: str, settings: Mapping[str, Any] | None = None) -> CollectResult:
        if not self.config.enabled:
            return CollectResult((), source_id, utc_now().isoformat(), degraded=("radar_disabled",))
        config = dict(settings or {})
        repos = config.get("repositories", ())
        if not isinstance(repos, (list, tuple)) or not repos:
            raise CollectorConfigurationError("GitHub collector requires a non-empty repositories list")
        results: list[CollectedOpportunity] = []
        degraded: list[str] = []
        for entry in repos[: self.config.max_candidates_per_source]:
            if isinstance(entry, str):
                full_name, branch, path, is_catalog = entry.strip(), "main", "README.md", False
                limit = min(60, self.config.max_candidates_per_source)
            elif isinstance(entry, Mapping):
                full_name = str(entry.get("full_name", entry.get("repo", ""))).strip()
                branch = str(entry.get("branch", "main")).strip()
                path = str(entry.get("path", "README.md")).strip()
                is_catalog = bool(entry.get("catalog", False))
                try:
                    limit = min(int(entry.get("limit", 60)), self.config.max_candidates_per_source)
                except (TypeError, ValueError):
                    degraded.append(f"{full_name}:invalid_limit")
                    continue
            else:
                degraded.append("invalid_repository_spec")
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", full_name):
                degraded.append("invalid_repository_name")
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", branch):
                degraded.append(f"{full_name}:invalid_branch")
                continue
            if (
                not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", path)
                or path.startswith("/")
                or any(part == ".." for part in path.split("/"))
                or not 1 <= limit <= self.config.max_candidates_per_source
            ):
                degraded.append(f"{full_name}:invalid_path_or_limit")
                continue
            owner, repo = full_name.split("/", 1)
            base = f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(branch, safe='')}"
            readme_url = base + "/" + quote(path, safe="/")
            try:
                readme = await self.fetch_text(readme_url)
            except (CollectorError, SourceReadError, OSError, TimeoutError) as exc:
                degraded.append(f"{full_name}:README:{type(exc).__name__}")
                continue
            body = str(readme.get("text", ""))
            if not body.strip():
                degraded.append(f"{full_name}:empty_README")
                continue

            if is_catalog:
                entries = _parse_github_markdown_catalog(body, limit)
                if not entries:
                    degraded.append(f"{full_name}:catalog_has_no_supported_entries")
                    continue
                for catalog_entry in entries:
                    try:
                        clean = sanitize_external_payload({
                            "title": catalog_entry["title"],
                            "summary": catalog_entry["description"],
                            "content": catalog_entry["description"],
                            "author": owner,
                            "url": catalog_entry["url"],
                        })
                    except (TypeError, ValueError):
                        degraded.append(f"{full_name}:invalid_catalog_item")
                        continue
                    linked_host = (urlsplit(clean.fields["url"]).hostname or "").casefold()
                    is_github_project = linked_host in {"github.com", "www.github.com", "gitlab.com", "codeberg.org"}
                    results.append(CollectedOpportunity(
                        source_id=f"github_catalog:{full_name}",
                        source_type="github_catalog",
                        external_id=clean.fields["url"],
                        url=clean.fields["url"],
                        evidence_url=readme_url,

                        title=clean.fields["title"] + (
                            " — " + clean.fields["summary"][:80] if clean.fields["summary"] else ""
                        ),
                        summary=clean.fields["summary"],
                        content=clean.fields["content"],
                        author=clean.fields["author"],
                        published_at=None,
                        expires_at=None,
                        license_text="",
                        metadata={
                            "catalog_source_repository": full_name,
                            "section": catalog_entry["section"],
                            "category_hint": catalog_entry["category_hint"],
                            "license_review_required": is_github_project,
                            "license_unknown_until_project_review": is_github_project,
                            "readme_url": readme_url,
                            "readme_sha256": str(readme.get("sha256", "")),
                        },
                    ))
                    if len(results) >= self.config.max_candidates_per_source:
                        break
                continue

            license_text = ""
            for license_name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
                try:
                    license_result = await self.fetch_text(base + "/" + license_name)
                    license_text = str(license_result.get("text", ""))
                    if license_text.strip():
                        break
                except (CollectorError, SourceReadError, OSError, TimeoutError):
                    continue
            first_heading = next(
                (line.lstrip("# ").strip() for line in body.splitlines()
                 if line.startswith("#") and line.lstrip("# ").strip()),
                repo,
            )
            description_lines = [
                re.sub(r"[*_>#]", "", line).strip()
                for line in body.splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "!"))
                and not line.lstrip().startswith(chr(96) * 3)
            ]
            summary = " ".join(description_lines[:3])[:1200]
            clean = sanitize_external_payload({
                "title": first_heading[:500],
                "summary": summary,
                "content": body[: self.config.max_text_chars],
                "author": owner,
                "url": f"https://github.com/{full_name}",
            })
            results.append(CollectedOpportunity(
                source_id=f"github:{full_name}",
                source_type=self.source_type,
                external_id=full_name,
                url=clean.fields["url"],
                evidence_url=readme_url,

                title=clean.fields["title"],
                summary=clean.fields["summary"],
                content=clean.fields["content"],
                author=clean.fields["author"],
                published_at=None,
                expires_at=None,
                license_text=sanitize_external_text(license_text, max_chars=5000).text,
                metadata={
                    "repository": full_name,
                    "readme_url": readme_url,
                    "license_file_present": bool(license_text.strip()),
                    "readme_sha256": str(readme.get("sha256", "")),
                    "readme_etag": readme.get("etag"),
                    "readme_last_modified": readme.get("last_modified"),
                    "license_unknown_until_reviewed": not bool(license_text.strip()),
                },
            ))
            if len(results) >= self.config.max_candidates_per_source:
                break
        return CollectResult(
            tuple(results[: self.config.max_candidates_per_source]),
            source_id, utc_now().isoformat(), degraded=tuple(degraded),
        )




def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _first_text(element: ET.Element, names: Sequence[str]) -> str:
    wanted = {name.casefold() for name in names}
    for child in element.iter():
        if _local_name(child.tag) in wanted and child.text:
            return child.text.strip()
    return ""


def _feed_items(xml_text: str, *, max_depth: int = 32, max_elements: int = 10_000) -> list[dict[str, str]]:
    encoded = xml_text.encode("utf-8", errors="replace")
    if len(encoded) > 1_000_000:
        raise CollectorError("RSS/Atom document exceeds 1 MiB")
    if re.search(rb"<!DOCTYPE|<!ENTITY", encoded, re.IGNORECASE):
        raise CollectorError("RSS/Atom DTD and entity declarations are not accepted")
    depth = 0
    element_count = 0
    root: ET.Element | None = None
    try:
        for event, element in ET.iterparse(io.BytesIO(encoded), events=("start", "end")):
            if event == "start":
                depth += 1
                element_count += 1
                if depth > max_depth:
                    raise CollectorError("RSS/Atom nesting depth exceeds the configured limit")
                if element_count > max_elements:
                    raise CollectorError("RSS/Atom element count exceeds the configured limit")
                if root is None:
                    root = element
            else:
                depth -= 1
    except ET.ParseError as exc:
        raise CollectorError("RSS/Atom document is malformed") from exc
    if root is None or _local_name(root.tag) not in {"rss", "feed", "rdf"}:
        raise CollectorError("document is not a supported RSS/Atom feed")
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    items: list[dict[str, str]] = []
    for node in entries[:500]:
        title = _first_text(node, ("title",))
        link = ""
        for child in node.iter():
            if _local_name(child.tag) != "link":
                continue
            href = child.attrib.get("href") or child.attrib.get("url")
            rel = child.attrib.get("rel", "alternate")
            if href and rel in {"alternate", ""}:
                link = href.strip()
                break
            if child.text and child.text.strip():
                link = child.text.strip()
        summary = _first_text(node, ("description", "summary", "content", "encoded"))
        published = _first_text(node, ("pubdate", "published", "updated", "date"))
        author = _first_text(node, ("creator", "author", "name"))
        identifier = _first_text(node, ("guid", "id"))
        if title and link:
            items.append({
                "title": title[:1000], "url": link[:4096],
                "summary": summary[:5000], "published_at": published[:100],
                "author": author[:200], "external_id": identifier[:500],
            })
    return items


def clean_date(raw: str) -> str | None:
    if not raw:
        return None
    candidate = raw.strip()
    from email.utils import parsedate_to_datetime
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()

class RSSAtomCollector(AsyncCollector):
    """RSS/Atom collector with per-feed filters applied before result limits."""

    source_type = "rss"

    async def collect(self, source_id: str, settings: Mapping[str, Any] | None = None) -> CollectResult:
        if not self.config.enabled:
            return CollectResult((), source_id, utc_now().isoformat(), degraded=("radar_disabled",))
        config = dict(settings or {})
        feeds = config.get("feeds", ())
        if not isinstance(feeds, (list, tuple)) or not feeds:
            raise CollectorConfigurationError("RSS collector requires a non-empty feeds list")
        output: list[CollectedOpportunity] = []
        degraded: list[str] = []
        for feed in feeds:
            if not isinstance(feed, Mapping):
                degraded.append("invalid_feed_spec")
                continue
            feed_id = str(feed.get("id", "")).strip()
            try:
                feed_url = validate_public_url(feed.get("url", ""))
                response = await self.fetch_text(feed_url)
                items = _feed_items(str(response.get("text", "")))
                include_words = tuple(str(x).casefold() for x in feed.get("include_title_keywords", ()))
                exclude_words = tuple(str(x).casefold() for x in feed.get("exclude_title_keywords", ()))
                feed_limit = min(int(feed.get("limit", 30)), self.config.max_candidates_per_source)
                accepted = 0
                for item in items:
                    lowered = item["title"].casefold()
                    if include_words and not any(word in lowered for word in include_words):
                        continue
                    if exclude_words and any(word in lowered for word in exclude_words):
                        continue
                    item["url"] = urljoin(feed_url, item["url"])
                    clean = sanitize_external_payload(item)
                    output.append(CollectedOpportunity(
                        source_id=feed_id or source_id, source_type=self.source_type,
                        external_id=item["external_id"] or hashlib.sha256(item["url"].encode()).hexdigest()[:24],
                        url=clean.fields["url"], title=clean.fields["title"],
                        evidence_url=feed_url,

                        summary=clean.fields["summary"], content=clean.fields["summary"],
                        author=clean.fields["author"], published_at=clean_date(item["published_at"]),
                        expires_at=None, license_text="",
                        metadata={
                            "feed_id": feed_id, "feed_url": feed_url,
                            "etag": response.get("etag"), "last_modified": response.get("last_modified"),
                            "cache_hit": bool(response.get("cache_hit", False)),
                        },
                    ))
                    accepted += 1
                    if accepted >= feed_limit:
                        break
            except (CollectorError, SourceReadError, OSError, TimeoutError, ValueError) as exc:
                degraded.append(f"{feed_id or 'feed'}:{type(exc).__name__}")
        return CollectResult(tuple(output[: self.config.max_candidates_per_source]),
                             source_id, utc_now().isoformat(), degraded=tuple(degraded))


class CommunityJSONCollector(AsyncCollector):
    """Reddit/V2EX-like JSON adapter; fails closed until a safe JSON reader is injected."""

    source_type = "community_json"

    async def collect(self, source_id: str, settings: Mapping[str, Any] | None = None) -> CollectResult:
        if not self.config.enabled:
            return CollectResult((), source_id, utc_now().isoformat(), degraded=("radar_disabled",))
        config = dict(settings or {})
        provider = str(config.get("provider", "reddit")).casefold()
        if provider not in {"reddit", "v2ex", "generic"}:
            raise CollectorConfigurationError("community JSON provider must be reddit, v2ex or generic")
        endpoints = config.get("endpoints", ())
        if provider == "reddit" and not endpoints:
            subreddits = config.get("subreddits", ())
            if not isinstance(subreddits, (list, tuple)) or not subreddits:
                raise CollectorConfigurationError("Reddit collector requires subreddits or endpoints")
            endpoints = [
                f"https://www.reddit.com/r/{quote(str(name), safe='')}/new.json?limit=25"
                for name in subreddits
            ]
        if provider == "v2ex" and not endpoints:
            nodes = config.get("nodes", ("free",))
            endpoints = [
                f"https://www.v2ex.com/api/topics/show.json?node_name={quote(str(node), safe='')}"
                for node in nodes
            ]
        if not isinstance(endpoints, (list, tuple)) or not endpoints:
            raise CollectorConfigurationError("community JSON collector requires endpoints")
        headers = {"Accept": "application/json", "User-Agent": "MuseOpportunityRadar/1.0"}
        output: list[CollectedOpportunity] = []
        degraded: list[str] = []
        for endpoint in endpoints[:20]:
            try:
                response = await self.fetch_json(str(endpoint), headers=headers)
                payload = response["data"]
                if provider == "reddit":
                    data = payload.get("data", {}) if isinstance(payload, Mapping) else {}
                    children = data.get("children", []) if isinstance(data, Mapping) else []
                    records = [child.get("data", {}) for child in children if isinstance(child, Mapping)]
                elif provider == "v2ex":
                    records = payload if isinstance(payload, list) else []
                else:
                    records = payload.get("items", []) if isinstance(payload, Mapping) else payload
                if not isinstance(records, list):
                    raise CollectorError("community JSON payload has an invalid item collection")
                for record in records[: self.config.max_candidates_per_source]:
                    if not isinstance(record, Mapping):
                        continue
                    if provider == "reddit":
                        external_id = str(record.get("id", ""))
                        permalink = str(record.get("permalink", ""))
                        permalink_url = (
                            permalink if permalink.startswith(("http://", "https://"))
                            else f"https://www.reddit.com{permalink}" if permalink else ""
                        )
                        evidence_url = permalink_url or str(endpoint)
                        url = str(record.get("url") or evidence_url)
                        title = str(record.get("title", ""))
                        summary = str(record.get("selftext", record.get("description", "")))
                        author = str(record.get("author", ""))
                        created = record.get("created_utc")
                    elif provider == "v2ex":
                        external_id = str(record.get("id", ""))
                        evidence_url = f"https://www.v2ex.com/t/{external_id}" if external_id else str(endpoint)
                        url = str(record.get("url") or evidence_url)
                        title = str(record.get("title", ""))
                        summary = str(record.get("content", record.get("summary", "")))
                        author_obj = record.get("member", {})
                        author = str(author_obj.get("username", "")) if isinstance(author_obj, Mapping) else ""
                        created = record.get("created")
                    else:
                        external_id = str(record.get("id", ""))
                        evidence_url = str(record.get("source_url") or endpoint)
                        url = str(record.get("url") or evidence_url)
                        title = str(record.get("title", ""))
                        summary = str(record.get("content", record.get("summary", "")))
                        author = str(record.get("author", ""))
                        created = record.get("created")
                    if not title or not url:
                        continue
                    evidence_url = validate_public_url(evidence_url)
                    source_key = hashlib.sha256(str(endpoint).encode("utf-8")).hexdigest()[:12]
                    published = None
                    if isinstance(created, (int, float)) and not isinstance(created, bool) and created > 0:
                        try:
                            published = datetime.fromtimestamp(float(created), tz=timezone.utc).isoformat()
                        except (OverflowError, OSError, ValueError):
                            published = None
                    clean = sanitize_external_payload({
                        "title": title, "summary": summary, "content": summary,
                        "author": author, "url": url,
                    })
                    output.append(CollectedOpportunity(
                        source_id=f"{provider}:{source_key}",
                        source_type="reddit" if provider == "reddit" else self.source_type,
                        external_id=external_id or hashlib.sha256(url.encode()).hexdigest()[:24],
                        url=clean.fields["url"], title=clean.fields["title"],
                        evidence_url=evidence_url,

                        summary=clean.fields["summary"],
                        content=sanitize_external_text(summary, max_chars=6000).text,
                        author=clean.fields["author"], published_at=published,
                        expires_at=None, license_text="",
                        metadata={
                            "provider": provider, "community_endpoint": safe_url_log_identity(endpoint),
                            "raw_payload_untrusted": True,
                        },
                    ))
            except (CollectorError, SourceReadError, OSError, TimeoutError, ValueError, TypeError) as exc:
                degraded.append(f"{source_id}:{safe_url_log_identity(endpoint)}:{type(exc).__name__}")
        return CollectResult(tuple(output[: self.config.max_candidates_per_source]),
                             source_id, utc_now().isoformat(), degraded=tuple(degraded))


class CollectorRegistry:
    """Explicit plugin registry; registering a collector never schedules or runs it."""

    def __init__(self) -> None:
        self._collectors: dict[str, AsyncCollector] = {}

    def register(self, name: str, collector: AsyncCollector) -> None:
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", name):
            raise ValueError("collector name must be a bounded lowercase identifier")
        if not isinstance(collector, AsyncCollector):
            raise TypeError("collector must inherit AsyncCollector")
        if name in self._collectors:
            raise ValueError(f"collector already registered: {name}")
        self._collectors[name] = collector

    def get(self, name: str) -> AsyncCollector:
        try:
            return self._collectors[name]
        except KeyError as exc:
            raise KeyError(f"unknown Radar collector: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._collectors))

