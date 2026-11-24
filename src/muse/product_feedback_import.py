"""Import a user's offline Muse product worksheet as an unverified experiment record.

The importer never promotes claims, changes Initiative lifecycle state, dispatches
work, or treats creator observations as external factual evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from muse.initiative_engine import InitiativeStore
from muse.product_builder import LocalProductBuilder, ProductBuildError
from muse.research_knowledge import ResearchKnowledgeStore

MAX_IMPORT_BYTES = 1024 * 1024
_MAX_ROWS = 40
_MAX_TEXT = 12_000
_ROOT_KEYS = {
    "initiative_id", "title", "objective", "stable", "changing", "tensions",
    "lessons", "hypotheses", "experiments",
}
_HYPOTHESIS_KEYS = {"title", "statement", "test", "falsifier"}
_EXPERIMENT_KEYS = {"title", "method", "expected", "result"}
_TEXT_FIELDS = ("title", "objective", "stable", "changing", "tensions", "lessons")


class ProductFeedbackImportError(ValueError):
    """The exported product worksheet is invalid or cannot be safely attached."""


def parse_product_feedback_json(raw: bytes) -> Any:
    """Parse an exported worksheet while rejecting ambiguous duplicate JSON keys."""
    if not isinstance(raw, bytes):
        raise ProductFeedbackImportError("worksheet input must be UTF-8 JSON bytes")
    if len(raw) > MAX_IMPORT_BYTES:
        raise ProductFeedbackImportError("worksheet exceeds the 1 MiB import limit")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProductFeedbackImportError("worksheet contains duplicate JSON object keys")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except ProductFeedbackImportError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProductFeedbackImportError("worksheet is not valid UTF-8 JSON") from exc


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > _MAX_TEXT or "\x00" in value
            or (required and not value.strip())):
        raise ProductFeedbackImportError(f"{field} must be bounded text")
    return value.strip()


def _rows(value: Any, field: str, keys: set[str]) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > _MAX_ROWS:
        raise ProductFeedbackImportError(f"{field} must be a list of at most {_MAX_ROWS} rows")
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != keys:
            raise ProductFeedbackImportError(f"{field}[{index}] has an unsupported schema")
        result.append({key: _text(item[key], f"{field}[{index}].{key}") for key in sorted(keys)})
    return result


def _linked_inspiration_node_id(initiative: dict[str, Any]) -> str | None:
    """Read the exact source claim recorded by offline inspiration productization."""
    concept_raw = initiative.get("concept")
    if not isinstance(concept_raw, str):
        return None
    try:
        concept = json.loads(concept_raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(concept, dict) or "inspiration_productization" not in concept:
        return None
    productization = concept.get("inspiration_productization")
    if not isinstance(productization, dict):
        raise ProductFeedbackImportError("Initiative inspiration productization metadata is invalid")
    inspiration_id = productization.get("inspiration_id")
    if not isinstance(inspiration_id, str) or not re.fullmatch(r"[0-9a-f]{32}", inspiration_id):
        raise ProductFeedbackImportError("Initiative inspiration node reference is invalid")
    return inspiration_id


def import_offline_product_feedback(
    workspace: str | Path, payload: dict[str, Any], *, max_bytes: int = MAX_IMPORT_BYTES,
) -> dict[str, Any]:
    """Persist one exported worksheet as an idempotent Research Wiki experiment node."""
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ProductFeedbackImportError("workspace must be a directory")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_IMPORT_BYTES:
        raise ProductFeedbackImportError("max_bytes must be between 1 and 1 MiB")
    if not isinstance(payload, dict) or set(payload) != _ROOT_KEYS:
        raise ProductFeedbackImportError("worksheet does not match the Muse offline product export schema")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ProductFeedbackImportError("worksheet contains invalid JSON values") from exc
    if len(encoded) > max_bytes:
        raise ProductFeedbackImportError("worksheet exceeds the 1 MiB import limit")

    initiative_id = payload.get("initiative_id")
    if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
        raise ProductFeedbackImportError("initiative_id must be a 32-character lowercase hex ID")
    clean = {field: _text(payload[field], field, required=field in {"title", "objective"}) for field in _TEXT_FIELDS}
    hypotheses = _rows(payload.get("hypotheses"), "hypotheses", _HYPOTHESIS_KEYS)
    experiments = _rows(payload.get("experiments"), "experiments", _EXPERIMENT_KEYS)
    meaningful = any(clean[field] for field in ("stable", "changing", "tensions", "lessons"))
    meaningful = meaningful or any(any(row.values()) for row in hypotheses)
    meaningful = meaningful or any(any(row.values()) for row in experiments)
    if not meaningful:
        raise ProductFeedbackImportError("worksheet contains no creator observations or experiment notes")

    initiative_database = root / "data" / "initiatives" / "muse.sqlite3"
    knowledge_database = root / "data" / "research" / "knowledge.sqlite3"
    with InitiativeStore(initiative_database) as initiatives:
        initiative = initiatives.get_by_id(initiative_id)
        if initiative is None:
            raise ProductFeedbackImportError("the referenced Initiative does not exist")
        if initiative.get("status") != "testing":
            raise ProductFeedbackImportError("product feedback can only attach to an Initiative in testing")
        if initiative.get("title") != clean["title"]:
            raise ProductFeedbackImportError("worksheet title does not match the Initiative")
        inspiration_node_id = _linked_inspiration_node_id(initiative)
        try:
            artifact = LocalProductBuilder(root).verify(initiatives, initiative_id)
        except (ProductBuildError, OSError, ValueError, KeyError) as exc:
            raise ProductFeedbackImportError("the current offline product artifact failed integrity verification") from exc
        if artifact.get("validated") is not True or artifact.get("status") != "testing":
            raise ProductFeedbackImportError("the current offline product is not verified and awaiting review")

        normalized = {
            "schema_version": 1,
            "initiative_id": initiative_id,
            **clean,
            "hypotheses": hypotheses,
            "experiments": experiments,
            "product_input_sha256": artifact.get("input_sha256"),
            "product_package_sha256": artifact.get("package_sha256"),
        }
        digest = hashlib.sha256(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        external_ref = f"offline-product-feedback:{initiative_id}:{digest}"
        summary_parts = [clean[field] for field in ("stable", "changing", "tensions", "lessons") if clean[field]]
        summary_parts.extend(
            f"假设：{row['title']}；观察：{row['statement']}；验证：{row['test']}；反证：{row['falsifier']}"
            for row in hypotheses if any(row.values())
        )
        summary_parts.extend(
            f"实验：{row['title']}；方法：{row['method']}；预期：{row['expected']}；结果：{row['result']}"
            for row in experiments if any(row.values())
        )
        summary = "\n".join(summary_parts)[:2000] or clean["objective"][:2000]
        metadata = {
            "origin": "offline_product_creator_feedback",
            "initiative_id": initiative_id,
            "product_input_sha256": artifact.get("input_sha256"),
            "inspiration_node_id": inspiration_node_id,
            "product_package_sha256": artifact.get("package_sha256"),
            "worksheet_sha256": digest,
            "objective": clean["objective"],
            "stable_conditions": clean["stable"],
            "changing_events": clean["changing"],
            "unresolved_tensions": clean["tensions"],
            "lessons": clean["lessons"],
            "hypotheses": hypotheses,
            "experiments": experiments,
            "epistemic_status": "creator_recorded_observation_unverified",
            "is_external_factual_evidence": False,
            "requires_independent_review": True,
            "claim_promotion_allowed": False,
        }
        with ResearchKnowledgeStore(knowledge_database) as knowledge:
            inspiration_node = knowledge.get_node(inspiration_node_id) if inspiration_node_id else None
            if inspiration_node_id and (inspiration_node is None or inspiration_node.get("node_type") != "claim"):
                raise ProductFeedbackImportError("the Initiative's source inspiration claim is missing or invalid")
            existing = knowledge.connection.execute(
                "SELECT id FROM knowledge_nodes WHERE natural_key=? AND deleted_at IS NULL",
                ("experiment:" + external_ref,),
            ).fetchone()
            node = knowledge.upsert_node(
                "experiment", f"离线产品实验反馈：{clean['title'][:160]}",
                summary=summary, external_ref=external_ref, metadata=metadata,
                provenance=(f"creator-initiative:{initiative_id}", f"product-package-sha256:{artifact.get('package_sha256')}"),
            )
            if inspiration_node is not None:
                knowledge.add_edge(
                    inspiration_node["id"], node["id"], "tested_by",
                    metadata={
                        "origin": "offline_product_creator_feedback",
                        "initiative_id": initiative_id,
                        "worksheet_sha256": digest,
                        "product_package_sha256": artifact.get("package_sha256"),
                        "epistemic_status": "creator_recorded_observation_unverified",
                        "claim_promotion_allowed": False,
                    },
                )
            knowledge.link_node_to_initiative(
                initiatives, initiative_id, node["id"], relationship="related",
            )
    return {
        "status": "completed",
        "knowledge_node_id": node["id"],
        "inspiration_node_id": inspiration_node_id,
        "inspiration_linked": inspiration_node is not None,
        "initiative_id": initiative_id,
        "worksheet_sha256": digest,
        "created": existing is None,
        "idempotent": True,
        "epistemic_status": "creator_recorded_observation_unverified",
        "requires_independent_review": True,
        "claim_promotion_allowed": False,
        "initiative_status_changed": False,
        "dispatch_started": False,
    }
