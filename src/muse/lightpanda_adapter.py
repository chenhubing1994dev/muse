"""Optional Lightpanda CDP adapter for bounded public-page research.

Lightpanda is an external browser process. Muse connects only to a loopback CDP
endpoint, owns authorization and evidence persistence, and never delegates
scheduling or research policy to the browser.
"""
from __future__ import annotations

import hashlib
import importlib
import ipaddress
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from muse.research_fetch import _validate_public_url
from muse.error_journal import record_suppressed_error

MAX_CAPTURE_CHARS = 50_000
MAX_LINKS = 200


class LightpandaError(RuntimeError):
    """A bounded Lightpanda browser operation failed."""


class LightpandaUnavailableError(LightpandaError):
    """The optional Playwright bridge or Lightpanda CDP runtime is unavailable."""


def _load_playwright() -> Any:
    """Load the optional Python Playwright bridge only when explicitly used."""
    try:
        return importlib.import_module("playwright.sync_api")
    except ImportError as exc:
        raise LightpandaUnavailableError(
            "Playwright is optional; install Muse with the 'lightpanda' extra to enable CDP"
        ) from exc


def _validate_cdp_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("Lightpanda CDP URL must contain 1-2048 characters")
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "ws"} or not parsed.hostname:
        raise ValueError("Lightpanda CDP endpoint must be an absolute local HTTP or WebSocket URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("Lightpanda CDP endpoint must not contain credentials or fragments")
    host = parsed.hostname.rstrip(".").casefold()
    if host != "localhost":
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise ValueError("Lightpanda CDP endpoint must use a loopback hostname or address") from exc
        if not address.is_loopback:
            raise ValueError("Lightpanda CDP endpoint must be loopback-only")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Lightpanda CDP endpoint has an invalid port") from exc
    return normalized


class LightpandaBrowserAdapter:
    """Connect to an already-running local Lightpanda CDP service."""

    def __init__(
        self, *, workspace: str | Path, cdp_url: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("Muse workspace must already exist")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 5 <= timeout_seconds <= 120:
            raise ValueError("Lightpanda timeout must be between 5 and 120 seconds")
        configured = os.environ.get("MUSE_LIGHTPANDA_CDP_URL") if cdp_url is None else cdp_url
        self.cdp_url = _validate_cdp_url(configured) if configured else None
        self.timeout_seconds = float(timeout_seconds)

    def doctor(self) -> dict[str, Any]:
        base = {
            "provider": "lightpanda", "cdp_url": self.cdp_url,
            "execution_model": "external Lightpanda process over loopback CDP",
            "capabilities": ["javascript_dom", "page_title", "public_text", "link_inventory"],
            "screenshots": False, "default_enabled": False,
        }
        if not self.cdp_url:
            return {**base, "status": "disabled", "available": False, "runtime_ready": False}
        try:
            _load_playwright()
        except LightpandaUnavailableError:
            return {**base, "status": "dependency_missing", "available": False, "runtime_ready": False}
        return {**base, "status": "configured_unverified", "available": True,
                "runtime_ready": False, "runtime_probe_required": True}

    def probe_runtime(self) -> dict[str, Any]:
        status = self.doctor()
        if not status["available"]:
            return status
        browser = None
        try:
            playwright_api = _load_playwright()
            with playwright_api.sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    self.cdp_url, timeout=int(self.timeout_seconds * 1000),
                )
                version = str(browser.version or "")[:100]
                browser.close()
            return {**status, "status": "ready", "runtime_ready": True, "browser_version": version}
        except Exception as exc:
            if browser is not None:
                try:
                    browser.close()
                except Exception as _muse_suppressed:
                    record_suppressed_error("lightpanda_adapter.py:118", _muse_suppressed)
            return {**status, "status": "not_ready", "runtime_ready": False,
                    "runtime_error": type(exc).__name__}

    def _output_path(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.workspace / path
        resolved = path.resolve()
        if resolved == self.workspace or not resolved.is_relative_to(self.workspace):
            raise ValueError("Lightpanda evidence must be written inside the Muse workspace")
        return resolved

    @staticmethod
    def _validate_limits(max_chars: int, max_links: int) -> None:
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1000 <= max_chars <= MAX_CAPTURE_CHARS:
            raise ValueError(f"max_chars must be between 1000 and {MAX_CAPTURE_CHARS}")
        if isinstance(max_links, bool) or not isinstance(max_links, int) or not 0 <= max_links <= MAX_LINKS:
            raise ValueError(f"max_links must be between 0 and {MAX_LINKS}")

    def capture_url(
        self, url: str, *, output_directory: str | Path,
        authorization_confirmed: bool = False, approved: bool = False,
        public_only: bool = True, max_chars: int = 30_000, max_links: int = 100,
    ) -> dict[str, Any]:
        if authorization_confirmed is not True or approved is not True:
            raise PermissionError("Lightpanda capture requires explicit research authorization and approval")
        if public_only is not True:
            raise ValueError("Lightpanda adapter currently supports public-only capture")
        self._validate_limits(max_chars, max_links)
        # Reject unsafe output targets before performing DNS/network work.
        output = self._output_path(output_directory)
        if output.exists():
            raise FileExistsError("Lightpanda evidence directory already exists; refusing to overwrite")
        _validate_public_url(url)
        if not self.cdp_url:
            raise LightpandaUnavailableError("Lightpanda is disabled; configure MUSE_LIGHTPANDA_CDP_URL with a loopback CDP endpoint")
        if not self.doctor()["available"]:
            raise LightpandaUnavailableError("Playwright optional dependency is unavailable")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=False, exist_ok=False)

        browser = None
        context = None
        try:
            playwright_api = _load_playwright()
            with playwright_api.sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(
                    self.cdp_url, timeout=int(self.timeout_seconds * 1000),
                )
                context = browser.new_context(service_workers="block")
                def guard_websocket(websocket: Any) -> None:
                    websocket.close(
                        code=1008, reason="WebSocket access is disabled for public research capture",
                    )

                context.route_web_socket("**/*", guard_websocket)

                def guard_request(route: Any) -> None:
                    request_url = str(route.request.url)
                    try:
                        if urlsplit(request_url).scheme not in {"http", "https"}:
                            raise ValueError("non-HTTP page resource")
                        _validate_public_url(request_url)
                    except Exception:
                        route.abort()
                    else:
                        route.continue_()

                context.route("**/*", guard_request)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=int(self.timeout_seconds * 1000))
                final_url = str(page.url)
                _validate_public_url(final_url)
                title = str(page.title() or "")[:500]
                text = str(page.locator("body").inner_text(
                    timeout=int(self.timeout_seconds * 1000),
                ) or "").strip()[:max_chars]
                links: list[dict[str, str]] = []
                if max_links:
                    locator = page.locator("a[href]")
                    for index in range(min(locator.count(), max_links)):
                        try:
                            item = locator.nth(index).evaluate(
                                "(node) => ({text: (node.innerText || node.getAttribute('aria-label') || '').trim(), url: node.href})"
                            )
                        except Exception as _muse_suppressed:
                            record_suppressed_error("lightpanda_adapter.py:206", _muse_suppressed)
                            continue
                        if not isinstance(item, dict):
                            continue
                        link_url = item.get("url")
                        if (not isinstance(link_url, str) or len(link_url) > 2048
                                or not link_url.startswith(("https://", "http://"))):
                            continue
                        try:
                            _validate_public_url(link_url)
                        except Exception as _muse_suppressed:
                            record_suppressed_error("lightpanda_adapter.py:216", _muse_suppressed)
                            continue
                        links.append({"text": str(item.get("text") or "")[:300], "url": link_url})
                captured_at = datetime.now(timezone.utc).isoformat()
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                source = {
                    "requested_url": url, "url": final_url, "title": title,
                    "captured_at": captured_at, "page_sha256": digest,
                    "capture_method": "lightpanda-cdp",
                }
                evidence = {
                    "provider": "lightpanda", "source": source, "text": text,
                    "links": links, "assets": [], "source_metadata": {},
                    "limitations": [
                        "Lightpanda does not render visual screenshots.",
                        "Browser network egress must remain restricted by the host environment.",
                    ],
                }
                (output / "capture.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                )
                markdown = [
                    "# Website Study", "", f"- URL: {final_url}", f"- Title: {title}",
                    f"- Captured: {captured_at}", f"- Page SHA-256: {digest}",
                    "- Screenshot: not available (Lightpanda is non-rendering)", "",
                    "## Extracted text", "", text, "", "## Links", "",
                ]
                markdown.extend(f"- [{item['text'] or item['url']}]({item['url']})" for item in links)
                (output / "capture.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
                context.close()
                context = None
                browser.close()
                browser = None
                return {
                    "status": "captured", "provider": "lightpanda",
                    "capture_method": "lightpanda-cdp", "evidence_directory": str(output),
                    "source": source, "links": links, "assets": [], "screenshot_path": None,
                }
        except Exception as exc:
            shutil.rmtree(output, ignore_errors=True)
            if isinstance(exc, (ValueError, PermissionError, FileExistsError, LightpandaError)):
                raise
            raise LightpandaError("Lightpanda CDP capture failed: " + type(exc).__name__) from exc
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception as _muse_suppressed:
                    record_suppressed_error("lightpanda_adapter.py:262", _muse_suppressed)
            if browser is not None:
                try:
                    browser.close()
                except Exception as _muse_suppressed:
                    record_suppressed_error("lightpanda_adapter.py:267", _muse_suppressed)
