from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import webcodex_psmux_exec as adapter  # noqa: E402

SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from muse import chatgpt_bridge  # noqa: E402
from muse.terminal_runtime import HerdrCoreProvider, PsmuxProvider  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="muse-dev",
        description="Control detached Muse development jobs and the existing approval-gated Commander TaskQueue.",
    )
    commands = parser.add_subparsers(dest="action", required=True)

    run = commands.add_parser("run", help="run one script through the psmux adapter")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--script", help="inline script text")
    source.add_argument("--script-path", help="script path relative to the Muse project root")
    run.add_argument(
        "--language",
        choices=("powershell", "python", "bash", "batch"),
        default=None,
    )
    run.add_argument("--cwd", default=".", help="project-relative working directory")
    run.add_argument("--session", default=adapter.DEFAULT_SESSION)
    run.add_argument("--timeout", type=int, default=120, help="execution timeout in seconds (1..7200)")

    inspect = commands.add_parser("inspect", help="inspect psmux windows and active job")
    inspect.add_argument("--session", default=adapter.DEFAULT_SESSION)

    listing = commands.add_parser("list", help="list recent retained jobs without changing lock state")
    listing.add_argument("--session", default=adapter.DEFAULT_SESSION)
    listing.add_argument("--limit", type=int, default=20, help="maximum jobs to show (1..100)")

    recover = commands.add_parser("recover", help="recover one known psmux job by job id")
    recover.add_argument("job_id")

    terminal = commands.add_parser("terminal", help="inspect optional terminal runtime providers without dispatching work")
    terminal_commands = terminal.add_subparsers(dest="terminal_action", required=True)
    for terminal_action in ("probe", "list"):
        terminal_command = terminal_commands.add_parser(
            terminal_action,
            help="probe provider capabilities" if terminal_action == "probe" else "list existing terminal sessions read-only",
        )
        terminal_command.add_argument("--provider", choices=("psmux", "herdr-core"), default="psmux")
        terminal_command.add_argument("--session", default=adapter.DEFAULT_SESSION, help="psmux session (ignored by Herdr Core)")
        if terminal_action == "list":
            terminal_command.add_argument("--limit", type=int, default=20, choices=range(1, 101))

    commander = commands.add_parser("commander", help="inspect and manage approval-gated Commander Tickets")
    commander_commands = commander.add_subparsers(dest="commander_action", required=True)

    commander_list = commander_commands.add_parser("list", help="show payload-free TaskQueue status")
    commander_list.add_argument("--database", default=str(chatgpt_bridge.DEFAULT_DATABASE))
    commander_list.add_argument("--limit", type=int, default=20, choices=range(1, 201))

    commander_status = commander_commands.add_parser("status", help="inspect one Ticket without exposing its payload")
    commander_status.add_argument("--task-id", required=True)
    commander_status.add_argument("--database", default=str(chatgpt_bridge.DEFAULT_DATABASE))
    commander_status.add_argument("--events-limit", type=int, default=20)

    commander_submit = commander_commands.add_parser("submit", help="create an approval-gated Commander Ticket")
    commander_submit.add_argument("--request", required=True)
    commander_submit.add_argument("--request-id", required=True, help="stable idempotency key; required to prevent duplicate submissions")
    commander_submit.add_argument("--title")
    commander_submit.add_argument("--database", default=str(chatgpt_bridge.DEFAULT_DATABASE))
    commander_submit.add_argument("--priority", type=int, default=50, choices=range(0, 101))
    commander_submit.add_argument("--estimated-seconds", type=int, default=900)
    commander_submit.add_argument("--base-url", default=chatgpt_bridge.DEFAULT_LMSTUDIO_URL)
    commander_submit.add_argument("--model", default=None)
    commander_submit.add_argument("--timeout", type=int, default=20, choices=range(1, 31))

    commander_approve = commander_commands.add_parser("approve", help="record human approval; does not dispatch")
    commander_approve.add_argument("--task-id", required=True)
    commander_approve.add_argument("--approved-by", required=True)
    commander_approve.add_argument("--database", default=str(chatgpt_bridge.DEFAULT_DATABASE))
    return parser


def _process_exit_code(result: dict[str, Any]) -> int:
    status = result.get("status")
    if status == "outcome_unknown":
        return 3
    if status != "completed":
        return 2
    code = result.get("exit_code")
    return code if isinstance(code, int) and not isinstance(code, bool) else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "run":
        request: dict[str, Any] = {
            "action": "execute",
            "expected_cwd": args.cwd,
            "session": args.session,
            "timeout_secs": args.timeout,
        }
        if args.script_path is not None:
            request["script_path"] = args.script_path
        else:
            request["script"] = args.script
        if args.language is not None:
            request["language"] = args.language

        result = adapter.execute_request(request, project_root=str(PROJECT_ROOT))
    elif args.action == "inspect":
        result = adapter.inspect_session({"session": args.session})
    elif args.action == "list":
        result = adapter.list_jobs({"session": args.session, "limit": args.limit})
    elif args.action == "recover":
        result = adapter.recover_job(args.job_id)
    elif args.action == "terminal":
        if args.provider == "herdr-core":
            provider = HerdrCoreProvider()
        else:
            provider = PsmuxProvider(
                inspect_session=adapter.inspect_session,
                list_jobs=adapter.list_jobs,
                session=args.session,
                job_limit=getattr(args, "limit", 20),
            )
        observation = provider.probe() if args.terminal_action == "probe" else provider.list_sessions()
        result = asdict(observation)
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0 if observation.available else 2
    elif args.action == "commander":
        try:
            if args.commander_action == "list":
                return chatgpt_bridge.command_task_list(args)
            if args.commander_action == "status":
                return chatgpt_bridge.command_task_status(args)
            if args.commander_action == "submit":
                return chatgpt_bridge.command_submit(args)
            return chatgpt_bridge.command_approve(args)
        except (OSError, ValueError, RuntimeError, PermissionError) as exc:
            print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]}, ensure_ascii=True))
            return 2

    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return _process_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
