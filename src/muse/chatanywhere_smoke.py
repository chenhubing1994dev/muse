"""One-shot, non-persistent ChatAnywhere -> Ruflo -> Muse execution smoke test."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from muse.error_journal import record_suppressed_error


def run_smoke_test(
    api_key: str,
    *,
    model: str = "gpt-4o-mini",
    runtime_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run one explicitly approved Muse ticket; keep the API key process-local."""
    secret = api_key.strip() if isinstance(api_key, str) else ""
    if not secret:
        raise ValueError("ChatAnywhere API key is required")
    model = model.strip() if isinstance(model, str) else ""
    if not model or len(model) > 128 or any(ord(char) < 32 for char in model):
        raise ValueError("model must be a valid model identifier")

    from muse.unified_runtime import MuseRuntime

    factory = runtime_factory or MuseRuntime
    names = ("CHATANYWHERE_API_KEY", "MUSE_RUFLO_PROVIDER", "CHATANYWHERE_MODEL")
    previous = {name: os.environ.get(name) for name in names}
    try:
        os.environ["CHATANYWHERE_API_KEY"] = secret
        os.environ["MUSE_RUFLO_PROVIDER"] = "chatanywhere"
        os.environ["CHATANYWHERE_MODEL"] = model

        with tempfile.TemporaryDirectory(prefix="muse-chatanywhere-smoke-") as folder:
            root = Path(folder)
            runtime = factory(
                root / "muse.sqlite3",
                workspace=root,
                memory_database=root / "memory.sqlite3",
            )
            try:
                task = runtime.enqueue(
                    "ChatAnywhere Ruflo one-agent smoke test",
                    {
                        "goal": "Reply with exactly MUSE_RUFLO_CHATANYWHERE_OK. Do not call tools or perform external actions.",
                        "role": "researcher",
                        "model": "sonnet",
                        "max_tokens": 128,
                    },
                    priority=100,
                )
                result = runtime.dispatch_once(task_id=task["id"], approved=True)
                if not isinstance(result, dict) or result.get("status") != "succeeded":
                    raise RuntimeError("Ruflo Agent execution did not complete successfully")
                provider_result = result.get("result") or {}
                output = provider_result.get("output") if isinstance(provider_result, dict) else None
                if not isinstance(output, str) or output.strip() != "MUSE_RUFLO_CHATANYWHERE_OK":
                    raise RuntimeError("Ruflo Agent did not return the expected marker")
                return {
                    "status": "succeeded",
                    "provider": "chatanywhere_openai_compat",
                    "model": model,
                    "task_id": result.get("task_id", task["id"]),
                    "agent_id": result.get("agent_id"),
                    "output": output,
                }
            finally:
                # Stop the per-workspace daemon before the temporary directory is removed.
                adapter = getattr(runtime, "ruflo", None)
                stop_daemon = getattr(adapter, "_run", None)
                if callable(stop_daemon):
                    try:
                        stop_daemon(("daemon", "stop"), timeout=30)
                    except Exception as _muse_suppressed:
                        record_suppressed_error("chatanywhere_smoke.py:78", _muse_suppressed)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        secret = ""

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one isolated ChatAnywhere-backed Ruflo Agent through Muse."
    )
    parser.add_argument("--model", default="gpt-4o-mini", help="ChatAnywhere model id (default: gpt-4o-mini)")
    args = parser.parse_args(argv)
    if not sys.stdin.isatty():
        parser.error("run this command in an interactive terminal so the API key can be entered without echo")
    try:
        key = getpass.getpass("ChatAnywhere API key (hidden; not saved): ")
    except (EOFError, KeyboardInterrupt):
        print("Smoke test cancelled.", file=sys.stderr)
        return 130
    if not key.strip():
        print("No API key entered; nothing was executed.", file=sys.stderr)
        return 2
    try:
        result = run_smoke_test(key, model=args.model)
    except Exception as exc:
        print(
            f"ChatAnywhere Ruflo smoke test failed ({type(exc).__name__}). "
            "The key was not written to a project file.",
            file=sys.stderr,
        )
        return 1
    finally:
        key = ""
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
