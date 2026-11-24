"""Deterministic, offline UTM attribution drafts for manual social distribution.

This module creates tracking metadata only. It never publishes, sends requests,
contacts platforms, or claims that a conversion/revenue event occurred.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, Mapping, Sequence


class SocialTrackingError(ValueError):
    """Raised when a campaign tracking draft is unsafe or incomplete."""


_PLATFORM_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_UTM_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}


def _tag(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise SocialTrackingError(f"{label} must be text")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 120 or any(ord(char) < 32 for char in cleaned):
        raise SocialTrackingError(f"{label} must be 1-120 printable characters")
    return cleaned


def build_tracking_url(
    destination_url: str,
    *,
    source: str,
    campaign: str,
    content: str,
    medium: str = "social",
    term: str | None = None,
) -> str:
    """Append deterministic UTM parameters without overwriting existing tags."""
    if not isinstance(destination_url, str) or len(destination_url) > 4096:
        raise SocialTrackingError("destination_url must be a URL no longer than 4096 characters")
    parsed = urlsplit(destination_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SocialTrackingError("destination_url must be an absolute HTTP(S) URL without credentials")
    if parsed.fragment:
        raise SocialTrackingError("destination_url fragments are not supported for campaign tracking")
    existing = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key.lower() in _UTM_KEYS for key, _ in existing):
        raise SocialTrackingError("destination_url already contains UTM parameters")
    values = [
        ("utm_source", _tag(source, "source")),
        ("utm_medium", _tag(medium, "medium")),
        ("utm_campaign", _tag(campaign, "campaign")),
        ("utm_content", _tag(content, "content")),
    ]
    if term is not None:
        values.append(("utm_term", _tag(term, "term")))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(existing + values), ""))


def build_social_tracking_manifest(
    destination_url: str,
    *,
    campaign: str,
    platforms: Sequence[str],
    content_by_platform: Mapping[str, str],
    medium: str = "social",
) -> dict[str, Any]:
    """Build one draft-only tracked destination per platform; no network access."""
    campaign_value = _tag(campaign, "campaign")
    medium_value = _tag(medium, "medium")
    if isinstance(platforms, (str, bytes)) or not isinstance(platforms, Sequence):
        raise SocialTrackingError("platforms must be a sequence of platform identifiers")
    platform_values = list(platforms)
    if any(not isinstance(item, str) or not _PLATFORM_KEY.fullmatch(item) for item in platform_values):
        raise SocialTrackingError("platform keys must be lowercase identifiers")
    selected = tuple(dict.fromkeys(platform_values))
    if not selected:
        raise SocialTrackingError("at least one platform is required")
    if not isinstance(content_by_platform, Mapping):
        raise SocialTrackingError("content_by_platform must be a mapping")
    drafts = []
    for platform in selected:
        if platform not in content_by_platform:
            raise SocialTrackingError(f"missing content attribution label for {platform}")
        content = _tag(content_by_platform[platform], f"content for {platform}")
        drafts.append({
            "platform": platform,
            "tracking_url": build_tracking_url(
                destination_url, source=platform, campaign=campaign_value,
                content=content, medium=medium_value,
            ),
            "release_state": "draft_only",
        })
    return {
        "schema_version": 1,
        "release_state": "draft_only",
        "can_publish": False,
        "auto_publish": False,
        "campaign": campaign_value,
        "medium": medium_value,
        "platforms": drafts,
        "revenue_state": "not_observed",
        "note": "UTM links are attribution drafts; clicks, conversions and revenue require platform/analytics evidence.",
    }
