"""Narrow local bridge entry point for ChatGPT -> WebCodex Runner -> Muse.

This CLI exposes local classification, approval-gated ticket intake, read-only Ticket observation, and explicit local conversation-history import.
It does not execute tools, invoke Ruflo, or bypass Muse's Commander policy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from muse.gemma_intent import LMStudioGemmaIntentClassifier
from muse.intent import classify_request
from muse.task_queue import TaskQueue
from muse.conversations import ConversationStore

DEFAULT_DATABASE = PROJECT_ROOT / "data" / "tasks" / "muse.sqlite3"
DEFAULT_LMSTUDIO_URL = "http://127.0.0.1:1234/v1"

DEFAULT_CONVERSATION_DATABASE = PROJECT_ROOT / "data" / "conversations" / "muse.sqlite3"
MAX_CHATGPT_EXPORT_BYTES = 100 * 1024 * 1024
MAX_IMPORTED_MESSAGE_CHARS = 20_000


def _extract_chatgpt_messages(conversation: dict[str, Any], *, max_messages: int) -> list[dict[str, Any]]:
    """Extract only the current user/assistant text branch from one ChatGPT export thread."""
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return []
    ordered_nodes: list[dict[str, Any]] = []
    current = conversation.get("current_node")
    visited: set[str] = set()
    while isinstance(current, str) and current in mapping and current not in visited:
        visited.add(current)
        node = mapping[current]
        if not isinstance(node, dict):
            break
        ordered_nodes.append(node)
        parent = node.get("parent")
        current = parent if isinstance(parent, str) else None
    if ordered_nodes:
        ordered_nodes.reverse()
    else:
        indexed = list(enumerate(mapping.values()))
        def sort_key(pair: tuple[int, Any]) -> tuple[float, int]:
            node = pair[1]
            message = node.get("message") if isinstance(node, dict) else None
            raw_time = message.get("create_time") if isinstance(message, dict) else None
            try:
                stamp = float(raw_time or 0)
            except (TypeError, ValueError, OverflowError):
                stamp = 0.0
            return stamp, pair[0]
        indexed.sort(key=sort_key)
        ordered_nodes = [node for _, node in indexed if isinstance(node, dict)]

    title = str(conversation.get("title") or "Untitled ChatGPT conversation")[:300]
    messages: list[dict[str, Any]] = []
    for node in ordered_nodes:
        message = node.get("message") if isinstance(node, dict) else None
        if not isinstance(message, dict):
            continue
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else None
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            continue
        metadata = message.get("metadata")
        message_type = metadata.get("message_type") if isinstance(metadata, dict) else None
        if isinstance(metadata, dict) and metadata.get("is_visually_hidden_from_conversation") is True:
            continue
        if isinstance(message_type, str) and message_type in {"analysis", "chain_of_thought", "notification", "delegate_summary"}:
            continue
        content = message.get("content")
        content_type = content.get("content_type") if isinstance(content, dict) else None
        if isinstance(content_type, str) and content_type in {"thoughts", "reasoning_recap"}:
            continue
        parts = content.get("parts", []) if isinstance(content, dict) else content
        if isinstance(parts, str):
            parts = [parts]
        if not isinstance(parts, list):
            continue
        text_parts: list[str] = []
        for part in parts:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                text_value = part.get("text")
                if isinstance(text_value, str):
                    text_parts.append(text_value)
        body = "\n".join(item.strip() for item in text_parts if item.strip()).strip()
        if not body:
            continue
        body = body[:MAX_IMPORTED_MESSAGE_CHARS]
        created_at = None
        raw_time = message.get("create_time")
        if isinstance(raw_time, (int, float)) and not isinstance(raw_time, bool):
            try:
                created_at = datetime.fromtimestamp(raw_time, timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                created_at = None
        messages.append({
            "role": role,
            "content": body,
            "created_at": created_at,
            "message_id": str(message.get("id") or "")[:128],
            "thread_title": title,
            "source": "chatgpt_export_local_import",
            "epistemic_status": "creator_dialogue_inspiration_only",
            "is_evidence": False,
            "is_prediction": False,
        })
        if len(messages) >= max_messages:
            break
    return messages


def command_import_chatgpt_export(args: argparse.Namespace) -> int:
    if not args.confirm_local_import:
        _json({
            "ok": False, "error": "explicit_local_import_confirmation_required",
            "network_upload": "none", "execution": "not_started",
        })
        return 2
    source = Path(args.input).expanduser()
    if not source.is_file():
        _json({"ok": False, "error": "export_file_not_found", "network_upload": "none"})
        return 2
    if source.stat().st_size > MAX_CHATGPT_EXPORT_BYTES:
        _json({"ok": False, "error": "export_file_exceeds_100_mib_limit", "network_upload": "none"})
        return 2
    payload = json.loads(source.read_text(encoding="utf-8"))
    conversations = payload.get("conversations") if isinstance(payload, dict) else payload
    if not isinstance(conversations, list):
        _json({"ok": False, "error": "unsupported_chatgpt_export_shape", "network_upload": "none"})
        return 2
    if not 1 <= args.max_threads <= 1000 or not 1 <= args.max_messages_per_thread <= 1000:
        raise ValueError("import bounds must be between 1 and 1000")
    if not 1 <= args.max_total_chars <= 10_000_000:
        raise ValueError("max_total_chars must be between 1 and 10000000")

    imported_threads = skipped_existing = skipped_empty = truncated_threads = 0
    total_messages = total_chars = 0
    with ConversationStore(args.database) as store:
        for index, conversation in enumerate(conversations[:args.max_threads]):
            if not isinstance(conversation, dict):
                skipped_empty += 1
                continue
            raw_id = conversation.get("id") or conversation.get("conversation_id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                raw_id = f"entry-{index}-{uuid.uuid5(uuid.NAMESPACE_URL, str(conversation.get('title', ''))).hex[:12]}"
            thread_id = "chatgpt-export:" + raw_id[:240]
            if store.load(thread_id) is not None and not args.overwrite:
                skipped_existing += 1
                continue
            messages = _extract_chatgpt_messages(
                conversation, max_messages=args.max_messages_per_thread,
            )
            if not messages:
                skipped_empty += 1
                continue
            remaining = args.max_total_chars - total_chars
            if remaining <= 0:
                truncated_threads += 1
                break
            accepted: list[dict[str, Any]] = []
            for message in messages:
                content = message["content"]
                if len(content) > remaining:
                    message = {**message, "content": content[:remaining]}
                    truncated_threads += 1
                accepted.append(message)
                remaining -= len(message["content"])
                if remaining <= 0:
                    break
            if not accepted:
                truncated_threads += 1
                break
            if len(accepted) < len(messages):
                truncated_threads += 1
            store.save(thread_id, accepted)
            imported_threads += 1
            total_messages += len(accepted)
            total_chars += sum(len(message["content"]) for message in accepted)
            if total_chars >= args.max_total_chars:
                break
    _json({
        "ok": True,
        "imported_threads": imported_threads,
        "imported_messages": total_messages,
        "imported_characters": total_chars,
        "skipped_existing": skipped_existing,
        "skipped_empty_or_unsupported": skipped_empty,
        "truncated_threads": truncated_threads,
        "database": str(args.database),
        "network_upload": "none_local_only",
        "epistemic_status": "creator_dialogue_inspiration_only",
    })
    return 0



def _json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str))


def _configure_local_model(base_url: str, model: str | None, timeout: int) -> None:
    os.environ["MUSE_GEMMA_ENABLED"] = "1"
    os.environ["MUSE_GEMMA_BACKEND"] = "lmstudio"
    os.environ["MUSE_GEMMA_LMSTUDIO_URL"] = base_url
    os.environ["MUSE_GEMMA_TIMEOUT_SECONDS"] = str(timeout)
    if model:
        os.environ["MUSE_GEMMA_LMSTUDIO_MODEL"] = model


def _add_model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=os.environ.get("MUSE_GEMMA_LMSTUDIO_URL", DEFAULT_LMSTUDIO_URL))
    parser.add_argument("--model", default=os.environ.get("MUSE_GEMMA_LMSTUDIO_MODEL") or None)
    parser.add_argument("--timeout", type=int, default=20, choices=range(1, 31))


def command_health(args: argparse.Namespace) -> int:
    _configure_local_model(args.base_url, args.model, args.timeout)
    classifier = LMStudioGemmaIntentClassifier(
        base_url=args.base_url, model=args.model, timeout_seconds=float(args.timeout),
    )
    try:
        from muse.model_client import LMStudioClient
        models = LMStudioClient(args.base_url, model=args.model, timeout=float(args.timeout)).list_models()
        _json({
            "ok": True,
            "bridge": "chatgpt-webcodex-muse",
            "commander": "existing_muse_commander",
            "lm_studio": "reachable",
            "models": [item.get("id") for item in models if isinstance(item, dict)],
            "classifier_ready": bool(models),
            "execution": "not_started",
        })
        return 0
    except Exception as exc:
        _json({"ok": False, "error": type(exc).__name__, "execution": "not_started"})
        return 2


def command_classify(args: argparse.Namespace) -> int:
    _configure_local_model(args.base_url, args.model, args.timeout)
    try:
        classifier = LMStudioGemmaIntentClassifier(
            base_url=args.base_url, model=args.model, timeout_seconds=float(args.timeout),
        )
    except ValueError:
        _json({"ok": False, "error": "invalid_or_non_loopback_endpoint", "execution": "not_started"})
        return 2
    result = classifier.classify(args.request)
    if result is None:
        _json({
            "ok": False, "classification": None,
            "fallback": "deterministic_or_human_review",
            "execution": "not_started",
        })
        return 2
    _json({
        "ok": True, "source": "local_lm_studio_gemma",
        "advisory_only": True, "classification": result,
        "execution": "not_started",
    })
    return 0


def command_route(args: argparse.Namespace) -> int:
    _configure_local_model(args.base_url, args.model, args.timeout)
    result = classify_request(args.request)
    _json({
        "ok": True, "source": result.get("classifier_source", "deterministic_rules"),
        "advisory_only": result.get("classifier_source") == "optional_local_gemma",
        "intent": result, "execution": "not_started",
    })
    return 0


def command_submit(args: argparse.Namespace) -> int:
    _configure_local_model(args.base_url, args.model, args.timeout)
    intent = classify_request(args.request)
    request_key = args.request_id or uuid.uuid4().hex
    payload = {
        "goal": args.request,
        "source": "chatgpt_webcodex_bridge",
        "intent": intent,
        "approval_required": True,
        "execution_authorized": False,
    }
    with TaskQueue(args.database) as queue:
        task = queue.enqueue(
            args.title or args.request[:120],
            payload,
            priority=args.priority,
            estimated_seconds=args.estimated_seconds,
            recovery_policy="manual",
            idempotency_key="chatgpt-bridge:" + request_key,
            require_approval=True,
        )
    _json({
        "ok": True,
        "task_id": task["id"],
        "status": task["status"],
        "approval_required": True,
        "intent_source": intent.get("classifier_source", "deterministic_rules"),
        "next_action": "explicit_human_approval_required",
        "execution": "not_started",
    })
    return 0


def command_approve(args: argparse.Namespace) -> int:
    with TaskQueue(args.database) as queue:
        task = queue.approve(args.task_id, approved_by=args.approved_by)
    _json({
        "ok": True, "task_id": task["id"], "status": task["status"],
        "approved_by": task["payload"]["_approval"]["approved_by"],
        "next_action": "separate_dispatch_required",
        "execution": "not_started",
    })
    return 0


def _safe_workflow_status(queue: TaskQueue, task_id: str) -> dict[str, Any] | None:
    """Project the existing workflow ledger into a payload-free status summary."""
    events = queue.workflow_events(task_id, limit=500)
    bound = next((event for event in events if event["event_type"] == "workflow.bound"), None)
    if bound is None:
        return None
    stage_event = next(
        (event for event in reversed(events) if event["event_type"] == "workflow.stage_changed"),
        None,
    )
    stage_payload = stage_event["payload"] if stage_event else {}
    stage = stage_payload.get("stage", "plan")
    step_id = stage_payload.get("step_id")
    attempt = stage_payload.get("attempt", 0)
    if not isinstance(stage, str) or len(stage) > 48:
        stage = "unknown"
    if not isinstance(step_id, str) or len(step_id) > 128:
        step_id = None
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        attempt = 0
    latest = events[-1] if events else bound
    return {
        "status": queue.workflow_status(task_id),
        "revision": queue.workflow_revision(task_id),
        "stage": stage,
        "step_id": step_id,
        "attempt": attempt,
        "event_count": len(events),
        "last_event_type": latest["event_type"],
        "updated_at": latest["created_at"],
    }


def command_task_status(args: argparse.Namespace) -> int:
    """Read one Commander Ticket without exposing its payload or event contents."""
    if not isinstance(args.task_id, str) or not args.task_id.strip() or len(args.task_id) > 128:
        _json({"ok": False, "status": "failed", "error": "invalid_task_id"})
        return 2
    if isinstance(args.events_limit, bool) or not isinstance(args.events_limit, int) or not 1 <= args.events_limit <= 100:
        _json({"ok": False, "status": "failed", "error": "events_limit_must_be_1_to_100"})
        return 2
    with TaskQueue(args.database) as queue:
        task = queue.get(args.task_id)
        if task is None:
            _json({"ok": False, "status": "failed", "error": "task_not_found", "task_id": args.task_id})
            return 2
        events = queue.events(args.task_id, limit=args.events_limit)
        workflow = _safe_workflow_status(queue, args.task_id)
    approval = task.get("payload", {}).get("_approval", {})
    safe_events = [{
        "event_id": item["event_id"], "event_type": item["event_type"],
        "actor": item["actor"], "created_at": item["created_at"],
    } for item in events]
    _json({
        "ok": True, "status": "completed", "ticket": {
            "task_id": task["id"], "title": task["title"], "status": task["status"],
            "created_at": task["created_at"], "updated_at": task["updated_at"],
            "attempts": task["attempts"], "priority": task["priority"],
            "recovery_policy": task["recovery_policy"],
            "approval_required": bool(task.get("payload", {}).get("approval_required")),
            "approved_by": approval.get("approved_by") if isinstance(approval, dict) else None,
            "lease_owner": task["lease_owner"], "lease_expires_at": task["lease_expires_at"],
        }, "events": safe_events, "event_count": len(safe_events),
        "workflow": workflow,
        "execution": "not_started", "read_only": True,
    })
    return 0


def command_task_list(args: argparse.Namespace) -> int:
    """Read the existing Commander queue's payload-free operational snapshot."""
    with TaskQueue(args.database) as queue:
        snapshot = queue.snapshot(limit=args.limit)
    _json({"ok": True, "status": "completed", **snapshot, "execution": "not_started", "read_only": True})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ChatGPT -> WebCodex Runner -> Muse local bridge (no autonomous execution)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="check local LM Studio and bridge readiness")
    _add_model_options(health)
    health.set_defaults(handler=command_health)

    classify = sub.add_parser("classify", help="classify one request directly with local Gemma")
    classify.add_argument("--request", required=True)
    _add_model_options(classify)
    classify.set_defaults(handler=command_classify)

    route = sub.add_parser("route", help="run deterministic routing first, Gemma only for unknown intent")
    route.add_argument("--request", required=True)
    _add_model_options(route)
    route.set_defaults(handler=command_route)

    submit = sub.add_parser("submit", help="create one approval-gated Commander ticket")
    submit.add_argument("--request", required=True)
    submit.add_argument("--request-id")
    submit.add_argument("--title")
    submit.add_argument("--database", default=str(DEFAULT_DATABASE))
    submit.add_argument("--priority", type=int, default=50, choices=range(0, 101))
    submit.add_argument("--estimated-seconds", type=int, default=900)
    _add_model_options(submit)
    submit.set_defaults(handler=command_submit)

    approve = sub.add_parser("approve", help="approve one waiting Commander ticket; does not dispatch it")
    approve.add_argument("--task-id", required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--database", default=str(DEFAULT_DATABASE))
    approve.set_defaults(handler=command_approve)

    task_status = sub.add_parser("task-status", help="read one Commander Ticket and safe lifecycle metadata")
    task_status.add_argument("--task-id", required=True)
    task_status.add_argument("--database", default=str(DEFAULT_DATABASE))
    task_status.add_argument("--events-limit", type=int, default=20)
    task_status.set_defaults(handler=command_task_status)

    task_list = sub.add_parser("task-list", help="read a payload-free Commander queue snapshot")
    task_list.add_argument("--database", default=str(DEFAULT_DATABASE))
    task_list.add_argument("--limit", type=int, default=20)
    task_list.set_defaults(handler=command_task_list)
    import_export = sub.add_parser(
        "import-chatgpt-export",
        help="import a user-supplied ChatGPT data export into Muse local conversation memory",
    )
    import_export.add_argument("--input", required=True, help="path to the exported conversations.json")
    import_export.add_argument("--database", default=str(DEFAULT_CONVERSATION_DATABASE))
    import_export.add_argument("--confirm-local-import", action="store_true", help="confirm this private export is stored locally in Muse")
    import_export.add_argument("--overwrite", action="store_true", help="replace previously imported thread snapshots")
    import_export.add_argument("--max-threads", type=int, default=200)
    import_export.add_argument("--max-messages-per-thread", type=int, default=200)
    import_export.add_argument("--max-total-chars", type=int, default=2_000_000)
    import_export.set_defaults(handler=command_import_chatgpt_export)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, RuntimeError, PermissionError) as exc:
        _json({"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
