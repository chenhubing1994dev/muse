"""Optional Scrapling parser integration for Muse's evidence-backed research.

Network access remains owned by Muse's existing guarded readers. This adapter
accepts already-fetched public HTML and uses Scrapling only for structured,
adaptive extraction; it never launches a browser or makes HTTP requests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from muse.research_fetch import _validate_public_url


class ScraplingUnavailableError(RuntimeError):
    """Raised when the optional Scrapling package is not installed."""


class ScraplingHTMLParser:
    """Extract bounded fields from already-fetched HTML with Scrapling selectors."""

    MAX_HTML_BYTES = 2_000_000
    MAX_FIELDS = 32
    MAX_SELECTOR_CHARS = 500
    MAX_VALUES_PER_FIELD = 100
    MAX_VALUE_CHARS = 4_000
    MAX_TOTAL_CHARS = 20_000

    def __init__(self, workspace_root: str | Path, *, selector_factory: Callable[..., Any] | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._selector_factory = selector_factory

    def _factory(self) -> Callable[..., Any]:
        if self._selector_factory is not None:
            return self._selector_factory
        try:
            from scrapling import Selector
        except ImportError as exc:
            raise ScraplingUnavailableError(
                "Scrapling is optional; install Muse's web-scraping extra to enable it"
            ) from exc
        self._selector_factory = Selector
        return Selector

    def _adaptive_storage(self) -> Path:
        storage = (self.workspace_root / "data" / "scrapling" / "adaptive").resolve()
        if not storage.is_relative_to(self.workspace_root):
            raise ValueError("adaptive storage must remain inside the Muse workspace")
        storage.mkdir(parents=True, exist_ok=True)
        return storage

    def extract(self, html: str | bytes, *, url: str, selectors: dict[str, dict[str, str]],
                adaptive_mode: str = "off") -> dict[str, Any]:
        if not isinstance(html, (str, bytes)):
            raise TypeError("html must be text or bytes")
        raw = html.encode("utf-8") if isinstance(html, str) else html
        if not raw or len(raw) > self.MAX_HTML_BYTES:
            raise ValueError("HTML must contain 1 byte to 2,000,000 bytes")
        _validate_public_url(url)
        parsed = urlsplit(url)
        if not isinstance(selectors, dict) or not 1 <= len(selectors) <= self.MAX_FIELDS:
            raise ValueError("selectors must contain 1-32 named fields")
        if adaptive_mode not in {"off", "learn", "reuse"}:
            raise ValueError("adaptive_mode must be off, learn or reuse")

        kwargs: dict[str, Any] = {"url": url}
        if adaptive_mode != "off":
            storage_file = self._adaptive_storage() / "selectors.sqlite3"
            kwargs.update(adaptive=True, storage_args={"storage_file": str(storage_file)})
        page = self._factory()(raw, **kwargs)
        extracted: dict[str, list[str]] = {}
        total_chars = 0
        for field, spec in selectors.items():
            if not isinstance(field, str) or not field.strip() or len(field) > 80:
                raise ValueError("selector field names must contain 1-80 characters")
            if not isinstance(spec, dict) or set(spec) != {"type", "query"}:
                raise ValueError("each selector must contain exactly type and query")
            kind, query = spec["type"], spec["query"]
            if not isinstance(kind, str) or kind not in {"css", "xpath"} or not isinstance(query, str) or not query.strip() or len(query) > self.MAX_SELECTOR_CHARS:
                raise ValueError("selector type/query is invalid")
            method = getattr(page, kind)
            if adaptive_mode == "learn":
                selected = method(query, auto_save=True)
            elif adaptive_mode == "reuse":
                selected = method(query, adaptive=True)
            else:
                selected = method(query)
            values = selected.getall() if callable(getattr(selected, "getall", None)) else list(selected)
            field_values: list[str] = []
            for value in values[:self.MAX_VALUES_PER_FIELD]:
                cleaned = str(value).strip()
                if not cleaned:
                    continue
                remaining = self.MAX_TOTAL_CHARS - total_chars
                if remaining <= 0:
                    break
                bounded = cleaned[:min(self.MAX_VALUE_CHARS, remaining)]
                field_values.append(bounded)
                total_chars += len(bounded)
            extracted[field] = field_values
            if total_chars >= self.MAX_TOTAL_CHARS:
                break
        return {
            "url": url, "domain": (parsed.hostname or "").casefold(), "parser": "scrapling",
            "adaptive_mode": adaptive_mode, "fields": extracted, "field_count": len(extracted),
            "value_count": sum(len(values) for values in extracted.values()),
            "source_kind": "already_fetched_public_html",
        }
