"""Reproducible experiment manifests and evidence-linked result recording.

The bridge records metadata and results; it does not execute commands or grant
resource permissions. Existing approved runners/adapters remain responsible
for execution.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from typing import Any, Iterable

from muse.research_knowledge import ResearchKnowledgeStore


_ALLOWED_KINDS = frozenset({"test", "benchmark", "research", "verification"})
_ALLOWED_EXECUTORS = frozenset({"python", "python3", "py", "unittest", "pytest"})


class ExperimentBridge:
    """Persist versioned experiment manifests and link reviewed outcomes to claims."""

    def __init__(self, knowledge: ResearchKnowledgeStore) -> None:
        if not isinstance(knowledge, ResearchKnowledgeStore):
            raise TypeError("knowledge must be a ResearchKnowledgeStore")
        self.knowledge = knowledge

    @staticmethod
    def _text(value: Any, field: str, maximum: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} must be non-empty text of at most {maximum} characters")
        if "\x00" in value:
            raise ValueError(f"{field} must not contain NUL")
        return value.strip()

    @staticmethod
    def _relative_path(value: str, field: str) -> str:
        value = ExperimentBridge._text(value, field, 500).replace("\\", "/")
        if value.startswith("/") or re.match(r"^[A-Za-z]:", value) or any(part == ".." for part in value.split("/")):
            raise ValueError(f"{field} must be a workspace-relative path without traversal")
        return value

    def register_manifest(
        self, *, title: str, kind: str, argv: Iterable[str], cwd: str = ".",
        timeout_seconds: int = 900, memory_budget_mb: int = 4096,
        environment_keys: Iterable[str] = (),
        environment: dict[str, str] | None = None,
        code_fingerprints: dict[str, str] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        title = self._text(title, "title", 200)
        if kind not in _ALLOWED_KINDS:
            raise ValueError("unsupported experiment kind")
        if isinstance(argv, (str, bytes)):
            raise ValueError("argv must be a sequence of discrete arguments")
        args = list(argv)
        if not 1 <= len(args) <= 32:
            raise ValueError("argv must contain 1-32 arguments")
        args = [self._text(arg, "argv item", 500) for arg in args]
        if args[0].casefold() not in _ALLOWED_EXECUTORS:
            raise ValueError("experiment executor is not allowlisted")
        if any(any(token in arg for token in (";", "&&", "||", "|", "\n", "\r")) for arg in args):
            raise ValueError("argv must contain discrete arguments, not shell expressions")
        cwd = self._relative_path(cwd, "cwd")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be between 1 and 3600")
        if isinstance(memory_budget_mb, bool) or not isinstance(memory_budget_mb, int) or not 128 <= memory_budget_mb <= 65536:
            raise ValueError("memory_budget_mb must be between 128 and 65536")
        if isinstance(environment_keys, (str, bytes)):
            raise ValueError("environment_keys must be a sequence of variable names")
        raw_env_keys = list(environment_keys)
        if len(raw_env_keys) > 32 or any(not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) for key in raw_env_keys):
            raise ValueError("environment_keys must contain at most 32 uppercase variable names")
        env_keys = sorted(set(raw_env_keys))
        if len(env_keys) > 32:
            raise ValueError("environment_keys must contain at most 32 uppercase variable names")
        env = environment or {}
        if not isinstance(env, dict) or len(env) > 12:
            raise ValueError("environment must be a bounded metadata object")
        safe_environment = {}
        for key, value in env.items():
            if key not in {"python_version", "platform", "machine", "runtime"}:
                raise ValueError("environment contains a non-allowlisted field")
            safe_environment[key] = self._text(value, f"environment.{key}", 160)
        fingerprints = code_fingerprints or {}
        if not isinstance(fingerprints, dict) or len(fingerprints) > 100:
            raise ValueError("code_fingerprints must be a mapping of at most 100 files")
        safe_fingerprints = {}
        for path, digest in fingerprints.items():
            safe_path = self._relative_path(path, "code fingerprint path")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("code fingerprints must be lowercase SHA-256 digests")
            safe_fingerprints[safe_path] = digest
        task_ref = self._text(task_id, "task_id", 160) if task_id else None
        manifest_id = uuid.uuid4().hex
        manifest = {
            "manifest_version": 1, "manifest_id": manifest_id, "kind": kind,
            "argv": args, "cwd": cwd, "timeout_seconds": timeout_seconds,
            "memory_budget_mb": memory_budget_mb, "budget_mode": "runner_enforced_runtime_memory_declared_only",
            "environment_keys": env_keys,
            "environment": safe_environment, "code_fingerprints": safe_fingerprints,
            "task_id": task_ref,
        }
        canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(canonical.encode("utf-8")) > 12000:
            raise ValueError("experiment manifest exceeds 12000 bytes")
        manifest_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        manifest["manifest_hash"] = manifest_hash
        node = self.knowledge.upsert_node(
            "experiment", title, summary=f"Registered {kind} experiment manifest v1.",
            external_ref=f"experiment-manifest:{manifest_id}", status="registered",
            metadata=manifest,
            provenance=tuple(f"file:{path}:{digest}" for path, digest in list(safe_fingerprints.items())[:32]),
        )
        return {"manifest_id": manifest_id, "manifest_ref": f"experiment-manifest:{manifest_id}",
                "node_id": node["id"], "manifest_hash": manifest_hash, "manifest": manifest}

    def record_result(
        self, manifest_id: str, *, status: str, duration_seconds: float,
        metrics: dict[str, int | float], summary: str, exit_code: int | None = None,
        run_id: str | None = None, raw_evidence: str | None = None,
    ) -> dict[str, Any]:
        manifest_id = self._text(manifest_id, "manifest_id", 64)
        manifest = self.knowledge.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE node_type='experiment' AND external_ref=? AND deleted_at IS NULL",
            (f"experiment-manifest:{manifest_id}",),
        ).fetchone()
        if manifest is None:
            raise ValueError("registered experiment manifest does not exist")
        if status not in {"succeeded", "failed", "budget_exceeded", "cancelled", "inconclusive"}:
            raise ValueError("unsupported experiment result status")
        if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) or not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("duration_seconds must be a finite non-negative number")
        if isinstance(exit_code, bool) or (exit_code is not None and not isinstance(exit_code, int)):
            raise ValueError("exit_code must be an integer or null")
        if not isinstance(metrics, dict) or len(metrics) > 30:
            raise ValueError("metrics must be a bounded object")
        safe_metrics: dict[str, int | float] = {}
        for key, value in metrics.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise ValueError("metric keys must be stable lowercase identifiers")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("metrics must contain only finite numeric values")
            safe_metrics[key] = value
        summary = self._text(summary, "summary", 500)
        if raw_evidence is not None and (not isinstance(raw_evidence, str) or len(raw_evidence) > 200000):
            raise ValueError("raw_evidence must be text of at most 200000 characters")
        run_id = self._text(run_id or uuid.uuid4().hex, "run_id", 160)
        manifest_node = self.knowledge.get_node(manifest["id"])
        assert manifest_node is not None
        if duration_seconds > manifest_node["metadata"]["timeout_seconds"] and status == "succeeded":
            status = "budget_exceeded"
        metadata = {
            "manifest_id": manifest_id, "manifest_hash": manifest_node["metadata"]["manifest_hash"],
            "status": status, "duration_seconds": round(float(duration_seconds), 3),
            "timeout_seconds": manifest_node["metadata"]["timeout_seconds"],
            "memory_budget_mb": manifest_node["metadata"]["memory_budget_mb"],
            "budget_mode": manifest_node["metadata"]["budget_mode"],
            "environment": manifest_node["metadata"]["environment"],
            "code_fingerprints": manifest_node["metadata"]["code_fingerprints"],
            "task_id": manifest_node["metadata"].get("task_id"),
            "budget_exceeded": status == "budget_exceeded" or duration_seconds > manifest_node["metadata"]["timeout_seconds"],
            "exit_code": exit_code,
            "metrics": safe_metrics,
            "raw_evidence_sha256": hashlib.sha256(raw_evidence.encode("utf-8")).hexdigest() if raw_evidence is not None else None,
            "raw_evidence_chars": len(raw_evidence) if raw_evidence is not None else 0,
        }
        run_ref = f"experiment-run:{run_id}"
        existing_row = self.knowledge.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE node_type='experiment' AND external_ref=? AND deleted_at IS NULL",
            (run_ref,),
        ).fetchone()
        if existing_row is not None:
            existing_run = self.knowledge.get_node(existing_row["id"])
            if existing_run is None:
                raise RuntimeError("existing experiment run disappeared during idempotency check")
            if (existing_run["status"] != status or existing_run["summary"] != summary
                    or existing_run["metadata"] != metadata):
                raise ValueError("experiment run_id already exists with a different result")
            # A prior process may have persisted the run node and crashed before
            # creating its manifest edge. Replaying the exact immutable result may
            # safely repair this idempotent relationship without rewriting the run.
            self.knowledge.add_edge(manifest["id"], existing_run["id"], "tested_by")
            return {"run_id": run_id, "run_ref": run_ref,
                    "node_id": existing_run["id"], "status": status, "metrics": safe_metrics}
        run = self.knowledge.upsert_node(
            "experiment", f"Experiment run {run_id[:80]}", summary=summary,
            external_ref=f"experiment-run:{run_id}", status=status,
            metadata=metadata, provenance=(f"experiment-manifest:{manifest_id}",),
        )
        self.knowledge.add_edge(manifest["id"], run["id"], "tested_by")
        return {"run_id": run_id, "run_ref": f"experiment-run:{run_id}",
                "node_id": run["id"], "status": status, "metrics": safe_metrics}

    @staticmethod
    def _claim_review_artifact_id(claim_id: str, run_ref: str, refs: list[str], raw_evidence: str) -> str:
        lineage = {
            "claim_id": claim_id, "run_ref": run_ref, "evidence_refs": sorted(set(refs)),
            "raw_evidence_sha256": hashlib.sha256(raw_evidence.encode("utf-8")).hexdigest(),
        }
        digest = hashlib.sha256(json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return f"claim-review:{claim_id}:{digest}"

    def review_claim(
        self, claim_id: str, *, run_ref: str, raw_evidence: str,
        evidence_refs: Iterable[str], review_gate: Any,
    ) -> dict[str, Any]:
        """Review original experiment output independently before changing a Claim."""
        from muse.review_gate import IndependentReviewGate
        if not isinstance(review_gate, IndependentReviewGate):
            raise TypeError("review_gate must be a trusted IndependentReviewGate")
        claim = self.knowledge.get_node(self._text(claim_id, "claim_id", 128))
        if claim is None or claim["node_type"] != "claim":
            raise ValueError("claim_id must identify an existing claim")
        builder_id = claim["metadata"].get("author_id")
        if not isinstance(builder_id, str) or not builder_id.strip():
            raise ValueError("claim must record its author_id before independent review")
        run_ref = self._text(run_ref, "run_ref", 200)
        run_row = self.knowledge.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE node_type='experiment' AND external_ref=? AND deleted_at IS NULL",
            (run_ref,),
        ).fetchone()
        if run_row is None:
            raise ValueError("claim evidence must be a stored experiment run")
        run = self.knowledge.get_node(run_row["id"])
        assert run is not None
        status = run["metadata"].get("status")
        if status not in {"succeeded", "failed", "budget_exceeded"}:
            raise ValueError("inconclusive or cancelled experiment cannot change Claim status")
        if not isinstance(raw_evidence, str) or not raw_evidence.strip() or len(raw_evidence) > 200000:
            raise ValueError("raw_evidence must contain the original bounded experiment output")
        raw_evidence_hash = hashlib.sha256(raw_evidence.encode("utf-8")).hexdigest()
        if run["metadata"].get("raw_evidence_sha256") != raw_evidence_hash:
            raise ValueError("raw_evidence does not match the hash captured with this experiment result")
        if isinstance(evidence_refs, (str, bytes)):
            raise ValueError("evidence_refs must be an iterable of stored source/experiment references")
        refs = list(dict.fromkeys(self._text(ref, "evidence_ref", 4096) for ref in evidence_refs))
        if run_ref not in refs:
            raise ValueError("evidence_refs must include the experiment run")
        evidence_nodes = []
        for ref in refs:
            row = self.knowledge.connection.execute(
                "SELECT id,node_type FROM knowledge_nodes WHERE deleted_at IS NULL AND (id=? OR external_ref=?)",
                (ref, ref),
            ).fetchone()
            if row is None or row["node_type"] not in {"source", "experiment"}:
                raise ValueError(f"evidence reference must identify a stored source or experiment: {ref[:80]}")
            evidence_nodes.append(self.knowledge.get_node(row["id"]))
        evidence_package = {
            "claim": {"id": claim["id"], "title": claim["title"], "summary": claim["summary"], "metadata": claim["metadata"]},
            "experiment_run": run, "stored_evidence": evidence_nodes,
            "raw_experiment_output": raw_evidence, "evidence_refs": refs,
        }
        review_artifact_id = self._claim_review_artifact_id(claim_id, run_ref, refs, raw_evidence)
        review = review_gate.review(
            artifact_id=review_artifact_id, builder_id=builder_id,
            artifact=json.dumps(evidence_package["claim"], ensure_ascii=False, sort_keys=True),
            test_evidence=json.dumps(evidence_package, ensure_ascii=False, sort_keys=True, default=str),
            lineage=json.dumps({
                "run_ref": run_ref, "evidence_refs": refs,
                "raw_evidence_sha256": hashlib.sha256(raw_evidence.encode("utf-8")).hexdigest(),
            }, ensure_ascii=False, sort_keys=True),
        )
        if not review.approved:
            return {"status": "review_rejected", "claim": claim, "review_id": review.review_id,
                    "reviewer_id": review.reviewer_id,
                    "findings": [finding.__dict__ for finding in review.findings]}
        target_status = "supported" if status == "succeeded" else "refuted"
        updated = self.link_claim(
            claim_id, status=target_status, run_ref=run_ref, evidence_refs=refs,
            raw_evidence=raw_evidence, review_store=review_gate.store, review_id=review.review_id,
        )
        return {"status": updated["status"], "claim": updated, "review_id": review.review_id,
                "reviewer_id": review.reviewer_id,
                "findings": [finding.__dict__ for finding in review.findings]}

    def link_claim(
        self, claim_id: str, *, status: str, run_ref: str,
        review_store: Any, review_id: str, evidence_refs: Iterable[str], raw_evidence: str,
    ) -> dict[str, Any]:
        """Transition only with a persisted review of this exact Claim and evidence bundle."""
        from muse.review_gate import ReviewStore
        if not isinstance(review_store, ReviewStore):
            raise TypeError("review_store must be the persisted ReviewStore used by the review gate")
        review = review_store.get_review(review_id)
        if review is None or not review["approved"]:
            raise ValueError("claim transition requires a persisted approved independent review")
        run_ref = self._text(run_ref, "run_ref", 200)
        if not isinstance(raw_evidence, str) or not raw_evidence.strip() or len(raw_evidence) > 200000:
            raise ValueError("raw_evidence must contain the original bounded experiment output")
        if isinstance(evidence_refs, (str, bytes)):
            raise ValueError("evidence_refs must be an iterable of stored source/experiment references")
        refs = list(dict.fromkeys(self._text(ref, "evidence_ref", 4096) for ref in evidence_refs))
        if run_ref not in refs:
            raise ValueError("evidence_refs must include the experiment run")
        expected_artifact_id = self._claim_review_artifact_id(claim_id, run_ref, refs, raw_evidence)
        if review["artifact_id"] != expected_artifact_id:
            raise ValueError("review record does not belong to this exact Claim and evidence bundle")
        row = self.knowledge.connection.execute(
            "SELECT id FROM knowledge_nodes WHERE node_type='experiment' AND external_ref=? AND deleted_at IS NULL",
            (run_ref,),
        ).fetchone()
        if row is None:
            raise ValueError("claim evidence must be a stored experiment run")
        run = self.knowledge.get_node(row["id"])
        assert run is not None
        if run["metadata"].get("raw_evidence_sha256") != hashlib.sha256(raw_evidence.encode("utf-8")).hexdigest():
            raise ValueError("raw_evidence does not match the hash captured with this experiment result")
        if status == "supported" and run["metadata"].get("status") != "succeeded":
            raise ValueError("a claim can be supported only by a successful experiment run")
        if status == "refuted" and run["metadata"].get("status") not in {"failed", "budget_exceeded"}:
            raise ValueError("a claim can be refuted only by a failed or budget-exceeded experiment run")
        if status not in {"supported", "refuted"}:
            raise ValueError("experiment bridge only transitions claims to supported or refuted")
        if review["reviewer_id"] == review["builder_id"]:
            raise ValueError("claim reviewer must be independent from its author")
        claim = self.knowledge.get_node(claim_id)
        if claim is None or claim["node_type"] != "claim":
            raise ValueError("claim_id must identify an existing Claim")
        if review["builder_id"] != claim["metadata"].get("author_id"):
            raise ValueError("review record builder does not match Claim author")
        updated = self.knowledge.transition_claim(
            claim_id, status, evidence_refs=refs, reviewer_id=str(review["reviewer_id"]),
        )
        updated["metadata"]["review_id"] = review_id
        with self.knowledge.connection:
            self.knowledge.connection.execute(
                "UPDATE knowledge_nodes SET metadata_json=? WHERE id=?",
                (json.dumps(updated["metadata"], ensure_ascii=False, sort_keys=True), claim_id),
            )
            self.knowledge._event("node", claim_id, "claim_independent_review_linked", {
                "review_id": review_id, "reviewer_id": review["reviewer_id"],
                "run_ref": run_ref, "evidence_refs": refs,
            })
        result = self.knowledge.get_node(claim_id)
        assert result is not None
        return result
