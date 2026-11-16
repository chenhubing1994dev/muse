"""Optional, provenance-preserving adapter for the third-party Zi Wei knowledge catalog.

The upstream package is never imported at module import time. Text quotations are
not copied into Muse; only their hashes and locators are retained so upstream
content rights remain governed by each source record.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any


class ZiweiEngineUnavailable(RuntimeError):
    """Raised when the optional Zi Wei package is not installed."""


def _value(value: Any) -> Any:
    if hasattr(value, "value"):
        return _value(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _value(item) for key, item in value.items()}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)[:500]


def _text(value: Any, field: str, limit: int, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a non-empty string no longer than {limit} characters")
    return value.strip()


def _items(catalog: Any, name: str, limit: int) -> tuple[Any, ...]:
    value = getattr(catalog, name, None)
    if not isinstance(value, (tuple, list)) or len(value) > limit:
        raise ValueError(f"catalog.{name} must be a list or tuple of at most {limit} records")
    return tuple(value)


def _record_id(record: Any, field: str = "id") -> str:
    return _text(getattr(record, field, None), field, 256)  # type: ignore[return-value]


def _existing_node(store: Any, node_type: str, external_ref: str) -> dict[str, Any] | None:
    row = store.connection.execute(
        "SELECT id FROM knowledge_nodes WHERE natural_key=? AND deleted_at IS NULL",
        (f"{node_type}:{external_ref}",),
    ).fetchone()
    return store.get_node(row["id"]) if row else None


def _upsert(
    store: Any,
    node_type: str,
    title: str,
    *,
    external_ref: str,
    summary: str | None,
    metadata: dict[str, Any],
    provenance: tuple[str, ...] = (),
) -> dict[str, Any]:
    existing = _existing_node(store, node_type, external_ref)
    if node_type == "claim" and existing is not None and existing["status"] != "proposed":
        # Never overwrite a human-reviewed claim lifecycle state via catalog refresh.
        return existing
    status = "proposed" if node_type == "claim" else "active"
    return store.upsert_node(
        node_type,
        title[:500],
        summary=summary[:2000] if summary else None,
        external_ref=external_ref,
        status=status,
        metadata=metadata,
        provenance=provenance,
    )


def sync_ziwei_knowledge_catalog(
    knowledge_store: Any,
    *,
    catalog: Any | None = None,
    catalog_version: str | None = None,
) -> dict[str, Any]:
    """Sync a Zi Wei catalog into Research Wiki without importing quotation bodies.

    Pass a catalog object for tests or offline operation. If omitted, the optional
    ziwei distribution is imported lazily and its load_catalog() function is used.
    """
    if catalog is None:
        try:
            from ziwei.knowledge import load_catalog
        except ImportError as error:
            raise ZiweiEngineUnavailable(
                "optional ziwei==0.4.1 is not installed in this Python environment"
            ) from error
        catalog = load_catalog()

    if catalog_version is None:
        try:
            catalog_version = package_version("ziwei")
        except PackageNotFoundError:
            catalog_version = "provided-catalog"
    catalog_version = _text(catalog_version, "catalog_version", 80)  # type: ignore[assignment]

    concepts = _items(catalog, "concepts", 128)
    variants = _items(catalog, "variants", 256)
    sources = _items(catalog, "sources", 64)
    citations = _items(catalog, "citations", 256)
    entries = _items(catalog, "entries", 256)
    if not sources or not citations or not concepts or not variants or not entries:
        raise ValueError("Zi Wei catalog must include sources, citations, concepts, variants, and entries")

    def index(records: tuple[Any, ...], kind: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for record in records:
            identifier = _record_id(record)
            if identifier in result:
                raise ValueError(f"duplicate {kind} id: {identifier}")
            result[identifier] = record
        return result

    source_by_id = index(sources, "source")
    citation_by_id = index(citations, "citation")
    concept_by_id = index(concepts, "concept")
    variant_by_id = index(variants, "variant")
    entry_by_id = index(entries, "entry")

    for citation in citations:
        if _record_id(citation, "source_id") not in source_by_id:
            raise ValueError(f"citation {_record_id(citation)} has unknown source_id")
    for variant in variants:
        if any(item not in concept_by_id for item in _value(getattr(variant, "concept_ids", ()))):
            raise ValueError(f"variant {_record_id(variant)} has unknown concept_ids")
    for entry in entries:
        for field, available in (
            ("concept_ids", concept_by_id),
            ("variant_ids", variant_by_id),
            ("citation_ids", citation_by_id),
        ):
            if any(item not in available for item in _value(getattr(entry, field, ()))):
                raise ValueError(f"entry {_record_id(entry)} has unknown {field}")

    identity = {
        "version": catalog_version,
        "concept_ids": sorted(concept_by_id),
        "variant_ids": sorted(variant_by_id),
        "source_ids": sorted(source_by_id),
        "citation_ids": sorted(citation_by_id),
        "entry_ids": sorted(entry_by_id),
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    catalog_ref = f"ziwei-catalog:{catalog_version}:{fingerprint}"
    catalog_node = _upsert(
        knowledge_store, "source", f"紫微斗数知识目录 {catalog_version}",
        external_ref=catalog_ref,
        summary="Versioned upstream rule and source catalog; not empirical evidence.",
        metadata={
            "catalog_kind": "third_party_ziwei_knowledge_catalog",
            "catalog_version": catalog_version,
            "catalog_fingerprint": fingerprint,
            "counts": {
                "sources": len(sources), "citations": len(citations),
                "concepts": len(concepts), "variants": len(variants), "entries": len(entries),
            },
            "upstream_code_license": "MIT",
            "content_rights_policy": "per_source_record_not_inferred_from_package_code_license",
            "not_empirical_evidence": True,
            "not_prediction": True,
        },
    )

    source_nodes: dict[str, dict[str, Any]] = {}
    for source in sources:
        source_id = _record_id(source)
        url = _text(getattr(source, "url", None), f"source.{source_id}.url", 4096)
        metadata = {
            "catalog_version": catalog_version,
            "catalog_fingerprint": fingerprint,
            "upstream_source_id": source_id,
            "edition": _text(getattr(source, "edition", ""), "source.edition", 1200, optional=True),
            "layer": _value(getattr(source, "layer", None)),
            "lineage_id": _value(getattr(source, "lineage_id", None)),
            "provenance_note": _text(getattr(source, "provenance", ""), "source.provenance", 1500, optional=True),
            "claims": _text(getattr(source, "claims", ""), "source.claims", 1500, optional=True),
            "limits": _text(getattr(source, "limits", ""), "source.limits", 1500, optional=True),
            "rights": _text(getattr(source, "rights", ""), "source.rights", 1500, optional=True),
            "upstream_status": _value(getattr(source, "status", None)),
            "authors": _value(getattr(source, "authors", ())),
            "checked_on": _value(getattr(source, "checked_on", None)),
            "not_empirical_evidence": True,
        }
        node = _upsert(
            knowledge_store, "source", _text(getattr(source, "title", None), "source.title", 500),
            external_ref=f"ziwei-source:{source_id}",
            summary=metadata["provenance_note"],
            metadata=metadata,
            provenance=(url,) if url else (),
        )
        source_nodes[source_id] = node
        knowledge_store.add_edge(catalog_node["id"], node["id"], "relates", metadata={
            "relationship_status": "catalog_contains_source",
            "not_empirical_evidence": True,
        })

    citation_nodes: dict[str, dict[str, Any]] = {}
    for citation in citations:
        citation_id = _record_id(citation)
        source_id = _record_id(citation, "source_id")
        quote = getattr(citation, "quote", None)
        if quote is not None and not isinstance(quote, str):
            raise ValueError(f"citation {citation_id} quote must be text or None")
        metadata = {
            "catalog_version": catalog_version,
            "upstream_citation_id": citation_id,
            "upstream_source_id": source_id,
            "locator": _text(getattr(citation, "locator", None), "citation.locator", 1200),
            "layer": _value(getattr(citation, "layer", None)),
            "aspects": _value(getattr(citation, "aspects", ())),
            "limits": _text(getattr(citation, "limits", ""), "citation.limits", 1500, optional=True),
            "quote_imported": False,
            "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest() if quote else None,
            "quote_length": len(quote) if quote else 0,
            "not_empirical_evidence": True,
        }
        node = _upsert(
            knowledge_store, "source", f"紫微引文定位：{metadata['locator']}",
            external_ref=f"ziwei-citation:{citation_id}",
            summary=f"Upstream citation locator for source {source_id}; quotation body intentionally omitted.",
            metadata=metadata,
            provenance=(source_by_id[source_id].url,),
        )
        citation_nodes[citation_id] = node
        knowledge_store.add_edge(node["id"], source_nodes[source_id]["id"], "relates", metadata={
            "relationship_status": "citation_locates_source",
            "not_empirical_evidence": True,
        })

    concept_nodes: dict[str, dict[str, Any]] = {}
    for concept in concepts:
        concept_id = _record_id(concept)
        metadata = {
            "catalog_version": catalog_version,
            "upstream_concept_id": concept_id,
            "concept_kind": _value(getattr(concept, "kind", None)),
            "aliases": _value(getattr(concept, "aliases", ())),
            "related_ids": _value(getattr(concept, "related_ids", ())),
            "options": _value(getattr(concept, "options", ())),
            "epistemic_status": "traditional_concept_record",
            "not_empirical_evidence": True,
        }
        node = _upsert(
            knowledge_store, "idea", _text(getattr(concept, "name", None), "concept.name", 500),
            external_ref=f"ziwei-concept:{concept_id}",
            summary="Traditional Zi Wei concept metadata; not an empirical finding.",
            metadata=metadata,
        )
        concept_nodes[concept_id] = node
        knowledge_store.add_edge(catalog_node["id"], node["id"], "relates", metadata={
            "relationship_status": "catalog_contains_concept",
            "not_empirical_evidence": True,
        })

    variant_nodes: dict[str, dict[str, Any]] = {}
    for variant in variants:
        variant_id = _record_id(variant)
        concept_ids = _value(getattr(variant, "concept_ids", ()))
        metadata = {
            "catalog_version": catalog_version,
            "upstream_variant_id": variant_id,
            "scope": _text(getattr(variant, "scope", ""), "variant.scope", 1200, optional=True),
            "concept_ids": concept_ids,
            "tracking_url": _text(getattr(variant, "tracking_url", ""), "variant.tracking_url", 4096, optional=True),
            "aliases": _value(getattr(variant, "aliases", ())),
            "school_ids": _value(getattr(variant, "school_ids", ())),
            "conditions": _value(getattr(variant, "conditions", ())),
            "option": _value(getattr(variant, "option", None)),
            "implementation": _value(getattr(variant, "implementation", None)),
            "epistemic_status": "traditional_rule_variant_not_consensus",
            "not_empirical_evidence": True,
        }
        node = _upsert(
            knowledge_store, "idea", _text(getattr(variant, "name", None), "variant.name", 500),
            external_ref=f"ziwei-variant:{variant_id}",
            summary=_text(getattr(variant, "definition", ""), "variant.definition", 2000, optional=True),
            metadata=metadata,
        )
        variant_nodes[variant_id] = node
        knowledge_store.add_edge(catalog_node["id"], node["id"], "relates", metadata={
            "relationship_status": "catalog_contains_rule_variant",
            "not_empirical_evidence": True,
        })
        for concept_id in concept_ids:
            knowledge_store.add_edge(node["id"], concept_nodes[concept_id]["id"], "relates", metadata={
                "relationship_status": "rule_variant_applies_to_concept",
                "not_empirical_evidence": True,
            })

    entry_nodes: dict[str, dict[str, Any]] = {}
    for entry in entries:
        entry_id = _record_id(entry)
        concept_ids = _value(getattr(entry, "concept_ids", ()))
        variant_ids = _value(getattr(entry, "variant_ids", ()))
        citation_ids = _value(getattr(entry, "citation_ids", ()))
        metadata = {
            "catalog_version": catalog_version,
            "catalog_fingerprint": fingerprint,
            "upstream_entry_id": entry_id,
            "concept_ids": concept_ids,
            "variant_ids": variant_ids,
            "citation_ids": citation_ids,
            "premises": _value(getattr(entry, "premises", ())),
            "premise_status": _value(getattr(entry, "premise_status", None)),
            "curation_status": _value(getattr(entry, "curation", None)),
            "show_by_default": bool(getattr(entry, "show_by_default", False)),
            "limits": _text(getattr(entry, "limits", ""), "entry.limits", 1500, optional=True),
            "exceptions": _value(getattr(entry, "exceptions", ())),
            "alternatives": _value(getattr(entry, "alternatives", ())),
            "recheck": _text(getattr(entry, "recheck", ""), "entry.recheck", 1200, optional=True),
            "tracking_url": _text(getattr(entry, "tracking_url", ""), "entry.tracking_url", 4096, optional=True),
            "epistemic_status": "traditional_framework_statement_not_empirical",
            "requires_independent_review": True,
            "not_empirical_evidence": True,
            "not_prediction": True,
        }
        statement = _text(getattr(entry, "statement", None), "entry.statement", 2000)
        node = _upsert(
            knowledge_store, "claim", statement,
            external_ref=f"ziwei-entry:{entry_id}",
            summary=statement,
            metadata=metadata,
            provenance=tuple(f"ziwei-citation:{citation_id}" for citation_id in citation_ids),
        )
        entry_nodes[entry_id] = node
        for citation_id in citation_ids:
            knowledge_store.add_edge(node["id"], citation_nodes[citation_id]["id"], "relates", metadata={
                "relationship_status": "traditional_statement_cited_by_source_locator",
                "not_empirical_evidence": True,
            })
        for variant_id in variant_ids:
            knowledge_store.add_edge(node["id"], variant_nodes[variant_id]["id"], "relates", metadata={
                "relationship_status": "traditional_statement_uses_rule_variant",
                "not_empirical_evidence": True,
            })
        for concept_id in concept_ids:
            knowledge_store.add_edge(node["id"], concept_nodes[concept_id]["id"], "relates", metadata={
                "relationship_status": "traditional_statement_concerns_concept",
                "not_empirical_evidence": True,
            })

    return {
        "catalog_version": catalog_version,
        "catalog_fingerprint": fingerprint,
        "catalog_node_id": catalog_node["id"],
        "counts": {
            "sources": len(source_nodes), "citations": len(citation_nodes),
            "concepts": len(concept_nodes), "variants": len(variant_nodes),
            "entries": len(entry_nodes),
        },
        "quote_bodies_imported": 0,
    }
