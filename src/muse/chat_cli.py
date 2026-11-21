"""Interactive one-turn chat CLI backed by persistent local conversation threads."""

from __future__ import annotations

import argparse
import ipaddress
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


from muse.assistant import ChatModel, MuseAssistant
from muse.conversations import ConversationStore
from muse.memory import MemoryStore
from muse.memory_extraction import LocalMemoryCandidateExtractor
from muse.model_client import LMStudioClient, ModelError
from muse.model_call_context import current_model_call_context, model_call_scope


def is_loopback_endpoint(base_url: str) -> bool:
    """Return true only for HTTP endpoints addressed to localhost/loopback IPs."""
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return False
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def run_turn(
    database: str | Path,
    thread_id: str,
    message: str,
    model: ChatModel,
    *,
    memory_database: str | Path | None = "data/memory/muse.sqlite3",
    usage_database: str | Path = "data/tasks/muse.sqlite3",
    propose_memories: bool = False,
) -> dict[str, Any]:
    if propose_memories and memory_database is None:
        raise ValueError("memory candidate extraction requires an enabled memory database")
    with ExitStack() as stack:
        conversations = stack.enter_context(ConversationStore(database))
        memory = None
        if memory_database is not None and (Path(memory_database).is_file() or propose_memories):
            memory = stack.enter_context(MemoryStore(memory_database))
        extractor = LocalMemoryCandidateExtractor(model) if propose_memories else None
        assistant = MuseAssistant(
            conversations,
            model,
            system_prompt="You are Muse, a thoughtful, practical personal AI companion. Be honest about uncertainty and never claim an action you did not perform.",
            memory=memory,
            memory_candidate_extractor=extractor,
        )
        if current_model_call_context() is not None:
            result = assistant.reply(thread_id, message)
        else:
            # Standalone chat and opt-in memory extraction are project work, not fake task/agent work.
            with model_call_scope(usage_database, task_id=None, agent_id=None, project_id="muse"):
                result = assistant.reply(thread_id, message)
        checkpoint = conversations.load(thread_id)
        return {**result, "messages": checkpoint["messages"]}




def main() -> int:
    parser = argparse.ArgumentParser(description="Chat with Muse using a local LM Studio model")
    parser.add_argument("message", help="message to send to Muse")
    parser.add_argument("--thread", default="main", help="persistent conversation thread id")
    parser.add_argument("--db", default="data/conversations/muse.sqlite3")
    parser.add_argument("--memory-db", default="data/memory/muse.sqlite3")
    parser.add_argument("--no-memory", action="store_true", help="disable local memory retrieval for this turn")
    parser.add_argument(
        "--propose-memories", action="store_true",
        help="opt in to local-model memory extraction; candidates remain pending until explicitly approved",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:1234/v1")
    parser.add_argument("--model", default=None, help="optional exact model id exposed by LM Studio")
    args = parser.parse_args()
    if args.no_memory and args.propose_memories:
        parser.error("--propose-memories cannot be combined with --no-memory")
    if args.propose_memories and not is_loopback_endpoint(args.base_url):
        parser.error("--propose-memories requires a localhost/loopback model endpoint; private messages must stay local")

    try:
        result = run_turn(
            args.db,
            args.thread,
            args.message,
            LMStudioClient(args.base_url, model=args.model),
            memory_database=None if args.no_memory else args.memory_db,
            propose_memories=args.propose_memories,
        )
    except (ModelError, OSError, ValueError) as exc:
        parser.exit(2, f"Muse could not complete this turn: {exc}\n")
    print(result["reply"])
    print(f"[thread={result['thread_id']} revision={result['revision']} messages={len(result['messages'])}]")
    if args.propose_memories:
        print(f"[memory candidates pending approval={result['memory_candidates_created']} rejected={result['memory_candidates_rejected']} status={result['memory_extraction_status']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())