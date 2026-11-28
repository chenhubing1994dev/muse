"""HTTPS-only server web portal with a constrained browser API gateway.

The portal owns browser authentication. Its upstream desktop API remains loopback
and its Bearer token never reaches the browser. Browser reads and low-risk inspiration
writes are allowlisted; plugin intake accepts declarative manifests only and never
executes plugin code.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import ssl
import threading
import time
from http.cookies import SimpleCookie
from html import unescape

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from muse.capabilities import CapabilityRegistry
from muse.plugin_platform import (
    PluginError, PluginRegistry, commander_task_queue_adapter, sync_plugin_capabilities, validate_manifest,
)
from muse.status_api import create_status_server
from muse.task_queue import TaskQueue
from muse.error_journal import record_suppressed_error


SESSION_COOKIE = "MuseWebSession"
SESSION_TTL_SECONDS = 8 * 60 * 60
MAX_LOGIN_BODY = 4096
MAX_PLUGIN_DISCOVERY_BODY = 70 * 1024
MAX_INSPIRATION_BODY = 8192
MAX_API_RESPONSE = 1024 * 1024
MAX_PRODUCT_RESPONSE = 20 * 1024 * 1024

UPSTREAM_READ_ROUTES = {
    "overview": "/api/v1/overview",
    "agents": "/api/v1/agents?limit=200",
    "inspirations": "/api/v1/inspirations?limit=20",
    "knowledge": "/api/v1/knowledge/graph?limit=80",
    "products": "/api/v1/products?limit=50",

    "system": "/api/v1/system-dashboard",
}
UPSTREAM_WRITE_ROUTES = {
    "inspirations": "/api/v1/inspirations",
    "inspiration-evolve": "/api/v1/inspirations/evolve",
    "daemon-start": "/api/v1/daemon/start",
    "daemon-pause": "/api/v1/daemon/pause",
    "daemon-resume": "/api/v1/daemon/resume",
    "daemon-stop": "/api/v1/daemon/stop",
}


class PortalError(ValueError):
    pass



class PortalUpstreamError(PortalError):
    pass


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PortalError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise PortalError(f"non-finite JSON number is not allowed: {value}")


def validate_public_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PortalError("web portal public origin is malformed") from exc
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise PortalError("web portal requires one exact HTTPS public origin")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".lan", ".home", ".internal")):
        raise PortalError("public origin must not use a local-only hostname")
    try:
        import ipaddress
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise PortalError("public origin must not use a private or non-routable IP address")
    host_part = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != 443:
        host_part += f":{port}"
    return f"https://{host_part}"


def parse_trusted_proxy_cidrs(values: Any) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse an explicit, bounded allowlist of proxy peer networks."""
    if not isinstance(values, (list, tuple)) or len(values) > 16:
        raise PortalError("trusted proxy CIDRs must be a list of at most 16 networks")
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 64:
            raise PortalError("each trusted proxy CIDR must be non-empty text of at most 64 characters")
        try:
            network = ipaddress.ip_network(value.strip(), strict=True)
        except ValueError as exc:
            raise PortalError("trusted proxy CIDR is invalid or has host bits set") from exc
        minimum_prefix = 16 if network.version == 4 else 48
        if network.prefixlen < minimum_prefix:
            raise PortalError("trusted proxy CIDR is too broad; use a /16-or-narrower IPv4 or /48-or-narrower IPv6 network")
        if network not in networks:
            networks.append(network)
    return tuple(networks)


def resolve_client_address(
    remote_address: str, forwarded_for_values: list[str],
    trusted_proxy_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> str:
    """Use one X-Forwarded-For IP only when the direct peer is an allowlisted proxy.

    The proxy must overwrite X-Forwarded-For with its observed client address.
    Duplicate headers, comma-separated chains, malformed IPs and untrusted peers
    fall back to the direct TCP peer, preserving a safe shared rate-limit bucket.
    """
    try:
        remote = ipaddress.ip_address(remote_address)
    except (TypeError, ValueError):
        return "unknown"
    if not any(remote in network for network in trusted_proxy_networks):
        return remote.compressed
    if not isinstance(forwarded_for_values, list) or len(forwarded_for_values) != 1:
        return remote.compressed
    raw_candidate = forwarded_for_values[0]
    if not isinstance(raw_candidate, str):
        return remote.compressed
    candidate = raw_candidate.strip()
    if not candidate or "," in candidate or "%" in candidate:
        return remote.compressed
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        return remote.compressed


def build_portal_tls_context(certfile: str | Path, keyfile: str | Path) -> ssl.SSLContext:
    cert = Path(certfile).expanduser()
    key = Path(keyfile).expanduser()
    if not cert.is_file() or cert.is_symlink() or not key.is_file() or key.is_symlink():
        raise PortalError("web portal TLS certificate and key must be existing regular files")
    if cert.resolve() == key.resolve():
        raise PortalError("web portal TLS certificate and key must be separate files")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if hasattr(ssl.TLSVersion, "TLSv1_3"):
        context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.options |= getattr(ssl, "OP_NO_COMPRESSION", 0)
    context.set_alpn_protocols(["http/1.1"])
    try:
        context.load_cert_chain(str(cert), str(key))
    except (OSError, ssl.SSLError) as exc:
        raise PortalError("web portal TLS certificate/key could not be loaded") from exc
    return context


def safe_static_file(root: str | Path, request_path: str) -> Path | None:
    """Resolve only regular files contained by the configured static root."""
    base = Path(root).resolve()
    if not request_path.startswith("/") or "\\" in request_path or "\x00" in request_path:
        return None
    relative = request_path.lstrip("/") or "index.html"
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        return None
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if not candidate.is_file() or candidate.is_symlink():
        return None
    return candidate


class PortalSessionStore:
    def __init__(self, database: str | Path, password: str) -> None:
        if not isinstance(password, str):
            raise PortalError("MUSE_WEB_PASSWORD must contain 32-1024 UTF-8 bytes")
        try:
            password_bytes = password.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise PortalError("MUSE_WEB_PASSWORD must contain valid UTF-8 text") from exc
        if not 32 <= len(password_bytes) <= 1024:
            raise PortalError("MUSE_WEB_PASSWORD must contain 32-1024 UTF-8 bytes")
        database_path = Path(database)
        if database_path.is_symlink() or (database_path.exists() and not database_path.is_file()):
            raise PortalError("web session database must be a regular non-symlink file")
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, timeout=5.0, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        try:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS web_sessions (
                    session_sha256 TEXT PRIMARY KEY,
                    csrf_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS web_login_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_address TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('login_failure','login_success','login_rate_limited','logout')),
                    attempted_at REAL NOT NULL
                )
            """)
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_web_login_events_address_time "
                "ON web_login_events(client_address, attempted_at DESC)"
            )
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS web_admin_credentials (
                    credential_id INTEGER PRIMARY KEY CHECK(credential_id=1),
                    algorithm TEXT NOT NULL CHECK(algorithm='scrypt'),
                    salt BLOB NOT NULL CHECK(length(salt)=16),
                    password_hash BLOB NOT NULL CHECK(length(password_hash)=32),
                    updated_at REAL NOT NULL
                )
            """)
            self.connection.commit()
            self.connection.execute("BEGIN IMMEDIATE")
            credential = self.connection.execute(
                "SELECT algorithm,salt,password_hash FROM web_admin_credentials WHERE credential_id=1"
            ).fetchone()
            if credential is None:
                self._salt = secrets.token_bytes(16)
                self._password_digest = hashlib.scrypt(
                    password_bytes, salt=self._salt, n=2**14, r=8, p=1, dklen=32,
                )
                self.connection.execute(
                    "INSERT INTO web_admin_credentials(credential_id,algorithm,salt,password_hash,updated_at) "
                    "VALUES(1,'scrypt',?,?,?)",
                    (self._salt, self._password_digest, time.time()),
                )
            else:
                self._salt = bytes(credential["salt"])
                self._password_digest = bytes(credential["password_hash"])
                if credential["algorithm"] != "scrypt" or len(self._salt) != 16 or len(self._password_digest) != 32:
                    raise PortalError("stored administrator credential record is invalid")
                candidate = hashlib.scrypt(
                    password_bytes, salt=self._salt, n=2**14, r=8, p=1, dklen=32,
                )
                if not hmac.compare_digest(candidate, self._password_digest):
                    raise PortalError("MUSE_WEB_PASSWORD does not match stored administrator credential")
            self.connection.execute("DELETE FROM web_sessions")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            self.connection.close()
            raise

    def login_allowed(self, address: str, *, now: float | None = None) -> bool:
        if not isinstance(address, str) or not address or len(address) > 128:
            return False
        current = time.time() if now is None else float(now)
        window_start = current - 900
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS failures FROM web_login_events "
                "WHERE client_address=? AND outcome='login_failure' AND attempted_at>? "
                "AND attempted_at>COALESCE((SELECT MAX(attempted_at) FROM web_login_events "
                "WHERE client_address=? AND outcome='login_success'), ?)",
                (address, window_start, address, window_start),
            ).fetchone()
        return bool(row and int(row["failures"]) < 5)

    def record_login_event(self, address: str, outcome: str, *, now: float | None = None) -> None:
        if not isinstance(address, str) or not address or len(address) > 128:
            address = "unknown"
        if outcome not in {"login_failure", "login_success", "login_rate_limited", "logout"}:
            raise PortalError("unsupported portal login audit event")
        current = time.time() if now is None else float(now)
        with self._lock:
            self.connection.execute(
                "DELETE FROM web_login_events WHERE attempted_at<?", (current - 7 * 86400,)
            )
            self.connection.execute(
                "INSERT INTO web_login_events(client_address,outcome,attempted_at) VALUES(?,?,?)",
                (address, outcome, current),
            )
            self.connection.execute(
                "DELETE FROM web_login_events WHERE id NOT IN "
                "(SELECT id FROM web_login_events ORDER BY attempted_at DESC,id DESC LIMIT 10000)"
            )
            self.connection.commit()

    def authenticate(self, address: str, supplied: Any, *, now: float | None = None) -> str:
        """Atomically rate-limit, verify credentials, and persist the login outcome.

        The lock spans the failure-count read, password verification, and audit write so
        concurrent request threads cannot all pass the same pre-check.
        """
        if not isinstance(address, str) or not address or len(address) > 128:
            address = "unknown"
        current = time.time() if now is None else float(now)
        window_start = current - 900
        with self._lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS failures FROM web_login_events "
                "WHERE client_address=? AND outcome='login_failure' AND attempted_at>? "
                "AND attempted_at>COALESCE((SELECT MAX(attempted_at) FROM web_login_events "
                "WHERE client_address=? AND outcome='login_success'), ?)",
                (address, window_start, address, window_start),
            ).fetchone()
            if not row or int(row["failures"]) >= 5:
                outcome, result = "login_rate_limited", "rate_limited"
            else:
                matched = False
                if isinstance(supplied, str):
                    try:
                        encoded = supplied.encode("utf-8")
                    except UnicodeEncodeError:
                        encoded = b""
                    if len(encoded) <= 1024:
                        credential = self.connection.execute(
                            "SELECT algorithm,salt,password_hash FROM web_admin_credentials WHERE credential_id=1"
                        ).fetchone()
                        if credential and credential["algorithm"] == "scrypt":
                            salt = bytes(credential["salt"])
                            expected = bytes(credential["password_hash"])
                            if len(salt) == 16 and len(expected) == 32:
                                candidate = hashlib.scrypt(encoded, salt=salt, n=2**14, r=8, p=1, dklen=32)
                                matched = hmac.compare_digest(candidate, expected)
                                if matched:
                                    self._salt, self._password_digest = salt, expected
                outcome = "login_success" if matched else "login_failure"
                result = "authenticated" if matched else "invalid"
            self.connection.execute(
                "DELETE FROM web_login_events WHERE attempted_at<?", (current - 7 * 86400,)
            )
            self.connection.execute(
                "INSERT INTO web_login_events(client_address,outcome,attempted_at) VALUES(?,?,?)",
                (address, outcome, current),
            )
            self.connection.execute(
                "DELETE FROM web_login_events WHERE id NOT IN "
                "(SELECT id FROM web_login_events ORDER BY attempted_at DESC,id DESC LIMIT 10000)"
            )
            self.connection.commit()
            return result

    def verify_password(self, supplied: str) -> bool:
        if not isinstance(supplied, str):
            return False
        try:
            encoded = supplied.encode("utf-8")
        except UnicodeEncodeError:
            return False
        if len(encoded) > 1024:
            return False
        with self._lock:
            credential = self.connection.execute(
                "SELECT algorithm,salt,password_hash FROM web_admin_credentials WHERE credential_id=1"
            ).fetchone()
            if credential is None or credential["algorithm"] != "scrypt":
                return False
            salt = bytes(credential["salt"])
            expected = bytes(credential["password_hash"])
            if len(salt) != 16 or len(expected) != 32:
                return False
            candidate = hashlib.scrypt(encoded, salt=salt, n=2**14, r=8, p=1, dklen=32)
            matched = hmac.compare_digest(candidate, expected)
            if matched:
                self._salt = salt
                self._password_digest = expected
            return matched

    def rotate_password(self, current_password: str, new_password: str) -> None:
        """Rotate the persisted administrator credential and revoke all sessions."""
        if not isinstance(current_password, str):
            raise PortalError("current administrator password is incorrect")
        if not isinstance(new_password, str):
            raise PortalError("new administrator password must contain 32-1024 UTF-8 bytes")
        try:
            current_bytes = current_password.encode("utf-8")
            new_bytes = new_password.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise PortalError("administrator passwords must contain valid UTF-8 text") from exc
        if not 32 <= len(new_bytes) <= 1024:
            raise PortalError("new administrator password must contain 32-1024 UTF-8 bytes")
        with self._lock:
            candidate = hashlib.scrypt(
                current_bytes, salt=self._salt, n=2**14, r=8, p=1, dklen=32,
            )
            if not hmac.compare_digest(candidate, self._password_digest):
                raise PortalError("current administrator password is incorrect")
            salt = secrets.token_bytes(16)
            digest = hashlib.scrypt(new_bytes, salt=salt, n=2**14, r=8, p=1, dklen=32)
            try:
                self.connection.execute(
                    "UPDATE web_admin_credentials SET salt=?,password_hash=?,updated_at=? WHERE credential_id=1",
                    (salt, digest, time.time()),
                )
                self.connection.execute("DELETE FROM web_sessions")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            self._salt = salt
            self._password_digest = digest

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self) -> tuple[str, float]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = time.time()
        expiry = now + SESSION_TTL_SECONDS
        with self._lock:
            self.connection.execute("DELETE FROM web_sessions WHERE expires_at<=?", (now,))
            self.connection.execute(
                "INSERT INTO web_sessions(session_sha256,csrf_sha256,created_at,expires_at) VALUES(?,?,?,?)",
                (self._digest(token), self._digest(csrf), now, expiry),
            )
            self.connection.commit()
        return token, expiry

    def rotate_csrf(self, token: str) -> str | None:
        if not isinstance(token, str) or len(token) > 128:
            return None
        session_digest = self._digest(token)
        csrf = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            row = self.connection.execute(
                "SELECT expires_at FROM web_sessions WHERE session_sha256=?", (session_digest,)
            ).fetchone()
            if row is None or row["expires_at"] <= now:
                self.connection.execute("DELETE FROM web_sessions WHERE session_sha256=?", (session_digest,))
                self.connection.commit()
                return None
            self.connection.execute(
                "UPDATE web_sessions SET csrf_sha256=? WHERE session_sha256=?",
                (self._digest(csrf), session_digest),
            )
            self.connection.commit()
        return csrf

    def validate(self, token: str) -> bool:
        if not isinstance(token, str) or len(token) > 128:
            return False
        now = time.time()
        with self._lock:
            row = self.connection.execute(
                "SELECT expires_at FROM web_sessions WHERE session_sha256=?", (self._digest(token),)
            ).fetchone()
            if row is None:
                return False
            if row["expires_at"] <= now:
                self.connection.execute("DELETE FROM web_sessions WHERE session_sha256=?", (self._digest(token),))
                self.connection.commit()
                return False
            return True

    def verify_csrf(self, token: str, supplied: str) -> bool:
        if not isinstance(token, str) or not isinstance(supplied, str) or len(token) > 128 or len(supplied) > 128:
            return False
        with self._lock:
            row = self.connection.execute(
                "SELECT csrf_sha256,expires_at FROM web_sessions WHERE session_sha256=?",
                (self._digest(token),),
            ).fetchone()
        return bool(row and row["expires_at"] > time.time()
                    and hmac.compare_digest(row["csrf_sha256"], self._digest(supplied)))

    def revoke(self, token: str) -> None:
        if not isinstance(token, str) or len(token) > 128:
            return
        with self._lock:
            self.connection.execute("DELETE FROM web_sessions WHERE session_sha256=?", (self._digest(token),))
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            self.connection.close()


class WebPortalServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(
        self, address: tuple[str, int], *, public_origin: str, certfile: str | Path,
        keyfile: str | Path, session_db: str | Path, password: str,
        upstream_url: str, upstream_token: str, plugin_db: str | Path,
        task_db: str | Path, capability_db: str | Path, static_dir: str | Path,
        trusted_proxy_cidrs: tuple[str, ...] = (),
    ) -> None:
        self.public_origin = validate_public_origin(public_origin)
        parsed = urlsplit(self.public_origin)
        self.expected_host = parsed.netloc.lower()
        self.trusted_proxy_networks = parse_trusted_proxy_cidrs(trusted_proxy_cidrs)
        self.tls_context = build_portal_tls_context(certfile, keyfile)
        self.sessions = PortalSessionStore(session_db, password)
        upstream = urlsplit(upstream_url)
        if upstream.scheme != "http" or upstream.hostname not in {"127.0.0.1", "localhost", "::1"} or not upstream.port:
            self.sessions.close()
            raise PortalError("web portal upstream must be a loopback HTTP status API")
        self.upstream_url = upstream_url.rstrip("/")
        self.upstream_token = upstream_token
        self.plugin_db = Path(plugin_db)
        self.task_db = Path(task_db)
        self.capability_db = Path(capability_db)
        database_paths = (self.plugin_db, self.task_db, self.capability_db)
        if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in database_paths):
            self.sessions.close()
            raise PortalError("portal plugin, task and capability databases must be regular non-symlink files")
        if len({str(path.resolve()) for path in database_paths}) != len(database_paths):
            self.sessions.close()
            raise PortalError("portal plugin, task and capability databases must be separate files")
        static_root = Path(static_dir)
        if static_root.is_symlink() or not static_root.is_dir():
            self.sessions.close()
            raise PortalError("web portal static directory must be a regular non-symlink directory")
        self.static_dir = static_root.resolve()
        self._request_slots = threading.BoundedSemaphore(8)
        super().__init__(address, PortalRequestHandler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        active_socket = request
        try:
            request.settimeout(5.0)
            secure = self.tls_context.wrap_socket(request, server_side=True)
            active_socket = secure
            secure.settimeout(10.0)
            if secure.version() not in {"TLSv1.2", "TLSv1.3"}:
                raise ssl.SSLError("unsupported portal TLS protocol")
            self.finish_request(secure, client_address)
        except (ssl.SSLError, TimeoutError, OSError):
            pass
        except Exception:
            self.handle_error(active_socket, client_address)
        finally:
            self.shutdown_request(active_socket)
            self._request_slots.release()

    def login_allowed(self, address: str) -> bool:
        return self.sessions.login_allowed(address)

    def record_login_failure(self, address: str) -> None:
        self.sessions.record_login_event(address, "login_failure")

    def record_login_success(self, address: str) -> None:
        self.sessions.record_login_event(address, "login_success")

    def record_login_rate_limited(self, address: str) -> None:
        self.sessions.record_login_event(address, "login_rate_limited")

    def local_readiness(self) -> bool:
        """Check local Portal stores without creating or mutating databases."""
        try:
            if not self.static_dir.is_dir() or not self.upstream_token:
                return False
            with self.sessions._lock:
                credential = self.sessions.connection.execute(
                    "SELECT 1 FROM web_admin_credentials WHERE credential_id=1"
                ).fetchone()
            if credential is None:
                return False
            for path in (self.plugin_db, self.task_db, self.capability_db):
                if path.is_symlink() or not path.is_file():
                    return False
                uri = path.resolve().as_uri() + "?mode=ro"
                connection = sqlite3.connect(uri, uri=True, timeout=1.0)
                try:
                    table = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
                    ).fetchone()
                    if table is None:
                        return False
                finally:
                    connection.close()
            return True
        except (OSError, sqlite3.Error):
            return False

    def close_portal(self) -> None:
        self.sessions.close()


class PortalRequestHandler(BaseHTTPRequestHandler):
    server: WebPortalServer
    protocol_version = "HTTP/1.1"
    server_version = "MuseWebPortal"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _secure(self) -> bool:
        return isinstance(self.connection, ssl.SSLSocket) and self.connection.version() in {"TLSv1.2", "TLSv1.3"}

    def _authority_ok(self, *, require_origin: bool) -> bool:
        hosts = self.headers.get_all("Host") or []
        origins = self.headers.get_all("Origin") or []
        if not self._secure() or len(hosts) != 1 or hosts[0].lower() != self.server.expected_host:
            return False
        if require_origin:
            return len(origins) == 1 and origins[0] == self.server.public_origin
        return len(origins) <= 1 and (not origins or origins[0] == self.server.public_origin)

    def _origin_ok(self) -> bool:
        return self._authority_ok(require_origin=True)

    def _client_address(self) -> str:
        remote = self.client_address[0] if self.client_address else "unknown"
        forwarded = self.headers.get_all("X-Forwarded-For") or []
        return resolve_client_address(remote, forwarded, self.server.trusted_proxy_networks)

    def _cookie_token(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception as _muse_suppressed:
            record_suppressed_error("web_portal.py:661", _muse_suppressed)
            return ""
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _session(self) -> str | None:
        token = self._cookie_token()
        return token if self.server.sessions.validate(token) else None

    def _send(self, status: int, body: bytes, content_type: str, *, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True

    def _send_product_file(
        self, body: bytes, *, action: str, initiative_id: str, preview_policy: str | None = None,
    ) -> None:
        is_preview = action == "preview"
        if is_preview and not preview_policy:
            raise PortalError("verified preview policy is required")
        content_type = "text/html; charset=utf-8" if is_preview else "application/zip"
        extension = "html" if is_preview else "zip"
        disposition = "inline" if is_preview else "attachment"
        csp = (
            "sandbox allow-scripts allow-same-origin allow-downloads allow-modals; " + preview_policy
            if is_preview else "default-src 'none'; sandbox"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Content-Disposition",
            f'{disposition}; filename="muse-product-{initiative_id[:8]}.{extension}"',
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", csp)
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True



    def _json(self, status: int, payload: dict[str, Any], extra_headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(body) > MAX_API_RESPONSE:
            body = b'{"error":"portal response exceeds the configured size limit"}'
            status = 503
        self._send(status, body, "application/json; charset=utf-8")
        # Headers must be emitted before end_headers; this helper intentionally
        # keeps responses header-minimal. Cookies are sent by _json_with_headers.
    def _json_with_headers(self, status: int, payload: dict[str, Any], headers: dict[str, str]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True
    def _read_json(self, maximum: int = MAX_LOGIN_BODY) -> dict[str, Any]:
        if self.headers.get_all("Transfer-Encoding"):
            raise PortalError("transfer-encoded request bodies are not supported")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise PortalError("application/json is required")
        length_headers = self.headers.get_all("Content-Length") or []
        if len(length_headers) != 1 or not length_headers[0].isascii() or not length_headers[0].isdigit():
            raise PortalError("exactly one decimal content length is required")
        try:
            length = int(length_headers[0])
        except ValueError as exc:
            raise PortalError("invalid content length") from exc
        if not 1 <= length <= maximum:
            raise PortalError("request body size is invalid")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise PortalError("incomplete request body")
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict):
            raise PortalError("request body must be an object")
        return payload


    def _upstream(self, route: str) -> dict[str, Any]:
        path = UPSTREAM_READ_ROUTES.get(route)
        if path is None:
            raise PortalError("read route is not allowlisted")
        request = Request(
            self.server.upstream_url + path,
            headers={"Authorization": "Bearer " + self.server.upstream_token, "Accept": "application/json"},
            method="GET",
        )
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, new_url):
                return None
        try:
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=4.0) as response:
                raw = response.read(MAX_API_RESPONSE + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise PortalError("Muse local read service is unavailable") from exc
        if len(raw) > MAX_API_RESPONSE:
            raise PortalError("Muse read response exceeds the portal limit")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or value.get("api_version") != 1:
            raise PortalError("Muse local API returned an incompatible response")
        return value

    def _upstream_product_file(
        self, initiative_id: str, version_id: str, action: str,
    ) -> tuple[int, bytes, str | None]:
        if (not re.fullmatch(r"[0-9a-f]{32}", initiative_id)
                or not re.fullmatch(r"[0-9a-f]{20}", version_id)
                or action not in {"preview", "download"}):
            raise PortalError("product file route is not allowlisted")
        path = f"/api/v1/products/{initiative_id}/{version_id}/{action}"
        expected_type = "text/html" if action == "preview" else "application/zip"
        request = Request(
            self.server.upstream_url + path,
            headers={
                "Authorization": "Bearer " + self.server.upstream_token,
                "Accept": expected_type,
            },
            method="GET",
        )

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, new_url):
                return None

        try:
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=15.0) as response:
                status = int(response.status)
                content_type = response.headers.get_content_type()
                raw = response.read(MAX_PRODUCT_RESPONSE + 1)
        except HTTPError as exc:
            if exc.code in {404, 409, 413}:
                return int(exc.code), b"", None
            raise PortalUpstreamError("Muse local product service is unavailable") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise PortalUpstreamError("Muse local product service is unavailable") from exc
        if len(raw) > MAX_PRODUCT_RESPONSE:
            return 413, b"", None
        if status != 200 or content_type != expected_type or not raw:
            raise PortalUpstreamError("Muse local product service returned an incompatible file")

        preview_policy = None
        if action == "preview":
            try:
                html_text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PortalUpstreamError("Muse product preview is not valid UTF-8") from exc
            match = re.search(
                r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"\s*/?>',
                html_text,
                flags=re.IGNORECASE,
            )
            if not match:
                raise PortalUpstreamError("Muse product preview is missing its integrity CSP")
            preview_policy = unescape(match.group(1))
            required_directives = (
                "default-src 'none'",
                "connect-src 'none'",
                "img-src 'none'",
                "font-src 'none'",
                "media-src 'none'",
                "object-src 'none'",
                "frame-src 'none'",
                "base-uri 'none'",
                "form-action 'none'",
            )
            has_script_hash = re.search(
                r"(?:^|;\s*)script-src 'sha256-[A-Za-z0-9+/]{43}='(?:;|$)",
                preview_policy,
            )
            has_style_hash = re.search(
                r"(?:^|;\s*)style-src 'sha256-[A-Za-z0-9+/]{43}='(?:;|$)",
                preview_policy,
            )
            if (not all(item in preview_policy for item in required_directives)
                    or not has_script_hash or not has_style_hash
                    or any(item in preview_policy.casefold() for item in ("unsafe-inline", "unsafe-eval", "http:", "https:", "data:", "blob:"))):
                raise PortalUpstreamError("Muse product preview CSP is too permissive or incomplete")
        return status, raw, preview_policy


    def _upstream_post(self, route: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        path = UPSTREAM_WRITE_ROUTES.get(route)
        if path is None:
            raise PortalError("write route is not allowlisted")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = Request(
            self.server.upstream_url + path,
            data=body,
            headers={
                "Authorization": "Bearer " + self.server.upstream_token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, new_url):
                return None

        try:
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=180.0) as response:
                status = int(response.status)
                raw = response.read(MAX_API_RESPONSE + 1)
        except HTTPError as exc:
            status = int(exc.code)
            if status not in {400, 404, 409, 413, 415, 429}:
                raise PortalUpstreamError("Muse local write service is unavailable") from exc
            raw = exc.read(MAX_API_RESPONSE + 1)
            if len(raw) > MAX_API_RESPONSE:
                raise PortalUpstreamError("Muse write error response exceeds the portal limit") from exc
            try:
                value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, PortalError):
                value = {}
            error = value.get("error") if isinstance(value, dict) else None
            return status, {"error": error[:300] if isinstance(error, str) else "request rejected"}
        except (URLError, TimeoutError, OSError) as exc:
            raise PortalUpstreamError("Muse local write service is unavailable") from exc
        if len(raw) > MAX_API_RESPONSE:
            raise PortalUpstreamError("Muse write response exceeds the portal limit")
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, PortalError) as exc:
            raise PortalUpstreamError("Muse local write service returned invalid JSON") from exc
        if not isinstance(value, dict) or value.get("api_version") != 1 or status not in {200, 201, 202}:
            raise PortalUpstreamError("Muse local write service returned an incompatible response")
        return status, value



    def do_GET(self) -> None:
        if not self._authority_ok(require_origin=False):
            self._json(400, {"error": "verified HTTPS and the configured Host/Origin are required"})
            return
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._json(400, {"error": "query strings are not accepted"})
            return
        if parsed.path == "/readyz":
            try:
                ready = self.server.local_readiness()
                if ready:
                    self._upstream("overview")
                else:
                    raise PortalError("local readiness checks failed")
            except Exception:
                self._json(503, {"api_version": 1, "status": "not_ready", "check": "readiness"})
            else:
                self._json(200, {"api_version": 1, "status": "ready", "check": "readiness"})
            return
        if parsed.path == "/healthz":
            self._json(200, {"api_version": 1, "status": "ok", "check": "liveness"})
            return
        if parsed.path == "/api/v1/portal/session":
            session = self._session()
            if not session:
                self._json(401, {"error": "authentication required"})
                return
            csrf = self.server.sessions.rotate_csrf(session)
            if not csrf:
                self._json(401, {"error": "authentication required"})
                return
            self._json(200, {"api_version": 1, "authenticated": True, "csrf_token": csrf,
                             "session_expires_in": SESSION_TTL_SECONDS})
            return
        if parsed.path.startswith("/api/v1/portal/"):
            if not self._session():
                self._json(401, {"error": "authentication required"})
                return
            if parsed.path == "/api/v1/portal/plugins":
                try:
                    with PluginRegistry(self.server.plugin_db) as registry:
                        with CapabilityRegistry(self.server.capability_db) as catalog:
                            sync_plugin_capabilities(registry, catalog)
                        result = {"api_version": 1, "plugins": registry.list_plugins(limit=200)}
                    self._json(200, result)
                except Exception:
                    self._json(503, {"error": "plugin registry is unavailable"})
                return
            if parsed.path == "/api/v1/portal/tasks":
                try:
                    with TaskQueue(self.server.task_db) as queue:
                        records = queue.list_tasks(limit=40)
                    safe_tasks = []
                    for task in records:
                        title = task.get("title")
                        status = task.get("status")
                        workload = task.get("workload_class")
                        safe_tasks.append({
                            "id": str(task.get("id") or "")[:64],
                            "title": (title[:160] if isinstance(title, str) else "Untitled task"),
                            "status": status if isinstance(status, str) else "unknown",
                            "created_at": str(task.get("created_at") or "")[:64],
                            "updated_at": str(task.get("updated_at") or "")[:64],
                            "priority": task.get("priority") if isinstance(task.get("priority"), int) and not isinstance(task.get("priority"), bool) else 0,
                            "attempts": task.get("attempts") if isinstance(task.get("attempts"), int) and not isinstance(task.get("attempts"), bool) else 0,
                            "workload_class": workload if isinstance(workload, str) else "general",
                        })
                    self._json(200, {"api_version": 1, "tasks": safe_tasks, "payloads_exposed": False})
                except Exception:
                    self._json(503, {"error": "Commander task ledger is unavailable"})
                return

            product_match = re.fullmatch(
                r"/api/v1/portal/products/([0-9a-f]{32})/([0-9a-f]{20})/(preview|download)",
                parsed.path,
            )
            if product_match:
                initiative_id, version_id, action = product_match.groups()
                try:
                    status, body, preview_policy = self._upstream_product_file(initiative_id, version_id, action)
                except PortalUpstreamError:
                    self._json(503, {"error": "product file is unavailable"})
                    return
                if status != 200:
                    self._json(status, {"error": "product file is unavailable"})
                else:
                    self._send_product_file(
                        body, action=action, initiative_id=initiative_id, preview_policy=preview_policy,
                    )
                return
                return
            route = parsed.path.rsplit("/", 1)[-1]
            if route in UPSTREAM_READ_ROUTES and parsed.path == f"/api/v1/portal/{route}":
                try:
                    self._json(200, self._upstream(route))
                except (PortalError, ValueError, OSError):
                    self._json(503, {"error": "requested Muse data is unavailable"})
                return
            self._json(404, {"error": "portal API route not found"})
            return
            return
        if parsed.path == "/":
            static_path = safe_static_file(self.server.static_dir, "/index.html")
        else:
            static_path = safe_static_file(self.server.static_dir, parsed.path)
        if static_path is None:
            self._json(404, {"error": "resource not found"})
            return
        try:
            body = static_path.read_bytes()
        except OSError:
            self._json(404, {"error": "resource not found"})
            return
        if len(body) > 2 * 1024 * 1024:
            self._json(413, {"error": "static resource exceeds the size limit"})
            return
        content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
        if content_type == "text/javascript":
            content_type = "application/javascript"
        self._send(200, body, content_type + ("; charset=utf-8" if content_type.startswith(("text/", "application/javascript")) else ""))

    def do_POST(self) -> None:
        if not self._origin_ok():
            self._json(403, {"error": "same-origin verified HTTPS request required"})
            return
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._json(400, {"error": "query strings are not accepted"})
            return
        if parsed.path == "/api/v1/portal/login":
            address = self._client_address()
            try:
                payload = self._read_json()
                if set(payload) != {"password"}:
                    self._json(400, {"error": "invalid login request"})
                    return
                authentication = self.server.sessions.authenticate(address, payload["password"])
                if authentication == "rate_limited":
                    self._json(429, {"error": "too many login attempts; retry later"})
                    return
                if authentication != "authenticated":
                    self._json(401, {"error": "invalid credentials"})
                    return
                token, expiry = self.server.sessions.create()
                csrf = self.server.sessions.rotate_csrf(token)
                self._json_with_headers(200, {"api_version": 1, "authenticated": True,
                    "csrf_token": csrf, "session_expires_at": expiry}, {
                    "Set-Cookie": f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; Secure; HttpOnly; SameSite=Strict",
                })
            except (PortalError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                self._json(400, {"error": "invalid login request"})
            return

        session = self._session()
        if not session or not self.server.sessions.verify_csrf(session, self.headers.get("X-Muse-CSRF", "")):
            self._json(403, {"error": "session or CSRF validation failed"})
            return
        if parsed.path == "/api/v1/portal/logout":
            address = self._client_address()
            self.server.sessions.record_login_event(address, "logout")
            self.server.sessions.revoke(session)
            self._json_with_headers(200, {"api_version": 1, "authenticated": False}, {
                "Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict",
            })
            return

        if parsed.path == "/api/v1/portal/inspirations":
            try:
                payload = self._read_json(maximum=MAX_INSPIRATION_BODY)
                required_fields = {"content", "allow_external_research"}
                optional_fields = {"title", "tags", "research_domains", "source"}
                if (not required_fields.issubset(payload)
                        or set(payload) - required_fields - optional_fields):
                    raise PortalError("inspiration request fields are invalid")
                content = payload["content"]
                allow_external = payload["allow_external_research"]
                if not isinstance(content, str) or not content.strip() or len(content.strip()) > 2000:
                    raise PortalError("inspiration content must contain 1-2000 characters")
                if not isinstance(allow_external, bool):
                    raise PortalError("allow_external_research must be boolean")
                status, result = self._upstream_post("inspirations", payload)
                self._json(status, result)
            except PortalUpstreamError:
                self._json(503, {"error": "Muse inspiration service is unavailable"})
            except (PortalError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)[:300]})
            except RecursionError:
                self._json(400, {"error": "inspiration payload nesting exceeds the request limit"})
            except Exception:
                self._json(503, {"error": "inspiration submission is unavailable"})
            return

        if parsed.path == "/api/v1/portal/runtime/control":
            try:
                payload = self._read_json(maximum=1024)
                action = payload.get("action") if isinstance(payload, dict) else None
                if not isinstance(payload, dict) or set(payload) != {"action"} or not isinstance(action, str) or action not in {"start", "pause", "resume", "stop"}:
                    raise PortalError("runtime action must be start, pause, resume, or stop")
                status, result = self._upstream_post("daemon-" + action, {})
                self._json(status, result)
            except PortalUpstreamError:
                self._json(503, {"error": "Muse autonomous runtime control is unavailable"})
            except (PortalError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)[:300]})
            except Exception:
                self._json(503, {"error": "runtime control is unavailable"})
            return

        if parsed.path == "/api/v1/portal/inspirations/evolve":
            try:
                payload = self._read_json(maximum=4096)
                required_fields = {"inspiration_id", "request_id"}
                allowed_fields = required_fields | {"create_offline_product"}
                create_offline_product = payload.get("create_offline_product", False)
                if (
                    set(payload) not in {frozenset(required_fields), frozenset(allowed_fields)}
                    or not isinstance(payload.get("inspiration_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", payload["inspiration_id"])
                    or not isinstance(payload.get("request_id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", payload["request_id"])
                    or not isinstance(create_offline_product, bool)
                ):
                    raise PortalError("evolution requires valid inspiration_id, request_id and optional boolean create_offline_product")
                payload["create_offline_product"] = create_offline_product
                status, result = self._upstream_post("inspiration-evolve", payload)
                self._json(status, result)
            except PortalUpstreamError:
                self._json(503, {"error": "Muse inspiration evolution is unavailable"})
            except (PortalError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)[:300]})
            except RecursionError:
                self._json(400, {"error": "evolution payload nesting exceeds the request limit"})
            except Exception:
                self._json(503, {"error": "inspiration evolution is unavailable"})
            return
        if parsed.path == "/api/v1/portal/plugins/discover":
            try:
                payload = self._read_json(maximum=MAX_PLUGIN_DISCOVERY_BODY)
                if set(payload) != {"manifest"} or not isinstance(payload["manifest"], dict):
                    raise PortalError("plugin discovery requires exactly one manifest object")
                try:
                    validated_manifest = validate_manifest(payload["manifest"])
                except PluginError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                with PluginRegistry(self.server.plugin_db) as registry:
                    result = registry.discover(validated_manifest)
                    with CapabilityRegistry(self.server.capability_db) as catalog:
                        sync_plugin_capabilities(registry, catalog)
                self._json(201, {
                    "api_version": 1, "plugin": result, "review_required": True,
                    "execution_enabled": False,
                    "message": "Manifest registered as an unverified candidate; approval and runtime review are separate.",
                })
            except PluginError as exc:
                self._json(409, {"error": str(exc)})
            except (PortalError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
            except RecursionError:
                self._json(400, {"error": "plugin manifest nesting exceeds the request limit"})
            except Exception:
                self._json(503, {"error": "plugin discovery is unavailable"})
            return

        request_match = re.fullmatch(
            r"/api/v1/portal/plugins/([a-z0-9.-]{1,96})/capabilities/([a-z0-9._-]{1,96})/request",
            parsed.path,
        )
        if request_match:
            try:
                payload = self._read_json(maximum=MAX_PLUGIN_DISCOVERY_BODY)
                if set(payload) != {"payload"} or not isinstance(payload["payload"], dict):
                    raise PortalError("capability request requires exactly one JSON object field: payload")
                with PluginRegistry(self.server.plugin_db) as registry:
                    result = registry.commander_request(
                        request_match.group(1), request_match.group(2), payload["payload"],
                        enqueue=commander_task_queue_adapter(self.server.task_db),
                        requested_by="web-portal",
                    )
                self._json(202, {"api_version": 1, **result})
            except PluginError as exc:
                self._json(409, {"error": str(exc)})
            except (PortalError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
            except RecursionError:
                self._json(400, {"error": "capability payload nesting exceeds the request limit"})
            except Exception:
                self._json(503, {"error": "Commander request submission is unavailable"})
            return

        if parsed.path.startswith("/api/v1/portal/plugins/"):
            self._json(405, {"error": "browser-side plugin approval, activation and disable are not enabled"})
            return
        self._json(405, {"error": "portal write route is not enabled"})

    def do_PUT(self) -> None:
        self._json(405, {"error": "only explicitly allowlisted POST workflows are enabled"})

    def do_PATCH(self) -> None:
        self._json(405, {"error": "only explicitly allowlisted POST workflows are enabled"})

    def do_DELETE(self) -> None:
        self._json(405, {"error": "only explicitly allowlisted POST workflows are enabled"})

    def do_OPTIONS(self) -> None:
        self._json(405, {"error": "cross-origin requests are not supported"})


def resolve_portal_data_paths(
    task_db: str | Path,
    *,
    agent_db: str | Path | None = None,
    improvement_db: str | Path | None = None,
    knowledge_db: str | Path | None = None,
    report_dir: str | Path | None = None,
    products_dir: str | Path | None = None,
    gpu_resource_db: str | Path | None = None,
    memory_db: str | Path | None = None,
    plugin_db: str | Path | None = None,
) -> dict[str, Path | None]:
    """Resolve optional stores from the real Muse data layout without creating empty stores."""
    task_path = Path(task_db).resolve()
    if not task_path.is_file():
        raise ValueError("task-db must point to an existing Muse TaskQueue database")
    if task_path.parent.name == "tasks" and task_path.parent.parent.name == "data":
        workspace_root = task_path.parent.parent.parent
    elif task_path.parent.name == "tasks":
        workspace_root = task_path.parent.parent
    else:
        workspace_root = task_path.parent

    data_root = workspace_root / "data"
    products_candidate = Path(products_dir).resolve() if products_dir is not None else data_root / "products"
    if products_candidate.is_dir() and products_candidate.parent.name == "data":
        workspace_root = products_candidate.parent.parent
        data_root = workspace_root / "data"

    def existing_file(value: str | Path | None, default: Path) -> Path | None:
        candidate = Path(value).resolve() if value is not None else default.resolve()
        return candidate if candidate.is_file() else None

    def existing_dir(value: str | Path | None, default: Path) -> Path | None:
        candidate = Path(value).resolve() if value is not None else default.resolve()
        return candidate if candidate.is_dir() else None

    return {
        "workspace_root": workspace_root.resolve(),
        "agent_db": existing_file(agent_db, data_root / "agents" / "muse.sqlite3"),
        "improvement_db": existing_file(improvement_db, data_root / "improvements" / "muse.sqlite3"),
        "knowledge_db": existing_file(knowledge_db, data_root / "research" / "knowledge.sqlite3"),
        "report_dir": existing_dir(report_dir, data_root / "research" / "reports"),
        "products_dir": existing_dir(products_dir, data_root / "products"),
        "gpu_resource_db": existing_file(gpu_resource_db, data_root / "gpu-resources.sqlite3"),
        "memory_db": (Path(memory_db).resolve() if memory_db is not None
                      else (data_root / "memory" / "muse.sqlite3").resolve()),
        "plugin_db": (Path(plugin_db).resolve() if plugin_db is not None
                      else (data_root / "plugins" / "registry.sqlite3").resolve()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start Muse HTTPS portal with cross-device inspiration workflows and Commander-governed plugins")
    parser.add_argument("--task-db", required=True)
    parser.add_argument("--progress-db", required=True)
    parser.add_argument("--capability-db", required=True)
    parser.add_argument("--agent-db")
    parser.add_argument("--improvement-db")
    parser.add_argument("--knowledge-db")
    parser.add_argument("--report-dir")
    parser.add_argument("--products-dir")
    parser.add_argument("--gpu-resource-db")
    parser.add_argument("--memory-db")
    parser.add_argument("--plugin-db")
    parser.add_argument("--session-db", required=True)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--origin", required=True, help="Exact public HTTPS origin, e.g. https://muse.example.com")
    parser.add_argument("--trusted-proxy-cidr", action="append", default=[], help="Optional trusted proxy peer network; only one X-Forwarded-For IP is used for login rate limiting")
    parser.add_argument("--static-dir", default="web-portal")
    parser.add_argument("--bind", default="127.0.0.1", help="Defaults to loopback; public binding must be explicit")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args(argv)
    password = os.environ.get("MUSE_WEB_PASSWORD", "")
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeEncodeError:
        parser.error("set MUSE_WEB_PASSWORD to a secret containing 32-1024 valid UTF-8 bytes")
    if not 32 <= len(password_bytes) <= 1024:
        parser.error("set MUSE_WEB_PASSWORD to a secret containing 32-1024 UTF-8 bytes")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        parse_trusted_proxy_cidrs(args.trusted_proxy_cidr)
    except PortalError as exc:
        parser.error(str(exc))
    try:
        data_paths = resolve_portal_data_paths(
            args.task_db, agent_db=args.agent_db, improvement_db=args.improvement_db,
            knowledge_db=args.knowledge_db, report_dir=args.report_dir, products_dir=args.products_dir,
            gpu_resource_db=args.gpu_resource_db, memory_db=args.memory_db, plugin_db=args.plugin_db,
        )
    except ValueError as exc:
        parser.error(str(exc))
    plugin_db = data_paths["plugin_db"]
    assert isinstance(plugin_db, Path)
    upstream, token = create_status_server(
        task_db=args.task_db, progress_db=args.progress_db, capability_db=args.capability_db,
        agent_db=data_paths["agent_db"], improvement_db=data_paths["improvement_db"], port=0,
        knowledge_db=data_paths["knowledge_db"], report_dir=data_paths["report_dir"],
        products_dir=data_paths["products_dir"], gpu_resource_db=data_paths["gpu_resource_db"],
        memory_db=data_paths["memory_db"], plugin_db=plugin_db,
    )
    upstream_thread = threading.Thread(target=upstream.serve_forever, name="muse-web-upstream", daemon=True)
    upstream_thread.start()
    try:
        server = WebPortalServer(
            (args.bind, args.port), public_origin=args.origin, certfile=args.cert, keyfile=args.key,
            session_db=args.session_db, password=password,
            upstream_url=f"http://127.0.0.1:{upstream.server_address[1]}", upstream_token=token,
            plugin_db=plugin_db, task_db=args.task_db, capability_db=args.capability_db,
            static_dir=args.static_dir,
            trusted_proxy_cidrs=tuple(args.trusted_proxy_cidr),
        )
    except (OSError, PortalError) as exc:
        upstream.shutdown()
        upstream.server_close()
        parser.error(str(exc))
    print(json.dumps({
        "service": "muse-web-portal", "origin": validate_public_origin(args.origin),
        "configured_public_url": validate_public_origin(args.origin),
        "trusted_proxy_cidrs": args.trusted_proxy_cidr,
        "address": args.bind, "port": args.port, "tls": "1.2-1.3",
        "browser_api": "authenticated-read+inspiration-capture/evolution+plugin-manifest-discovery+commander-approval-submit",
        "browser_plugin_approval_exposed": False,
        "desktop_bearer_exposed": False,
        "commander_execution_exposed": False,
    }, separators=(",", ":")))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.close_portal()
        upstream.shutdown()
        upstream.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
