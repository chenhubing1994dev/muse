"""Safely turn an explicitly selected offline inspiration into a local prototype.

This adapter reuses InitiativeStore and LocalProductBuilder. It does not perform
research, execute generated code, dispatch work, publish products, or invoke GPU
services. Symbolic inspiration remains a hypothesis, never evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from muse.initiative_engine import InitiativeStore
from muse.product_builder import LocalProductBuilder, ProductBuildError


class InspirationProductizationError(ValueError):
    """The inspiration cannot be converted into a safe offline prototype."""


class InspirationProductizationConflict(RuntimeError):
    """An existing deterministic Initiative conflicts with this input."""


def _bounded_text(value: Any, name: str, *, maximum: int, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise InspirationProductizationError(f"{name} must be bounded text")
    return value.strip()


def _bounded_rows(values: Iterable[str] | None, name: str, *, maximum_rows: int = 40) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes, dict)):
        raise InspirationProductizationError(f"{name} must be a list of text items")
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise InspirationProductizationError(f"{name} must be iterable") from exc
    result: list[str] = []
    for index, item in enumerate(iterator):
        if index >= maximum_rows:
            raise InspirationProductizationError(f"{name} exceeds {maximum_rows} items")
        if not isinstance(item, str) or not item.strip() or len(item) > 2000:
            raise InspirationProductizationError(f"{name} contains invalid text")
        result.append(item.strip())
    return result


def _safe_blueprint_reference(workspace: Path, value: str) -> str:
    relative = Path(_bounded_text(value, "blueprint_path", maximum=500, required=True))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise InspirationProductizationError("blueprint_path must be a safe workspace-relative path")
    candidate = workspace / relative
    if candidate.is_symlink():
        raise InspirationProductizationError("blueprint_path must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(workspace)
    except (OSError, ValueError) as exc:
        raise InspirationProductizationError("blueprint_path must resolve inside the workspace") from exc
    if not resolved.is_file():
        raise InspirationProductizationError("blueprint_path must reference a file")
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise InspirationProductizationError("blueprint_path contains a symlink")
    return relative.as_posix()


def build_offline_inspiration_product(
    *,
    workspace: str | Path,
    inspiration_id: str,
    title: str,
    summary: str,
    blueprint_path: str,
    report: dict[str, Any],
    hypotheses: Iterable[str] = (),
    uncertainties: Iterable[str] | None = None,
    counter_evidence: Iterable[str] | None = None,
    experiments: Iterable[str] | None = None,
    evolution_cycle_id: str | None = None,
    symbolic_framework_id: str | None = None,
    symbolic_framework_ids: Iterable[str] | None = None,
    allow_external_research: bool = False,
) -> dict[str, Any]:
    """Create/reuse a draft Initiative and build its verified offline product.

    The Initiative identity is deterministic per inspiration and idempotent for
    unchanged inputs. Existing records with different content are rejected rather
    than silently reused or overwritten. The resulting product remains in
    testing until a human reviews it.
    ``symbolic_framework_ids`` preserves multiple inspiration lenses without
    collapsing the four symbolic traditions into one source label.
    """
    if allow_external_research is not False:
        raise InspirationProductizationError("offline productization requires allow_external_research=False")
    if not isinstance(report, dict):
        raise InspirationProductizationError("report must be an object")
    if report.get("synthesis_status") != "local_offline_only":
        raise InspirationProductizationError("report must be local_offline_only")
    if "external_network_called" in report:
        if not isinstance(report["external_network_called"], bool):
            raise InspirationProductizationError("report.external_network_called must be a boolean")
        if report["external_network_called"]:
            raise InspirationProductizationError("offline report cannot claim external network activity")
    if "allow_external_research" in report:
        if not isinstance(report["allow_external_research"], bool):
            raise InspirationProductizationError("report.allow_external_research must be a boolean")
        if report["allow_external_research"]:
            raise InspirationProductizationError("offline report cannot allow external research")

    root = Path(workspace).resolve()
    if not root.is_dir():
        raise InspirationProductizationError("workspace must be an existing directory")
    inspiration_key = _bounded_text(inspiration_id, "inspiration_id", maximum=128, required=True)
    clean_title = _bounded_text(title, "title", maximum=300, required=True)
    clean_summary = _bounded_text(summary, "summary", maximum=3000, required=True)
    blueprint_ref = _safe_blueprint_reference(root, blueprint_path)
    if symbolic_framework_id is not None and symbolic_framework_ids is not None:
        raise InspirationProductizationError("provide symbolic_framework_id or symbolic_framework_ids, not both")
    if symbolic_framework_ids is None:
        framework_ids = (
            [] if symbolic_framework_id is None
            else [_bounded_text(symbolic_framework_id, "symbolic_framework_id", maximum=100, required=True)]
        )
    else:
        framework_ids = _bounded_rows(symbolic_framework_ids, "symbolic_framework_ids", maximum_rows=4)
        if len(set(framework_ids)) != len(framework_ids):
            raise InspirationProductizationError("symbolic_framework_ids must not contain duplicates")
    framework_id = framework_ids[0] if len(framework_ids) == 1 else None
    known_frameworks = {"iching", "qimen", "ziwei", "liu_ren"}
    framework_status = (
        "not_provided" if not framework_ids
        else "recognized_framework" if len(framework_ids) == 1 and framework_ids[0] in known_frameworks
        else "recognized_frameworks" if all(item in known_frameworks for item in framework_ids)
        else "unrecognized_source_metadata"
    )
    epistemic_status = "symbolic_hypothesis_only" if framework_ids else "inspiration_hypothesis_only"

    synthesis = report.get("synthesis")
    if not isinstance(synthesis, dict):
        raise InspirationProductizationError("offline report synthesis must be an object")
    clean_hypotheses = _bounded_rows(hypotheses, "hypotheses")
    creator_hypotheses = [
        f"【创作者假设／待验证，非证据】{item}"
        for item in _bounded_rows(synthesis.get("creator_hypotheses", []), "creator_hypotheses")
    ]
    clean_hypotheses = _bounded_rows([*creator_hypotheses, *clean_hypotheses], "hypotheses")
    clean_uncertainties = _bounded_rows(
        uncertainties if uncertainties is not None else synthesis.get("uncertainties", []),
        "uncertainties",
    )
    clean_counter_evidence = _bounded_rows(
        counter_evidence if counter_evidence is not None else synthesis.get("counter_evidence", []),
        "counter_evidence",
    )
    clean_experiments = _bounded_rows(
        experiments if experiments is not None else synthesis.get("testable_experiments", []),
        "experiments",
    )
    if not clean_hypotheses:
        raise InspirationProductizationError("product readiness requires at least one testable hypothesis")
    if not clean_experiments:
        raise InspirationProductizationError("product readiness requires at least one experiment")
    executive_summary = _bounded_text(
        synthesis.get("executive_summary", clean_summary),
        "executive_summary",
        maximum=1200,
    )
    stable_suffix = hashlib.sha256(inspiration_key.encode("utf-8")).hexdigest()[:12]
    initiative_title = f"离线灵感原型｜{stable_suffix}"
    concept_data = {
        "research_question": clean_summary,
        "inspiration_title": clean_title,
        "key_discoveries": {
            "executive_summary": executive_summary,
            "creator_hypothesis_summary": executive_summary,
            "epistemic_status": "creator_inspired_hypothesis_only",
            "evidence_review_state": "not_researched",
            "creator_inspired_hypotheses": clean_hypotheses,
            "uncertainties": clean_uncertainties,
            "counter_evidence": clean_counter_evidence,
            "testable_experiments": clean_experiments,
            "tags": _bounded_rows(synthesis.get("tags", []), "tags", maximum_rows=12),
            "research_domains": _bounded_rows(synthesis.get("research_domains", []), "research_domains", maximum_rows=12),
        },
        "inspiration_productization": {
            "schema_version": 1,
            "source": "offline_inspiration",
            "inspiration_id": inspiration_key,
            "symbolic_framework_id": framework_id,
            "symbolic_framework_ids": framework_ids,
            "symbolic_framework_status": framework_status,
            "blueprint_path": blueprint_ref,
            "epistemic_status": epistemic_status,
            "symbolic_inspiration_is_evidence": False,
            "external_network_called": False,
            "requires_human_review": True,
        },
    }
    concept = json.dumps(concept_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(concept) > 3800:
        raise InspirationProductizationError("product concept exceeds InitiativeStore's safe storage budget")
    references = [f"inspiration:{inspiration_key}", blueprint_ref]
    expected_fields = {
        "title": initiative_title,
        "kind": "tool",
        "creator_value": "把创造者的离线灵感转化为可检验、可持续积累的本地工具。",
        "concept": concept,
        "first_step": "离线打开原型，检查假设、反证、实验与不确定项，再提交人工反馈。",
        "expected_output": "经过完整性校验、等待人工验收的离线交互式灵感实验台。",
        "capability_gap": "离线灵感尚未形成可交互、可测试的本地产品原型。",
        "priority": 65,
        "autonomy_level": "draft",
    }
    database = root / "data" / "initiatives" / "muse.sqlite3"
    with InitiativeStore(database) as store:
        initiative = store.propose(
            expected_fields["title"],
            kind=expected_fields["kind"],
            creator_value=expected_fields["creator_value"],
            concept=expected_fields["concept"],
            first_step=expected_fields["first_step"],
            expected_output=expected_fields["expected_output"],
            inspiration_refs=references,
            capability_gap=expected_fields["capability_gap"],
            priority=expected_fields["priority"],
            autonomy_level=expected_fields["autonomy_level"],
        )
        for field, expected in expected_fields.items():
            if initiative.get(field) != expected:
                raise InspirationProductizationConflict(
                    f"existing Initiative conflicts with this inspiration: {field}"
                )
        if initiative.get("inspiration_refs") != references:
            raise InspirationProductizationConflict("existing Initiative has different inspiration references")
        if initiative.get("status") not in {"proposed", "testing"}:
            raise InspirationProductizationConflict("existing Initiative is not eligible for draft productization")
        builder = LocalProductBuilder(root)
        product = builder.build(store, initiative["id"], evolution_cycle_id=evolution_cycle_id)
        verified = builder.verify(store, initiative["id"])
        if verified.get("package_integrity") != "passed":
            raise ProductBuildError("offline inspiration product package integrity did not pass")
        initiative_status = (store.get_by_id(initiative["id"]) or {}).get("status", "proposed")

    return {
        "status": "completed",
        "initiative_id": initiative["id"],
        "initiative_status": initiative_status,
        "product": product,
        "verified": verified.get("package_integrity") == "passed",
        "package_integrity": verified.get("package_integrity"),
        "validated": False,
        "publishable": False,
        "provenance": {
            "source": "offline_inspiration",
            "inspiration_id": inspiration_key,
            "symbolic_framework_id": framework_id,
            "symbolic_framework_ids": framework_ids,
            "symbolic_framework_status": framework_status,
            "blueprint_path": blueprint_ref,
            "epistemic_status": epistemic_status,
            "symbolic_inspiration_is_evidence": False,
            "external_network_called": False,
        },
        "approval_required": True,
        "human_review_required": True,
        "published": False,
        "dispatch_started": False,
        "gpu_work_started": False,
        "automatic_code_changes": False,
    }
