"""Build a durable, self-contained prompt for resuming Muse development."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from typing import Sequence


def _tail(path: Path, max_chars: int) -> str:
    if max_chars < 1:
        raise ValueError("max_chars must be greater than zero")
    if not path.is_file():
        return f"[File not found: {path}]"
    content = path.read_text(encoding="utf-8").strip()
    if len(content) <= max_chars:
        return content
    return "[Earlier content omitted; newest project context follows]\n" + content[-max_chars:]


def build_resume_prompt(
    progress_path: str | Path,
    architecture_path: str | Path,
    *,
    max_chars: int = 12000,
) -> str:
    """Return a prompt that rehydrates project state and defines the next safe action."""
    progress = _tail(Path(progress_path), max_chars)
    architecture = _tail(Path(architecture_path), max_chars)
    return "\n".join(
        [
            "# Muse development continuation",
            "You are resuming an existing software project. Continue from the actual workspace; do not restart the design or ask the user to repeat prior decisions.",
            "",
            "## Mandatory resume procedure",
            "1. Inspect the current project files and compare them with the project notes below. Treat source code and tests as ground truth if notes are stale.",
            "2. Check the latest test and validation state. Re-run focused tests before changing code; never assume a previous run passed.",
            "3. Select the smallest high-value unfinished engineering task. Implement it, add regression tests, and update the progress/architecture notes.",
            "4. After each meaningful change, validate it. If a command times out or has an unknown outcome, observe/recover that exact execution; never blindly launch a duplicate.",
            "5. Preserve user constraints: new software and model assets belong on D: rather than C:; do not install, delete, publish, spend money, or change OS services/scheduled tasks without the required approval.",
            "6. Keep working through safe, independently verifiable tasks in the current execution window. At the end, write a precise checkpoint: changed files, tests, failures, next task, and anything needing user approval.",
            "7. Be truthful about execution limits. A local timer or reminder does not make a ChatGPT turn run continuously; do not claim background work unless a persistent local Runner is actually running and verified.",
            "",
            "## Recent progress log (newest content)",
            progress,
            "",
            "## Architecture and implementation status",
            architecture,
            "",
            "## Immediate instruction",
            "Resume now. Inspect the live workspace, identify the next unfinished item from the architecture and progress notes, and make a real tested improvement. Do not reply with only a plan or a promise.",
            "",
        ]
    )


def write_resume_prompt(
    output_path: str | Path,
    progress_path: str | Path,
    architecture_path: str | Path,
    *,
    max_chars: int = 12000,
) -> Path:
    """Atomically write the generated continuation prompt."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_resume_prompt(progress_path, architecture_path, max_chars=max_chars)
    fd, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(prompt)
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Generate a self-contained Muse continuation prompt")
    parser.add_argument("--progress", default=str(root / "docs" / "PROGRESS.md"))
    parser.add_argument("--architecture", default=str(root / "docs" / "ARCHITECTURE.md"))
    parser.add_argument("--output", default=str(root / "data" / "handoff" / "continue_prompt.md"))
    parser.add_argument("--stdout", action="store_true", help="print the prompt instead of writing a file")
    parser.add_argument("--max-chars", type=int, default=12000)
    args = parser.parse_args(argv)
    prompt = build_resume_prompt(args.progress, args.architecture, max_chars=args.max_chars)
    if args.stdout:
        print(prompt)
    else:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(prompt)
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
