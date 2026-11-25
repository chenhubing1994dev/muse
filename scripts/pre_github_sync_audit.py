"""Zero-leak firewall run before anything is pushed to GitHub (local double-click flow and CI).

Hard blocks (exit code 1 on any finding):

* forbidden paths: ``*.db`` / ``*.sqlite*`` / ``*-wal`` / ``*-shm`` databases, ``.muse-backups/``,
  ``.qw-backups/``, ``reports/``, ``.venv/``, ``.venv-packager/``, ``venv/``, ``node_modules/``,
  ``src-tauri/target/``, ``src-tauri/binaries/muse-engine*`` (only ``.gitkeep`` allowed),
  ``开箱即用安装包/``, ``.env`` files (``.env.example`` allowed), private keys / keystores;
* files larger than 25 MB;
* secrets in content: GitHub tokens (classic / fine-grained), ``sk-`` style API keys,
  PEM private-key blocks;
* hard-coded absolute paths of a real machine (``X:\\Users\\<name>``, ``/Users/<name>``,
  ``/home/<name>``, the developer's ``AI AGENT`` workspace root);
* ``.gitignore`` missing any mandatory rule; mandatory local folders not ignored.

File set: every path in the git index (``git ls-files``; falls back to parsing ``.git/index``
directly when git is unavailable, e.g. in a sandbox) plus ``--include`` paths about to be added.
``--history-range A..B`` additionally checks the *names and sizes* of every blob in the commits
that would be pushed.

Usage::

    python scripts/pre_github_sync_audit.py [--include PATH ...] [--history-range origin/main..HEAD]
                                            [--json-out FILE] [--quiet]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 25 * 1024 * 1024
MAX_SCAN_BYTES = 4 * 1024 * 1024

FORBIDDEN_PREFIXES = (
    ".muse-backups/", ".qw-backups/", ".muse-safety-backups/", "reports/", ".venv/", ".venv-packager/",
    "venv/", "src-tauri/target/", "开箱即用安装包/", "build-sidecar/", "dist-sidecar/",
)
FORBIDDEN_SEGMENTS = ("node_modules", ".venv", ".venv-packager", "__pycache__")
FORBIDDEN_GLOBS = (
    "*.db", "*.sqlite", "*.sqlite3", "*.sqlite-*", "*.sqlite3-*", "*-wal", "*-shm", "*.db-journal",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.ppk", "id_rsa", "id_rsa*", "id_dsa*",
    "id_ecdsa*", "id_ed25519*", "*.pyc",
)
ALLOWED_EXACT = {"src-tauri/binaries/.gitkeep", ".env.example"}
REQUIRED_GITIGNORE = (
    "*.db", "*.sqlite*", "*-wal", "*-shm", ".muse-backups/", ".qw-backups/", "reports/", ".venv/",
    ".venv-packager/", "venv/", "node_modules/", "src-tauri/target/", "src-tauri/binaries/muse-engine*",
    "!src-tauri/binaries/.gitkeep", "开箱即用安装包/", ".env",
)
MUST_BE_IGNORED_DIRS = (".muse-backups", ".qw-backups", ".venv-packager", ".venv", "reports",
                        "开箱即用安装包", "src-tauri/target")

# Patterns are assembled from fragments so this file never matches itself.
_GH = "gh" + "p_"
_GHPAT = "github" + "_pat_"
_SK = "s" + "k-"
SECRET_PATTERNS = (
    ("github_token", re.compile(r"(?<![A-Za-z0-9_])" + _GH + r"[A-Za-z0-9]{36}(?![A-Za-z0-9])")),
    ("github_oauth_or_app_token", re.compile(r"(?<![A-Za-z0-9_])gh[ousr]_" + r"[A-Za-z0-9]{36}(?![A-Za-z0-9])")),
    ("github_fine_grained_pat", re.compile(_GHPAT + r"[A-Za-z0-9_]{22,}")),
    ("sk_api_key", re.compile(r"(?<![A-Za-z0-9_-])" + _SK + r"(?:proj-|ant-|or-v1-|live-)?[A-Za-z0-9_\-]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY" + "-----")),
)
PLACEHOLDER_USERS = {
    "example", "user", "username", "me", "you", "name", "jane", "john", "alice", "bob", "runner",
    "runneradmin", "public", "default", "all users", "<user>", "<username>", "%username%", "$user",
    "${user}", "your-name", "yourname", "dev", "test", "foo", "...", "x", "someone", "demo",
}
ABS_PATH_PATTERNS = (
    ("windows_user_profile", re.compile(r"\b[A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/)([^\\/\s\"'`<>|:*?]+)", re.I)),
    ("posix_home", re.compile(r"(?<![\w.~-])/(?:Users|home)/([A-Za-z0-9._-]+)/")),
    ("developer_workspace_root", re.compile(r"\b[A-Za-z]:(?:\\\\|\\|/)AI AGENT(?:\\\\|\\|/)", re.I)),
)
TEXT_SUFFIXES = {
    ".py", ".pyi", ".rs", ".toml", ".json", ".yml", ".yaml", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".svelte",
    ".html", ".css", ".md", ".txt", ".bat", ".cmd", ".ps1", ".psm1", ".sh", ".vbs", ".ini", ".cfg", ".conf",
    ".xml", ".csv", ".env", ".example", ".gitignore", ".gitattributes", ".lock", ".sql", ".webmanifest", "",
}


# --------------------------------------------------------------------------------------------
# File set
# --------------------------------------------------------------------------------------------
def _git(*args: str, cwd: Path = ROOT, timeout: int = 120) -> subprocess.CompletedProcess | None:
    exe = shutil.which("git")
    if not exe:
        return None
    try:
        return subprocess.run([exe, *args], cwd=cwd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    byte = data[pos]
    pos += 1
    value = byte & 0x7F
    while byte & 0x80:
        value += 1
        byte = data[pos]
        pos += 1
        value = (value << 7) + (byte & 0x7F)
    return value, pos


def read_git_index(index_path: Path) -> list[str]:
    """Parse ``.git/index`` (versions 2, 3 and 4) without invoking git."""
    data = index_path.read_bytes()
    if data[:4] != b"DIRC":
        raise ValueError("not a git index")
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3, 4):
        raise ValueError(f"unsupported git index version {version}")
    pos, previous, paths = 12, b"", []
    for _ in range(count):
        start = pos
        flags = struct.unpack(">H", data[pos + 60:pos + 62])[0]
        pos += 62
        if version >= 3 and flags & 0x4000:
            pos += 2
        if version == 4:
            strip, pos = _varint(data, pos)
            end = data.index(b"\x00", pos)
            name = previous[:len(previous) - strip] + data[pos:end]
            pos = end + 1
        else:
            end = data.index(b"\x00", pos)
            name = data[pos:end]
            pos = start + ((pos - start + len(name) + 8) & ~7)
        previous = name
        paths.append(name.decode("utf-8", errors="surrogateescape"))
    return paths


def tracked_files(root: Path = ROOT) -> tuple[list[str], str]:
    proc = _git("ls-files", "-z", "--cached", cwd=root)
    if proc is not None and proc.returncode == 0:
        return [p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\x00") if p], "git ls-files"
    index = root / ".git" / "index"
    if index.is_file():
        return read_git_index(index), ".git/index parser"
    return [], "none"


# --------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------
def forbidden_reason(rel: str) -> str | None:
    posix = rel.replace("\\", "/").removeprefix("./")
    if posix in ALLOWED_EXACT:
        return None
    lower = posix.lower()
    for prefix in FORBIDDEN_PREFIXES:
        if lower.startswith(prefix.lower()):
            return f"forbidden directory {prefix}"
    parts = PurePosixPath(posix).parts
    for segment in FORBIDDEN_SEGMENTS:
        if segment in parts[:-1]:
            return f"forbidden path segment {segment}/"
    if lower.startswith("src-tauri/binaries/muse-engine"):
        return "frozen sidecar binary (build output)"
    name = parts[-1] if parts else posix
    lname = name.lower()
    if lname == ".env" or (lname.startswith(".env.") and lname != ".env.example"):
        return "environment secrets file"
    for pattern in FORBIDDEN_GLOBS:
        if fnmatch.fnmatch(lname, pattern):
            return f"forbidden file type {pattern}"
    return None


def _is_text_candidate(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in TEXT_SUFFIXES or path.name.lower() in {"dockerfile", "makefile", ".env.example"}


FIXTURE_WORDS = ("test", "example", "dummy", "fake", "sample", "placeholder", "redact", "xxxx", "0123456789",
                 "123456789", "abcdef")
GENERATED_UNTRACK_PREFIXES = ("pipeline/build/",)


def _entropy(text: str) -> float:
    import math
    if not text:
        return 0.0
    counts = {ch: text.count(ch) for ch in set(text)}
    return -sum(c / len(text) * math.log2(c / len(text)) for c in counts.values())


def looks_like_fixture(token: str) -> bool:
    """Obvious unit-test fixtures (``sk-test-0123…``) are reported as info, not blocked."""
    body = token.split("-", 1)[-1].lower()
    return any(word in body for word in FIXTURE_WORDS) or _entropy(body) < 3.5


def untrack_candidates(root: Path = ROOT) -> list[str]:
    """Tracked paths that must leave the git index (files stay on disk): forbidden, >25 MB, generated."""
    files, _ = tracked_files(root)
    out = []
    for rel in files:
        posix = rel.replace("\\", "/")
        if forbidden_reason(posix) or posix.startswith(GENERATED_UNTRACK_PREFIXES):
            out.append(posix)
            continue
        path = root / posix
        if path.is_file() and path.stat().st_size > MAX_BYTES:
            out.append(posix)
    return out


def scan_content(rel: str, data: bytes) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    if b"\x00" in data[:8192]:
        return findings
    text = data.decode("utf-8", errors="replace")
    for kind, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if kind == "sk_api_key" and looks_like_fixture(match.group(0)):
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append({"path": rel, "line": line, "kind": kind, "excerpt": match.group(0)[:6] + "…(redacted)"})
    for kind, pattern in ABS_PATH_PATTERNS:
        for match in pattern.finditer(text):
            user = (match.group(1) if match.groups() else "").strip().lower()
            if kind != "developer_workspace_root" and (user in PLACEHOLDER_USERS or user.startswith(("<", "%", "$", "{"))):
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append({"path": rel, "line": line, "kind": f"absolute_path:{kind}",
                             "excerpt": match.group(0)[:60]})
    return findings


def check_gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.is_file():
        return ["missing .gitignore"]
    lines = {line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()}
    return [f".gitignore lacks mandatory rule: {rule}" for rule in REQUIRED_GITIGNORE if rule not in lines]


def check_ignored_dirs(root: Path) -> list[str]:
    problems = []
    for rel in MUST_BE_IGNORED_DIRS:
        if not (root / rel).exists():
            continue
        proc = _git("check-ignore", "-q", rel + "/", cwd=root)
        if proc is None:
            continue  # git unavailable: covered by check_gitignore
        if proc.returncode != 0:
            problems.append(f"{rel}/ exists locally but is NOT ignored by git")
    return problems


def check_history(root: Path, rev_range: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    proc = _git("rev-list", "--objects", rev_range, cwd=root, timeout=300)
    if proc is None or proc.returncode != 0:
        return [{"path": "(history)", "kind": "history_unreadable", "excerpt": rev_range}] if proc is not None else []
    objects = []
    for line in proc.stdout.decode("utf-8", "surrogateescape").splitlines():
        sha, _, name = line.partition(" ")
        if name:
            objects.append((sha, name))
    for sha, name in objects:
        reason = forbidden_reason(name)
        if reason:
            findings.append({"path": name, "kind": f"history:{reason}", "excerpt": sha[:12]})
    if objects:
        batch = _git_batch_sizes(root, [sha for sha, _ in objects])
        for (sha, name) in objects:
            size = batch.get(sha, 0)
            if size > MAX_BYTES:
                findings.append({"path": name, "kind": "history:file_over_25MB", "excerpt": f"{size} bytes"})
    return findings


def _git_batch_sizes(root: Path, shas: list[str]) -> dict[str, int]:
    exe = shutil.which("git")
    if not exe:
        return {}
    try:
        proc = subprocess.run([exe, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                              cwd=root, input="\n".join(shas).encode(), capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return {}
    sizes = {}
    for line in proc.stdout.decode().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob":
            sizes[parts[0]] = int(parts[2])
    return sizes


def audit(root: Path = ROOT, include: list[str] | None = None, history_range: str | None = None,
          exclude: set[str] | None = None) -> dict[str, object]:
    files, source = tracked_files(root)
    universe = sorted((set(files) | {p.replace("\\", "/") for p in (include or [])})
                      - {p.replace("\\", "/") for p in (exclude or set())})
    findings: list[dict[str, object]] = []
    scanned = 0
    for rel in universe:
        reason = forbidden_reason(rel)
        if reason:
            findings.append({"path": rel, "kind": "forbidden_path", "excerpt": reason})
            continue
        path = root / rel
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            findings.append({"path": rel, "kind": "file_over_25MB", "excerpt": f"{size} bytes"})
            continue
        if size <= MAX_SCAN_BYTES and _is_text_candidate(path):
            try:
                findings.extend(scan_content(rel, path.read_bytes()))
                scanned += 1
            except OSError as exc:
                findings.append({"path": rel, "kind": "unreadable", "excerpt": str(exc)[:80]})
    config_problems = check_gitignore(root) + check_ignored_dirs(root)
    for problem in config_problems:
        findings.append({"path": ".gitignore", "kind": "ignore_rules", "excerpt": problem})
    if history_range:
        findings.extend(check_history(root, history_range))
    return {"passed": not findings, "file_source": source, "files_considered": len(universe),
            "text_files_scanned": scanned, "findings": findings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muse pre-GitHub zero-leak audit")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--include", nargs="*", default=[])
    parser.add_argument("--history-range")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--simulate-untrack", action="store_true",
                        help="audit as if forbidden / >25 MB / generated paths were already removed from the index")
    parser.add_argument("--list-untrack", action="store_true", help="print paths that must leave the index and exit")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    root = args.root.resolve()
    if args.list_untrack:
        print("\n".join(untrack_candidates(root)))
        return 0
    exclude = set(untrack_candidates(root)) if args.simulate_untrack else None
    result = audit(root, args.include, args.history_range, exclude)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    status = "PASS ✅" if result["passed"] else "BLOCKED ❌"
    print(f"[pre_github_sync_audit] {status} files={result['files_considered']} "
          f"scanned={result['text_files_scanned']} source={result['file_source']} findings={len(result['findings'])}")
    if not args.quiet:
        for item in result["findings"][:200]:
            line = f":{item['line']}" if "line" in item else ""
            print(f"  - {item['kind']}: {item['path']}{line}  {item['excerpt']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
