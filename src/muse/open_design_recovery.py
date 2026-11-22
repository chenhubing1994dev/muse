"""Persistence-only recovery for completed Open Design runs.

This module deliberately has no OpenDesignAdapter dependency: recovery must never
start, poll, or resubmit a cloud run. The caller must obtain the result and event id
from the trusted append-only TaskQueue history, not from a user-supplied ticket payload.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from muse.open_design_evidence import persist_open_design_artifact_evidence


_TASK_ID = re.compile(r"[0-9a-f]{32}\Z")
_EVENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def persist_completed_open_design_run_evidence(
    *,
    knowledge_database: str | Path,
    initiative_database: str | Path,
    payload: dict[str, Any],
    task_id: str,
    completed_result: dict[str, Any],
    completion_event_id: str,
) -> dict[str, Any]:
    """Persist evidence from a previously completed run without starting another run.

    Security boundary: the Worker must first prove that completion_event_id is a
    trusted TaskQueue event for task_id and that its result contains the completed
    Open Design run. This function validates the event identifier and all run/task/
    project/evidence bindings, but intentionally does not open the TaskQueue database.
    """
    if not isinstance(payload, dict) or payload.get("kind") != "open_design_run":
        raise ValueError("recovery requires the original Open Design task payload")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise ValueError("task_id must be a 32-character lowercase UUID hex string")
    if not isinstance(completion_event_id, str) or not _EVENT_ID.fullmatch(completion_event_id):
        raise ValueError("recovery requires a trusted TaskQueue completion event id")
    if not isinstance(completed_result, dict) or completed_result.get("status") != "completed":
        raise ValueError("recovery requires a completed Open Design run result")
    result_task_id = completed_result.get("task_id")
    if result_task_id is not None and result_task_id != task_id:
        raise ValueError("completed Open Design result task_id does not match the TaskQueue ticket")
    project_id = payload.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("original Open Design task payload is missing its project id")
    if completed_result.get("project_id") != project_id:
        raise ValueError("completed Open Design result project does not match the approved task")
    run_id = completed_result.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("completed Open Design result is missing its run id")
    evidence = completed_result.get("artifact_evidence")
    if not isinstance(evidence, dict) or evidence.get("complete") is not True:
        raise ValueError("persistence-only recovery requires a complete artifact evidence bundle")
    if evidence.get("project_id") != project_id:
        raise ValueError("artifact evidence project does not match the approved Open Design task")

    persisted = persist_open_design_artifact_evidence(
        knowledge_database=knowledge_database,
        initiative_database=initiative_database,
        task_id=task_id,
        run_id=run_id,
        evidence=evidence,
        initiative_id=payload.get("initiative_id"),
    )
    return {
        **persisted,
        "recovery_mode": "persistence_only",
        "recovered_from_event_id": completion_event_id,
        "new_open_design_run_started": False,
    }
