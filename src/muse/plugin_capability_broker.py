"""B394 WS4 plugin sandbox: Capability Broker (deny-by-default) + supply-chain lock verification.

The plugin registry stores declarative manifests only. The broker is the *single* door through
which a plugin capability may ever obtain a permission at runtime:

* ``issue_grant`` succeeds only when the plugin is ``enabled``, was explicitly approved, carries a
  ``verified`` publisher Ed25519 signature for the exact manifest hash, the capability exists in
  that manifest, and every requested permission is both declared and within the risk ceiling.
* Grants are short-lived bearer tokens (only their SHA-256 is stored), bound to one capability,
  revocable, and metered against the risk-class resource budget — the budget becomes enforced.
* ``authorize`` fails closed on *any* doubt: unknown/expired/revoked token, plugin no longer
  enabled, manifest or signature changed since issue, undeclared permission, or budget exceeded.
* ``verify_supply_chain`` checks a lock file of SHA-256 digests for every bundled artifact,
  refusing traversal, symlinks, absolute paths, duplicates and oversize files.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import stat
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from muse.plugin_platform import RISK_PERMISSION_CEILINGS, RISK_RESOURCE_BUDGETS, PluginError, PluginRegistry

GRANT_TTL_SECONDS = 300
METERED_KEYS = ("cpu_seconds", "wall_time_seconds", "max_output_bytes", "max_network_requests",
                "max_filesystem_write_bytes")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class CapabilityDenied(PermissionError):
    """The broker refused a capability grant or use (fail closed)."""


class CapabilityBroker:
    def __init__(self, registry: PluginRegistry, database: str | Path, *,
                 clock: Callable[[], float] = time.time, ttl_seconds: int = GRANT_TTL_SECONDS) -> None:
        if not isinstance(registry, PluginRegistry):
            raise TypeError("registry must be a trusted PluginRegistry")
        self.registry = registry
        self.clock = clock
        self.ttl = int(ttl_seconds)
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_grants(
                grant_id TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL UNIQUE, plugin_id TEXT NOT NULL,
                capability_id TEXT NOT NULL, permissions_json TEXT NOT NULL, risk TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL, signature_sha256 TEXT NOT NULL, budget_json TEXT NOT NULL,
                usage_json TEXT NOT NULL DEFAULT '{}', issued_by TEXT NOT NULL, issued_at REAL NOT NULL,
                expires_at REAL NOT NULL, revoked_at REAL);
            CREATE TABLE IF NOT EXISTS capability_broker_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT, plugin_id TEXT NOT NULL, event TEXT NOT NULL,
                detail_json TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TRIGGER IF NOT EXISTS capability_broker_audit_no_update BEFORE UPDATE ON capability_broker_audit
                BEGIN SELECT RAISE(ABORT,'broker audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS capability_broker_audit_no_delete BEFORE DELETE ON capability_broker_audit
                BEGIN SELECT RAISE(ABORT,'broker audit is append-only'); END;
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "CapabilityBroker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _audit(self, plugin_id: str, event: str, **detail: Any) -> None:
        self.db.execute("INSERT INTO capability_broker_audit(plugin_id,event,detail_json,created_at) VALUES(?,?,?,?)",
                        (plugin_id, event, json.dumps(detail, ensure_ascii=False, default=str)[:4000], self.clock()))

    def _deny(self, plugin_id: str, reason: str) -> CapabilityDenied:
        with self.db:
            self._audit(plugin_id or "-", "denied", reason=reason)
        return CapabilityDenied(reason)

    def _current_plugin(self, plugin_id: str) -> dict[str, Any]:
        try:
            plugin = self.registry.get(plugin_id)
        except PluginError as exc:
            raise self._deny(plugin_id, "plugin not found") from exc
        if plugin["status"] != "enabled" or not plugin.get("approved_by"):
            raise self._deny(plugin_id, "plugin is not approved and enabled")
        signature = plugin.get("signature") or {}
        if signature.get("status") != "verified" or not _HEX64.fullmatch(str(signature.get("artifact_sha256", ""))):
            raise self._deny(plugin_id, "plugin has no verified publisher signature for this manifest")
        return plugin

    def issue_grant(self, plugin_id: str, capability_id: str, permissions: list[str], *, issued_by: str) -> dict[str, Any]:
        plugin = self._current_plugin(plugin_id)
        capability = next((c for c in plugin["manifest"]["capabilities"] if c["id"] == capability_id), None)
        if capability is None:
            raise self._deny(plugin_id, "capability is not declared in the signed manifest")
        if not isinstance(permissions, list) or not permissions or len(set(permissions)) != len(permissions):
            raise self._deny(plugin_id, "permissions must be a non-empty unique list")
        requested = set(permissions)
        if not requested <= set(capability["permissions"]):
            raise self._deny(plugin_id, "requested permission was not declared by the capability")
        if not requested <= RISK_PERMISSION_CEILINGS[capability["risk"]]:
            raise self._deny(plugin_id, "requested permission exceeds the risk ceiling")
        token = secrets.token_urlsafe(32)
        grant_id = secrets.token_hex(16)
        now = self.clock()
        budget = {k: v for k, v in RISK_RESOURCE_BUDGETS[capability["risk"]].items()}
        with self.db:
            self.db.execute(
                "INSERT INTO capability_grants(grant_id,token_sha256,plugin_id,capability_id,permissions_json,risk,"
                "manifest_sha256,signature_sha256,budget_json,issued_by,issued_at,expires_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (grant_id, hashlib.sha256(token.encode()).hexdigest(), plugin_id, capability_id,
                 json.dumps(sorted(requested)), capability["risk"], plugin["manifest_sha256"],
                 plugin["signature"]["signature_sha256"], json.dumps(budget), str(issued_by)[:120], now, now + self.ttl))
            self._audit(plugin_id, "grant_issued", grant_id=grant_id, capability_id=capability_id,
                        permissions=sorted(requested))
        return {"grant_id": grant_id, "token": token, "expires_at": now + self.ttl, "budget": budget,
                "runtime_enforced": True}

    def authorize(self, token: str, permission: str, *, usage: dict[str, float] | None = None) -> dict[str, Any]:
        try:
            if not isinstance(token, str) or not 20 <= len(token) <= 200:
                raise CapabilityDenied("token malformed")
            row = self.db.execute("SELECT * FROM capability_grants WHERE token_sha256=?",
                                  (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            if row is None:
                raise CapabilityDenied("unknown grant")
            plugin_id = row["plugin_id"]
            if row["revoked_at"] is not None:
                raise self._deny(plugin_id, "grant revoked")
            if self.clock() >= row["expires_at"]:
                raise self._deny(plugin_id, "grant expired")
            plugin = self._current_plugin(plugin_id)
            if plugin["manifest_sha256"] != row["manifest_sha256"] \
                    or plugin["signature"].get("signature_sha256") != row["signature_sha256"]:
                raise self._deny(plugin_id, "plugin manifest or signature changed since grant was issued")
            if permission not in json.loads(row["permissions_json"]):
                raise self._deny(plugin_id, f"permission not granted: {permission}")
            budget, used = json.loads(row["budget_json"]), json.loads(row["usage_json"] or "{}")
            usage = usage or {}
            for key, amount in usage.items():
                if key not in METERED_KEYS or isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
                    raise self._deny(plugin_id, f"invalid usage metric: {key}")
                if used.get(key, 0) + amount > budget.get(key, 0):
                    raise self._deny(plugin_id, f"resource budget exceeded: {key}")
            if permission == "gpu.request" and not budget.get("gpu_allowed"):
                raise self._deny(plugin_id, "GPU not permitted for this risk class")
            for key, amount in usage.items():
                used[key] = used.get(key, 0) + amount
            with self.db:
                self.db.execute("UPDATE capability_grants SET usage_json=? WHERE grant_id=?",
                                (json.dumps(used), row["grant_id"]))
                self._audit(plugin_id, "authorized", grant_id=row["grant_id"], permission=permission, usage=usage)
            return {"allowed": True, "grant_id": row["grant_id"], "usage": used}
        except CapabilityDenied:
            raise
        except Exception as exc:  # fail closed on every unexpected condition
            raise CapabilityDenied(f"broker failure: {type(exc).__name__}") from exc

    def revoke_plugin(self, plugin_id: str, *, actor: str) -> int:
        with self.db:
            cursor = self.db.execute("UPDATE capability_grants SET revoked_at=? WHERE plugin_id=? AND revoked_at IS NULL",
                                     (self.clock(), plugin_id))
            self._audit(plugin_id, "revoked", actor=actor, grants=cursor.rowcount)
        return cursor.rowcount

    def audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM capability_broker_audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))]


def verify_supply_chain(lock: dict[str, Any], bundle_root: str | Path, *, max_file_bytes: int = 64 * 1024 * 1024) -> dict[str, Any]:
    """Verify ``{"schema":"muse.plugin.lock.v1","artifacts":[{"path","sha256","size"}]}`` against files."""
    if not isinstance(lock, dict) or lock.get("schema") != "muse.plugin.lock.v1" \
            or not isinstance(lock.get("artifacts"), list) or not 1 <= len(lock["artifacts"]) <= 512:
        raise PluginError("supply-chain lock schema is invalid")
    root = Path(bundle_root).resolve(strict=True)
    seen: set[str] = set()
    verified = []
    for item in lock["artifacts"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise PluginError("lock artifact entry is invalid")
        relative = str(item["path"]).replace("\\", "/")
        pure = PurePosixPath(relative)
        if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative) or any(p in {"", ".", ".."} for p in relative.split("/")):
            raise PluginError("lock artifact path must be relative without traversal")
        if relative.casefold() in seen:
            raise PluginError("duplicate lock artifact path")
        seen.add(relative.casefold())
        if not isinstance(item["sha256"], str) or _HEX64.fullmatch(item["sha256"]) is None:
            raise PluginError("lock artifact digest must be lowercase SHA-256")
        cursor = root
        for part in pure.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise PluginError("lock artifact path contains a symlink")
        target = cursor.resolve(strict=False)
        if root not in target.parents or not target.is_file():
            raise PluginError(f"lock artifact missing or outside bundle: {relative}")
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != item["size"] or info.st_size > max_file_bytes:
            raise PluginError(f"lock artifact size mismatch: {relative}")
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise PluginError(f"lock artifact SHA-256 mismatch: {relative}")
        verified.append(relative)
    unlisted = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                      if p.is_file() and p.relative_to(root).as_posix().casefold() not in seen
                      and p.relative_to(root).as_posix() != "plugin.lock.json")
    if unlisted:
        raise PluginError(f"bundle contains files not pinned by the lock: {unlisted[:5]}")
    return {"status": "verified", "artifacts": len(verified)}
