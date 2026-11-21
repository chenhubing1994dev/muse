"""Read-only provenance trace for MPT-derived Muse video recipes.

This module never opens a writable SQLite connection, schedules work, or publishes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


def _read_only_connection(path: Path) -> sqlite3.Connection | None:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents) or not path.is_file():
        return None
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _digest(value: Any) -> str | None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_json(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _approval_trace(
    task_db: Path, task_id: str | None, initiative_id: str | None,
    attempt_id: str, brief: dict[str, Any],
) -> dict[str, Any]:
    if not task_id:
        return {"ticket_id": None, "status": "not_linked", "human_decision_verified": False}
    connection = _read_only_connection(task_db)
    if connection is None:
        return {"ticket_id": task_id, "status": "store_unavailable", "human_decision_verified": False}
    try:
        task = connection.execute("SELECT id,status,payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            return {"ticket_id": task_id, "status": "ticket_not_found", "human_decision_verified": False}
        payload = _safe_json(task["payload_json"])
        approval = _safe_json(payload.get("_approval"))
        events = connection.execute(
            "SELECT event_type,actor,payload_json,created_at FROM task_events WHERE task_id=? ORDER BY created_at,rowid LIMIT 500",
            (task_id,),
        ).fetchall()
        decision_verified = any(
            event["event_type"] == "human.decision"
            and event["actor"] == approval.get("approved_by")
            and _safe_json(event["payload_json"]).get("decision") == "approved"
            for event in events
        )
        brief_payload = _safe_json(payload.get("video_brief"))
        stored_digest = _digest(brief)
        ticket_digest = _digest(brief_payload)
        brief_matches = bool(stored_digest and ticket_digest and stored_digest == ticket_digest
                             and payload.get("video_brief_sha256") == stored_digest)
        scope_matches = (
            payload.get("kind") == "business_opportunity_video_approval"
            and payload.get("approval_scope") == "one_video_attempt_only"
        )
        approved_flag = payload.get("_approved") is True
        initiative_matches = (
            payload.get("initiative_id") == initiative_id if initiative_id is not None
            else payload.get("initiative_id") is None
        )
        attempt_matches = payload.get("attempt_id") == attempt_id
        verified = (
            decision_verified and brief_matches and scope_matches and approved_flag
            and initiative_matches and attempt_matches
        )
        return {
            "ticket_id": task_id,
            "status": task["status"],
            "human_decision_verified": bool(verified),
            "decision_actor": approval.get("approved_by") if verified else None,
            "brief_matches_creator_brief": bool(brief_matches),
            "attempt_matches_ticket": bool(attempt_matches),
        }
    except sqlite3.Error:
        return {"ticket_id": task_id, "status": "store_unavailable", "human_decision_verified": False}
    finally:
        connection.close()


def build_video_recipe_trace(
    workspace: str | Path,
    attempt_id: str,
    *,
    initiative_db: str | Path | None = None,
    task_db: str | Path | None = None,
) -> dict[str, Any] | None:
    """Build a privacy-minimal trace; None means the attempt does not exist."""
    if not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", attempt_id):
        raise ValueError("attempt_id must be 1-64 safe identifier characters")
    root = Path(workspace)
    if root.is_symlink():
        raise ValueError("workspace root must not be a symlink")
    root = root.resolve()
    initiative_path = Path(initiative_db) if initiative_db is not None else root / "data" / "initiatives" / "muse.sqlite3"
    connection = _read_only_connection(initiative_path)
    if connection is None:
        raise FileNotFoundError("initiative store is unavailable")
    try:
        attempt_row = connection.execute(
            "SELECT * FROM initiative_video_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if attempt_row is None:
            return None
        attempt = dict(attempt_row)
        events = [
            {**dict(row), "details": _safe_json(row["details_json"])}
            for row in connection.execute(
                "SELECT event_type,details_json,created_at FROM initiative_video_attempt_events "
                "WHERE attempt_id=? ORDER BY sequence", (attempt_id,),
            ).fetchall()
        ]
        initiative = None
        if attempt.get("initiative_id"):
            row = connection.execute(
                "SELECT id,title,concept,status FROM creator_initiatives WHERE id=?",
                (attempt["initiative_id"],),
            ).fetchone()
            initiative = dict(row) if row is not None else None
    except sqlite3.Error as exc:
        raise FileNotFoundError("initiative store schema is unavailable") from exc
    finally:
        connection.close()

    concept = _safe_json(initiative.get("concept")) if initiative else {}
    creator_brief = _safe_json(concept.get("creator_brief"))
    brief = _safe_json(creator_brief.get("video"))
    brief_hash = brief.get("recipe_sha256")
    attempt_hash = attempt.get("recipe_sha256")
    fingerprint_matches = (
        attempt_hash is None and brief_hash is None
        or isinstance(attempt_hash, str) and attempt_hash == brief_hash
    )
    recipe = {
        "candidate_id": brief.get("candidate_id") if isinstance(brief.get("candidate_id"), str) else None,
        "recipe_sha256": attempt_hash if isinstance(attempt_hash, str) else None,
        "creator_brief_sha256": brief_hash if isinstance(brief_hash, str) else None,
        "brief_fingerprint_matches_attempt": bool(fingerprint_matches),
        "mapping_status": (
            _safe_json(brief.get("mapping_report")).get("status")
            if brief.get("mapping_report") is not None else None
        ),
    }
    ticket = _approval_trace(
        Path(task_db) if task_db is not None else root / "data" / "tasks" / "muse.sqlite3",
        attempt.get("approval_task_id"), attempt.get("initiative_id"), attempt_id, brief,
    )

    product: dict[str, Any] = {"status": "not_built"}
    release: dict[str, Any] = {
        "status": "not_prepared", "can_publish": False,
        "note": "A publish package is a manual-review draft, never publish authorization.",
    }
    if attempt.get("status") == "succeeded" and attempt.get("product_id") and attempt.get("version_id"):
        products_root = root / "data" / "products"
        try:
            from muse.video_product_builder import VideoProductBuilder, VideoProductError
            verified = VideoProductBuilder.verify_path(
                products_root, attempt["product_id"], attempt["version_id"],
            )
            manifest_matches = (
                attempt.get("manifest_sha256") is None
                or attempt.get("manifest_sha256") == verified.get("manifest_sha256")
            )
            product_recipe_matches = verified.get("recipe_sha256") == attempt_hash
            product = {
                "status": "verified" if manifest_matches and product_recipe_matches else "mismatch",
                "product_id": attempt["product_id"],
                "version_id": attempt["version_id"],
                "manifest_sha256": verified.get("manifest_sha256"),
                "manifest_matches_attempt": bool(manifest_matches),
                "recipe_matches_attempt": bool(product_recipe_matches),
                "mp4_sha256": verified.get("mp4_sha256"),
                "package_sha256": verified.get("package_sha256"),
                "quality_gate": _safe_json(verified.get("quality_gate")),
            }
            product_dir = products_root / attempt["product_id"] / attempt["version_id"]
            release_dir = product_dir / "publish-package-v1"
            checklist_path = release_dir / "release-checklist.json"
            package_manifest_path = release_dir / "manifest.json"
            quality_report_path = release_dir / "release-quality-report.json"
            if (release_dir.is_symlink() or checklist_path.is_symlink()
                    or package_manifest_path.is_symlink() or quality_report_path.is_symlink()):
                release = {"status": "unsafe_path", "can_publish": False}
            elif checklist_path.is_file() and package_manifest_path.is_file():
                checklist = _safe_json(checklist_path.read_text(encoding="utf-8"))
                package_manifest = _safe_json(package_manifest_path.read_text(encoding="utf-8"))
                package_matches_product = (
                    package_manifest.get("package_type") == "muse_manual_platform_publish_pack"
                    and package_manifest.get("initiative_id") == attempt["product_id"]
                    and package_manifest.get("version_id") == attempt["version_id"]
                    and package_manifest.get("source_video_sha256") == product.get("mp4_sha256")
                    and package_manifest.get("muse_product_zip_sha256") == product.get("package_sha256")
                )
                report_hash = None
                report = {}
                report_matches_package = False
                files = package_manifest.get("files")
                expected_report_hash = files.get("release-quality-report.json") if isinstance(files, dict) else None
                if quality_report_path.is_file() and isinstance(expected_report_hash, str):
                    report_hash = hashlib.sha256(quality_report_path.read_bytes()).hexdigest()
                    report = _safe_json(quality_report_path.read_text(encoding="utf-8"))
                    embedded_report = package_manifest.get("release_quality_report")
                    report_matches_package = (
                        len(expected_report_hash) == 64
                        and report_hash == expected_report_hash
                        and isinstance(embedded_report, dict)
                        and _digest(report) == _digest(embedded_report)
                    )
                release_valid = (
                    package_matches_product and report_matches_package
                    and checklist.get("release_state") == "draft_only"
                    and package_manifest.get("release_state") == "draft_only"
                    and checklist.get("can_publish") is False
                    and package_manifest.get("can_publish") is False
                    and report.get("can_publish") is False
                )
                release = {
                    "status": "draft_only" if release_valid else "invalid_or_mismatched",
                    "manifest_matches_product": bool(package_matches_product),
                    "quality_report_matches_package": bool(report_matches_package),
                    "quality_report_sha256": report_hash if report_matches_package else None,
                    "overall_status": report.get("overall_status") if report_matches_package else "unavailable_or_unverified",
                    "blocking_items": report.get("blocking_items", []) if report_matches_package and isinstance(report.get("blocking_items"), list) else [],
                    "checks": report.get("checks", {}) if report_matches_package and isinstance(report.get("checks"), dict) else {},
                    "can_publish": False,
                    "editorial_and_fact_review": checklist.get("editorial_and_fact_review", "unknown"),
                    "visual_and_model_rights_review": checklist.get("visual_and_model_rights_review", "unknown"),
                    "privacy_and_person_release_review": checklist.get("privacy_and_person_release_review", "unknown"),
                    "account_authorization": checklist.get("account_authorization", "unknown"),
                    "official_publish_api": checklist.get("official_publish_api", "unknown"),
                }
            review_ledger_path = root / "data" / "reviews" / "visual-content-reviews.sqlite3"
            evidence_pack_dir = product_dir / "visual-review-evidence-v1"
            if release.get("status") == "draft_only" or evidence_pack_dir.is_dir():
                try:
                    from muse.video_human_review import VisualReviewLedger, verify_visual_review_evidence
                    from muse.video_publish_package import _review_record_status
                    evidence = verify_visual_review_evidence(
                        product_dir, attempt["product_id"], attempt["version_id"],
                        product_dir / "video.mp4",
                    )
                    with VisualReviewLedger(
                        "data/reviews/visual-content-reviews.sqlite3", workspace_root=root,
                    ) as review_ledger:
                        saved_reviews = review_ledger.list_reviews(
                            attempt["product_id"], attempt["version_id"], limit=100,
                        )
                    if saved_reviews:
                        latest = saved_reviews[-1]
                        evidence_status = _review_record_status(
                            latest, category="visual_content",
                            initiative_id=attempt["product_id"],
                            version_id=attempt["version_id"],
                            video_sha256=evidence["video_sha256"],
                            required_artifacts=evidence["artifact_sha256"],
                            release_manifest_sha256=evidence["release_manifest_sha256"],
                        )
                        checks = dict(release.get("checks", {}))
                        checks["visual_content_review"] = {
                            "status": evidence_status,
                            "evidence": "append-only visual review ledger",
                            "record_id": latest.get("record_id"),
                            "reviewer": latest.get("reviewer", {}).get("label"),
                            "reviewed_at": latest.get("reviewed_at"),
                            "review_sha256": latest.get("review_sha256"),
                            "identity_assurance": "self_asserted_local_session",
                        }
                        visual_anomaly = checks.get("visual_anomaly_review")
                        if isinstance(visual_anomaly, dict):
                            visual_anomaly = dict(visual_anomaly)
                            visual_anomaly["status"] = evidence_status
                            visual_anomaly["human_review_record_id"] = latest.get("record_id")
                            checks["visual_anomaly_review"] = visual_anomaly
                        if release.get("status") == "draft_only":
                            blockers = [
                                name for name, item in checks.items()
                                if not isinstance(item, dict)
                                or item.get("status") not in {
                                    "passed", "verified_human_review_digest_scope_bound",
                                }
                            ]
                            release["blocking_items"] = blockers
                            release["overall_status"] = (
                                "blocked_pending_human_review" if blockers
                                else "ready_for_separate_release_authorization"
                            )
                        else:
                            release["blocking_items"] = ["release_package_not_prepared"]
                            release["overall_status"] = "blocked_pending_release_package"
                        release["checks"] = checks
                        release["visual_content_review_live_overlay"] = True
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as review_error:
                    release["visual_content_review_live_overlay"] = "unavailable_or_mismatched"
                    release["visual_content_review_error_type"] = type(review_error).__name__
        except (OSError, ValueError, KeyError, json.JSONDecodeError, VideoProductError):
            product = {"status": "unavailable_or_invalid"}
    elif attempt.get("status") == "failed":
        product = {"status": "not_built", "failure_code": attempt.get("failure_code"), "error_type": attempt.get("error_type")}
    approval_consistent = not attempt.get("approval_task_id") or ticket.get("human_decision_verified") is True
    if attempt.get("status") == "succeeded":
        product_consistent = product.get("status") == "verified"
    elif attempt.get("status") in {"queued", "running", "failed", "cancelled"}:
        product_consistent = product.get("status") == "not_built"
    else:
        product_consistent = False
    chain_consistent = bool(fingerprint_matches and approval_consistent and product_consistent)
    return {
        "schema_version": 1,
        "read_only": True,
        "attempt": {
            "attempt_id": attempt_id, "status": attempt.get("status"),
            "mode": attempt.get("mode"), "created_at": attempt.get("created_at"),
            "started_at": attempt.get("started_at"), "finished_at": attempt.get("finished_at"),
            "approval_task_id": attempt.get("approval_task_id"),
            "failure_code": attempt.get("failure_code"),
        },
        "candidate": recipe,
        "approval": ticket,
        "product": product,
        "release_review": release,
        "events": events,
        "chain_consistent": chain_consistent,
        "execution": "not_started" if attempt.get("status") == "queued" else "already_recorded",
        "publishing": "not_performed",
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only trace of an MPT-derived Muse video recipe")
    parser.add_argument("attempt_id")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--initiative-db")
    parser.add_argument("--task-db")
    args = parser.parse_args(argv)
    try:
        trace = build_video_recipe_trace(
            args.workspace, args.attempt_id,
            initiative_db=args.initiative_db, task_db=args.task_db,
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "unavailable", "error": str(exc)}, ensure_ascii=False))
        return 2
    if trace is None:
        print(json.dumps({"status": "not_found", "attempt_id": args.attempt_id}, ensure_ascii=False))
        return 1
    print(json.dumps(trace, ensure_ascii=False, indent=2))
    return 0 if trace["chain_consistent"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
