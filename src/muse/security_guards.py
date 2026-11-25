"""B398 WS9 security guards shared by Omega modules.

* :func:`validate_outbound_url` + :class:`PinnedResolver` — SSRF and DNS-rebinding defence: http(s)
  only, no credentials, port allow-list, every resolved address must be public (no loopback,
  RFC1918, link-local/metadata, CGNAT, multicast, reserved, unspecified, IPv4-mapped/NAT64
  private, 6to4/Teredo wrapping private), and the connect-time resolution must be a subset of the
  pinned first resolution.
* :func:`safe_join` / :func:`safe_extract_zip` — path traversal and Zip-Slip defence (absolute
  paths, drive letters, UNC, ``..``, NUL, symlinks, reparse via resolve, bombs).
* :class:`LeaseFence` — SQLite compare-and-swap lease with a monotonically increasing fencing token;
  writes carrying a stale token are rejected even after a zombie worker wakes up.
* :func:`scan_untrusted_text` / :func:`wrap_untrusted` — prompt-injection heuristics and an
  authority-free envelope for model context.
"""
from __future__ import annotations

import ipaddress
import re
import sqlite3
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class SecurityViolation(ValueError):
    pass


# ----------------------------------------------------------------------------- SSRF / DNS rebinding
def is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return is_public_address(str(address.ipv4_mapped))
        if address in _NAT64:
            return is_public_address(str(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)))
        if address.sixtofour is not None:
            return is_public_address(str(address.sixtofour))
        if address.teredo is not None:
            return all(is_public_address(str(part)) for part in address.teredo)
    if address in _CGNAT:
        return False
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_reserved or address.is_unspecified or not address.is_global)


def validate_outbound_url(url: str) -> dict[str, Any]:
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise SecurityViolation("url is malformed")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise SecurityViolation("only http(s) URLs are allowed")
    if parts.username or parts.password or "@" in parts.netloc:
        raise SecurityViolation("credentials in URLs are refused")
    host = (parts.hostname or "").rstrip(".").casefold()
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal", ".lan")):
        raise SecurityViolation("local host names are refused")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise SecurityViolation("port is invalid") from exc
    if port not in ALLOWED_PORTS:
        raise SecurityViolation("port is not allowed")
    if re.fullmatch(r"[0-9.]+|0x[0-9a-f.x]+|\d+", host) and not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host):
        raise SecurityViolation("obfuscated numeric host refused")
    literal = host.strip("[]")
    try:
        ipaddress.ip_address(literal)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal and not is_public_address(literal):
        raise SecurityViolation("literal private/reserved address refused")
    return {"scheme": parts.scheme, "host": host, "port": port}


class PinnedResolver:
    """Resolve once, require every address public, then require connect-time answers ⊆ pinned set."""

    def __init__(self, resolver: Callable[[str], Iterable[str]]) -> None:
        self.resolver = resolver
        self.pins: dict[str, frozenset[str]] = {}

    def pin(self, url: str) -> frozenset[str]:
        host = validate_outbound_url(url)["host"]
        addresses = frozenset(self.resolver(host))
        if not addresses:
            raise SecurityViolation("host did not resolve")
        if not all(is_public_address(a) for a in addresses):
            raise SecurityViolation("host resolves to a non-public address")
        self.pins[host] = addresses
        return addresses

    def check_connect(self, url: str) -> str:
        host = validate_outbound_url(url)["host"]
        if host not in self.pins:
            raise SecurityViolation("host was not pinned before connecting")
        current = frozenset(self.resolver(host))
        if not current or not current <= self.pins[host]:
            raise SecurityViolation("DNS rebinding detected: connect-time address differs from pinned address")
        return sorted(current)[0]


# ----------------------------------------------------------------------------- traversal / zip-slip
def safe_join(root: str | Path, user_path: str) -> Path:
    if not isinstance(user_path, str) or not user_path or "\x00" in user_path or len(user_path) > 400:
        raise SecurityViolation("path is invalid")
    text = unicodedata.normalize("NFKC", user_path).replace("\\", "/")
    if text.startswith("/") or text.startswith("//") or re.match(r"^[A-Za-z]:", text) or "::" in text:
        raise SecurityViolation("absolute, UNC or drive paths are refused")
    if any(part in {"", ".", ".."} for part in text.split("/")):
        raise SecurityViolation("path traversal refused")
    base = Path(root).resolve(strict=True)
    cursor = base
    for part in PurePosixPath(text).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise SecurityViolation("symlinked path component refused")
    resolved = cursor.resolve(strict=False)
    if resolved != base and base not in resolved.parents:
        raise SecurityViolation("path escapes the root")
    return resolved


def safe_extract_zip(archive_path: str | Path, destination: str | Path, *, max_total: int = 64 * 1024 * 1024,
                     max_members: int = 1000, max_ratio: int = 100) -> list[str]:
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > max_members:
            raise SecurityViolation("too many archive members")
        total = 0
        plan = []
        for info in infos:
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise SecurityViolation("archive symlink refused")
            path = safe_join(target, info.filename.rstrip("/")) if not info.is_dir() else None
            total += info.file_size
            if total > max_total or (info.compress_size and info.file_size / info.compress_size > max_ratio):
                raise SecurityViolation("archive exceeds size/ratio limits")
            if path is not None:
                plan.append((info, path))
        for info, path in plan:   # validate everything before writing anything
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(path, "wb") as sink:
                sink.write(source.read(max_total + 1))
            written.append(path.relative_to(target.resolve()).as_posix())
    return written


# ----------------------------------------------------------------------------- lease fencing
class LeaseFence:
    def __init__(self, database: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.db = sqlite3.connect(Path(database), timeout=10, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS lease_fence(resource TEXT PRIMARY KEY, owner TEXT, token INTEGER NOT NULL,
                expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS fenced_writes(id INTEGER PRIMARY KEY AUTOINCREMENT, resource TEXT NOT NULL,
                token INTEGER NOT NULL, owner TEXT NOT NULL, value TEXT NOT NULL);
        """)

    def close(self) -> None:
        self.db.close()

    def acquire(self, resource: str, owner: str, *, ttl: float = 30.0) -> int | None:
        now = self.clock()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT owner, token, expires_at FROM lease_fence WHERE resource=?", (resource,)).fetchone()
            if row is not None and row[0] is not None and row[2] > now and row[0] != owner:
                self.db.execute("ROLLBACK")
                return None
            token = (row[1] if row else 0) + 1
            self.db.execute("INSERT INTO lease_fence(resource,owner,token,expires_at) VALUES(?,?,?,?) ON CONFLICT(resource)"
                            " DO UPDATE SET owner=excluded.owner, token=excluded.token, expires_at=excluded.expires_at",
                            (resource, owner, token, now + ttl))
            self.db.execute("COMMIT")
            return token
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def write(self, resource: str, owner: str, token: int, value: str) -> bool:
        """Atomic fenced write: succeeds only while ``token`` is the current, unexpired lease token."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT owner, token, expires_at FROM lease_fence WHERE resource=?", (resource,)).fetchone()
            if row is None or row[0] != owner or row[1] != token or row[2] <= self.clock():
                self.db.execute("ROLLBACK")
                return False
            self.db.execute("INSERT INTO fenced_writes(resource,token,owner,value) VALUES(?,?,?,?)", (resource, token, owner, value))
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def writes(self, resource: str) -> list[tuple[int, str, str]]:
        return [tuple(r) for r in self.db.execute("SELECT token, owner, value FROM fenced_writes WHERE resource=? ORDER BY id",
                                                  (resource,))]


# ----------------------------------------------------------------------------- prompt injection
INJECTION_PATTERNS = (
    ("override", re.compile(r"(?i)\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules?|prompts?)")),
    ("override_zh", re.compile(r"(忽略|无视|忘记).{0,10}(之前|以上|所有|全部).{0,6}(指令|指示|规则|提示)")),
    ("role_hijack", re.compile(r"(?i)(^|\n)\s*(system|assistant|developer)\s*[:：]|<\|im_start\|>|\[\[?system\]?\]")),
    ("tool_call", re.compile(r"(?i)\"(tool|function)_?(call|name)\"\s*:|<tool_call>|\bcall the (\w+ )?tool\b")),
    ("exfiltration", re.compile(r"(?i)(send|post|upload|reveal|print).{0,30}(api[_ -]?key|token|password|secret|\.env|system prompt)")),
    ("approval_forgery", re.compile(r"(?i)(you are|this is) (now )?(approved|authori[sz]ed)|已(获|经)?(批准|授权)")),
)
_HIDDEN = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\U000e0000-\U000e007f]")


def scan_untrusted_text(text: str) -> dict[str, Any]:
    text = text if isinstance(text, str) else ""
    findings = [name for name, pattern in INJECTION_PATTERNS if pattern.search(unicodedata.normalize("NFKC", text))]
    hidden = len(_HIDDEN.findall(text))
    if hidden:
        findings.append("hidden_unicode")
    return {"suspicious": bool(findings), "findings": findings, "hidden_characters": hidden}


def wrap_untrusted(text: str, *, source: str) -> dict[str, Any]:
    cleaned = _HIDDEN.sub("", text if isinstance(text, str) else "")[:20000]
    return {"role": "data", "instruction_authority": "none", "source": source[:200],
            "content": cleaned, "scan": scan_untrusted_text(text)}
