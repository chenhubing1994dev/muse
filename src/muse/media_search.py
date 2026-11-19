"""Bounded image discovery and download for Muse's Phase 3 media studio.

Search is fail-closed: without an injected search callable or an explicitly
configured SearXNG endpoint, callers receive a blocked status instead of a
fabricated result set. Downloaded bytes are verified and never executed.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class MediaSearchBlocked(RuntimeError):
    """Raised when no approved media search backend is available."""


@dataclass(frozen=True)
class MediaAsset:
    title: str
    url: str
    license: str = "unknown"
    source_ref: str = ""
    source: str = ""
    width: int | None = None
    height: int | None = None
    score: float = 0.0
    snippet: str = ""


_STYLE_TERMS = {
    "hk-retro": ("hong kong", "hk", "retro", "neon", "vintage", "street", "港风", "复古", "霓虹"),
    "pixel": ("pixel", "pixel art", "8-bit", "16-bit", "sprite", "retro game", "像素"),
    "cosmic": ("cosmic", "space", "nebula", "galaxy", "stars", "universe", "星空", "宇宙", "星云"),
}
_SUPPORTED_STYLES = frozenset(_STYLE_TERMS)


def _safe_media_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if any(ord(char) < 32 for char in value):
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return value


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= 100000 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if 0 < parsed <= 100000 else None
    return None


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").casefold()


def _default_search_callable(endpoint: str | None) -> Callable[..., list[dict[str, Any]]] | None:
    resolved = endpoint or os.environ.get("MUSE_SEARXNG_URL")
    if not resolved:
        return None
    try:
        from muse.research import SearxngSearchAdapter
    except ImportError:
        return None
    adapter = SearxngSearchAdapter(resolved)

    def search(query: str, *, limit: int) -> list[dict[str, Any]]:
        return adapter.search(query, limit=limit)

    return search


class MediaSearchEngine:
    """Score, filter and record media results from an approved search backend."""

    def __init__(
        self,
        *,
        search_callable: Callable[..., list[dict[str, Any]]] | None = None,
        endpoint: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.search_callable = search_callable or _default_search_callable(endpoint)

    def search_images(
        self,
        query: str,
        style: str,
        *,
        endpoint: str | None = None,
        limit: int = 20,
        min_width: int = 0,
        target_ratio: float | None = None,
    ) -> list[MediaAsset]:
        if style not in _SUPPORTED_STYLES:
            raise ValueError("style must be hk-retro, pixel or cosmic")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        search = self.search_callable
        if endpoint and search is None:
            from muse.research import SearxngSearchAdapter
            adapter = SearxngSearchAdapter(endpoint)
            search = lambda q, *, limit: adapter.search(q, limit=limit)
        if search is None:
            raise MediaSearchBlocked(
                "media search is blocked: configure MUSE_SEARXNG_URL or inject an approved search callable"
            )
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("query must contain 1-500 characters")
        try:
            raw = search(query.strip(), limit=limit)
        except Exception as exc:
            raise MediaSearchBlocked(f"media search provider failed: {type(exc).__name__}") from exc
        if not isinstance(raw, list):
            raise MediaSearchBlocked("media search provider returned an invalid result set")

        terms = _STYLE_TERMS[style]
        assets: list[MediaAsset] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = _safe_media_url(item.get("url"))
            if not url or url in seen:
                continue
            text = " ".join(str(item.get(key) or "") for key in ("title", "snippet", "description", "tags")).casefold()
            score = float(sum(3 if term in text else 0 for term in terms))
            score += 1 if style in text else 0
            if score <= 0:
                continue
            width = _int(item.get("width"))
            height = _int(item.get("height"))
            if min_width and (width is None or width < min_width):
                continue
            if target_ratio is not None:
                if width is None or height is None or abs(width / height - target_ratio) > 0.02:
                    continue
            seen.add(url)
            host = _host_of(url)
            assets.append(MediaAsset(
                title=str(item.get("title") or "")[:500],
                url=url,
                license=str(item.get("license") or "unknown")[:120],
                source_ref=str(item.get("source_ref") or item.get("sourceRef") or "")[:300],
                source=str(item.get("source") or item.get("engine") or host)[:200],
                width=width,
                height=height,
                score=score,
                snippet=str(item.get("snippet") or item.get("content") or "")[:2000],
            ))
        assets.sort(key=lambda asset: (-asset.score, asset.url))
        return assets[:limit]


def download_asset(
    asset: MediaAsset,
    dest_dir: str | Path,
    *,
    max_bytes: int,
    allowed_hosts: list[str] | tuple[str, ...] | set[str] | None = None,
    timeout: float = 15.0,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    """Download one asset into a temp file; verify size before atomic rename."""
    if not isinstance(asset, MediaAsset):
        raise TypeError("asset must be a MediaAsset")
    url = _safe_media_url(asset.url)
    if url is None:
        raise ValueError("asset URL must be credential-free HTTP(S)")
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if allowed_hosts is not None:
        approved = {str(value).casefold().strip().rstrip(".") for value in allowed_hosts}
        if host not in approved:
            raise ValueError("asset host is outside the allowlist")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 50 * 1024 * 1024:
        raise ValueError("max_bytes must be between 1 byte and 50 MiB")
    root = Path(dest_dir)
    if not root.is_dir():
        raise ValueError("destination directory must exist")
    request = Request(url, headers={"Accept": "image/*", "User-Agent": "MuseMediaStudio/0.1"})
    temporary: Path | None = None
    try:
        with opener(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length") if hasattr(response, "headers") else None
            if declared:
                try:
                    if int(declared) > max_bytes:
                        raise ValueError("asset exceeds configured size limit")
                except (TypeError, ValueError) as exc:
                    if isinstance(exc, ValueError) and "size limit" in str(exc):
                        raise
            handle, temp_name = tempfile.mkstemp(prefix="muse-asset-", suffix=".download", dir=str(root))
            temporary = Path(temp_name)
            written = 0
            with os.fdopen(handle, "wb") as stream:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("asset exceeds configured size limit after download")
                    stream.write(chunk)
            if written <= 0:
                raise ValueError("downloaded asset is empty")
            extension = re.sub(r"[^a-z0-9]", "", Path(parsed.path).suffix.casefold()) or "bin"
            target = root / f"asset-{os.urandom(8).hex()}.{extension}"
            temporary.replace(target)
            temporary = None
            return {
                "path": str(target.resolve()), "bytes": written, "source_url": url,
                "host": host, "sha256": __import__("hashlib").sha256(target.read_bytes()).hexdigest(),
                "license_status": "unreviewed", "reuse_allowed": False,
                "manifest": asdict(asset),
            }
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)
