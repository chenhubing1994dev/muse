"""Safe bridge to an installed Ruflo CLI/MCP engine.

Muse keeps its durable task and approval ledger; Ruflo supplies swarm, agent,
memory and learning tools. Calls use argv (never a shell) and a strict tool
allowlist. The adapter does not copy or vendor Ruflo implementation code.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

from muse.model_client import LMStudioClient

_READ_ONLY_TOOLS = frozenset({
    "system_status", "system_health", "system_info", "swarm_status",
    "swarm_health", "agent_list", "agent_status", "agent_health",
    "memory_search", "memory_retrieve", "memory_stats",
    "neural_status", "neural_patterns", "autopilot_status",
    "autopilot_progress", "autopilot_history", "mission_get",
    "mission_events", "team_status", "coordination_metrics",
})
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_NEVER_ALLOW = frozenset({"system_reset", "terminal_execute", "terminal_create", "terminal_close", "browser_act", "mcp_stop"})
MAX_PARAMS_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 128 * 1024

def load_muse_model_config(workspace: str | Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Load non-secret model routing from config/muse.toml with environment overrides."""
    env = os.environ if environ is None else environ
    config_path = Path(workspace) / "config" / "muse.toml"
    models: dict[str, Any] = {}
    if config_path.is_file():
        try:
            parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("config/muse.toml is unreadable or invalid TOML") from exc
        section = parsed.get("models", {})
        if not isinstance(section, dict):
            raise ValueError("[models] must be a TOML table")
        models = section
    raw_provider = env.get("MUSE_RUFLO_PROVIDER") or models.get("provider") or ""
    if not isinstance(raw_provider, str):
        raise ValueError("[models].provider must be a string")
    provider = raw_provider.strip().casefold()
    if provider not in {"", "lmstudio", "llama_cpp", "chatanywhere", "ollama", "anthropic", "anthropic_messages"}:
        raise ValueError("unsupported Muse model provider")
    default_url = "http://127.0.0.1:1235/v1" if provider == "llama_cpp" else "http://127.0.0.1:1234/v1"
    url_override = env.get("MUSE_LLAMA_CPP_URL") if provider == "llama_cpp" else env.get("MUSE_LMSTUDIO_URL")
    model_override = env.get("MUSE_LLAMA_CPP_MODEL") if provider == "llama_cpp" else env.get("MUSE_LMSTUDIO_MODEL")
    base_url = url_override or models.get("base_url") or default_url
    model = model_override or models.get("model") or ""
    if not isinstance(base_url, str) or not isinstance(model, str):
        raise ValueError("[models].base_url and [models].model must be strings")
    return {"provider": provider, "base_url": base_url, "model": model}



def resolve_lmstudio_ruflo_config(environ: dict[str, str] | None = None) -> tuple[str, str]:
    """Resolve a loopback LM Studio OpenAI endpoint and a model Ruflo can call.

    Ruflo's Ollama-compatible executor accepts OpenAI chat-completions endpoints.
    This bridge is deliberately explicit and local-only; it never infers a cloud
    provider or invents an API key.
    """
    env = os.environ if environ is None else environ
    raw_url = (env.get("MUSE_LLAMA_CPP_URL") or env.get("MUSE_LMSTUDIO_URL") or "http://127.0.0.1:1234/v1").strip().rstrip("/")
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname
        if (parsed.scheme != "http" or not host or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError
        _ = parsed.port
        if not (host.casefold() == "localhost" or ipaddress.ip_address(host).is_loopback):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("local OpenAI-compatible model URL must be a credential-free HTTP loopback URL") from None
    api_url = raw_url if parsed.path.rstrip("/").endswith("/v1") else raw_url + "/v1"
    models = LMStudioClient(api_url, timeout=3.0).list_models()
    model_ids = [item.get("id") for item in models if isinstance(item, dict)
                 and isinstance(item.get("id"), str) and item.get("id")]
    if not model_ids:
        raise ValueError("local OpenAI-compatible server has no models available for Ruflo")
    configured_model = (env.get("MUSE_LLAMA_CPP_MODEL") or env.get("MUSE_LMSTUDIO_MODEL") or "").strip()
    if configured_model and configured_model not in model_ids:
        raise ValueError("configured local model is not currently exposed by the server")
    selected_model = configured_model or model_ids[0]
    return api_url[:-3], selected_model


DEFAULT_RUFLO_CLI = Path(r"D:\Tools\Ruflo\npx-cache\node_modules\ruflo\bin\ruflo.js")

class RufloError(RuntimeError):
    """Ruflo CLI/MCP call failed or returned an invalid response."""


@dataclass(frozen=True)
class RufloResult:
    tool: str
    result: Any
    raw_output: str


class RufloMCPAdapter:
    """Invoke pinned Ruflo MCP tools through its supported CLI bridge."""
    @classmethod
    def from_installed(
        cls, *, workspace: str | Path, node_path: str | None = None,
        cli_path: str | Path | None = None, timeout_seconds: float = 60.0,
        enabled_tools: Sequence[str] = (),
    ) -> "RufloMCPAdapter":
        """Resolve the pinned D-drive installation, with explicit environment overrides."""
        node = node_path or os.environ.get("MUSE_RUFLO_NODE") or shutil.which("node")
        cli = Path(cli_path or os.environ.get("MUSE_RUFLO_CLI") or DEFAULT_RUFLO_CLI)
        if not node:
            raise FileNotFoundError("Node.js was not found; set MUSE_RUFLO_NODE")
        if not cli.is_file():
            raise FileNotFoundError("Ruflo CLI was not found: " + str(cli))
        return cls((node, str(cli)), workspace=workspace, timeout_seconds=timeout_seconds,
                   enabled_tools=enabled_tools)


    def __init__(
        self,
        command_prefix: Sequence[str],
        *,
        workspace: str | Path,
        timeout_seconds: float = 60.0,
        enabled_tools: Sequence[str] = (),
    ) -> None:
        if not command_prefix or any(not isinstance(x, str) or not x.strip() for x in command_prefix):
            raise ValueError("command_prefix must contain executable and fixed arguments")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 1 and 600")
        if any(not isinstance(x, str) or not _TOOL_NAME.fullmatch(x) for x in enabled_tools):
            raise ValueError("enabled_tools contains an invalid tool name")
        self.command_prefix = tuple(command_prefix)
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.timeout_seconds = float(timeout_seconds)
        self.enabled_tools = frozenset(enabled_tools)

    @property
    def allowed_tools(self) -> frozenset[str]:
        return _READ_ONLY_TOOLS | self.enabled_tools

    def _run(self, args: Sequence[str], *, timeout: float | None = None) -> str:
        child_env = os.environ.copy()
        chatanywhere_key = child_env.pop("CHATANYWHERE_API_KEY", None)
        chatanywhere_model = child_env.pop("CHATANYWHERE_MODEL", None)
        chatanywhere_base_url = child_env.pop("CHATANYWHERE_BASE_URL", None)
        requested_provider = child_env.get("MUSE_RUFLO_PROVIDER", "").strip().casefold()
        if not requested_provider:
            try:
                requested_provider = load_muse_model_config(self.workspace, child_env)["provider"]
            except ValueError as exc:
                raise RufloError("Muse model configuration is invalid: " + type(exc).__name__) from exc
        is_agent_execute = len(args) >= 4 and tuple(args[:3]) == ("mcp", "exec", "-t") and args[3] == "agent_execute"
        if requested_provider in {"lmstudio", "llama_cpp"} and is_agent_execute:
            try:
                local_settings = load_muse_model_config(self.workspace, child_env)
                local_env = dict(child_env)
                local_env["MUSE_LLAMA_CPP_URL" if requested_provider == "llama_cpp" else "MUSE_LMSTUDIO_URL"] = local_settings["base_url"]
                if local_settings["model"]:
                    local_env["MUSE_LLAMA_CPP_MODEL" if requested_provider == "llama_cpp" else "MUSE_LMSTUDIO_MODEL"] = local_settings["model"]
                local_base_url, local_model = resolve_lmstudio_ruflo_config(local_env)
            except Exception as exc:
                raise RufloError("local OpenAI-compatible provider is unavailable: " + type(exc).__name__) from exc
            child_env["RUFLO_PROVIDER"] = "ollama"
            child_env["OLLAMA_BASE_URL"] = local_base_url
            child_env["OLLAMA_DEFAULT_MODEL"] = local_model
            child_env["CLAUDE_FLOW_ROUTER_FALLBACK_MAX_RETRIES"] = "0"
            child_env["AUTOPILOT_STEP_TIMEOUT"] = "210000"
            child_env["MUSE_RUFLO_MCP_TIMEOUT_MS"] = "210000"
            child_env["MCP_TOOL_TIMEOUT_MS"] = "210000"
            for secret_name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENROUTER_API_KEY",
                                "CHATANYWHERE_API_KEY", "CHATANYWHERE_MODEL", "CHATANYWHERE_BASE_URL"):
                child_env.pop(secret_name, None)
        chatanywhere_selected = requested_provider in {"chatanywhere", "ollama"} or (
            not requested_provider
            and not (child_env.get("ANTHROPIC_API_KEY") or child_env.get("ANTHROPIC_AUTH_TOKEN"))
        )
        if chatanywhere_key and chatanywhere_selected:
            base_url = (chatanywhere_base_url or "https://api.chatanywhere.org").rstrip("/")
            if base_url.endswith("/v1"):
                base_url = base_url[:-3]
            if base_url not in {"https://api.chatanywhere.org", "https://api.chatanywhere.tech"}:
                raise ValueError("CHATANYWHERE_BASE_URL must be an official HTTPS ChatAnywhere host")
            model = (chatanywhere_model or "gpt-4o-mini").strip()
            if not model or len(model) > 128 or any(ord(char) < 32 for char in model):
                raise ValueError("CHATANYWHERE_MODEL must be a valid model identifier")
            child_env["OLLAMA_API_KEY"] = chatanywhere_key
            child_env["OLLAMA_BASE_URL"] = base_url
            child_env["OLLAMA_DEFAULT_MODEL"] = model
        try:
            base_timeout = self.timeout_seconds if timeout is None else timeout
            # Local model generation can legitimately exceed the short control-call budget.
            effective_timeout = max(base_timeout, 240.0 if requested_provider in {"lmstudio", "llama_cpp"} else 180.0) if is_agent_execute else base_timeout
            completed = subprocess.run(
                [*self.command_prefix, *args],
                cwd=str(self.workspace),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
                env=child_env,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RufloError("Ruflo process failed: " + type(exc).__name__) from exc
        output = completed.stdout or ""
        if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise RufloError("Ruflo output exceeds 128 KiB")
        if completed.returncode != 0:
            detail = (completed.stderr or output).strip()
            raise RufloError("Ruflo exited with code " + str(completed.returncode) + ": " + detail[-1200:])
        return output
    def health(self) -> RufloResult:
        return self.call_tool("system_status", {})

    def call_tool(self, name: str, params: dict[str, Any], *, approved: bool = False) -> RufloResult:
        if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
            raise ValueError("invalid Ruflo tool name")
        if name in _NEVER_ALLOW:
            raise PermissionError("high-risk Ruflo tool is blocked from the Muse bridge: " + name)
        if name not in self.allowed_tools:
            raise PermissionError("Ruflo tool is not allowlisted: " + name)
        if name not in _READ_ONLY_TOOLS and approved is not True:
            raise PermissionError("non-read-only Ruflo tool requires explicit approval: " + name)
        if not isinstance(params, dict):
            raise ValueError("Ruflo tool parameters must be a JSON object")
        try:
            encoded = json.dumps(params, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Ruflo parameters must be finite JSON data") from exc
        if len(encoded.encode("utf-8")) > MAX_PARAMS_BYTES:
            raise ValueError("Ruflo parameters exceed 32 KiB")
        output = self._run(("mcp", "exec", "-t", name, "-p", encoded))
        marker = "Result:"
        index = output.rfind(marker)
        payload = output[index + len(marker):].strip() if index >= 0 else output.strip()
        try:
            result: Any = json.loads(payload)
        except json.JSONDecodeError:
            result = payload
        return RufloResult(name, result, output)

    def list_tools(self) -> str:
        """Return Ruflo's own tool inventory; no second tool registry is maintained here."""
        return self._run(("mcp", "tools"), timeout=min(self.timeout_seconds, 90.0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Use the pinned Ruflo MCP engine from Muse")
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--enable-tool", action="append", default=[], help="explicitly allow a non-read-only tool")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("status", help="read Ruflo health")
    commands.add_parser("tools", help="list Ruflo MCP tools")
    call = commands.add_parser("call", help="call one allowlisted MCP tool")
    call.add_argument("name")
    call.add_argument("--params", default="{}")
    call.add_argument("--approve", action="store_true", help="approve this non-read-only call")
    args = parser.parse_args(argv)
    try:
        adapter = RufloMCPAdapter.from_installed(
            workspace=args.workspace, timeout_seconds=args.timeout, enabled_tools=args.enable_tool,
        )
        if args.action == "status":
            result = adapter.health().result
        elif args.action == "tools":
            result = adapter.list_tools()
        else:
            params = json.loads(args.params)
            result = adapter.call_tool(args.name, params, approved=args.approve).result
    except (RufloError, OSError, ValueError, PermissionError, json.JSONDecodeError) as exc:
        parser.exit(2, "muse-ruflo: " + type(exc).__name__ + ": " + str(exc) + "\\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

