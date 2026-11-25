"""Append-only, scope-bound human visual review ledger for video products.

Reviewer identity is explicitly self-asserted; hashes detect evidence drift but
are not signatures or proof of a person's real-world identity.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CHECKLIST_KEYS = (
    "subject_framing", "shot_continuity", "visual_anomalies",
    "subtitle_visibility", "full_video_playback",
)
DECISIONS = {"approved", "changes_requested", "rejected"}
FINDING_SEVERITIES = {"blocker", "major", "minor"}
FINDING_CATEGORIES = {"framing", "continuity", "visual_artifact", "subtitle", "audio", "other"}


class VisualReviewError(ValueError):
    pass

class VisualReviewEvidenceError(VisualReviewError):
    """The selected video/release evidence cannot be trusted for this review."""



def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_visual_review_evidence(
    product_dir: str | Path, initiative_id: str, version_id: str, video_path: str | Path,
) -> dict[str, Any]:
    product_lexical = Path(product_dir)
    if product_lexical.is_symlink():
        raise VisualReviewEvidenceError("visual review product path is unsafe")
    root = product_lexical.resolve(strict=True)
    evidence_dir = root / "visual-review-evidence-v1"
    if evidence_dir.is_symlink() or evidence_dir.exists():
        from muse.video_visual_evidence import verify_visual_review_evidence_pack
        return verify_visual_review_evidence_pack(product_lexical, initiative_id, version_id, video_path)
    release = root / "publish-package-v1"
    manifest_path = release / "manifest.json"
    report_path = release / "release-quality-report.json"
    sheet_path = release / "contact-sheet.png"
    visual_quality_path = release / "visual-quality-report.json"
    for path in (release, manifest_path, report_path, sheet_path, visual_quality_path):
        if path.is_symlink():
            raise VisualReviewEvidenceError("visual review evidence path is unsafe")
    if not all(path.is_file() for path in (manifest_path, report_path, sheet_path)):
        raise FileNotFoundError("verified publish contact sheet is required before visual review")
    if manifest_path.stat().st_size > 1024 * 1024 or report_path.stat().st_size > 1024 * 1024 or sheet_path.stat().st_size > 10 * 1024 * 1024:
        raise VisualReviewEvidenceError("visual review evidence exceeds configured limits")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualReviewEvidenceError("visual review evidence metadata is invalid") from exc
    if not isinstance(manifest, dict) or not isinstance(report, dict):
        raise VisualReviewEvidenceError("visual review evidence metadata must be JSON objects")
    files = manifest.get("files")
    checks = report.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    visual = checks.get("visual_anomaly_review")
    if not isinstance(visual, dict):
        visual = {}
    visual = visual.get("contact_sheet")
    if not isinstance(visual, dict):
        visual = {}
    video_lexical = Path(video_path)
    if video_lexical.is_symlink():
        raise VisualReviewEvidenceError("source video path is unsafe")
    video = video_lexical.resolve(strict=True)
    if not video.is_file():
        raise VisualReviewEvidenceError("source video is not a regular file")
    video_hash = sha256_file(video)
    sheet_hash = sha256_file(sheet_path)
    report_hash = sha256_file(report_path)
    sheet_bytes = sheet_path.read_bytes()
    visual_quality = None
    visual_quality_hash = None
    if isinstance(files, dict) and "visual-quality-report.json" in files:
        if (visual_quality_path.is_symlink() or not visual_quality_path.is_file()
                or visual_quality_path.stat().st_size > 2 * 1024 * 1024):
            raise VisualReviewEvidenceError("visual quality report is missing, unsafe or oversized")
        try:
            visual_quality = json.loads(visual_quality_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VisualReviewEvidenceError("visual quality report is invalid") from exc
        visual_quality_hash = sha256_file(visual_quality_path)
        anomaly_check = checks.get("visual_anomaly_review")
        diagnostic_summary = anomaly_check.get("visual_quality_diagnostics") if isinstance(anomaly_check, dict) else None
        if (not isinstance(visual_quality, dict)
                or visual_quality.get("schema_version") != 1
                or visual_quality.get("semantic_recognition") is not False
                or visual_quality.get("status") not in {"observed", "unavailable"}
                or not isinstance(diagnostic_summary, dict)
                or files.get("visual-quality-report.json") != visual_quality_hash
                or diagnostic_summary.get("sha256") != visual_quality_hash
                or diagnostic_summary.get("report") != "visual-quality-report.json"
                or diagnostic_summary.get("status") != visual_quality.get("status")
                or diagnostic_summary.get("semantic_recognition") is not False):
            raise VisualReviewEvidenceError("visual quality report is not bound to release evidence")
    if (manifest.get("package_type") != "muse_manual_platform_publish_pack"
            or manifest.get("initiative_id") != initiative_id
            or manifest.get("version_id") != version_id
            or manifest.get("release_state") != "draft_only"
            or manifest.get("can_publish") is not False
            or report.get("can_publish") is not False
            or not isinstance(files, dict)
            or files.get("video.mp4") != video_hash
            or manifest.get("source_video_sha256") != video_hash
            or files.get("release-quality-report.json") != report_hash
            or files.get("contact-sheet.png") != sheet_hash
            or visual.get("status") != "available"
            or visual.get("sha256") != sheet_hash
            or len(sheet_bytes) < 24
            or sheet_bytes[:8] != b"\x89PNG\r\n\x1a\n"
            or sheet_bytes[12:16] != b"IHDR"
            or int.from_bytes(sheet_bytes[16:20], "big") <= 0
            or int.from_bytes(sheet_bytes[20:24], "big") <= 0):
        raise VisualReviewEvidenceError("visual review evidence is missing or hash-mismatched")
    return {
        "video_sha256": video_hash,
        "artifact_sha256": {"video.mp4": video_hash, "contact-sheet.png": sheet_hash},
        "contact_sheet_path": str(sheet_path),
        "release_manifest_sha256": sha256_file(manifest_path),
        "visual_quality": visual_quality,
        "visual_quality_report_sha256": visual_quality_hash,
    }


def validate_visual_review_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {
        "decision", "reviewer", "reviewer_role", "rationale", "checklist", "confirmation", "findings",
    } or not {
        "decision", "reviewer", "reviewer_role", "rationale", "checklist", "confirmation",
    }.issubset(payload):
        raise VisualReviewError("body requires decision, reviewer, reviewer_role, rationale, checklist and confirmation; optional findings are supported")
    decision = payload["decision"]
    if not isinstance(decision, str) or decision not in DECISIONS:
        raise VisualReviewError("decision must be approved, changes_requested or rejected")
    if payload["confirmation"] != "RECORD-VISUAL-REVIEW":
        raise VisualReviewError("explicit human review confirmation is required")
    reviewer = payload["reviewer"]
    role = payload["reviewer_role"]
    rationale = payload["rationale"]
    if not isinstance(reviewer, str) or not 2 <= len(reviewer.strip()) <= 80:
        raise VisualReviewError("reviewer label must contain 2-80 characters")
    if not isinstance(role, str) or not 2 <= len(role.strip()) <= 60:
        raise VisualReviewError("reviewer role must contain 2-60 characters")
    if not isinstance(rationale, str) or not 20 <= len(rationale.strip()) <= 2000:
        raise VisualReviewError("rationale must contain 20-2000 characters")
    checklist = payload["checklist"]
    if not isinstance(checklist, dict) or set(checklist) != set(CHECKLIST_KEYS):
        raise VisualReviewError("all five visual review checklist items are required")
    if any(value is not True for value in checklist.values()):
        raise VisualReviewError("complete every visual review checklist item before recording a decision")
    findings = payload.get("findings", [])
    if not isinstance(findings, list) or len(findings) > 20:
        raise VisualReviewError("findings must be a list of at most 20 items")
    normalized_findings = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict) or set(finding) != {
            "timecode_ms", "shot_id", "severity", "category", "description", "expected_fix",
        }:
            raise VisualReviewError(f"finding {index + 1} has an invalid shape")
        timecode_ms = finding["timecode_ms"]
        if isinstance(timecode_ms, bool) or not isinstance(timecode_ms, int) or not 0 <= timecode_ms <= 86_400_000:
            raise VisualReviewError(f"finding {index + 1} timecode_ms must be between 0 and 86400000")
        shot_id = finding["shot_id"]
        if not isinstance(shot_id, str) or len(shot_id.strip()) > 80:
            raise VisualReviewError(f"finding {index + 1} shot_id must contain at most 80 characters")
        severity = finding["severity"]
        category = finding["category"]
        if (not isinstance(severity, str) or severity not in FINDING_SEVERITIES
                or not isinstance(category, str) or category not in FINDING_CATEGORIES):
            raise VisualReviewError(f"finding {index + 1} severity or category is invalid")
        description, expected_fix = finding["description"], finding["expected_fix"]
        if not isinstance(description, str) or not 10 <= len(description.strip()) <= 300:
            raise VisualReviewError(f"finding {index + 1} description must contain 10-300 characters")
        if not isinstance(expected_fix, str) or not 10 <= len(expected_fix.strip()) <= 300:
            raise VisualReviewError(f"finding {index + 1} expected_fix must contain 10-300 characters")
        normalized_findings.append({
            "timecode_ms": timecode_ms, "shot_id": shot_id.strip(), "severity": severity,
            "category": category, "description": description.strip(), "expected_fix": expected_fix.strip(),
        })
    if decision == "changes_requested" and not normalized_findings:
        raise VisualReviewError("changes_requested requires at least one structured finding")
    if decision == "approved" and normalized_findings:
        raise VisualReviewError("approved review cannot contain unresolved findings")
    return {
        "decision": decision,
        "reviewer": reviewer.strip(),
        "reviewer_role": role.strip(),
        "rationale": rationale.strip(),
        "checklist": {key: True for key in CHECKLIST_KEYS},
        "findings": normalized_findings,
    }


def build_visual_revision_brief(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Derive a deterministic, immutable revision brief from the latest bound review."""
    if not isinstance(record, dict) or record.get("decision") != "changes_requested":
        return None
    subject = record.get("subject")
    findings = record.get("findings")
    review_digest = record.get("review_sha256")
    if (not isinstance(subject, dict) or not isinstance(findings, list) or not findings
            or not isinstance(review_digest, str) or re.fullmatch(r"[0-9a-f]{64}", review_digest) is None):
        raise VisualReviewError("latest changes-requested review has no valid structured findings")
    brief_seed = hashlib.sha256((review_digest + ":visual-revision-brief-v1").encode("ascii")).hexdigest()
    tasks = []
    for index, finding in enumerate(findings):
        task_seed = hashlib.sha256((brief_seed + ":" + str(index)).encode("ascii")).hexdigest()
        tasks.append({**finding, "task_id": task_seed[:24], "status": "open"})
    return {
        "brief_id": brief_seed[:24], "source_review_id": record["record_id"],
        "source_review_sha256": review_digest, "source_version": subject["version_id"],
        "source_video_sha256": subject["video_sha256"], "created_at": record["reviewed_at"],
        "status": "open", "tasks": tasks,
    }



def _prepare_ledger_path(path: str | Path, workspace_root: str | Path) -> Path:
    """Create a workspace-contained ledger path without following symlink components."""
    root = Path(workspace_root).resolve(strict=True)
    relative = Path(path)
    if relative.is_absolute() or not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise VisualReviewError("visual review ledger path must be a safe workspace-relative path")
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise VisualReviewError("visual review ledger directories cannot be symlinks")
        cursor.mkdir(exist_ok=True)
        if not cursor.is_dir() or not cursor.resolve(strict=True).is_relative_to(root):
            raise VisualReviewError("visual review ledger directory escapes the workspace")
    target = cursor / relative.parts[-1]
    for candidate in (
        target, Path(str(target) + "-wal"), Path(str(target) + "-shm"),
        Path(str(target) + "-journal"),
    ):
        if candidate.is_symlink():
            raise VisualReviewError("visual review ledger files cannot be symlinks")
    if target.exists() and not target.is_file():
        raise VisualReviewError("visual review ledger must be a regular file")
    return target


class VisualReviewLedger:
    """SQLite append-only ledger; DB triggers reject record mutation/deletion."""

    def __init__(self, path: str | Path, *, workspace_root: str | Path | None = None):
        self.path = (
            _prepare_ledger_path(path, workspace_root)
            if workspace_root is not None else Path(path)
        )
        if self.path.is_symlink():
            raise VisualReviewError("visual review ledger file cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS visual_content_reviews (
                record_id TEXT PRIMARY KEY,
                initiative_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                video_sha256 TEXT NOT NULL,
                contact_sheet_sha256 TEXT NOT NULL,
                review_sha256 TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_visual_reviews_scope
                ON visual_content_reviews(initiative_id, version_id, reviewed_at);
            CREATE TRIGGER IF NOT EXISTS visual_reviews_no_update
                BEFORE UPDATE ON visual_content_reviews
                BEGIN SELECT RAISE(ABORT, 'visual review ledger is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS visual_reviews_no_delete
                BEFORE DELETE ON visual_content_reviews
                BEGIN SELECT RAISE(ABORT, 'visual review ledger is append-only'); END;
        """)
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "VisualReviewLedger":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def append(
        self, initiative_id: str, version_id: str, payload: dict[str, Any],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(initiative_id, str) or not isinstance(version_id, str):
            raise VisualReviewError("invalid product scope")
        artifacts = evidence.get("artifact_sha256")
        video_hash = evidence.get("video_sha256")
        if (not isinstance(artifacts, dict) or set(artifacts) != {"video.mp4", "contact-sheet.png"}
                or artifacts.get("video.mp4") != video_hash
                or any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in artifacts.values())):
            raise VisualReviewError("verified evidence hashes are required")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.list_reviews(initiative_id, version_id, limit=100)
            previous_row = self.connection.execute(
                """SELECT review_sha256 FROM visual_content_reviews
                   WHERE initiative_id=? AND version_id=?
                   ORDER BY rowid DESC LIMIT 1""",
                (initiative_id, version_id),
            ).fetchone()
            previous_digest = previous_row["review_sha256"] if previous_row else None
            reviewed_at = datetime.now(timezone.utc).isoformat()
            record: dict[str, Any] = {
                "schema_version": 1,
                "record_id": uuid.uuid4().hex,
                "category": "visual_content",
                "decision": payload["decision"],
                "reviewer": {
                    "label": payload["reviewer"],
                    "role": payload["reviewer_role"],
                    "identity_assurance": "self_asserted_local_session",
                },
                "reviewed_at": reviewed_at,
                "subject": {
                    "initiative_id": initiative_id,
                    "version_id": version_id,
                    "video_sha256": video_hash,
                    "artifact_sha256": artifacts,
                    "release_manifest_sha256": evidence.get("release_manifest_sha256"),
                },
                "checklist": payload["checklist"],
                "evidence_refs": ["video.mp4", "contact-sheet.png"],
                "rationale": payload["rationale"],
                "findings": payload.get("findings", []),
                "previous_record_sha256": previous_digest,
            }
            record["review_sha256"] = hashlib.sha256(_canonical(record)).hexdigest()
            self.connection.execute(
                """INSERT INTO visual_content_reviews
                   (record_id, initiative_id, version_id, decision, reviewed_at,
                    video_sha256, contact_sheet_sha256, review_sha256, record_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record["record_id"], initiative_id, version_id, record["decision"],
                 reviewed_at, video_hash, artifacts["contact-sheet.png"],
                 record["review_sha256"], _canonical(record).decode("utf-8")),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return record

    def list_reviews(self, initiative_id: str, version_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT record_id, decision, reviewed_at, video_sha256, contact_sheet_sha256,
                      review_sha256, record_json FROM visual_content_reviews
               WHERE initiative_id=? AND version_id=?
               ORDER BY rowid ASC""",
            (initiative_id, version_id),
        ).fetchall()
        records: list[dict[str, Any]] = []
        previous_digest = None
        for row in rows:
            record = json.loads(row["record_json"])
            subject = record.get("subject") if isinstance(record, dict) else None
            artifacts = subject.get("artifact_sha256") if isinstance(subject, dict) else None
            if (not isinstance(record, dict)
                    or record.get("record_id") != row["record_id"]
                    or record.get("decision") != row["decision"]
                    or record.get("reviewed_at") != row["reviewed_at"]
                    or record.get("previous_record_sha256") != previous_digest
                    or record.get("review_sha256") != row["review_sha256"]
                    or not isinstance(subject, dict)
                    or subject.get("initiative_id") != initiative_id
                    or subject.get("version_id") != version_id
                    or subject.get("video_sha256") != row["video_sha256"]
                    or not isinstance(artifacts, dict)
                    or artifacts.get("contact-sheet.png") != row["contact_sheet_sha256"]):
                raise VisualReviewError("visual review ledger hash chain is broken")
            payload = {key: value for key, value in record.items() if key != "review_sha256"}
            expected = hashlib.sha256(_canonical(payload)).hexdigest()
            if expected != row["review_sha256"]:
                raise VisualReviewError("visual review ledger record digest is invalid")
            previous_digest = row["review_sha256"]
            records.append(record)
        return records[-max(1, min(int(limit), 100)):]
