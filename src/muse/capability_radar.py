"""Generate ranked, approval-gated improvement proposals from redacted aggregates."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from muse.improvement import ImprovementStore
from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue
from muse.research_knowledge import ResearchKnowledgeStore
from muse.initiative_engine import InitiativeStore


_SIGNAL_SPECS: dict[str, dict[str, Any]] = {
    "task_failure": {
        "title": "Add structured task failure taxonomy and recovery guidance",
        "problem": "Recent operational aggregates show failed task records. Task titles, payloads and exception messages were not inspected.",
        "hypothesis": "Introduce stable error codes, retryability classes and redacted recovery guidance, then test recovery boundaries.",
        "base_priority": 90,
        "base_confidence": 0.78,
        "impact": "Reduce repeated task failures and make recovery decisions explainable.",
        "acceptance_criteria": [
            "Every supported handler failure maps to a stable error code and retryability class.",
            "Recovery guidance is generated from structured codes without exposing task payloads.",
            "Regression tests cover retryable, non-retryable and manual-review outcomes.",
        ],
        "rollback_plan": "Keep the taxonomy additive; disable code-based routing and return to the existing generic failure path if regressions appear.",
    },
    "repeated_failure_class": {
        "title": "Resolve recurring classified task failure patterns",
        "problem": "The Ticket event ledger shows repeated occurrences of one or more stable failure codes. Only redacted code/category counts were inspected; task titles, payloads and exception messages were not read.",
        "hypothesis": "Trace the repeated code to its owning stage or capability, verify the diagnosis with a deterministic reproduction, and propose a bounded fix with regression coverage.",
        "base_priority": 94,
        "base_confidence": 0.84,
        "impact": "Reduce recurring failures by targeting their stable root-cause categories instead of applying indiscriminate retries.",
        "acceptance_criteria": [
            "The proposal cites repeated stable error-code counts from the bounded lookback window.",
            "A reproducible test validates the suspected failing stage before a fix is proposed.",
            "No task content, exception message, or automatic retry is included in the recovery plan.",
        ],
        "rollback_plan": "Keep the existing bounded failure handling; revert only the isolated fix if its regression suite fails.",
    },
    "test_failure": {
        "title": "Add regression triage and benchmark gate",
        "problem": "Recent structured progress aggregates include failed test runs. Test output and private source content were not inspected.",
        "hypothesis": "Capture bounded test identifiers and aggregate failure classes, then add a repeatable baseline and independent regression review.",
        "base_priority": 85,
        "base_confidence": 0.74,
        "impact": "Shorten regression diagnosis and prevent unreviewed quality regressions.",
        "acceptance_criteria": [
            "Test failures are grouped by stable suite or failure category without storing raw output.",
            "A repeatable baseline records executed-test counts and pass/fail totals.",
            "A regression blocks completion until a reviewer or explicit operator decision resolves it.",
        ],
        "rollback_plan": "Keep the existing test runner as the source of truth and disable the new triage gate without deleting historical evidence.",
    },
    "recovery_churn": {
        "title": "Reduce repeated worker recovery and retry churn",
        "problem": "Recent queue aggregates show tasks being claimed more than once. Task content and payloads were not inspected.",
        "hypothesis": "Classify lease expiry and retry causes, then tune heartbeat/recovery boundaries with deterministic crash-recovery tests.",
        "base_priority": 72,
        "base_confidence": 0.70,
        "impact": "Reduce wasted worker capacity and prevent repeated execution after unstable leases.",
        "acceptance_criteria": [
            "Lease expiry and retry counts are measurable from metadata-only aggregates.",
            "Repeated claims distinguish crash recovery from ordinary handler failure.",
            "Tests prove stale workers cannot commit results after a replacement claim.",
        ],
        "rollback_plan": "Retain the current bounded retry policy and disable any new recovery tuning if lease-fencing tests fail.",
    },
    "user_feedback": {
        "title": "Address recurring user-reported workflow friction",
        "problem": "Explicit, allowlisted user feedback labels indicate recurring usability, accuracy, capability or reliability friction. No free-text feedback was stored.",
        "hypothesis": "Prioritize the most frequent and severe feedback categories, then implement a bounded workflow improvement with measurable acceptance tests.",
        "base_priority": 80,
        "base_confidence": 0.73,
        "impact": "Align Muse improvements with explicit user-reported friction rather than inferred conversation content.",
        "acceptance_criteria": [
            "Feedback intake accepts only allowlisted categories, tags and severity values.",
            "Radar aggregates feedback within the configured time window without reading free text.",
            "A proposal includes the observed category/tag counts and an explicit acceptance test.",
        ],
        "rollback_plan": "Disable feedback-driven proposal generation while retaining the structured feedback ledger for later review.",
    },
    "creator_video_failure": {
        "title": "Reduce recurring local video creation failures",
        "problem": "The Creator Initiative ledger contains repeated local video product job failures classified by stable error code and exception type; raw exception messages are not used as evidence.",
        "hypothesis": "Reproduce the failing generation or packaging stage under the same bounded settings, isolate the cause, and add a regression or recovery test before changing the pipeline.",
        "base_priority": 91,
        "base_confidence": 0.80,
        "impact": "Reduce wasted local GPU/CPU work and improve reliable video product delivery.",
        "acceptance_criteria": [
            "At least two failures share a stable code within the bounded lookback window.",
            "A deterministic reproduction or controlled fault-injection test identifies the affected stage.",
            "The fix preserves existing product integrity checks and never auto-publishes a failed product.",
        ],
        "rollback_plan": "Disable the new recovery branch and retain the prior fail-closed video job behavior if the reproduction or regression test fails.",
    },
    "repeated_correction": {
        "title": "Eliminate repeated manual correction loops",
        "problem": "Explicit feedback labels show the same workflow or result requiring repeated manual correction. No conversation text or correction content was stored.",
        "hypothesis": "Trace the repeated correction to a missing capability, weak validation or incomplete workflow, then add a regression test for the corrected path.",
        "base_priority": 92,
        "base_confidence": 0.81,
        "impact": "Reduce repetitive user rework and improve first-pass task completion.",
        "acceptance_criteria": [
            "Two or more repeated_manual_fix or repeated_retry labels are observed in the lookback window.",
            "The proposed fix has a deterministic regression test for the repeated correction path.",
            "The change preserves human approval for sensitive side effects.",
        ],
        "rollback_plan": "Revert the isolated workflow change and retain the correction labels as evidence if the regression test or review gate fails.",
    },
    "research_failure_churn": {
        "title": "Break repeated autonomous research failure loops",
        "problem": "The research knowledge ledger contains repeated failure signatures across distinct Tickets; only aggregate counts were inspected.",
        "hypothesis": "Identify the common failing stage, add a bounded recovery path and a deterministic regression test, then measure whether repeats decline.",
        "base_priority": 88,
        "base_confidence": 0.79,
        "impact": "Reduce repeated research failures and wasted retrieval work.",
        "acceptance_criteria": [
            "A repeated failure signature is linked to at least two distinct task references.",
            "The proposed recovery is bounded and has a regression test.",
            "No source body, query text or exception message is copied into the improvement proposal.",
        ],
        "rollback_plan": "Disable the new recovery branch and retain the prior bounded failure handling if regression tests fail.",
    },
    "claim_review_backlog": {
        "title": "Reduce the unverified research claim backlog",
        "problem": "The research knowledge ledger contains multiple proposed or unverified claims; only aggregate counts were inspected.",
        "hypothesis": "Add a reviewer work queue and evidence completeness checks without automatically promoting claims to facts.",
        "base_priority": 76,
        "base_confidence": 0.72,
        "impact": "Make research conclusions easier to verify and less likely to remain ambiguous.",
        "acceptance_criteria": [
            "The reviewer queue exposes bounded claim IDs and evidence completeness only.",
            "Only an independent reviewer with stored source or experiment evidence can transition claim status.",
            "No claim is auto-promoted by the radar or by a similarity score.",
        ],
        "rollback_plan": "Disable the queue presentation while retaining claim nodes and their audit history.",
    },
}


def scan_capabilities(
    task_database: str | Path,
    improvement_database: str | Path,
    *,
    now: datetime | None = None,
    lookback_hours: int = 24,
    research_knowledge_database: str | Path | None = None,
    initiative_database: str | Path | None = None,
) -> dict[str, Any]:
    """Turn time-bounded metadata-only signals into ranked, deduplicated proposals."""
    task_path = Path(task_database)
    if not task_path.is_file():
        raise FileNotFoundError(f"task database does not exist: {task_path}")
    if isinstance(lookback_hours, bool) or not isinstance(lookback_hours, int) or lookback_hours <= 0:
        raise ValueError("lookback_hours must be a positive integer")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)
    cutoff = checked_at - timedelta(hours=lookback_hours)

    with TaskQueue(task_path) as queue:
        task_metrics = queue.aggregate_attempt_metrics(since=cutoff.isoformat())
        failure_metrics = queue.aggregate_failure_metrics(since=cutoff.isoformat())
    with ProgressEventStore(task_path) as progress:
        event_counts = progress.aggregate_event_counts(since=cutoff)
        failure_codes = progress.aggregate_failure_codes(since=cutoff)
    with ImprovementStore(improvement_database) as feedback_store:
        feedback_aggregates = feedback_store.aggregate_feedback(since=cutoff)
    knowledge_path = Path(research_knowledge_database) if research_knowledge_database else (
        (task_path.parent.parent if task_path.parent.name.casefold() == "tasks" else task_path.parent)
        / "research" / "knowledge.sqlite3"
    )
    research_metrics = {
        "failure_signature_groups": 0, "repeated_failure_groups": 0,
        "repeated_failure_occurrences": 0, "proposed_claim_backlog": 0, "failed_experiments": 0,
    }
    research_followups: list[dict[str, Any]] = []
    if knowledge_path.is_file():
        with ResearchKnowledgeStore(knowledge_path) as knowledge:
            research_metrics = knowledge.aggregate_improvement_signals(since=cutoff)
            experiments = knowledge.list_nodes(node_type="experiment", limit=500)
            for experiment in experiments:
                metadata = experiment.get("metadata", {})
                outcome = metadata.get("outcome")
                status = metadata.get("status")
                if experiment.get("updated_at", "") < cutoff.isoformat():
                    continue
                if outcome not in {"refutes", "inconclusive", "failed"} and status != "failed":
                    continue
                research_followups.append({
                    "key": "research-experiment-followup:" + experiment["id"],
                    "title": "复核未成功研究实验的边界条件与替代解释",
                    "source_experiment_id": experiment["id"],
                    "source_run_id": metadata.get("run_id"),
                    "source_hypothesis_id": metadata.get("hypothesis_id"),
                    "outcome": outcome or status,
                    "candidate_only": True,
                    "engineering_proposal_created": False,
                    "next_step": "检查实验设计、测量误差、反例与替代解释；必要时设计新的可复现实验",
                })
            research_followups.sort(key=lambda item: item["source_experiment_id"])
            research_followups = research_followups[-50:]
    initiative_path = Path(initiative_database) if initiative_database else (
        (task_path.parent.parent if task_path.parent.name.casefold() == "tasks" else task_path.parent)
        / "initiatives" / "muse.sqlite3"
    )
    creator_video_failures = {"total": 0, "by_code": {}}
    if initiative_path.is_file() and not initiative_path.is_symlink():
        with InitiativeStore(initiative_path) as initiatives:
            creator_video_failures = initiatives.aggregate_failure_metrics(since=cutoff)

    recent_task_failures = event_counts.get(("task", "failed"), 0)
    recent_test_failures = event_counts.get(("test", "failed"), 0)
    failed_tasks = max(task_metrics["failed_tasks"], recent_task_failures)
    signals: list[dict[str, Any]] = []
    if failed_tasks:
        signals.append({"key": "task_failure", "count": failed_tasks})
    repeated_classified_failures = sum(
        count for count in failure_metrics["by_code"].values() if count >= 2
    )
    if repeated_classified_failures:
        signals.append({"key": "repeated_failure_class", "count": repeated_classified_failures})
    if recent_test_failures:
        signals.append({"key": "test_failure", "count": recent_test_failures})
    if task_metrics["retry_attempts"]:
        signals.append({"key": "recovery_churn", "count": task_metrics["retry_attempts"]})
    feedback_count = sum(item["count"] for item in feedback_aggregates)
    if feedback_count >= 2 or any(item["max_severity"] >= 4 for item in feedback_aggregates):
        signals.append({"key": "user_feedback", "count": max(1, feedback_count)})
    repeated_corrections = sum(
        item["count"] for item in feedback_aggregates
        if item["tag"] in {"repeated_manual_fix", "repeated_retry"}
    )
    if repeated_corrections >= 2:
        signals.append({"key": "repeated_correction", "count": repeated_corrections})
    if research_metrics["repeated_failure_groups"] >= 1:
        signals.append({"key": "research_failure_churn", "count": research_metrics["repeated_failure_occurrences"]})
    if research_metrics["proposed_claim_backlog"] >= 5:
        signals.append({"key": "claim_review_backlog", "count": research_metrics["proposed_claim_backlog"]})
    repeated_video_failure_codes = {
        code: count for code, count in creator_video_failures["by_code"].items() if count >= 2
    }
    if repeated_video_failure_codes:
        signals.append({"key": "creator_video_failure", "count": sum(repeated_video_failure_codes.values())})

    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    knowledge_link_errors: list[str] = []

    def link_classified_failure_research(proposal_id: str) -> list[dict[str, str]]:
        if not knowledge_path.is_file():
            return []
        links: list[dict[str, str]] = []
        try:
            with ResearchKnowledgeStore(knowledge_path) as knowledge:
                details = failure_metrics.get("by_code_detail", {})
                for error_code, detail in sorted(details.items()):
                    if detail.get("occurrences", 0) < 2:
                        continue
                    recorded = knowledge.record_classified_failure_research(
                        error_code=error_code,
                        category=detail.get("category", "unknown"),
                        occurrences=detail["occurrences"],
                        retryable=detail["retryable"],
                        non_retryable=detail["non_retryable"],
                        lookback_hours=lookback_hours,
                        observed_at=checked_at.isoformat(),
                        proposal_id=proposal_id,
                    )
                    links.append({
                        "error_code": error_code,
                        "failure_id": recorded["failure_id"],
                        "hypothesis_id": recorded["hypothesis_id"],
                    })
        except Exception as exc:
            # Keep the aggregate proposal available; expose only a safe error class, never exception text.
            knowledge_link_errors.append(type(exc).__name__)
        return links

    knowledge_state_sync = {"updated_nodes": 0, "unchanged_nodes": 0, "proposals_checked": 0}

    def sync_proposal_review_state(proposal: dict[str, Any]) -> None:
        if not knowledge_path.is_file():
            return
        try:
            with ResearchKnowledgeStore(knowledge_path) as knowledge:
                synced = knowledge.sync_improvement_proposal_state(
                    proposal["id"], proposal_status=proposal["status"],
                    events=proposal.get("events", []),
                )
            knowledge_state_sync["updated_nodes"] += synced["updated_nodes"]
            knowledge_state_sync["unchanged_nodes"] += synced["unchanged_nodes"]
            knowledge_state_sync["proposals_checked"] += 1
        except Exception as exc:
            # Do not copy free-text audit notes or exception details into telemetry.
            knowledge_link_errors.append(type(exc).__name__)

    with ImprovementStore(improvement_database) as store:
        trend = store.record_radar_snapshot(
            {item["key"]: item["count"] for item in signals},
            observed_at=checked_at, lookback_hours=lookback_hours,
        )
        proposals = store.list_proposals()
        for proposal in proposals:
            sync_proposal_review_state(proposal)
        known_signals = {
            proposal.get("metadata", {}).get("signal_key", proposal["title"]): proposal
            for proposal in proposals
        }
        for signal in signals:
            spec = _SIGNAL_SPECS[signal["key"]]
            title = spec["title"]
            priority = min(100, spec["base_priority"] + min(max(signal["count"] - 1, 0), 5) * 2)
            confidence = round(min(0.95, spec["base_confidence"] + min(signal["count"], 5) * 0.025), 3)
            evidence = {
                "lookback_hours": lookback_hours,
                "signal_count": signal["count"],
                "failed_tasks": task_metrics["failed_tasks"],
                "recent_task_failure_events": recent_task_failures,
                "test_failures": recent_test_failures,
                "retry_attempts": task_metrics["retry_attempts"],
                "retried_tasks": task_metrics["retried_tasks"],
                "failure_codes": failure_codes,
                "classified_failures": failure_metrics,
                "feedback": feedback_aggregates,
                "research_knowledge": research_metrics,
                "creator_video_failures": creator_video_failures,
                "observed_at": checked_at.isoformat(),
            }
            metadata = {
                "signal_key": signal["key"],
                "confidence": confidence,
                "impact": spec["impact"],
                "evidence": evidence,
                "acceptance_criteria": spec["acceptance_criteria"],
                "rollback_plan": spec["rollback_plan"],
            }
            previous = known_signals.get(signal["key"]) or known_signals.get(title)
            if previous is not None:
                knowledge_links = (
                    link_classified_failure_research(previous["id"])
                    if signal["key"] == "repeated_failure_class" else []
                )
                existing.append({
                    "id": previous["id"], "title": title, "status": previous["status"],
                    "signal_key": signal["key"],
                    "priority": previous.get("priority", priority),
                    "confidence": previous.get("metadata", {}).get("confidence", confidence),
                    "knowledge_links": knowledge_links,
                })
                continue
            proposal = store.create_proposal(
                title=title,
                problem=f"{spec['problem']} Aggregate count in the last {lookback_hours}h: {signal['count']}.",
                hypothesis=spec["hypothesis"],
                priority=priority,
                metadata=metadata,
            )
            knowledge_links = (
                link_classified_failure_research(proposal["id"])
                if signal["key"] == "repeated_failure_class" else []
            )
            sync_proposal_review_state(proposal)
            item = {
                "id": proposal["id"], "title": proposal["title"], "status": proposal["status"],
                "signal_key": signal["key"],
                "priority": priority, "confidence": confidence, "impact": spec["impact"],
                "evidence": evidence, "acceptance_criteria": spec["acceptance_criteria"],
                "rollback_plan": spec["rollback_plan"], "knowledge_links": knowledge_links,
            }
            created.append(item)
            known_signals[signal["key"]] = proposal

    created.sort(key=lambda item: (-item["priority"], item["title"]))
    existing.sort(key=lambda item: (-item["priority"], item["title"]))
    try:
        with ProgressEventStore(task_path) as progress:
            progress.record(
                "system", "capability-radar", "completed",
                summary=f"Capability scan completed: signals={len(signals)}; created={len(created)}; existing={len(existing)}.",
            )
    except Exception:
        # A telemetry failure must not undo a proposal or block the scheduled self-check.
        pass

    from muse.value_forge import ValueForge

    signals_seen = [
        {
            "key": item["key"],
            "count": item["count"],
            "priority": min(
                100,
                _SIGNAL_SPECS[item["key"]]["base_priority"]
                + min(max(item["count"] - 1, 0), 5) * 2,
            ),
            "confidence": round(
                min(
                    0.95,
                    _SIGNAL_SPECS[item["key"]]["base_confidence"]
                    + min(item["count"], 5) * 0.025,
                ),
                3,
            ),
        }
        for item in signals
    ]
    value_forge_result = ValueForge().evaluate_capability_radar_result({
        "signals_seen": signals_seen,
        "created": created,
        "existing": existing,
        "trend": trend,
    })

    return {
        "scanned_at": checked_at.isoformat(),
        "lookback_hours": lookback_hours,
        "signals_seen": signals_seen,
        "created": created,
        "existing": existing,
        "trend": trend,
        "value_forge": value_forge_result,
        "research_knowledge_signals": research_metrics,
        "research_followups": research_followups,
        "creator_video_failures": creator_video_failures,
        "knowledge_link_errors": knowledge_link_errors,
        "knowledge_state_sync": knowledge_state_sync,
        "approval_required": True,
    }

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate ranked capability proposals from operational aggregates")
    parser.add_argument("--task-db", default="data/tasks/muse.sqlite3")
    parser.add_argument("--improvement-db", default="data/improvements/muse.sqlite3")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--knowledge-db", default="data/research/knowledge.sqlite3")
    args = parser.parse_args(argv)
    result = scan_capabilities(
        args.task_db, args.improvement_db, lookback_hours=args.lookback_hours,
        research_knowledge_database=args.knowledge_db,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
