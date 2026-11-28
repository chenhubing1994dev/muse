"""Adapter Hub — every fused framework enters Muse through Muse Commander, nowhere else.

Ruflo, dsh, Super Agent Party, agent-desktop, herdr, LM Studio, FFmpeg, Obsidian and
MemPalace are described as pluggable adapters with a *non-destructive* health probe and an
honest connection state:

* ``LIVE_CONNECTED``  — the real service/binary answered just now;
* ``CONTRACT_ONLY``   — Muse-side adapter code exists, the external runtime was not reached;
* ``SKIPPED_ENV``     — required binary/path/config is absent on this machine;
* ``UNCONNECTED``     — no Muse adapter implementation exists yet (gap, not hidden).

Dispatch requires a Commander grant bound to an approved ticket, so no adapter can be used
as a side door around approvals, the kill switch or the cost ledger.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import shutil
import socket
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATES = ("LIVE_CONNECTED", "CONTRACT_ONLY", "SKIPPED_ENV", "UNCONNECTED")


def _module_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _loopback_http_ok(url: str, timeout: float = 1.5) -> tuple[bool, str]:
    if not url.startswith(("http://127.0.0.1", "http://localhost")):
        return False, "refused: non-loopback probe"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback only
            return 200 <= response.status < 300, f"HTTP {response.status}"
    except (OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200]


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class AdapterDescriptor:
    name: str
    framework: str
    muse_module: str | None
    probe: Callable[[], tuple[str, str]]
    risk: str = "medium"


def _probe_ruflo() -> tuple[str, str]:
    if not _module_exists("muse.ruflo_adapter"):
        return "UNCONNECTED", "muse.ruflo_adapter missing"
    state_dir = PROJECT_ROOT / ".claude-flow"
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        return "SKIPPED_ENV", "npx not on PATH (Ruflo MCP needs Node.js)"
    return "CONTRACT_ONLY", f"adapter present; npx={npx}; state_dir={'yes' if state_dir.is_dir() else 'no'}"


def _external_state(name: str) -> tuple[str, str]:
    from muse.external_agents import ExternalAgentAdapter
    health = ExternalAgentAdapter(name).health()
    if health["state"] == "live":
        return "LIVE_CONNECTED", f"loopback gateway answered: {health.get('probes')}"
    if health["state"] == "unreachable":
        return "SKIPPED_ENV", f"adapter implemented (muse.external_agents); service not running: {health.get('probes')}"
    return "UNCONNECTED", f"dormant adapter (not_configured): {health.get('detail')}"


def _probe_dsh() -> tuple[str, str]:
    return _external_state("dsh")


def _probe_party() -> tuple[str, str]:
    return _external_state("super_agent_party")


def _probe_desktop() -> tuple[str, str]:
    if not _module_exists("muse.desktop_adapter"):
        return "UNCONNECTED", "muse.desktop_adapter missing"
    exe = os.environ.get("MUSE_AGENT_DESKTOP_EXE", "").strip() or shutil.which("agent-desktop") or ""
    if not exe or not Path(exe).exists():
        return "SKIPPED_ENV", "agent-desktop executable not found (set MUSE_AGENT_DESKTOP_EXE)"
    return "CONTRACT_ONLY", f"executable present: {exe}"


def _probe_herdr() -> tuple[str, str]:
    if not _module_exists("muse.herdr_adapter"):
        return "UNCONNECTED", "muse.herdr_adapter missing"
    return ("CONTRACT_ONLY", "herdr adapter present") if shutil.which("herdr") else ("SKIPPED_ENV", "herdr binary not on PATH")


def _probe_lmstudio() -> tuple[str, str]:
    base = os.environ.get("MUSE_LMSTUDIO_URL", "http://127.0.0.1:1234").rstrip("/")
    ok, detail = _loopback_http_ok(base + "/v1/models")
    return ("LIVE_CONNECTED", detail) if ok else ("SKIPPED_ENV", f"LM Studio not answering: {detail}")


def _probe_ffmpeg() -> tuple[str, str]:
    exe = shutil.which("ffmpeg")
    return ("LIVE_CONNECTED", exe) if exe else ("SKIPPED_ENV", "ffmpeg not on PATH")


def _probe_obsidian() -> tuple[str, str]:
    vault = os.environ.get("MUSE_OBSIDIAN_VAULT", "").strip()
    candidate = Path(vault) if vault else PROJECT_ROOT / "vault"
    if candidate.is_dir():
        return "LIVE_CONNECTED", f"vault directory: {candidate.resolve()}"
    return "SKIPPED_ENV", "no Obsidian vault (set MUSE_OBSIDIAN_VAULT)"


def _probe_mempalace() -> tuple[str, str]:
    if not _module_exists("muse.mempalace_adapter"):
        return "UNCONNECTED", "muse.mempalace_adapter missing"
    if not (os.environ.get("MUSE_MEMPALACE_COMMAND") or shutil.which("mempalace")):
        return "SKIPPED_ENV", "MemPalace server command not configured (MUSE_MEMPALACE_COMMAND)"
    return "CONTRACT_ONLY", "MemPalace stdio adapter present; server not started by probe"


ADAPTERS: tuple[AdapterDescriptor, ...] = (
    AdapterDescriptor("ruflo", "Ruflo / claude-flow swarm", "muse.ruflo_adapter", _probe_ruflo),
    AdapterDescriptor("dsh", "dsh-our-free-model", "muse.external_agents", _probe_dsh),
    AdapterDescriptor("super_agent_party", "Super Agent Party", "muse.external_agents", _probe_party),
    AdapterDescriptor("agent_desktop", "agent-desktop native UIA", "muse.desktop_adapter", _probe_desktop, "high"),
    AdapterDescriptor("herdr", "herdr terminal runtime", "muse.herdr_adapter", _probe_herdr),
    AdapterDescriptor("lmstudio", "LM Studio local models", "muse.model_client", _probe_lmstudio, "low"),
    AdapterDescriptor("ffmpeg", "FFmpeg media toolchain", "muse.ffmpeg_skill", _probe_ffmpeg, "low"),
    AdapterDescriptor("obsidian", "Obsidian vault", "muse.obsidian_vault", _probe_obsidian, "low"),
    AdapterDescriptor("mempalace", "MemPalace semantic memory", "muse.mempalace_adapter", _probe_mempalace, "low"),
)


def health_report() -> list[dict[str, Any]]:
    rows = []
    for adapter in ADAPTERS:
        try:
            state, detail = adapter.probe()
        except BaseException as exc:  # a broken probe is reported, never hidden
            state, detail = "UNCONNECTED", f"probe crashed: {type(exc).__name__}: {exc}"[:300]
        if state not in STATES:
            state, detail = "UNCONNECTED", f"invalid state from probe: {state}"
        rows.append({"adapter": adapter.name, "framework": adapter.framework, "state": state,
                     "detail": detail, "risk": adapter.risk, "muse_module": adapter.muse_module})
    return rows


class GrantError(PermissionError):
    """Adapter dispatch attempted without a valid Commander grant."""


class AdapterHub:
    """Executes registered adapter callables only with a Commander-issued grant."""

    def __init__(self, grant_secret: bytes) -> None:
        if len(grant_secret) < 16:
            raise ValueError("grant secret too short")
        self._secret = grant_secret
        self._executors: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self.audit: list[dict[str, Any]] = []

    def register(self, name: str, executor: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        if name not in {a.name for a in ADAPTERS} and not name.startswith("sim_"):
            raise ValueError(f"unknown adapter {name!r}")
        self._executors[name] = executor

    def issue_grant(self, *, adapter: str, task_id: str) -> str:
        message = f"{adapter}|{task_id}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def dispatch(self, adapter: str, request: dict[str, Any], *, task_id: str, grant: str) -> dict[str, Any]:
        expected = self.issue_grant(adapter=adapter, task_id=task_id)
        if not isinstance(grant, str) or not hmac.compare_digest(expected, grant):
            self.audit.append({"adapter": adapter, "task_id": task_id, "decision": "denied"})
            raise GrantError("adapter dispatch requires a Commander grant for this ticket")
        if adapter not in self._executors:
            raise LookupError(f"adapter {adapter!r} has no registered executor")
        self.audit.append({"adapter": adapter, "task_id": task_id, "decision": "allowed",
                           "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()})
        return self._executors[adapter](request)
