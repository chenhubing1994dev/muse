"""Epistemic envelope for every JSON API response (T5).

Every JSON body leaving ``status_api`` carries:

* ``epistemic_tag``      — one of :data:`muse.epistemics.EPISTEMIC_TAGS` (the existing 8-value enum);
* ``epistemic_label_zh`` — its Chinese display label;
* ``epistemic_scope``    — ``"response"``: the tag classifies the response as a whole; individual
  records inside may carry stricter labels of their own (e.g. metaphysics lock labels).

Route → tag is decided by the most specific prefix in :data:`ROUTE_TAGS`; anything unmatched is
``observation`` (a report of Muse's own observed system state), errors are ``observation`` too.
A pre-existing ``epistemic_tag`` in the payload is never overwritten, and symbolic routes always
stay ``symbolic_inspiration``.
"""
from __future__ import annotations

from typing import Any

from muse.epistemics import EPISTEMIC_TAG_LABELS_ZH, EPISTEMIC_TAGS, canonical_tag

DEFAULT_TAG = "observation"
ROUTE_TAGS: tuple[tuple[str, str], ...] = (
    ("/api/v1/metaphysics/", "symbolic_inspiration"),
    ("/api/v1/control/xuanshu/", "symbolic_inspiration"),
    ("/api/v1/galaxy", "symbolic_inspiration"),
    ("/api/v1/prometheus/core", "symbolic_inspiration"),
    ("/api/v1/inspirations", "hypothesis"),
    ("/api/v1/knowledge/", "interpretation"),
    ("/api/v1/radar/", "hypothesis"),
    ("/api/v1/studio/", "fiction"),
    ("/api/v1/self-improvement", "experience"),
    ("/api/v1/improvements", "experience"),
    ("/api/v1/feedback", "experience"),
    ("/api/v1/products", "observation"),
)


def tag_for_route(path: str) -> str:
    best = ""
    tag = DEFAULT_TAG
    for prefix, candidate in ROUTE_TAGS:
        if path.startswith(prefix) and len(prefix) > len(best):
            best, tag = prefix, candidate
    return tag


def attach_epistemic(payload: Any, path: str) -> Any:
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    existing = out.get("epistemic_tag")
    tag = tag_for_route(path)
    if isinstance(existing, str) and existing:
        try:
            tag = canonical_tag(existing)
        except ValueError:
            tag = tag_for_route(path)
    if tag_for_route(path) == "symbolic_inspiration":
        tag = "symbolic_inspiration"
    if tag not in EPISTEMIC_TAGS:  # fail closed: never emit an unknown tag
        tag = DEFAULT_TAG
    out["epistemic_tag"] = tag
    out["epistemic_label_zh"] = EPISTEMIC_TAG_LABELS_ZH[tag]
    out["epistemic_scope"] = "response"
    return out
