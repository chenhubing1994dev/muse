"""Explicitly configured TLS transport for read-only Muse Companion access.

This listener is separate from the desktop loopback API. It never accepts the
desktop Bearer token, never trusts proxy headers, and exposes only pairing
bootstrap plus signed, read-only status/event requests. It is not started by
the desktop Host unless an operator explicitly launches this module.
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any
from urllib.parse import urlsplit

from muse.companion_security import CompanionDeviceStore, CompanionSecurityError
from muse.progress import ProgressEventStore
from muse.task_queue import TaskQueue

MAX_PAIRING_BODY_BYTES = 8192
MAX_RESPONSE_BYTES = 262144
MAX_EVENTS = 100
_TLS_VERSIONS = {"TLSv1.2", "TLSv1.3"}
_TAURI_WEBVIEW_ORIGINS = frozenset({"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"})
_CORS_ALLOWED_HEADERS = frozenset({
    "accept", "content-type", "x-muse-device-id", "x-muse-device-token",
    "x-muse-session-id", "x-muse-nonce", "x-muse-timestamp",
    "x-muse-signature", "last-event-id",
})
_CORS_ROUTE_METHODS = {
    "/api/v1/companion/pairing/request": "POST",
    "/api/v1/companion/pairing/claim": "POST",
    "/api/v1/companion/status": "GET",
    "/api/v1/companion/events": "GET",
}


def build_companion_server_context(certfile: str | Path, keyfile: str | Path) -> ssl.SSLContext:
    """Load an explicit server certificate and private key; never downgrade to HTTP."""
    certificate = Path(certfile).expanduser()
    private_key = Path(keyfile).expanduser()
    if not certificate.is_file() or certificate.is_symlink():
        raise ValueError("Companion TLS certificate must be an existing regular file")
    if not private_key.is_file() or private_key.is_symlink():
        raise ValueError("Companion TLS private key must be an existing regular file")
    if certificate.resolve() == private_key.resolve():
        raise ValueError("Companion TLS certificate and private key must be separate files")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if hasattr(ssl.TLSVersion, "TLSv1_3"):
        context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.options |= getattr(ssl, "OP_NO_COMPRESSION", 0)
    context.set_alpn_protocols(["http/1.1"])
    try:
        context.load_cert_chain(certfile=str(certificate), keyfile=str(private_key))
    except (OSError, ssl.SSLError) as exc:
        raise ValueError("Companion TLS certificate/private key could not be loaded") from exc
    return context


class CompanionTLSHTTPServer(ThreadingHTTPServer):
    """A separately configured TLS listener; no implicit network exposure."""
    daemon_threads = True
    request_queue_size = 16
    allow_reuse_address = False

    def __init__(
        self, address: tuple[str, int], *, database: str | Path,
        task_db: str | Path, progress_db: str | Path,
        certfile: str | Path, keyfile: str | Path,
    ) -> None:
        self.database = Path(database).expanduser()
        self.task_db = Path(task_db).expanduser()
        self.progress_db = Path(progress_db).expanduser()
        for label, path in (
            ("Companion", self.database),
            ("Commander task", self.task_db),
            ("Commander progress", self.progress_db),
        ):
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"{label} database must already exist as a regular file")
        self.tls_context = build_companion_server_context(certfile, keyfile)
        super().__init__(address, CompanionRequestHandler)
        self._tls_handshake_timeout = 5.0
        self._request_slots = BoundedSemaphore(8)

    def process_request(self, request, client_address) -> None:
        # Bound concurrent TLS handshakes and request workers; slow clients cannot
        # serialize the accept loop or create an unbounded thread backlog.
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address) -> None:
        active_socket = request
        try:
            request.settimeout(self._tls_handshake_timeout)
            secure_socket = self.tls_context.wrap_socket(request, server_side=True)
            active_socket = secure_socket
            secure_socket.settimeout(10.0)
            if secure_socket.version() not in _TLS_VERSIONS:
                raise ssl.SSLError("unsupported Companion TLS protocol")
            if secure_socket.selected_alpn_protocol() not in (None, "http/1.1"):
                raise ssl.SSLError("unsupported Companion application protocol")
            self.finish_request(secure_socket, client_address)
        except (ssl.SSLError, TimeoutError, OSError):
            # Malformed/plaintext probes and abandoned TLS handshakes are expected
            # network noise; never echo their request bytes or stack traces.
            pass
        except Exception:
            self.handle_error(active_socket, client_address)
        finally:
            self.shutdown_request(active_socket)
            self._request_slots.release()


class CompanionRequestHandler(BaseHTTPRequestHandler):
    server: CompanionTLSHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "MuseCompanion"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        # Do not log request paths, query strings, pairing codes or credentials.
        return

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in _TAURI_WEBVIEW_ORIGINS

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            status = 503
            body = b'{"error":"response exceeds the configured size limit"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        if origin in _TAURI_WEBVIEW_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _require_tls(self) -> bool:
        connection = self.connection
        return (
            isinstance(connection, ssl.SSLSocket)
            and connection.version() in _TLS_VERSIONS
            and connection.selected_alpn_protocol() in (None, "http/1.1")
        )

    def _read_json(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding") is not None:
            raise ValueError("transfer-encoded request bodies are not supported")
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ValueError("application/json required")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if not 1 <= length <= MAX_PAIRING_BODY_BYTES:
            raise ValueError("request body must contain 1-8192 bytes")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("incomplete request body")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def do_POST(self) -> None:
        if not self._origin_allowed():
            self._send(403, {"error": "origin not allowed"})
            return
        if not self._require_tls():
            self._send(400, {"error": "verified TLS transport is required"})
            return
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._send(400, {"error": "query strings are not accepted on pairing routes"})
            return
        try:
            payload = self._read_json()
            with CompanionDeviceStore(self.server.database) as store:
                if parsed.path == "/api/v1/companion/pairing/request":
                    required = {"challenge_id", "code", "device_name", "device_fingerprint", "claim_secret"}
                    if set(payload) != required:
                        raise ValueError("pairing request fields do not match the protocol")
                    result = store.request_pairing(**payload)
                    self._send(202, {"api_version": 1, "pairing": result})
                elif parsed.path == "/api/v1/companion/pairing/claim":
                    if set(payload) != {"request_id", "claim_secret"}:
                        raise ValueError("pairing claim fields do not match the protocol")
                    result = store.claim_pairing(**payload)
                    self._send(200, {"api_version": 1, "device": result})
                else:
                    self._send(404, {"error": "Companion route not found"})
        except CompanionSecurityError as exc:
            self._send(409, {"error": str(exc)})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            self._send(503, {"error": "Companion pairing service unavailable"})

    def do_GET(self) -> None:
        if not self._origin_allowed():
            self._send(403, {"error": "origin not allowed"})
            return
        if not self._require_tls():
            self._send(400, {"error": "verified TLS transport is required"})
            return
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._send(400, {"error": "query strings are not accepted on signed read routes"})
            return
        if parsed.path not in {"/api/v1/companion/status", "/api/v1/companion/events"}:
            self._send(404, {"error": "Companion route not found"})
            return
        device_id = self.headers.get("X-Muse-Device-ID", "")
        token = self.headers.get("X-Muse-Device-Token", "")
        session_id = self.headers.get("X-Muse-Session-ID", "")
        nonce = self.headers.get("X-Muse-Nonce", "")
        signature = self.headers.get("X-Muse-Signature", "")
        timestamp_text = self.headers.get("X-Muse-Timestamp", "")
        cursor = self.headers.get("Last-Event-ID", "")
        if parsed.path.endswith("/status") and cursor:
            self._send(400, {"error": "event cursor is valid only for the events route"})
            return
        try:
            if not re.fullmatch(r"[0-9]{1,16}", timestamp_text):
                raise ValueError("invalid request timestamp")
            with CompanionDeviceStore(self.server.database) as store:
                device = store.authenticate_request(
                    device_id, token, session_id=session_id, nonce=nonce,
                    timestamp_ms=int(timestamp_text), method="GET", path=parsed.path,
                    body=b"", signature=signature, cursor=cursor,
                    transport_verified=self._require_tls(),
                )
            if parsed.path.endswith("/status"):
                with TaskQueue(self.server.task_db) as queue:
                    counts = queue.snapshot(limit=1)["counts"]
                with ProgressEventStore(self.server.progress_db) as progress:
                    workers = progress.list_workers(limit=100)
                self._send(200, {
                    "api_version": 1,
                    "host": {"status": "online", "read_only": True},
                    "commander": {"queue_counts": counts},
                    "workers": {
                        "active": sum(1 for worker in workers if worker["state"] == "active" and not worker["stale"]),
                        "stale": sum(1 for worker in workers if worker["stale"]),
                    },
                    "device": {"device_id": device["device_id"], "permissions": device["permissions"]},
                    "generated_at": time.time(),
                })
                return
            with ProgressEventStore(self.server.progress_db) as progress:
                batch = list(reversed(progress.list_events(limit=MAX_EVENTS)))
            if cursor and not any(event["id"] == cursor for event in batch):
                self._send(200, {"api_version": 1, "reset": True, "events": []})
                return
            passed_cursor = not cursor
            events = []
            for event in batch:
                if event["id"] == cursor:
                    passed_cursor = True
                    continue
                if passed_cursor:
                    events.append({
                        "id": event["id"], "category": event["category"],
                        "event": event["event"], "created_at": event["created_at"],
                        "error_code": event.get("error_code"),
                    })
            self._send(200, {"api_version": 1, "reset": False, "events": events})
        except CompanionSecurityError:
            self._send(401, {"error": "device authentication failed"})
        except (ValueError, TypeError):
            self._send(400, {"error": "invalid signed Companion request"})
        except Exception:
            self._send(503, {"error": "Companion read service unavailable"})

    def do_PUT(self) -> None:
        self._send(405, {"error": "Companion protocol is read-only"})

    def do_PATCH(self) -> None:
        self._send(405, {"error": "Companion protocol is read-only"})

    def do_DELETE(self) -> None:
        self._send(405, {"error": "Companion protocol is read-only"})

    def do_OPTIONS(self) -> None:
        if not self._require_tls():
            self._send(400, {"error": "verified TLS transport is required"})
            return
        parsed = urlsplit(self.path)
        origin = self.headers.get("Origin")
        requested_method = self.headers.get("Access-Control-Request-Method", "").upper()
        requested_headers = {
            item.strip().lower() for item in self.headers.get("Access-Control-Request-Headers", "").split(",")
            if item.strip()
        }
        expected_method = _CORS_ROUTE_METHODS.get(parsed.path)
        if (
            parsed.query or parsed.fragment or origin not in _TAURI_WEBVIEW_ORIGINS
            or requested_method != expected_method or not requested_headers <= _CORS_ALLOWED_HEADERS
        ):
            self._send(403, {"error": "CORS preflight is not allowed"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin, Access-Control-Request-Method, Access-Control-Request-Headers")
        self.send_header("Access-Control-Allow-Methods", expected_method)
        self.send_header("Access-Control-Allow-Headers", ", ".join(sorted(requested_headers)))
        self.send_header("Access-Control-Max-Age", "300")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the explicitly configured Muse Companion TLS endpoint")
    parser.add_argument("--database", required=True, help="Companion security SQLite database")
    parser.add_argument("--task-db", required=True)
    parser.add_argument("--progress-db", required=True)
    parser.add_argument("--cert", required=True, help="TLS server certificate PEM")
    parser.add_argument("--key", required=True, help="TLS private key PEM")
    parser.add_argument("--bind", default="127.0.0.1", help="Explicit bind address; defaults to loopback")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        server = CompanionTLSHTTPServer(
            (args.bind, args.port), database=args.database,
            task_db=args.task_db, progress_db=args.progress_db,
            certfile=args.cert, keyfile=args.key,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    host, port = server.server_address[:2]
    print(json.dumps({
        "service": "muse-companion-tls", "address": host, "port": port,
        "tls_minimum": "1.2", "protocol": "muse-companion-v1",
        "desktop_bearer_shared": False, "read_only": True,
    }, separators=(",", ":")))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
