"""Consent-gated CLI for contextual inquiry tasks with optional encrypted case storage.

Task creation, exact-goal storage, and complete-case ciphertext storage require
separate confirmations. No confirmation grants review execution approval or
long-term knowledge-memory consent.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

from muse.contextual_inquiry import (
    ActionExperiment,
    ContextualInquiry,
    Hypothesis,
    Observation,
    SafetyRoute,
    SourceKind,
    StakeholderView,
    ViewStatus,
)
from muse.contextual_inquiry_bridge import enqueue_review_task
from muse.contextual_inquiry_security import protect_contextual_inquiry

from muse.task_queue import TaskQueue

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "tasks" / "muse.sqlite3"
MAX_INPUT_BYTES = 2 * 1024 * 1024


def inquiry_from_dict(data: dict[str, Any]) -> ContextualInquiry:
    """Validate a JSON inquiry without allowing the file to self-grant consent."""
    if not isinstance(data, dict):
        raise ValueError("inquiry JSON must be an object")
    allowed = {
        "inquiry_id", "user_goal", "observations", "hypotheses", "unknowns",
        "stakeholder_views", "action_experiments", "safety_route", "schema_version",
    }
    unexpected = set(data) - allowed
    if unexpected:
        raise ValueError("unsupported inquiry fields: " + ", ".join(sorted(unexpected)))

    observations = tuple(
        Observation(
            observation_id=item["observation_id"],
            source_kind=SourceKind(item["source_kind"]),
            content=item["content"],
            source_ref=item["source_ref"],
            observed_at=item.get("observed_at", ""),
        )
        for item in data.get("observations", [])
    )
    hypotheses = tuple(
        Hypothesis(
            hypothesis_id=item["hypothesis_id"],
            statement=item["statement"],
            source_refs=tuple(item.get("source_refs", ())),
            counter_refs=tuple(item.get("counter_refs", ())),
            confidence=item.get("confidence", 0.25),
            unsourced=item.get("unsourced", False),
            falsification_condition=item.get("falsification_condition", ""),
        )
        for item in data.get("hypotheses", [])
    )
    stakeholder_views = tuple(
        StakeholderView(
            stakeholder_id=item["stakeholder_id"],
            status=ViewStatus(item["status"]),
            statement=item.get("statement", ""),
            observation_ref=item.get("observation_ref", ""),
        )
        for item in data.get("stakeholder_views", [])
    )
    action_experiments = tuple(
        ActionExperiment(
            experiment_id=item["experiment_id"],
            owner=item["owner"],
            due_date=item["due_date"],
            resource_constraints=tuple(item.get("resource_constraints", ())),
            expected_signal=item["expected_signal"],
            stop_condition=item["stop_condition"],
            reversible=item.get("reversible", True),
        )
        for item in data.get("action_experiments", [])
    )
    return ContextualInquiry(
        inquiry_id=data["inquiry_id"],
        user_goal=data["user_goal"],
        observations=observations,
        hypotheses=hypotheses,
        unknowns=tuple(data.get("unknowns", ())),
        stakeholder_views=stakeholder_views,
        action_experiments=action_experiments,
        safety_route=SafetyRoute(data.get("safety_route", SafetyRoute.NORMAL.value)),
        consent_to_store=False,
        schema_version=data.get("schema_version", 1),
    )


def _ask_yes_no(prompt: str, input_fn: Callable[[str], str]) -> bool:
    try:
        answer = input_fn(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return isinstance(answer, str) and answer.strip().casefold() in {"y", "yes"}


def command_submit(args: argparse.Namespace, *, input_fn: Callable[[str], str] = input) -> int:
    source = Path(args.input).expanduser()
    if not source.is_file():
        raise FileNotFoundError("inquiry input file was not found")
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("inquiry input exceeds the 2 MiB limit")
    raw = json.loads(source.read_text(encoding="utf-8"))
    inquiry = inquiry_from_dict(raw)

    print(
        "This creates one approval-gated review task. The queue never receives plaintext observations, source text, "
        "third-party statements, unknowns, hypotheses, or action narratives. If you separately consent, it stores "
        "only a Windows DPAPI CurrentUser ciphertext envelope. Task approval and execution remain separate.",
        file=sys.stderr,
    )
    if not _ask_yes_no("Create this review task? [y/N] ", input_fn):
        print(json.dumps({"ok": False, "status": "cancelled", "writes": "none"}))
        return 2

    print(
        "The user goal may be personal. A yes stores only that goal in this task's "
        "local SQLite record; it does not grant execution approval or save a case "
        "to long-term knowledge memory.",
        file=sys.stderr,
    )
    include_goal = _ask_yes_no("Separately consent to store the user goal in this task? [y/N] ", input_fn)
    if include_goal:
        inquiry = replace(inquiry, consent_to_store=True)

    print(
        "A third, independent yes stores the complete case (including observations, hypotheses, unknowns, "
        "third-party statements and proposed experiments) as DPAPI-protected ciphertext in the existing local "
        "TaskQueue. Decryption requires this Windows user profile; it is not isolation from malware or other "
        "programs already running as this same user. This does not authorize review or long-term knowledge-memory "
        "storage; the task remains blocked until its review handler is ready.",
        file=sys.stderr,
    )
    store_case_context = _ask_yes_no("Separately consent to store the complete encrypted case context? [y/N] ", input_fn)
    encrypted_case_context = (
        protect_contextual_inquiry(inquiry, consent=True) if store_case_context else None
    )

    with TaskQueue(args.database) as queue:
        task = enqueue_review_task(
            queue,
            inquiry,
            include_user_goal=include_goal,
            case_storage_consent=store_case_context,
            encrypted_case_context=encrypted_case_context,
            priority=args.priority,
        )

    print(json.dumps({
        "ok": True,
        "task_id": task["id"],
        "status": task["status"],
        "approval_required": True,
        "execution": "not_started",
        "user_goal_storage": "task_record" if include_goal else "not_stored",
        "encrypted_case_context_stored": store_case_context,
        "case_context_attached": False,
        "long_term_case_storage": "not_performed",
        "next_action": (
            "do_not_approve_until_context_review_handler_is_registered"
            if store_case_context else "do_not_approve_until_context_reentry_is_available"
        ),
    }, ensure_ascii=False))
    return 0


def _priority(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("priority must be an integer from 0 to 100") from exc
    if not 0 <= parsed <= 100:
        raise argparse.ArgumentTypeError("priority must be an integer from 0 to 100")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an approval-gated Muse contextual inquiry review task with separate consent prompts"
    )
    parser.add_argument("--input", required=True, help="path to a local contextual inquiry JSON file")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE), help="existing Muse TaskQueue SQLite database")
    parser.add_argument("--priority", type=_priority, default=50)
    parser.set_defaults(handler=command_submit)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, RuntimeError, PermissionError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
