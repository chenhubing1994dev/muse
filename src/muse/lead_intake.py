"""Local-only, consent-gated inquiry intake for the Muse showcase."""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
import unicodedata
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 8192
CONSENT_VERSION = "muse-local-inquiry-v1"
SERVICE_CHOICES = {"AI 短视频制作", "多平台内容套件", "AI 工作流与原型", "其他合作"}
BUDGET_CHOICES = {"暂未确定", "低于 $300", "$300–$1,000", "$1,000–$3,000", "$3,000 以上"}
TIMELINE_CHOICES = {"弹性安排", "一周内", "两周内", "一个月内"}


class LeadValidationError(ValueError):
    """A lead submission did not meet the local intake contract."""


def _clean_text(value: Any, label: str, maximum: int, *, multiline: bool = False) -> str:
    if not isinstance(value, str):
        raise LeadValidationError(f"{label} must be text")
    cleaned = unicodedata.normalize("NFC", value).strip()
    if not cleaned or len(cleaned) > maximum:
        raise LeadValidationError(f"{label} is empty or too long")
    for character in cleaned:
        if unicodedata.category(character) == "Cc" and not (multiline and character in "\n\t"):
            raise LeadValidationError(f"{label} contains unsupported control characters")
    return cleaned


def validate_lead_payload(payload: Any) -> dict[str, str]:
    required = {"service", "brief", "budget", "timeline", "contact", "consent", "website", "submission_id"}
    if not isinstance(payload, dict) or set(payload) != required:
        raise LeadValidationError("submission schema is incomplete or contains unknown fields")
    if payload.get("consent") is not True:
        raise LeadValidationError("explicit consent is required")
    website = payload.get("website")
    if not isinstance(website, str) or len(website) > 160:
        raise LeadValidationError("invalid honeypot field")
    if website.strip():
        raise LeadValidationError("submission rejected")
    try:
        submission_id = str(uuid.UUID(_clean_text(payload.get("submission_id"), "submission_id", 36)))
    except (ValueError, AttributeError) as exc:
        raise LeadValidationError("invalid submission id") from exc
    service = _clean_text(payload.get("service"), "service", 80)
    budget = _clean_text(payload.get("budget"), "budget", 40)
    timeline = _clean_text(payload.get("timeline"), "timeline", 40)
    brief = _clean_text(payload.get("brief"), "brief", 1200, multiline=True)
    contact = _clean_text(payload.get("contact"), "contact", 160)
    if service not in SERVICE_CHOICES:
        raise LeadValidationError("unknown service choice")
    if budget not in BUDGET_CHOICES or timeline not in TIMELINE_CHOICES:
        raise LeadValidationError("unknown budget or timeline choice")
    if len(contact) < 5:
        raise LeadValidationError("contact information is too short")
    return {"submission_id": submission_id, "service": service, "brief": brief,
            "budget": budget, "timeline": timeline, "contact": contact}


class LeadStore:
    """Persist only explicitly consented leads in a dedicated local SQLite file."""

    def __init__(self, database_path: str | Path, *, retention_days: int = 90) -> None:
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or not 1 <= retention_days <= 365:
            raise ValueError("retention_days must be an integer from 1 to 365")
        path = Path(database_path).expanduser()
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ValueError("database path must be a regular file, not a symlink or directory")
        for parent in path.parents:
            if parent.exists() and parent.is_symlink():
                raise ValueError("database path cannot contain symlinked parent directories")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink():
            raise ValueError("database directory cannot be a symlink")
        self.database_path = path.resolve(strict=False)
        self.retention_days = retention_days
        self._initialize()
        self.prune_expired()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY,
                    lead_reference TEXT NOT NULL UNIQUE,
                    submission_id TEXT NOT NULL UNIQUE,
                    received_at TEXT NOT NULL,
                    retention_until TEXT NOT NULL,
                    service TEXT NOT NULL,
                    brief TEXT NOT NULL,
                    budget TEXT NOT NULL,
                    timeline TEXT NOT NULL,
                    contact TEXT NOT NULL,
                    consent_version TEXT NOT NULL,
                    consented_at TEXT NOT NULL
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_leads_retention ON leads(retention_until)")
            connection.commit()
        finally:
            connection.close()
        if os.name != "nt":
            os.chmod(self.database_path.parent, 0o700)
            if self.database_path.exists():
                os.chmod(self.database_path, 0o600)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)

    def prune_expired(self) -> int:
        now_text = self._now().isoformat()
        connection = self._connect()
        try:
            cursor = connection.execute("DELETE FROM leads WHERE retention_until <= ?", (now_text,))
            connection.commit()
            return int(cursor.rowcount)
        finally:
            connection.close()

    def submit(self, payload: Any) -> dict[str, Any]:
        values = validate_lead_payload(payload)
        now = self._now()
        now_text = now.isoformat()
        retention_text = (now + timedelta(days=self.retention_days)).isoformat()
        reference = f"MUSE-{now:%Y%m%d}-{secrets.token_hex(4).upper()}"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM leads WHERE retention_until <= ?", (now_text,))
            existing = connection.execute(
                "SELECT lead_reference FROM leads WHERE submission_id = ?", (values["submission_id"],)
            ).fetchone()
            if existing:
                connection.commit()
                return {"lead_reference": existing["lead_reference"], "duplicate": True,
                        "retention_days": self.retention_days}
            connection.execute(
                """INSERT INTO leads (
                    lead_reference, submission_id, received_at, retention_until,
                    service, brief, budget, timeline, contact, consent_version, consented_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (reference, values["submission_id"], now_text, retention_text, values["service"],
                 values["brief"], values["budget"], values["timeline"], values["contact"],
                 CONSENT_VERSION, now_text),
            )
            connection.commit()
            if os.name != "nt":
                os.chmod(self.database_path, 0o600)
            return {"lead_reference": reference, "duplicate": False, "retention_days": self.retention_days}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_leads(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT lead_reference, received_at, retention_until, service, budget, timeline
                FROM leads ORDER BY received_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def get_lead(self, lead_reference: str) -> dict[str, Any] | None:
        reference = _clean_text(lead_reference, "lead_reference", 40)
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT lead_reference, received_at, retention_until, service,
                brief, budget, timeline, contact, consent_version, consented_at
                FROM leads WHERE lead_reference = ?""",
                (reference,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def export_rows(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT lead_reference, received_at, retention_until, service,
                brief, budget, timeline, contact, consent_version, consented_at
                FROM leads ORDER BY received_at DESC"""
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()


    def count(self) -> int:
        connection = self._connect()
        try:
            row = connection.execute("SELECT COUNT(*) AS total FROM leads").fetchone()
            return int(row["total"])
        finally:
            connection.close()


def export_leads_csv(rows: list[dict[str, Any]], output_path: str | Path) -> Path:
    """Write an explicit local export without overwriting an existing file."""
    import csv

    columns = (
        "lead_reference", "received_at", "retention_until", "service", "brief",
        "budget", "timeline", "contact", "consent_version", "consented_at",
    )
    target = Path(output_path).expanduser()
    if target.exists() or target.is_symlink():
        raise FileExistsError("refusing to overwrite an existing lead export")
    for parent in target.parents:
        if parent.exists() and parent.is_symlink():
            raise ValueError("export path cannot contain symlinked parent directories")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise ValueError("export directory cannot be a symlink")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600 if os.name != "nt" else 0o666)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                safe = {}
                for column in columns:
                    value = str(row.get(column, ""))
                    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
                        value = "'" + value
                    safe[column] = value
                writer.writerow(safe)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target





class LeadRateLimiter:
    """In-memory per-client throttling; client IPs are never written to disk."""

    def __init__(self, *, max_requests: int = 5, window_seconds: int = 900) -> None:
        if max_requests < 1 or window_seconds < 1:
            raise ValueError("rate limit values must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._seen: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_key: str, *, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._seen[client_key]
            cutoff = moment - self.window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                return False
            bucket.append(moment)
            if len(self._seen) > 2048:
                for key in tuple(self._seen):
                    recent = self._seen[key]
                    while recent and recent[0] <= cutoff:
                        recent.popleft()
                    if not recent:
                        self._seen.pop(key, None)
            return True


def create_request_handler(store: LeadStore, static_root: str | Path, *,
                           rate_limiter: LeadRateLimiter | None = None):
    """Create a same-origin handler for static preview assets and POST /api/leads."""
    root = Path(static_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("static_root must be a directory")
    limiter = rate_limiter or LeadRateLimiter()

    class MuseLeadHandler(SimpleHTTPRequestHandler):
        server_version = "MuseLocalPreview/1.0"
        sys_version = ""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format: str, *args: Any) -> None:
            # Do not log IPs, request bodies, contact details, or other lead data.
            return

        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/api/leads":
                self._json(404, {"status": "not_found"})
                return
            origin = self.headers.get("Origin", "")
            host = self.headers.get("Host", "")
            try:
                parsed_origin = urlsplit(origin)
                allowed_origin = (
                    parsed_origin.scheme == "http"
                    and parsed_origin.netloc.lower() == host.lower()
                    and parsed_origin.hostname in {"127.0.0.1", "localhost"}
                    and parsed_origin.port == self.server.server_address[1]
                    and not parsed_origin.username
                    and not parsed_origin.password
                    and not parsed_origin.path
                    and not parsed_origin.query
                    and not parsed_origin.fragment
                )
            except ValueError:
                allowed_origin = False
            if not allowed_origin:
                self._json(403, {"status": "origin_rejected"})
                return
            if self.headers.get("Transfer-Encoding"):
                self._json(400, {"status": "invalid_request"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._json(415, {"status": "json_required"})
                return
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isdigit():
                self._json(411, {"status": "content_length_required"})
                return
            length = int(raw_length)
            if length > MAX_REQUEST_BYTES:
                self.close_connection = True
                self._json(413, {"status": "request_too_large"})
                return
            if not limiter.allow(self.client_address[0]):
                self._json(429, {"status": "rate_limited"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = store.submit(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, LeadValidationError):
                self._json(400, {"status": "invalid_submission"})
                return
            except sqlite3.Error:
                self._json(503, {"status": "local_store_unavailable"})
                return
            status = 200 if result["duplicate"] else 201
            self._json(status, {
                "status": "accepted",
                "lead_reference": result["lead_reference"],
                "duplicate": result["duplicate"],
                "retention_days": result["retention_days"],
            })

    return MuseLeadHandler
