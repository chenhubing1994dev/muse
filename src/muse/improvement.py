"""Durable, approval-gated self-improvement proposal ledger.

This module records ideas and research provenance. It never downloads,
executes, installs, or promotes third-party code by itself.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_LICENSE_STATES = {"permissive", "restrictive", "unknown", "not_applicable"}
_SECURITY_STATES = {"reviewed", "needs_review", "blocked"}
_FEEDBACK_CATEGORIES = frozenset({"reliability", "accuracy", "usability", "capability", "performance"})
_FEEDBACK_TAGS = frozenset({
    "repeated_manual_fix", "repeated_retry", "missing_tool", "incorrect_result",
    "slow_response", "workflow_gap", "hard_to_use",
})
_ALLOWED = {
    "proposed": {"researching", "rejected"},
    "researching": {"ready_for_review", "rejected"},
    "ready_for_review": {"researching", "rejected"},
    "approved": {"implemented"},
    "implemented": {"rolled_back"},
    "rejected": set(),
    "rolled_back": set(),
}


class ImprovementStore:
    """SQLite-backed proposal, source-provenance and transition audit store."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS improvement_proposals (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                problem TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 50,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                source_feedback_id TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS improvement_sources (
                id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL REFERENCES improvement_proposals(id),
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                license_status TEXT NOT NULL,
                security_status TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS improvement_events (
                id TEXT PRIMARY KEY,
                proposal_id TEXT NOT NULL REFERENCES improvement_proposals(id),
                from_status TEXT,
                to_status TEXT NOT NULL,
                actor TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS improvement_feedback (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                tag TEXT NOT NULL,
                severity INTEGER NOT NULL CHECK(severity BETWEEN 1 AND 5),
                created_at TEXT NOT NULL,
                source_feedback_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_improvement_feedback_created
                ON improvement_feedback(created_at, category, tag);
            CREATE TABLE IF NOT EXISTS capability_radar_snapshots (
                id TEXT PRIMARY KEY,
                lookback_hours INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                signal_counts_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_capability_radar_snapshots_window
                ON capability_radar_snapshots(lookback_hours, observed_at);
            """
        )
        self.connection.commit()

        self.connection.execute("""CREATE TABLE IF NOT EXISTS improvement_validations (
            proposal_id TEXT PRIMARY KEY REFERENCES improvement_proposals(id),
            baseline_run_ref TEXT NOT NULL,
            candidate_run_ref TEXT NOT NULL,
            candidate_fingerprint TEXT NOT NULL,
            reviewer_id TEXT NOT NULL,
            implementer_id TEXT NOT NULL,
            review_id TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(improvement_proposals)")}
        if "priority" not in columns:
            self.connection.execute("ALTER TABLE improvement_proposals ADD COLUMN priority INTEGER NOT NULL DEFAULT 50")
        if "metadata_json" not in columns:
            self.connection.execute("ALTER TABLE improvement_proposals ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
        if "source_feedback_id" not in columns:
            self.connection.execute("ALTER TABLE improvement_proposals ADD COLUMN source_feedback_id TEXT")
        self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_improvement_proposals_source_feedback ON improvement_proposals(source_feedback_id) WHERE source_feedback_id IS NOT NULL")
        feedback_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(improvement_feedback)")}
        if "source_feedback_id" not in feedback_columns:
            self.connection.execute("ALTER TABLE improvement_feedback ADD COLUMN source_feedback_id TEXT")
        self.connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_improvement_feedback_source ON improvement_feedback(source_feedback_id) WHERE source_feedback_id IS NOT NULL")
        self.connection.commit()

    def __enter__(self) -> "ImprovementStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be empty")
        return value.strip()

    def create_proposal(
        self, title: str, problem: str, hypothesis: str, *,
        priority: int = 50, metadata: dict[str, Any] | None = None,
        source_feedback_id: str | None = None,
    ) -> dict[str, Any]:
        title = self._required(title, "title")
        problem = self._required(problem, "problem")
        hypothesis = self._required(hypothesis, "hypothesis")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 100:
            raise ValueError("priority must be an integer from 0 to 100")
        if source_feedback_id is not None:
            source_feedback_id = self._required(source_feedback_id, "source_feedback_id")
            if len(source_feedback_id) > 128:
                raise ValueError("source_feedback_id is too long")
            existing = self.connection.execute(
                "SELECT id FROM improvement_proposals WHERE source_feedback_id=?",
                (source_feedback_id,),
            ).fetchone()
            if existing is not None:
                result = self.get(existing["id"])
                assert result is not None
                return result
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a JSON object")
        try:
            metadata_json = json.dumps(
                metadata, ensure_ascii=False, sort_keys=True, allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain finite JSON-compatible values") from exc
        if len(metadata_json.encode("utf-8")) > 16 * 1024:
            raise ValueError("metadata exceeds 16384 bytes")
        proposal_id, now = uuid.uuid4().hex, self._now()
        self.connection.execute(
            "INSERT INTO improvement_proposals "
            "(id,title,problem,hypothesis,status,created_at,updated_at,priority,metadata_json,source_feedback_id) "
            "VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?)",
            (proposal_id, title, problem, hypothesis, now, now, priority, metadata_json, source_feedback_id),
        )
        self._event(proposal_id, None, "proposed", "system", "proposal created", now)
        self.connection.commit()
        result = self.get(proposal_id)
        assert result is not None
        return result

    def create_feedback_proposal(self, feedback: dict[str, Any]) -> dict[str, Any]:
        """Convert a revise/reject review event into an unapproved, idempotent proposal."""
        if not isinstance(feedback, dict):
            raise ValueError("feedback must be an object")
        feedback_id = self._required(feedback.get("id"), "feedback.id")
        decision = feedback.get("decision")
        if decision not in {"revise", "reject"}:
            raise ValueError("only revise/reject feedback creates improvement proposals")
        feedback_text = self._required(feedback.get("feedback"), "feedback.feedback")
        initiative_id = self._required(feedback.get("initiative_id"), "feedback.initiative_id")
        evolution_cycle_id = feedback.get("evolution_cycle_id") or None
        if evolution_cycle_id is not None and (
            not isinstance(evolution_cycle_id, str)
            or not re.fullmatch(r"cycle_[0-9a-f]{32}", evolution_cycle_id)
        ):
            raise ValueError("feedback.evolution_cycle_id must be a valid Muse Evolution Cycle ID")
        metadata = {
            "origin": "initiative_review_feedback",
            "source_feedback_id": feedback_id,
            "initiative_id": initiative_id,
            "decision": decision,
            "reviewer": str(feedback.get("reviewer", ""))[:200],
            "source": str(feedback.get("source", ""))[:200],
            "feedback_created_at": str(feedback.get("created_at", ""))[:80],
            "feedback_text": feedback_text,
            "epistemic_status": "unverified_user_feedback_signal",
            "requires_research_and_independent_review": True,
        }
        if evolution_cycle_id:
            metadata["evolution_cycle_id"] = evolution_cycle_id
        title = f"反馈驱动改进候选：{initiative_id[:16]}（{decision}）"
        hypothesis = (
            "该评审反馈可能指出原型或流程中的问题；先将其作为待核验信号，"
            "通过独立资料、复现实验和反例检查后再决定是否采纳。反馈本身不是事实证据。"
        )
        return self.create_proposal(
            title, feedback_text, hypothesis,
            priority=75 if decision == "reject" else 65,
            metadata=metadata, source_feedback_id=feedback_id,
        )

    def record_feedback(
        self, category: str, tag: str, *, severity: int = 3,
        source_feedback_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist explicit, allowlisted feedback labels only; no free-text details."""
        if not isinstance(category, str) or category not in _FEEDBACK_CATEGORIES:
            raise ValueError("unsupported feedback category")
        if not isinstance(tag, str) or tag not in _FEEDBACK_TAGS:
            raise ValueError("unsupported feedback tag")
        if isinstance(severity, bool) or not isinstance(severity, int) or not 1 <= severity <= 5:
            raise ValueError("severity must be an integer from 1 to 5")
        if source_feedback_id is not None:
            source_feedback_id = self._required(source_feedback_id, "source_feedback_id")
            if len(source_feedback_id) > 128:
                raise ValueError("source_feedback_id is too long")
            existing = self.connection.execute(
                "SELECT * FROM improvement_feedback WHERE source_feedback_id=?", (source_feedback_id,)
            ).fetchone()
            if existing is not None:
                if (existing["category"], existing["tag"], existing["severity"]) != (category, tag, severity):
                    raise ValueError("source feedback id conflicts with existing structured feedback")
                return dict(existing)
        feedback_id, now = uuid.uuid4().hex, self._now()
        self.connection.execute(
            "INSERT INTO improvement_feedback (id, category, tag, severity, created_at, source_feedback_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (feedback_id, category, tag, severity, now, source_feedback_id),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM improvement_feedback WHERE id=?", (feedback_id,)
        ).fetchone()
        return dict(row)

    def aggregate_feedback(self, *, since: datetime) -> list[dict[str, Any]]:
        """Return category/tag counts and severity aggregates without individual records."""
        if not isinstance(since, datetime):
            raise ValueError("since must be a datetime")
        if since.tzinfo is None or since.utcoffset() is None:
            since = since.replace(tzinfo=timezone.utc)
        cutoff = since.astimezone(timezone.utc).isoformat()
        rows = self.connection.execute(
            "SELECT category, tag, COUNT(*) AS count, ROUND(AVG(severity), 2) AS average_severity, "
            "MAX(severity) AS max_severity FROM improvement_feedback WHERE created_at >= ? "
            "GROUP BY category, tag ORDER BY count DESC, max_severity DESC, category, tag",
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_radar_snapshot(
        self, signal_counts: dict[str, int], *, observed_at: datetime,
        lookback_hours: int = 24,
    ) -> dict[str, Any]:
        """Persist redacted radar counts and compare with a non-overlapping prior window."""
        import re

        if not isinstance(observed_at, datetime):
            raise ValueError("observed_at must be a datetime")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        if isinstance(lookback_hours, bool) or not isinstance(lookback_hours, int) or not 1 <= lookback_hours <= 168:
            raise ValueError("lookback_hours must be between 1 and 168")
        if not isinstance(signal_counts, dict) or len(signal_counts) > 64:
            raise ValueError("signal_counts must be a bounded object")
        normalized: dict[str, int] = {}
        for key, count in signal_counts.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise ValueError("signal key is invalid")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("signal counts must be non-negative integers")
            normalized[key] = count
        checked_at = observed_at.astimezone(timezone.utc)
        stamp = checked_at.isoformat()
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        self.connection.execute(
            "INSERT INTO capability_radar_snapshots (id, lookback_hours, observed_at, signal_counts_json) VALUES (?, ?, ?, ?)",
            (uuid.uuid4().hex, lookback_hours, stamp, encoded),
        )
        cutoff = (checked_at - timedelta(hours=lookback_hours)).isoformat()
        previous = self.connection.execute(
            "SELECT observed_at, signal_counts_json FROM capability_radar_snapshots "
            "WHERE lookback_hours=? AND observed_at<=? ORDER BY observed_at DESC, id DESC LIMIT 1",
            (lookback_hours, cutoff),
        ).fetchone()
        self.connection.execute(
            "DELETE FROM capability_radar_snapshots WHERE id NOT IN "
            "(SELECT id FROM capability_radar_snapshots WHERE lookback_hours=? ORDER BY observed_at DESC, id DESC LIMIT 1000) "
            "AND lookback_hours=?",
            (lookback_hours, lookback_hours),
        )
        self.connection.commit()
        current_total = sum(normalized.values())
        if previous is None:
            return {
                "status": "insufficient_data", "direction": "insufficient_data",
                "lookback_hours": lookback_hours, "observed_at": stamp,
                "current_total": current_total, "previous_total": None,
                "delta": None, "previous_observed_at": None, "signals": {},
            }
        previous_counts = json.loads(previous["signal_counts_json"])
        keys = sorted(set(normalized) | set(previous_counts))
        per_signal = {
            key: {
                "current": normalized.get(key, 0),
                "previous": previous_counts.get(key, 0),
                "delta": normalized.get(key, 0) - previous_counts.get(key, 0),
            }
            for key in keys
        }
        previous_total = sum(previous_counts.values())
        delta = current_total - previous_total
        direction = "improving" if delta < 0 else "worsening" if delta > 0 else "stable"
        return {
            "status": "compared", "direction": direction,
            "lookback_hours": lookback_hours, "observed_at": stamp,
            "current_total": current_total, "previous_total": previous_total,
            "delta": delta, "previous_observed_at": previous["observed_at"],
            "signals": per_signal,
        }

    def list_feedback(
        self, *, since: datetime | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """List bounded, structured feedback entries; no free-text is accepted or stored."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if since is None:
            rows = self.connection.execute(
                "SELECT * FROM improvement_feedback ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            if not isinstance(since, datetime):
                raise ValueError("since must be a datetime")
            if since.tzinfo is None or since.utcoffset() is None:
                since = since.replace(tzinfo=timezone.utc)
            cutoff = since.astimezone(timezone.utc).isoformat()
            rows = self.connection.execute(
                "SELECT * FROM improvement_feedback WHERE created_at >= ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_source(
        self,
        proposal_id: str,
        *,
        title: str,
        url: str,
        license_status: str = "unknown",
        security_status: str = "needs_review",
        notes: str = "",
    ) -> dict[str, Any]:
        title = self._required(title, "source title")
        url = self._required(url, "source URL")
        if not url.startswith(("https://", "http://")):
            raise ValueError("source URL must use http or https")
        if license_status not in _LICENSE_STATES:
            raise ValueError("invalid license_status")
        if security_status not in _SECURITY_STATES:
            raise ValueError("invalid security_status")
        proposal = self.connection.execute(
            "SELECT status FROM improvement_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if proposal is None:
            raise ValueError("proposal does not exist")
        source_id, now = uuid.uuid4().hex, self._now()
        self.connection.execute(
            "INSERT INTO improvement_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, proposal_id, title, url, license_status, security_status, notes.strip(), now),
        )
        self._event(
            proposal_id, proposal["status"], proposal["status"], "researcher",
            f"research source added: {title}", now,
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM improvement_sources WHERE id=?", (source_id,)
        ).fetchone()
        return dict(row)
    def review_source(
        self, source_id: str, *, license_status: str, security_status: str,
        reviewed_by: str, notes: str = "",
    ) -> dict[str, Any]:
        """Persist an explicit human review of one research source."""
        source_id = self._required(source_id, "source_id")
        reviewed_by = self._required(reviewed_by, "reviewed_by")
        if license_status not in _LICENSE_STATES:
            raise ValueError("invalid license_status")
        if security_status not in _SECURITY_STATES:
            raise ValueError("invalid security_status")
        if not isinstance(notes, str) or len(notes) > 2000:
            raise ValueError("review notes must be text of at most 2000 characters")
        row = self.connection.execute(
            "SELECT s.*, p.status AS proposal_status FROM improvement_sources s "
            "JOIN improvement_proposals p ON p.id=s.proposal_id WHERE s.id=?",
            (source_id,),
        ).fetchone()
        if row is None:
            raise ValueError("source does not exist")
        previous_notes = row["notes"] or ""
        combined_notes = (previous_notes + ("\nHuman review: " + notes.strip() if notes.strip() else ""))[:4000]
        self.connection.execute(
            "UPDATE improvement_sources SET license_status=?, security_status=?, notes=? WHERE id=?",
            (license_status, security_status, combined_notes, source_id),
        )
        now = self._now()
        self._event(
            row["proposal_id"], row["proposal_status"], row["proposal_status"], reviewed_by,
            f"source review: {row['title']} | license={license_status} | security={security_status}", now,
        )
        self.connection.commit()
        result = self.connection.execute(
            "SELECT * FROM improvement_sources WHERE id=?", (source_id,)
        ).fetchone()
        return dict(result)

    def _event(
        self, proposal_id: str, from_status: str | None, to_status: str,
        actor: str, note: str, created_at: str,
    ) -> None:
        self.connection.execute(
            "INSERT INTO improvement_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, proposal_id, from_status, to_status, actor, note, created_at),
        )

    def transition(
        self, proposal_id: str, status: str, *, actor: str, note: str = ""
    ) -> dict[str, Any]:
        actor = self._required(actor, "actor")
        row = self.connection.execute(
            "SELECT status FROM improvement_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError("proposal does not exist")
        current = row["status"]
        if status not in _ALLOWED.get(current, set()):
            raise ValueError(f"invalid transition: {current} -> {status}")
        if status == "implemented":
            validation = self.connection.execute(
                "SELECT reviewer_id, implementer_id FROM improvement_validations WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if (validation is None or validation["reviewer_id"] == validation["implementer_id"]
                    or actor != validation["implementer_id"]):
                raise ValueError("implementation requires verified experiment runs, an independent review and the validated implementer")
        now = self._now()
        self.connection.execute(
            "UPDATE improvement_proposals SET status=?, updated_at=? WHERE id=?",
            (status, now, proposal_id),
        )
        self._event(proposal_id, current, status, actor, note.strip(), now)
        self.connection.commit()
        result = self.get(proposal_id)
        assert result is not None
        return result

    def approve(self, proposal_id: str, *, approved_by: str) -> dict[str, Any]:
        approved_by = self._required(approved_by, "approved_by")
        row = self.connection.execute(
            "SELECT status FROM improvement_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise ValueError("proposal does not exist")
        if row["status"] != "ready_for_review":
            raise ValueError("proposal is not ready for review")
        sources = self.connection.execute(
            "SELECT license_status, security_status FROM improvement_sources WHERE proposal_id=?",
            (proposal_id,),
        ).fetchall()
        if not sources:
            raise ValueError("at least one reviewed source is required")
        if any(
            source["license_status"] not in {"permissive", "not_applicable"}
            or source["security_status"] != "reviewed"
            for source in sources
        ):
            raise ValueError("all sources must have an acceptable license review and completed security review")
        now = self._now()
        self.connection.execute(
            "UPDATE improvement_proposals SET status='approved', updated_at=? WHERE id=?",
            (now, proposal_id),
        )
        self._event(proposal_id, "ready_for_review", "approved", approved_by, "explicit human approval", now)
        self.connection.commit()
        result = self.get(proposal_id)
        assert result is not None
        return result

    def validate_and_promote(
        self, proposal_id: str, *, experiment_bridge: Any, baseline_run_ref: str,
        candidate_run_ref: str, review_gate: Any, implementer_id: str,
        capability_registry: Any | None = None, capability_id: str | None = None,
        initiative_store: Any | None = None,
    ) -> dict[str, Any]:
        """Promote only after comparable stored test runs and an exact independent review."""
        import hashlib
        import re
        from muse.experiment_bridge import ExperimentBridge
        from muse.review_gate import IndependentReviewGate

        if not isinstance(experiment_bridge, ExperimentBridge):
            raise TypeError("experiment_bridge must be a trusted ExperimentBridge")
        if not isinstance(review_gate, IndependentReviewGate):
            raise TypeError("review_gate must be a trusted IndependentReviewGate")
        implementer_id = self._required(implementer_id, "implementer_id")
        proposal = self.get(proposal_id)
        if proposal is None or proposal["status"] != "approved":
            raise ValueError("proposal must be explicitly approved before validation")
        capability = None
        if initiative_store is not None:
            from muse.initiative_engine import InitiativeStore
            if not isinstance(initiative_store, InitiativeStore):
                raise TypeError("initiative_store must be a trusted InitiativeStore")
        if capability_registry is not None or capability_id is not None:
            from muse.capabilities import CapabilityRegistry
            if not isinstance(capability_registry, CapabilityRegistry):
                raise TypeError("capability_registry must be a trusted CapabilityRegistry")
            capability_id = self._required(capability_id, "capability_id")
            capability = capability_registry.get(capability_id)
            if capability is None:
                raise ValueError("capability to upgrade must already exist in the registry")
            if capability["status"] not in {"unverified", "ready"}:
                raise ValueError("disabled, missing or approval-gated capabilities cannot be upgraded by validation")
        refs = [self._required(baseline_run_ref, "baseline_run_ref"),
                self._required(candidate_run_ref, "candidate_run_ref")]
        if refs[0] == refs[1]:
            raise ValueError("baseline and candidate runs must be different")

        def load_run(ref: str) -> dict[str, Any]:
            row = experiment_bridge.knowledge.connection.execute(
                "SELECT id FROM knowledge_nodes WHERE node_type='experiment' AND external_ref=? AND deleted_at IS NULL",
                (ref,),
            ).fetchone()
            if row is None:
                raise ValueError("baseline and candidate must reference stored experiment runs")
            node = experiment_bridge.knowledge.get_node(row["id"])
            if node is None:
                raise ValueError("experiment run disappeared during validation")
            return node

        baseline, candidate = (load_run(ref) for ref in refs)
        base_meta, candidate_meta = baseline["metadata"], candidate["metadata"]
        if base_meta.get("status") not in {"succeeded", "failed"}:
            raise ValueError("baseline run must have a completed success or failure result")
        if candidate_meta.get("status") != "succeeded":
            raise ValueError("candidate run must have succeeded within its declared budget")
        def load_manifest(run_meta: dict[str, Any]) -> dict[str, Any]:
            manifest_id = run_meta.get("manifest_id")
            if not isinstance(manifest_id, str) or not manifest_id:
                raise ValueError("baseline and candidate runs must reference registered test manifests")
            row = experiment_bridge.knowledge.connection.execute(
                "SELECT id FROM knowledge_nodes WHERE node_type='experiment' AND external_ref=? AND deleted_at IS NULL",
                (f"experiment-manifest:{manifest_id}",),
            ).fetchone()
            if row is None:
                raise ValueError("baseline and candidate test manifests must both exist")
            node = experiment_bridge.knowledge.get_node(row["id"])
            if node is None:
                raise ValueError("experiment manifest disappeared during validation")
            return node["metadata"]

        baseline_manifest, candidate_manifest = load_manifest(base_meta), load_manifest(candidate_meta)
        if baseline_manifest.get("kind") != "test" or candidate_manifest.get("kind") != "test":
            raise ValueError("baseline and candidate must both be test experiments")
        comparable_fields = ("argv", "cwd", "environment", "environment_keys")
        if any(baseline_manifest.get(key) != candidate_manifest.get(key) for key in comparable_fields):
            raise ValueError("baseline and candidate must use the same test command and environment")
        baseline_fingerprints = baseline_manifest.get("code_fingerprints", {})
        candidate_fingerprints = candidate_manifest.get("code_fingerprints", {})
        base_metrics = base_meta.get("metrics", {})
        candidate_metrics = candidate_meta.get("metrics", {})
        for label, metrics in (("baseline", base_metrics), ("candidate", candidate_metrics)):
            if not isinstance(metrics, dict) or any(
                isinstance(metrics.get(key), bool) or not isinstance(metrics.get(key), (int, float))
                for key in ("tests_run", "failures", "errors")
            ):
                raise ValueError(f"{label} run must record tests_run, failures and errors metrics")
        if base_metrics["tests_run"] < 1 or candidate_metrics["tests_run"] < base_metrics["tests_run"]:
            raise ValueError("candidate must execute at least as many tests as the baseline")
        if candidate_metrics["failures"] != 0 or candidate_metrics["errors"] != 0:
            raise ValueError("candidate test run must have zero failures and errors")
        valid_baseline_fingerprints = [value for value in baseline_fingerprints.values()
                                       if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)] if isinstance(baseline_fingerprints, dict) else []
        valid_fingerprints = [value for value in candidate_fingerprints.values()
                              if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)] if isinstance(candidate_fingerprints, dict) else []
        if not valid_baseline_fingerprints or not valid_fingerprints:
            raise ValueError("baseline and candidate must both include SHA-256 code fingerprints")
        if baseline_fingerprints == candidate_fingerprints:
            raise ValueError("candidate code fingerprints must differ from the baseline")
        fingerprints = candidate_fingerprints
        candidate_fingerprint = hashlib.sha256(
            json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        artifact_id = "improvement-validation:" + hashlib.sha256(
            json.dumps({"proposal_id": proposal_id, "baseline": refs[0], "candidate": refs[1],
                        "candidate_fingerprint": candidate_fingerprint}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        evidence = {
            "proposal": {"id": proposal_id, "title": proposal["title"], "problem": proposal["problem"],
                         "hypothesis": proposal["hypothesis"]},
            "baseline": {"ref": refs[0], "status": base_meta["status"], "metrics": base_metrics},
            "candidate": {"ref": refs[1], "status": candidate_meta["status"], "metrics": candidate_metrics,
                          "fingerprints": fingerprints},
            "comparison": {"tests_run_delta": candidate_metrics["tests_run"] - base_metrics["tests_run"],
                           "failures_delta": candidate_metrics["failures"] - base_metrics["failures"],
                           "errors_delta": candidate_metrics["errors"] - base_metrics["errors"]},
        }
        review = review_gate.review(
            artifact_id=artifact_id, builder_id=implementer_id,
            artifact=json.dumps(evidence["proposal"], ensure_ascii=False, sort_keys=True),
            test_evidence=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            lineage=json.dumps({"baseline_run_ref": refs[0], "candidate_run_ref": refs[1],
                                "candidate_fingerprint": candidate_fingerprint}, sort_keys=True),
        )
        stored_review = review_gate.store.get_review(review.review_id)
        if (not review.approved or stored_review is None or not stored_review["approved"]
                or stored_review["artifact_id"] != artifact_id
                or stored_review["builder_id"] != implementer_id
                or stored_review["reviewer_id"] == implementer_id):
            knowledge = experiment_bridge.knowledge
            proposal_metadata = proposal.get("metadata", {})
            rejected_node = knowledge.upsert_node(
                "decision", "独立验证审查未通过：" + proposal["title"],
                summary=f"独立 Reviewer {review.reviewer_id} 未批准该候选；保留拒绝理由，不升级能力。",
                external_ref="improvement-review:" + review.review_id,
                metadata={
                    "decision_kind": "improvement_validation_review",
                    "improvement_proposal_id": proposal_id,
                    "source_feedback_id": proposal.get("source_feedback_id") or proposal_metadata.get("source_feedback_id"),
                    "creator_initiative_id": proposal_metadata.get("initiative_id"),
                    "evolution_cycle_id": proposal_metadata.get("evolution_cycle_id"),
                    "baseline_run_ref": refs[0], "candidate_run_ref": refs[1],
                    "review_id": review.review_id, "reviewer_id": review.reviewer_id,
                    "implementer_id": implementer_id, "approved": False,
                    "findings": [
                        {"severity": item.severity, "title": item.title, "detail": item.detail[:500]}
                        for item in review.findings[:20]
                    ],
                    "epistemic_status": "engineering_review_rejected_not_external_evidence",
                },
                provenance=(refs[0], refs[1], "review:" + review.review_id), status="rejected",
            )
            knowledge.add_edge(rejected_node["id"], baseline["id"], "relates", metadata={
                "relationship_status": "rejected_review_baseline", "not_claim_evidence": True,
            })
            knowledge.add_edge(rejected_node["id"], candidate["id"], "relates", metadata={
                "relationship_status": "rejected_review_candidate", "not_claim_evidence": True,
            })
            initiative_linked = False
            initiative_link_error = None
            initiative_id = proposal_metadata.get("initiative_id")
            cycle_id = proposal_metadata.get("evolution_cycle_id")
            if initiative_store is not None and isinstance(initiative_id, str):
                try:
                    knowledge.link_node_to_initiative(
                        initiative_store, initiative_id, rejected_node["id"],
                        relationship="related", evolution_cycle_id=cycle_id,
                    )
                    initiative_linked = True
                except (ValueError, KeyError, sqlite3.Error) as exc:
                    initiative_link_error = type(exc).__name__
            return {"status": "review_rejected", "proposal": proposal, "review_id": review.review_id,
                    "validation_node_id": rejected_node["id"], "capability_upgrade": None,
                    "initiative_linked": initiative_linked, "initiative_link_error": initiative_link_error,
                    "findings": [finding.__dict__ for finding in review.findings]}

        now = self._now()
        metrics = {"baseline": base_metrics, "candidate": candidate_metrics,
                   "comparison": evidence["comparison"]}
        self.connection.execute(
            "INSERT OR REPLACE INTO improvement_validations "
            "(proposal_id,baseline_run_ref,candidate_run_ref,candidate_fingerprint,reviewer_id,implementer_id,review_id,metrics_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (proposal_id, refs[0], refs[1], candidate_fingerprint, review.reviewer_id,
             implementer_id, review.review_id, json.dumps(metrics, sort_keys=True), now),
        )
        self.connection.commit()
        promoted = self.transition(proposal_id, "implemented", actor=implementer_id,
                                   note=f"validated against {refs[0]} and {refs[1]}; review={review.review_id}")
        capability_upgrade = None
        if capability is not None and capability_registry is not None and capability_id is not None:
            upgraded_version = candidate_fingerprint[:16]
            capability_metadata = dict(capability.get("metadata", {}))
            capability_metadata["last_improvement_validation"] = {
                "proposal_id": proposal_id, "review_id": review.review_id,
                "reviewer_id": review.reviewer_id, "implementer_id": implementer_id,
                "baseline_run_ref": refs[0], "candidate_run_ref": refs[1],
                "candidate_fingerprint": candidate_fingerprint,
                "validated_at": now,
            }
            upgraded = capability_registry.register(
                capability_id=capability["capability_id"], domain=capability["domain"],
                name=capability["name"], description=capability["description"],
                executable=capability.get("executable"), capabilities=capability["capabilities"],
                requirements=capability["requirements"], trust=capability["trust"],
                license=capability["license"], status="ready", version=upgraded_version,
                metadata=capability_metadata,
            )
            capability_upgrade = {
                "capability_id": upgraded["capability_id"],
                "status": upgraded["status"], "version": upgraded["version"],
            }
        knowledge = experiment_bridge.knowledge
        proposal_metadata = proposal.get("metadata", {})
        validation_metadata = {
            "decision_kind": "improvement_validation",
            "improvement_proposal_id": proposal_id,
            "source_feedback_id": proposal.get("source_feedback_id") or proposal_metadata.get("source_feedback_id"),
            "creator_initiative_id": proposal_metadata.get("initiative_id"),
            "evolution_cycle_id": proposal_metadata.get("evolution_cycle_id"),
            "baseline_run_ref": refs[0], "candidate_run_ref": refs[1],
            "review_id": review.review_id, "reviewer_id": review.reviewer_id,
            "implementer_id": implementer_id, "candidate_fingerprint": candidate_fingerprint,
            "metrics": metrics, "capability_upgrade": capability_upgrade,
            "epistemic_status": "engineering_validation_not_external_evidence",
        }
        validation_node = knowledge.upsert_node(
            "decision", "改进验证通过：" + proposal["title"],
            summary=f"独立 Reviewer {review.reviewer_id} 确认候选实验通过；工程验证不作为外部事实证据。",
            external_ref=artifact_id, metadata=validation_metadata,
            provenance=(refs[0], refs[1], "review:" + review.review_id), status="approved",
        )
        knowledge.add_edge(validation_node["id"], baseline["id"], "relates", metadata={
            "relationship_status": "validated_baseline", "not_claim_evidence": True,
        })
        knowledge.add_edge(validation_node["id"], candidate["id"], "relates", metadata={
            "relationship_status": "validated_candidate", "not_claim_evidence": True,
        })
        for plan_node in knowledge.list_nodes(node_type="experiment", limit=500):
            plan_metadata = plan_node.get("metadata", {})
            if plan_metadata.get("improvement_proposal_id") == proposal_id:
                knowledge.add_edge(plan_node["id"], validation_node["id"], "tested_by", metadata={
                    "relationship_status": "independent_validation_and_promotion",
                    "not_claim_evidence": True,
                })
        initiative_linked = False
        initiative_link_error = None
        initiative_id = proposal_metadata.get("initiative_id")
        cycle_id = proposal_metadata.get("evolution_cycle_id")
        if initiative_store is not None and isinstance(initiative_id, str):
            try:
                knowledge.link_node_to_initiative(
                    initiative_store, initiative_id, validation_node["id"],
                    relationship="related", evolution_cycle_id=cycle_id,
                )
                initiative_linked = True
            except (ValueError, KeyError, sqlite3.Error) as exc:
                initiative_link_error = type(exc).__name__
        return {"status": "implemented", "proposal": promoted, "validation": promoted["validation"],
                "validation_node_id": validation_node["id"], "capability_upgrade": capability_upgrade,
                "initiative_linked": initiative_linked, "initiative_link_error": initiative_link_error,
                "review_id": review.review_id, "reviewer_id": review.reviewer_id,
                "findings": [finding.__dict__ for finding in review.findings]}

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM improvement_proposals WHERE id=?", (proposal_id,)
        ).fetchone()
        if row is None:
            return None
        sources = self.connection.execute(
            "SELECT * FROM improvement_sources WHERE proposal_id=? ORDER BY created_at, id",
            (proposal_id,),
        ).fetchall()
        events = self.connection.execute(
            "SELECT * FROM improvement_events WHERE proposal_id=? ORDER BY created_at, id",
            (proposal_id,),
        ).fetchall()
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json", "{}"))
        validation = self.connection.execute(
            "SELECT * FROM improvement_validations WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        if validation is not None:
            result["validation"] = dict(validation)
            result["validation"]["metrics"] = json.loads(result["validation"].pop("metrics_json"))
        else:
            result["validation"] = None
        result["sources"] = [dict(source) for source in sources]
        result["events"] = [dict(event) for event in events]
        return result

    def list_proposals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            rows = self.connection.execute(
                "SELECT id FROM improvement_proposals ORDER BY priority DESC, created_at, id"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT id FROM improvement_proposals WHERE status=? ORDER BY priority DESC, created_at, id",
                (status,),
            ).fetchall()
        return [self.get(row["id"]) for row in rows]
