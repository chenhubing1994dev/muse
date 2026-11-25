"""Signed Review Desk: one-click Ed25519 review of Commander tickets from the dashboard.

Independence model
------------------
* The Reviewer private key is generated on this PC and stored ONLY encrypted, outside
  the Muse repository (default ``%USERPROFILE%/MuseReviewerKeys``). Muse's
  self-evolution engine is confined to the workspace and cannot read or rewrite it.
* Every signature requires the creator's passphrase, typed in the dashboard at click
  time. The passphrase is never stored; Muse processes holding the API token still
  cannot approve their own work without it.
* Encryption is stdlib only: scrypt(N=2**15, r=8, p=1) key derivation, HMAC-SHA256
  counter-mode keystream, encrypt-then-MAC with HMAC-SHA256 (constant-time compare).
* Repeated wrong passphrases lock signing for 60 seconds (in-process throttle on top of
  the scrypt cost).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from muse.reviewer_attestation import (
    ReviewAttestationError,
    ReviewAttestationVerifier,
    active_backend,
    build_trust_record,
    canonical_json,
    create_attestation,
    load_trust_store,
    public_key_from_seed,
    render_trust_store,
    sha256_hex,
)
from muse.task_queue import TaskQueue

VAULT_SCHEMA = "muse-review-desk-vault-v1"
CONFIG_SCHEMA = "muse-review-desk-config-v1"
KEY_FILE = "reviewer-key.enc.json"
TRUST_FILE = "reviewer-trust-store.json"
CONFIG_FILE = "desk-config.json"
REVIEWER_ID = "creator"
PROPOSER_DEFAULT = "muse-worker"
MIN_PASSPHRASE = 10
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
ARTIFACT_PREVIEW_BYTES = 24 * 1024


class ReviewDeskError(RuntimeError):
    """User-facing failure; message is safe to show in the dashboard."""


def default_vault_dir() -> Path:
    override = os.environ.get("MUSE_REVIEWER_VAULT_DIR", "").strip()
    return Path(override) if override else Path.home() / "MuseReviewerKeys"


def _derive(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    material = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=SCRYPT["n"], r=SCRYPT["r"],
                              p=SCRYPT["p"], maxmem=64 * 1024 * 1024, dklen=64)
    return material[:32], material[32:]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def seal_seed(seed: bytes, passphrase: str) -> dict[str, str]:
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    enc_key, mac_key = _derive(passphrase, salt)
    ciphertext = bytes(a ^ b for a, b in zip(seed, _keystream(enc_key, nonce, len(seed))))
    tag = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).hexdigest()
    return {"salt": salt.hex(), "nonce": nonce.hex(), "ciphertext": ciphertext.hex(), "tag": tag}


def open_seed(sealed: dict[str, str], passphrase: str) -> bytes:
    try:
        salt, nonce = bytes.fromhex(sealed["salt"]), bytes.fromhex(sealed["nonce"])
        ciphertext, tag = bytes.fromhex(sealed["ciphertext"]), sealed["tag"]
    except (KeyError, ValueError, TypeError) as exc:
        raise ReviewDeskError("审查密钥文件已损坏") from exc
    enc_key, mac_key = _derive(passphrase, salt)
    expected = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(tag)):
        raise ReviewDeskError("口令不正确")
    return bytes(a ^ b for a, b in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext))))


class ReviewerVault:
    """Creator-held, passphrase-sealed Reviewer key plus its pinned trust store."""

    def __init__(self, directory: str | Path | None = None, *, workspace: str | Path | None = None,
                 clock=time.monotonic):
        self.directory = Path(directory) if directory is not None else default_vault_dir()
        self.workspace = Path(workspace).resolve() if workspace is not None else None
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: list[float] = []
        self._locked_until = 0.0

    def _inside_workspace(self) -> bool:
        if self.workspace is None:
            return False
        try:
            self.directory.resolve().relative_to(self.workspace)
        except ValueError:
            return False
        return True

    def status(self) -> dict[str, Any]:
        initialized = (self.directory / KEY_FILE).is_file() and (self.directory / CONFIG_FILE).is_file()
        info: dict[str, Any] = {"initialized": initialized, "vault_dir": str(self.directory),
                                "backend": active_backend(), "reviewer_id": REVIEWER_ID}
        if initialized:
            try:
                key = json.loads((self.directory / KEY_FILE).read_text(encoding="utf-8"))
                info.update({"key_id": key.get("key_id"), "fingerprint_sha256": key.get("fingerprint_sha256"),
                             "created_at": key.get("created_at")})
                self.trusted_keys()
                info["trust_store_ok"] = True
            except (OSError, ValueError, ReviewAttestationError) as exc:
                info.update({"trust_store_ok": False, "error": str(exc)[:200]})
        return info

    def initialize(self, passphrase: str, *, confirm_passphrase: str) -> dict[str, Any]:
        if self._inside_workspace():
            raise ReviewDeskError("审查密钥目录不能位于 Muse 项目目录内")
        if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE or len(passphrase) > 256:
            raise ReviewDeskError(f"口令至少 {MIN_PASSPHRASE} 个字符")
        if passphrase != confirm_passphrase:
            raise ReviewDeskError("两次输入的口令不一致")
        if (self.directory / KEY_FILE).exists():
            raise ReviewDeskError("审查密钥已存在；如需更换请先在密钥目录中备份并移走旧文件")
        self.directory.mkdir(parents=True, exist_ok=True)
        seed = secrets.token_bytes(32)
        public = public_key_from_seed(seed)
        key_id = "rk-desk-" + time.strftime("%Y%m%d") + "-" + secrets.token_hex(4)
        record = build_trust_record(key_id=key_id, reviewer_id=REVIEWER_ID, public_key=public, purpose="production")
        trust_bytes = render_trust_store([record])
        key_doc = {"schema": VAULT_SCHEMA, "key_id": key_id, "reviewer_id": REVIEWER_ID,
                   "public_key_hex": public.hex(), "fingerprint_sha256": record["fingerprint_sha256"],
                   "kdf": {"name": "scrypt", **SCRYPT}, "sealed_seed": seal_seed(seed, passphrase),
                   "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        with (self.directory / KEY_FILE).open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(key_doc, indent=2) + "\n")
        (self.directory / TRUST_FILE).write_bytes(trust_bytes)
        config = {"schema": CONFIG_SCHEMA, "trust_store": str((self.directory / TRUST_FILE).resolve()),
                  "trust_store_sha256": sha256_hex(trust_bytes)}
        (self.directory / CONFIG_FILE).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        return self.status()

    def trusted_keys(self) -> dict[str, dict[str, Any]]:
        try:
            config = json.loads((self.directory / CONFIG_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReviewDeskError("尚未初始化审查密钥") from exc
        if not isinstance(config, dict) or config.get("schema") != CONFIG_SCHEMA:
            raise ReviewDeskError("审查密钥配置无效")
        return load_trust_store(config.get("trust_store", ""), config.get("trust_store_sha256", ""))

    def _check_lockout(self) -> None:
        now = self.clock()
        if now < self._locked_until:
            raise ReviewDeskError(f"口令错误次数过多，请 {int(self._locked_until - now) + 1} 秒后再试")

    def _record_failure(self) -> None:
        now = self.clock()
        self._failures = [stamp for stamp in self._failures if now - stamp < LOCKOUT_SECONDS] + [now]
        if len(self._failures) >= MAX_FAILURES:
            self._locked_until = now + LOCKOUT_SECONDS
            self._failures = []

    def sign(self, passphrase: str, binding: dict[str, str], verdict: str, findings: list[str]) -> dict[str, Any]:
        with self._lock:
            self._check_lockout()
            try:
                key = json.loads((self.directory / KEY_FILE).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ReviewDeskError("尚未初始化审查密钥") from exc
            if not isinstance(passphrase, str) or not passphrase:
                raise ReviewDeskError("请输入审查口令")
            try:
                seed = open_seed(key.get("sealed_seed", {}), passphrase)
            except ReviewDeskError:
                self._record_failure()
                raise
            self._failures = []
        if public_key_from_seed(seed).hex() != key.get("public_key_hex"):
            raise ReviewDeskError("审查密钥文件与公钥不匹配")
        return create_attestation(seed=seed, key_id=key["key_id"], reviewer_id=key["reviewer_id"],
                                  binding=binding, verdict=verdict, findings=findings, ttl_seconds=300)


def _preview(task: dict[str, Any]) -> dict[str, Any]:
    artifact = canonical_json(task["payload"].get("_result"))
    evidence = str(task["payload"].get("_test_evidence") or "")
    review = task["payload"].get("_signed_review") or {}
    binding = review.get("binding") or {}
    return {
        "task_id": task["id"], "title": task["title"], "kind": task["payload"].get("kind"),
        "status": task["status"], "review_status": review.get("status"),
        "requested_at": review.get("requested_at"), "binding": binding,
        "artifact_preview": artifact[:ARTIFACT_PREVIEW_BYTES].decode("utf-8", errors="replace"),
        "artifact_truncated": len(artifact) > ARTIFACT_PREVIEW_BYTES,
        "artifact_bytes": len(artifact),
        "test_evidence_preview": evidence[:8000], "test_evidence_truncated": len(evidence) > 8000,
        "hashes_match": (sha256_hex(artifact) == binding.get("artifact_sha256")
                         and sha256_hex(evidence.encode("utf-8")) == binding.get("test_evidence_sha256")),
    }


def list_pending(task_db: str | Path, *, limit: int = 100) -> list[dict[str, Any]]:
    with TaskQueue(task_db) as queue:
        return [{key: value for key, value in _preview(task).items()
                 if key not in {"artifact_preview", "test_evidence_preview"}}
                for task in queue.pending_signed_reviews(limit=limit)]


def detail(task_db: str | Path, task_id: str) -> dict[str, Any] | None:
    with TaskQueue(task_db) as queue:
        task = queue.get(task_id)
    if task is None or not isinstance(task["payload"].get("_signed_review"), dict):
        return None
    return _preview(task)


def decide(task_db: str | Path, task_id: str, *, verdict: str, passphrase: str, vault: ReviewerVault,
           receipts_db: str | Path, note: str = "") -> dict[str, Any]:
    if verdict not in {"approve", "reject"}:
        raise ReviewDeskError("verdict 只能是 approve 或 reject")
    if not isinstance(note, str) or len(note) > 500:
        raise ReviewDeskError("备注最多 500 字")
    with TaskQueue(task_db) as queue:
        task = queue.get(task_id)
        if task is None:
            raise ReviewDeskError("票据不存在")
        review = task["payload"].get("_signed_review")
        if task["status"] != "waiting_approval" or not isinstance(review, dict) or review.get("status") != "pending":
            raise ReviewDeskError("该票据不在待签名状态")
        if not _preview(task)["hashes_match"]:
            raise ReviewDeskError("票据结果或测试日志在停放后被改动，拒绝签名")
        trusted = vault.trusted_keys()
        attestation = vault.sign(passphrase, dict(review["binding"]), verdict, [note] if note else [])
        with ReviewAttestationVerifier(trusted, receipts_db, require_production=True) as verifier:
            completed = queue.complete_signed_review(task_id, attestation=attestation, verifier=verifier,
                                                     actor="review-desk")
    signed = completed["payload"].get("_signed_review") or {}
    return {"task_id": task_id, "status": completed["status"], "review_status": signed.get("status"),
            "key_id": signed.get("key_id"), "attestation_sha256": signed.get("attestation_sha256")}
