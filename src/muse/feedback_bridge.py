"""B396 creative feedback one-click return bridge (browser offline worksheet → local Status API).

The creator fills an offline worksheet in the browser and uploads one of:

* ``.json`` manifest  — ``{"schema":"muse.feedback.v1","claim_id":..,"results":[..],"files":[{"name","sha256"}]}``
* ``.html`` worksheet — the same JSON embedded in ``<script type="application/json" id="muse-feedback">``
  (parsed with ``html.parser``; nothing is executed)
* ``.zip`` bundle     — ``manifest.json`` + listed files; every member is SHA-256 checked against the
  manifest; Zip-Slip, absolute paths, symlinks, duplicates, oversize members and compression bombs
  are refused

The upload is verified against the client-declared SHA-256, stored append-only and **idempotently**
(same bytes → same ``feedback_id`` → ``duplicate``), and linked to the original Claim with a
``tested_by`` edge. Symbolic (玄枢) payloads must still carry their immutable labels.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import sqlite3
import stat
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from muse.research_closed_loop import ClaimDossierStore
from muse.xuanshu_fusion import XuanshuLabelError, validate_labels

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_ZIP_TOTAL = 32 * 1024 * 1024
MAX_ZIP_MEMBERS = 200
MAX_RATIO = 100
SCHEMA = "muse.feedback.v1"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class FeedbackRejected(ValueError):
    pass


class _EmbeddedJson(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.capture = False
        self.chunks: list[str] = []
        self.found = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "application/json" and attributes.get("id") == "muse-feedback":
            self.capture = True
            self.found += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = False

    def handle_data(self, data):
        if self.capture:
            self.chunks.append(data)


def _safe_member(name: str) -> str:
    text = name.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or any(p in {"", ".", ".."} for p in text.split("/")) \
            or "\x00" in text or len(text) > 200:
        raise FeedbackRejected(f"unsafe archive member path: {name[:80]}")
    return PurePosixPath(text).as_posix()


def _parse_manifest(raw: bytes) -> dict[str, Any]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeedbackRejected("feedback manifest is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise FeedbackRejected("feedback manifest schema must be muse.feedback.v1")
    claim_id = document.get("claim_id")
    if not isinstance(claim_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", claim_id):
        raise FeedbackRejected("claim_id is missing or invalid")
    results = document.get("results")
    if not isinstance(results, list) or not 1 <= len(results) <= 500:
        raise FeedbackRejected("results must be a non-empty list (<=500)")
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("metric"), str) or not item["metric"].strip():
            raise FeedbackRejected("every result needs a metric name")
        value = item.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise FeedbackRejected("result values must be numbers or short text")
        if isinstance(value, str) and len(value) > 500:
            raise FeedbackRejected("result text too long")
    if document.get("xuanshu") is not None:
        try:
            validate_labels(document["xuanshu"], path="xuanshu")
        except XuanshuLabelError as exc:
            raise FeedbackRejected(str(exc)) from exc
    files = document.get("files", [])
    if not isinstance(files, list) or len(files) > MAX_ZIP_MEMBERS:
        raise FeedbackRejected("files must be a bounded list")
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) \
                or not isinstance(entry.get("sha256"), str) or _HEX64.fullmatch(entry["sha256"]) is None:
            raise FeedbackRejected("manifest file entries need name + lowercase sha256")
        _safe_member(entry["name"])
    return document


def parse_upload(filename: str, data: bytes) -> dict[str, Any]:
    lower = (filename or "").casefold()
    if lower.endswith(".json"):
        return {"format": "manifest", "manifest": _parse_manifest(data), "verified_files": 0}
    if lower.endswith((".html", ".htm")):
        parser = _EmbeddedJson()
        try:
            parser.feed(data.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise FeedbackRejected("worksheet HTML must be UTF-8") from exc
        if parser.found != 1:
            raise FeedbackRejected("worksheet must contain exactly one muse-feedback JSON block")
        return {"format": "html", "manifest": _parse_manifest("".join(parser.chunks).strip().encode("utf-8")),
                "verified_files": 0}
    if lower.endswith(".zip"):
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise FeedbackRejected("ZIP archive is corrupt") from exc
        with archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ZIP_MEMBERS:
                raise FeedbackRejected("ZIP must contain 1-200 members")
            names, total = {}, 0
            for info in infos:
                name = _safe_member(info.filename)
                if info.is_dir():
                    continue
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise FeedbackRejected("ZIP symlinks are refused")
                if name.casefold() in {n.casefold() for n in names}:
                    raise FeedbackRejected("duplicate ZIP member")
                total += info.file_size
                if total > MAX_ZIP_TOTAL or (info.compress_size and info.file_size / info.compress_size > MAX_RATIO):
                    raise FeedbackRejected("ZIP exceeds size/compression-ratio limits (possible zip bomb)")
                names[name] = info
            if "manifest.json" not in names:
                raise FeedbackRejected("ZIP must contain manifest.json at its root")
            manifest = _parse_manifest(archive.read(names["manifest.json"]))
            listed = {_safe_member(e["name"]): e["sha256"] for e in manifest.get("files", [])}
            unlisted = sorted(set(names) - set(listed) - {"manifest.json"})
            if unlisted:
                raise FeedbackRejected(f"ZIP contains files not listed in the manifest: {unlisted[:5]}")
            for name, digest in listed.items():
                if name not in names:
                    raise FeedbackRejected(f"manifest lists a missing file: {name}")
                with archive.open(names[name]) as stream:
                    content = stream.read(MAX_ZIP_TOTAL + 1)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise FeedbackRejected(f"SHA-256 mismatch for {name}")
            return {"format": "zip", "manifest": manifest, "verified_files": len(listed)}
    raise FeedbackRejected("supported uploads: .json manifest, .html worksheet, .zip bundle")


class FeedbackBridge:
    def __init__(self, database: str | Path, claims: ClaimDossierStore, *, clock: Callable[[], float] = time.time) -> None:
        self.claims, self.clock = claims, clock
        path = Path(database)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS feedback_uploads(
                feedback_id TEXT PRIMARY KEY, claim_id TEXT NOT NULL, filename TEXT NOT NULL, format TEXT NOT NULL,
                bytes_sha256 TEXT NOT NULL UNIQUE, size INTEGER NOT NULL, results_json TEXT NOT NULL,
                verified_files INTEGER NOT NULL, uploaded_at REAL NOT NULL);
            CREATE TRIGGER IF NOT EXISTS feedback_uploads_no_update BEFORE UPDATE ON feedback_uploads
                BEGIN SELECT RAISE(ABORT,'feedback uploads are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS feedback_uploads_no_delete BEFORE DELETE ON feedback_uploads
                BEGIN SELECT RAISE(ABORT,'feedback uploads are append-only'); END;
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "FeedbackBridge":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def upload(self, *, filename: str, content_base64: str, declared_sha256: str) -> dict[str, Any]:
        if not isinstance(content_base64, str) or len(content_base64) > (MAX_UPLOAD_BYTES * 4) // 3 + 8:
            raise FeedbackRejected("upload exceeds 8 MiB")
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise FeedbackRejected("content_base64 is not valid base64") from exc
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise FeedbackRejected("upload must be 1 byte to 8 MiB")
        digest = hashlib.sha256(data).hexdigest()
        if not isinstance(declared_sha256, str) or declared_sha256.casefold() != digest:
            raise FeedbackRejected("declared SHA-256 does not match the uploaded bytes")
        if not isinstance(filename, str) or not re.fullmatch(r"[\w\u4e00-\u9fff .()-]{1,120}\.(json|html?|zip)", filename, re.I):
            raise FeedbackRejected("filename must be a plain .json/.html/.zip name")
        parsed = parse_upload(filename, data)
        manifest = parsed["manifest"]
        claim_id = manifest["claim_id"]
        if not self.claims.exists(claim_id):
            raise FeedbackRejected("claim_id does not reference an existing Claim dossier")
        feedback_id = "fb-" + digest[:32]
        existing = self.db.execute("SELECT * FROM feedback_uploads WHERE bytes_sha256=?", (digest,)).fetchone()
        if existing is not None:
            return {"status": "duplicate", "feedback_id": existing["feedback_id"], "claim_id": existing["claim_id"],
                    "tested_by_linked": True}
        with self.db:
            self.db.execute("INSERT INTO feedback_uploads VALUES(?,?,?,?,?,?,?,?,?)",
                            (feedback_id, claim_id, filename, parsed["format"], digest, len(data),
                             json.dumps(manifest["results"], ensure_ascii=False), parsed["verified_files"], self.clock()))
        self.claims.link_feedback(claim_id, feedback_id, clock=self.clock)
        return {"status": "accepted", "feedback_id": feedback_id, "claim_id": claim_id, "format": parsed["format"],
                "results": len(manifest["results"]), "verified_files": parsed["verified_files"],
                "bytes_sha256": digest, "relation": "tested_by", "tested_by_linked": True}

    def list_for_claim(self, claim_id: str) -> list[dict[str, Any]]:
        return [{**dict(r), "results": json.loads(r["results_json"])} for r in self.db.execute(
            "SELECT * FROM feedback_uploads WHERE claim_id=? ORDER BY uploaded_at", (claim_id,))]
