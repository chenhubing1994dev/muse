"""Public-research adapters that augment Muse's existing search and source reader.

No scheduler lives here. The adapters are data providers for ResearchPipeline;
browser access is public-only, bounded, source-linked, and optional.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from muse.research_fetch import SourceReadError, _validate_public_url


_LOGIN_OR_CHALLENGE_MARKERS = (
    "verify your browser", "complete the check below", "checking your browser",
    "captcha", "access denied", "please enable javascript",
    "sign in to continue", "log in to continue", "you must be logged in",
    "sign in to view", "subscribe to continue", "paywall",
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


class BrowserAugmentedSearchAdapter:
    """Merge an existing search provider with bounded public ego-lite search."""

    def __init__(
        self, primary: Any, browser: Any, *, workspace: str | Path,
        allowed_queries: set[str] | tuple[str, ...] | list[str],
    ) -> None:
        self.primary = primary
        self.browser = browser
        self.workspace = Path(workspace).resolve()
        self.allowed_queries = {item.strip() for item in allowed_queries if isinstance(item, str) and item.strip()}
        self._failures: list[dict[str, str]] = []
        self._failure_lock = threading.Lock()

    def _record_failure(self, stage: str, query: str, exc: Exception) -> None:
        with self._failure_lock:
            self._failures.append({
                "stage": stage, "query": query[:300], "error": type(exc).__name__,
            })

    def drain_failures(self) -> list[dict[str, str]]:
        with self._failure_lock:
            failures = list(self._failures)
            self._failures.clear()
        return failures

    @staticmethod
    def _dedupe_key(url: str) -> str:
        parsed = urlsplit(url)
        return parsed._replace(fragment="").geturl().rstrip("/").casefold()

    def search(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 16:
            raise ValueError("limit must be between 1 and 16")
        primary_items: list[dict[str, Any]] = []
        browser_items: list[dict[str, Any]] = []
        try:
            primary_results = self.primary.search(query, limit=limit)
            if isinstance(primary_results, list):
                primary_items = [item for item in primary_results if isinstance(item, dict)]
        except Exception as exc:
            self._record_failure("primary_search", query, exc)

        # Only the curated support/counter queries supplied by the current
        # research topic may be sent to a public browser search engine.
        public_query = query.strip() in self.allowed_queries and not _EMAIL_PATTERN.search(query)
        if public_query and self.browser.doctor().get("available"):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            query_hash = hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:12]
            output = self.workspace / "data" / "research" / "browser-search" / f"{query_hash}-{stamp}"
            try:
                discovered = self.browser.search_web(
                    query, output_directory=output,
                    authorization_confirmed=True, approved=True,
                    engine="google", max_results=min(20, limit),
                )
                if isinstance(discovered, list):
                    browser_items = [item for item in discovered if isinstance(item, dict)]
            except Exception as exc:
                self._record_failure("browser_search", query, exc)

        # Interleave primary and browser results so a full primary page cannot
        # crowd out the browser's additional publishers and dynamic-site links.
        interleaved: list[dict[str, Any]] = []
        for index in range(max(len(primary_items), len(browser_items))):
            if index < len(primary_items):
                interleaved.append(primary_items[index])
            if index < len(browser_items):
                interleaved.append(browser_items[index])
        unique: dict[str, dict[str, Any]] = {}
        for item in interleaved:
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                continue
            unique.setdefault(self._dedupe_key(url), item)
            if len(unique) >= limit:
                break
        return list(unique.values())


class HybridSourceReader:
    """Use the existing safe HTTP reader first, then ego-lite for dynamic public pages."""

    def __init__(
        self, primary: Any, browser: Any, *, workspace: str | Path,
        max_chars: int = 30_000, asset_download_enabled: bool = True,
        max_assets_per_page: int = 3,
    ) -> None:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1000 <= max_chars <= 50_000:
            raise ValueError("max_chars must be between 1000 and 50000")
        if not isinstance(asset_download_enabled, bool):
            raise TypeError("asset_download_enabled must be a boolean")
        if isinstance(max_assets_per_page, bool) or not isinstance(max_assets_per_page, int) or not 0 <= max_assets_per_page <= 5:
            raise ValueError("max_assets_per_page must be between 0 and 5")
        self.primary = primary
        self.browser = browser
        self.workspace = Path(workspace).resolve()
        self.max_chars = max_chars
        self.asset_download_enabled = asset_download_enabled
        self.max_assets_per_page = max_assets_per_page

    @staticmethod
    def _needs_browser(text: str) -> bool:
        cleaned = text.strip()
        if len(cleaned) < 200:
            return True
        head = cleaned[:2500].casefold()
        return any(marker in head for marker in _LOGIN_OR_CHALLENGE_MARKERS)

    @staticmethod
    def _selected_image_assets(assets: list[dict[str, Any]], page_host: str, limit: int) -> list[str]:
        if limit <= 0:
            return []
        selected: list[str] = []
        image_suffixes = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".svg")
        for item in assets:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str):
                continue
            host = (urlsplit(url).hostname or "").casefold()
            kind = str(item.get("kind") or "").casefold()
            content_type = str(item.get("type") or "").casefold()
            path = urlsplit(url).path.casefold()
            is_collectible = (kind == "img" or kind == "document" or content_type.startswith("image/")
                              or content_type == "application/pdf" or path.endswith(image_suffixes + (".pdf",)))
            if host == page_host and is_collectible and url not in selected:
                selected.append(url)
            if len(selected) >= limit:
                break
        return selected

    def read(self, url: str) -> dict[str, Any]:
        primary_error: Exception | None = None
        try:
            page = self.primary.read(url)
            if not self._needs_browser(str(page.get("text") or "")):
                return {**page, "capture_method": "http"}
            primary_error = SourceReadError("HTTP source was too short or returned a login/challenge page")
        except SourceReadError as exc:
            primary_error = exc
        if not self.browser.doctor().get("available"):
            if isinstance(primary_error, Exception):
                raise primary_error
            raise SourceReadError("HTTP source unavailable and ego-lite browser is not installed")

        _validate_public_url(url)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        output = self.workspace / "data" / "research" / "browser-captures" / f"{url_hash}-{stamp}"
        try:
            captured = self.browser.capture_url(
                url, output_directory=output, authorization_confirmed=True,
                approved=True, max_chars=self.max_chars, max_links=100,
                max_assets=100, public_only=True,
            )
            evidence_dir = Path(captured["evidence_directory"])
            evidence = json.loads((evidence_dir / "capture.json").read_text(encoding="utf-8"))
            text = str(evidence.get("text") or "").strip()[:self.max_chars]
            if self._needs_browser(text):
                shutil.rmtree(evidence_dir, ignore_errors=True)
                raise SourceReadError("ego-lite returned a login wall, access challenge, or insufficient public content")
            source = evidence.get("source") if isinstance(evidence.get("source"), dict) else {}
            final_url = str(source.get("url") or url)
            _validate_public_url(final_url)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            assets = evidence.get("assets") if isinstance(evidence.get("assets"), list) else []
            page_host = (urlsplit(final_url).hostname or "").casefold()
            asset_manifest_path = None
            asset_count = 0
            asset_error = None
            selected_assets = self._selected_image_assets(
                assets, page_host, self.max_assets_per_page,
            ) if self.asset_download_enabled else []
            if selected_assets:
                try:
                    asset_result = self.browser.download_assets(
                        final_url, selected_assets, allowed_domains=[page_host],
                        output_directory=evidence_dir / "assets",
                        authorization_confirmed=True, approved=True, public_only=True,
                        max_assets=self.max_assets_per_page,
                        max_asset_bytes=2 * 1024 * 1024, max_total_bytes=5 * 1024 * 1024,
                    )
                    asset_manifest_path = asset_result.get("manifest_path")
                    asset_count = int(asset_result.get("downloaded_count", 0))
                except Exception as exc:
                    asset_error = type(exc).__name__
            screenshot_path = (
                str(evidence_dir / str(source["screenshot_path"]))
                if source.get("screenshot_path") else None
            )
            return {
                "url": final_url, "content_type": "text/html; source=ego-lite",
                "text": text, "sha256": digest,
                "fetched_at": str(source.get("captured_at") or datetime.now(timezone.utc).isoformat()),
                "truncated": len(text) >= self.max_chars,
                "source_metadata": evidence.get("source_metadata") or {},
                "capture_method": "ego-lite",
                "browser_evidence_directory": str(evidence_dir),
                "browser_screenshot_path": screenshot_path,
                "asset_manifest_path": str(asset_manifest_path) if asset_manifest_path else None,
                "asset_count": asset_count,
                "asset_download_error": asset_error,
                "asset_inventory_count": len(assets),
            }
        except Exception as exc:
            if isinstance(exc, SourceReadError):
                raise
            raise SourceReadError(
                "ego-lite public browser fallback failed: " + type(exc).__name__
            ) from exc
