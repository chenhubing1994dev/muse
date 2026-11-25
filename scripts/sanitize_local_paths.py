"""Remove machine-specific absolute paths from tracked files before a GitHub sync (idempotent).

Every file is backed up to ``.qw-backups/sanitize-<stamp>/`` before it is rewritten.

1. Functional scripts get *portable* replacements (``$PSScriptRoot`` / ``%~dp0`` /
   ``WScript.ScriptFullName`` / ``Path(__file__)`` / ``Path.home()``), so they keep working from
   any checkout location.
2. Tracked Markdown / text documents: the workspace root becomes ``<MUSE_ROOT>``, a user
   profile becomes ``%USERPROFILE%`` and POSIX homes become ``~/``.
3. Test fixtures: drive-rooted sample paths are rewritten consistently
   (``…\\AI AGENT\\…`` → ``…\\Workspace\\…``, real user names → ``example``).

Usage: ``python scripts/sanitize_local_paths.py [--dry-run]``
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = r"(?:\\\\|\\|/)"
ROOT_RE = re.compile(r"\b[A-Za-z]:" + SEP + r"AI AGENT" + SEP + r"Muse", re.I)
AGENT_RE = re.compile(r"\b([A-Za-z]:" + SEP + r")AI AGENT", re.I)
PROFILE_RE = re.compile(r"\b[A-Za-z]:" + SEP + r"Users" + SEP + r"(?:Administrator|ADMINI~1)", re.I)
TEST_PROFILE_RE = re.compile(r"(\b[A-Za-z]:" + SEP + r"Users" + SEP + r")(?:Administrator|ADMINI~1)", re.I)
POSIX_HOME_RE = re.compile(r"(?<![\w.~-])/(?:Users|home)/([A-Za-z0-9._-]+)/")
POSIX_PLACEHOLDERS = {"example", "user", "username", "me", "you", "name", "jane", "john", "runner", "runneradmin",
                      "dev", "test", "demo", "someone", "x"}
DOC_SUFFIXES = {".md", ".txt"}
SKIP_PREFIXES = ("reports/", ".qw-backups/", ".muse-", "external/", "third_party/", "node_modules/", "pipeline/build/")

# (relative path, old text, new text) — exact, portable replacements for functional files
_RAW_EDITS: tuple[tuple[str, str, str], ...] = (
    ("scripts/Install-Muse-GitHubBackupTask.ps1", "$RepoRoot = '@WS@'",
     "$RepoRoot = Split-Path -Parent $PSScriptRoot"),
    ("scripts/Muse-GitHubSync.ps1", "$RepoRoot = '@WS@'", "$RepoRoot = Split-Path -Parent $PSScriptRoot"),
    ("scripts/Muse-GitHubWatch.ps1", "$RepoRoot = '@WS@'", "$RepoRoot = Split-Path -Parent $PSScriptRoot"),
    ("scripts/download_mingli_release.ps1", '[string]$Destination = "@WS@\\models\\mingli-ai-v0.1.0-assets",',
     '[string]$Destination = (Join-Path (Split-Path -Parent $PSScriptRoot) "models\\mingli-ai-v0.1.0-assets"),'),
    ("scripts/setup_mingli_converter_env.ps1",
     '[string]$EnvironmentPath = "@WS@\\third_party\\llama.cpp-converter-review\\.venv"',
     '[string]$EnvironmentPath = (Join-Path (Split-Path -Parent $PSScriptRoot) "third_party\\llama.cpp-converter-review\\.venv")'),
    ("scripts/muse-github-watch.cmd", 'cd /d "@WS@"', 'cd /d "%~dp0.."'),
    ("scripts/muse-github-watch.cmd", '-File "@WS@\\scripts\\Muse-GitHubWatch.ps1"',
     '-File "%~dp0Muse-GitHubWatch.ps1"'),
    ("scripts/muse-supervisor.cmd", 'cd /d "@WS@"', 'cd /d "%~dp0.."'),
    ("scripts/muse-supervisor.cmd",
     '"@WS@\\.venv\\Scripts\\python.exe" -m muse.model_supervisor --workspace "@WS@"',
     '".venv\\Scripts\\python.exe" -m muse.model_supervisor --workspace "%CD%"'),
    ("scripts/muse-watchdog-hidden.vbs", 'Set sh = CreateObject("WScript.Shell")\r\nsh.CurrentDirectory = "@WS@"',
     'Set sh = CreateObject("WScript.Shell")\r\nsh.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName('
     'CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName))'),
    ("scripts/muse-watchdog-hidden.vbs", 'Set sh = CreateObject("WScript.Shell")\nsh.CurrentDirectory = "@WS@"',
     'Set sh = CreateObject("WScript.Shell")\nsh.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName('
     'CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName))'),
    ("scripts/muse-watchdog-hidden.vbs", 'sh.Run "cmd.exe /c ""@WS@\\scripts\\muse-watchdog.cmd""", 0, True',
     'sh.Run "cmd.exe /c """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) '
     '& "\\muse-watchdog.cmd""", 0, True'),
    ("scripts/muse-supervisor-hidden.vbs", 'sh.Run "cmd.exe /c ""@WS@\\scripts\\muse-supervisor.cmd""", 0, True',
     'sh.Run "cmd.exe /c """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) '
     '& "\\muse-supervisor.cmd""", 0, True'),
    ("scripts/muse-github-watch-hidden.vbs", 'sh.Run "cmd.exe /c ""@WS@\\scripts\\muse-github-watch.cmd""", 0, True',
     'sh.Run "cmd.exe /c """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) '
     '& "\\muse-github-watch.cmd""", 0, True'),
    ("ember-fire/build.ps1", '$Lib = "@ADM@\\lemo-opuscar"',
     '$Lib = if ($env:MUSE_LEMO_ROOT) { $env:MUSE_LEMO_ROOT } else { Join-Path $env:USERPROFILE "lemo-opuscar" }'),
    ("scripts/muse-inspiration-flagship-video.py",
     'LEMO_PYTHON = Path(r"@ADM@\\lemo-opuscar\\.venv\\Scripts\\python.exe")',
     'LEMO_PYTHON = Path(os.environ.get("MUSE_LEMO_PYTHON") or (Path.home() / "lemo-opuscar" / ".venv" / "Scripts" / "python.exe"))'),
    ("scripts/muse-inspiration-flagship-video.py",
     'PY314_EXEC = Path(r"@ADM@\\AppData\\Local\\Programs\\Python\\Python314\\python.exe")',
     'PY314_EXEC = Path(os.environ.get("MUSE_PY314_EXEC") or (Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))'
     ' / "Programs" / "Python" / "Python314" / "python.exe"))'),
    ("scripts/webcodex_psmux_exec.py", 'DEFAULT_PROJECT_ROOT = r"@WS@"',
     'DEFAULT_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])'),
    ("tests/test_webcodex_psmux_exec.py", 'self.assertIn("@WSP@/src", wrapper)',
     'self.assertIn((Path(adapter.DEFAULT_PROJECT_ROOT) / "src").as_posix(), wrapper)'),
    ("src/muse/inspiration_evolution.py",
     '"test_command": "@WSE@\\\\.venv\\\\Scripts\\\\python.exe -m unittest tests.test_inspiration_evolution tests.test_autonomous_research",',
     '"test_command": "python -m unittest tests.test_inspiration_evolution tests.test_autonomous_research",'),
    ("scripts/audit_and_repair_inspiration_refs.py", "from @WSE2@", "from the Muse project root"),
    ("scripts/audit_and_repair_inspiration_refs.py", "from @WS@", "from the Muse project root"),
    ("scripts/Repair-And-Verify.ps1", '"@WS@\\scripts\\Repair-And-Verify.ps1"', '".\\scripts\\Repair-And-Verify.ps1"'),
    ("scripts/Repair-And-Verify.ps1", "@ADM@\\...\\Python311", "%LOCALAPPDATA%\\...\\Python311"),
)


# Machine-specific fragments are assembled at runtime so this file never contains them literally.
_TOKENS = {
    "@WSE@": "d:" + "\\\\" + "AI AGENT" + "\\\\" + "Muse",
    "@WSE2@": "D:" + "\\\\" + "AI AGENT" + "\\\\" + "Muse",
    "@WS@": "D:" + "\\" + "AI AGENT" + "\\" + "Muse",
    "@WSP@": "D:" + "/" + "AI AGENT" + "/" + "Muse",
    "@ADM@": "C:" + "\\" + "Users" + "\\" + "Administrator",
}


def _expand(text: str) -> str:
    for token, value in _TOKENS.items():
        text = text.replace(token, value)
    return text


FUNCTIONAL_EDITS: tuple[tuple[str, str, str], ...] = tuple(
    (rel, _expand(old), _expand(new)) for rel, old, new in _RAW_EDITS
)


def _posix_home(match: re.Match[str]) -> str:
    return match.group(0) if match.group(1).lower() in POSIX_PLACEHOLDERS else "~/"


def sanitize_doc(text: str) -> str:
    text = ROOT_RE.sub("<MUSE_ROOT>", text)
    text = AGENT_RE.sub("<AI_AGENT_ROOT>", text)
    text = PROFILE_RE.sub("%USERPROFILE%", text)
    return POSIX_HOME_RE.sub(_posix_home, text)


def sanitize_test(text: str) -> str:
    text = AGENT_RE.sub(lambda m: m.group(1) + "Workspace", text)
    text = text.replace("/mnt/d/AI AGENT/", "/mnt/d/Workspace/")
    return TEST_PROFILE_RE.sub(lambda m: m.group(1) + "example", text)


def _tracked(root: Path) -> list[str]:
    sys.dont_write_bytecode = True
    audit_path = root / "scripts" / "pre_github_sync_audit.py"
    namespace: dict[str, object] = {"__name__": "pre_github_sync_audit", "__file__": str(audit_path)}
    exec(compile(audit_path.read_text(encoding="utf-8"), str(audit_path), "exec"), namespace)  # noqa: S102
    files, _ = namespace["tracked_files"](root)  # type: ignore[operator]
    return files


def run(root: Path = ROOT, *, dry_run: bool = False) -> list[str]:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root / ".qw-backups" / f"sanitize-{stamp}"
    changed: dict[str, str] = {}

    def load(rel: str) -> str | None:
        if rel in changed:
            return changed[rel]
        path = root / rel
        if not path.is_file():
            return None
        return path.read_bytes().decode("utf-8")

    for rel, old, new in FUNCTIONAL_EDITS:
        text = load(rel)
        if text is not None and old in text:
            changed[rel] = text.replace(old, new)
    for rel in _tracked(root):
        posix = rel.replace("\\", "/")
        if posix.startswith(SKIP_PREFIXES):
            continue
        suffix = Path(posix).suffix.lower()
        if suffix in DOC_SUFFIXES:
            transform = sanitize_doc
        elif posix.startswith("tests/") and suffix == ".py":
            transform = sanitize_test
        else:
            continue
        try:
            text = load(posix)
        except UnicodeDecodeError:
            continue
        if text is None:
            continue
        updated = transform(text)
        if updated != text:
            changed[posix] = updated
    written = []
    for rel, text in sorted(changed.items()):
        path = root / rel
        original = path.read_bytes()
        if original.decode("utf-8") == text:
            continue
        written.append(rel)
        if dry_run:
            continue
        target = backup_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        path.write_bytes(text.encode("utf-8"))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    written = run(dry_run=args.dry_run)
    print(f"[sanitize_local_paths] {'would rewrite' if args.dry_run else 'rewrote'} {len(written)} file(s)")
    for rel in written:
        print("  -", rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
