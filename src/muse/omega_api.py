"""B398 Omega API surface for the Status API (loopback + Bearer + Origin enforced by the caller).

One dispatcher keeps the large ``status_api`` diff tiny. Every handler validates its own input,
returns JSON-serialisable data, and never echoes secrets. Mutating routes require an explicit
confirmation phrase where the action is destructive or safety-relevant.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

MAX_JSON_BYTES = 64 * 1024
MAX_UPLOAD_JSON_BYTES = 12 * 1024 * 1024
UPLOAD_ROUTES = frozenset({"/api/v1/omega/feedback/upload", "/api/v1/omega/quotes/extract"})
OMEGA_PAGES = (
    {"href": "./review-desk.html", "label": "待签名审查台", "icon": "✍"},
    {"href": "./unit-economics.html", "label": "单位经济 · 报价识别", "icon": "¥"},
    {"href": "./consciousness-map.html", "label": "十维意识星图", "icon": "✶"},
    {"href": "./xuanshu-lab.html", "label": "玄枢灵感工坊", "icon": "☯"},
    {"href": "./evolution-lab.html", "label": "自我进化实验室", "icon": "⟳"},
    {"href": "./research-bridge.html", "label": "深度研究 · 反馈回传", "icon": "⌕"},
    {"href": "./video-factory.html", "label": "一键电影级视频工坊", "icon": "🎬"},
    {"href": "./portal.html", "label": "普罗米修斯计划门户", "icon": "∞"},
    {"href": "./os.html", "label": "Super Agent OS 调度中心", "icon": "☯"},
)
_CLAIM = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}\Z")


class OmegaNotFound(LookupError):
    pass


def _paths(workspace: Path) -> dict[str, Path]:
    data = workspace / "data"
    return {"safety": data / "autonomy" / "safety.sqlite3", "versions": data / "evolution" / "versions.sqlite3",
            "claims": data / "research" / "claims.sqlite3", "feedback": data / "research" / "feedback.sqlite3",
            "memory": data / "memory" / "muse.sqlite3", "kill": workspace / "KILL_SWITCH"}


def max_body_bytes(path: str) -> int:
    return MAX_UPLOAD_JSON_BYTES if path in UPLOAD_ROUTES else MAX_JSON_BYTES


def _b64(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError("content_base64 is required")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("content_base64 is not valid base64") from exc


def _get(path: str, query: str, workspace: Path) -> dict[str, Any]:
    p = _paths(workspace)
    if path == "/api/v1/omega/nav":
        return {"pages": list(OMEGA_PAGES)}
    if path == "/api/v1/omega/consciousness":
        from muse.consciousness_core import read_core_status
        return read_core_status(workspace)
    if path == "/api/v1/omega/safety":
        from muse.autonomy_safety import SafetyController
        with SafetyController(p["safety"], kill_switch_file=p["kill"]) as safety:
            return {"status": safety.status(), "events": safety.events(limit=30)}
    if path == "/api/v1/omega/evolution/versions":
        from muse.evolution_guard import PROTECTED_SURFACES, EvolutionVersionLedger
        with EvolutionVersionLedger(p["versions"], workspace) as ledger:
            return {"versions": ledger.list_versions(), "protected_surfaces": [label for label, _ in PROTECTED_SURFACES]}
    match = re.fullmatch(r"/api/v1/omega/claims/([a-z0-9][a-z0-9._-]{0,95})", path)
    if match:
        from muse.feedback_bridge import FeedbackBridge
        from muse.research_closed_loop import ClaimDossierStore
        with ClaimDossierStore(p["claims"]) as claims:
            dossier = claims.latest(match.group(1))
            if dossier is None:
                raise OmegaNotFound("claim not found")
            with FeedbackBridge(p["feedback"], claims) as bridge:
                return {"dossier": dossier, "feedback": bridge.list_for_claim(match.group(1))}
    raise OmegaNotFound("unknown omega route")


def _post(path: str, payload: dict[str, Any], workspace: Path) -> tuple[int, dict[str, Any]]:
    p = _paths(workspace)
    if path == "/api/v1/omega/safety/control":
        from muse.autonomy_safety import KILL_RESET_PHRASE, SafetyController
        action = payload.get("action")
        with SafetyController(p["safety"], kill_switch_file=p["kill"]) as safety:
            if action == "pause":
                return 200, {"status": safety.pause(actor="dashboard", reason=str(payload.get("reason", ""))[:200])}
            if action == "resume":
                return 200, {"status": safety.resume(actor="dashboard")}
            if action == "kill":
                if payload.get("confirmation") != "KILL-SWITCH":
                    raise ValueError("explicit KILL-SWITCH confirmation required")
                return 200, {"status": safety.engage_kill_switch(actor="dashboard", reason=str(payload.get("reason", "")))}
            if action == "reset":
                return 200, {"status": safety.reset_kill_switch(actor="dashboard", confirmation=payload.get("confirmation", ""))}
        raise ValueError(f"action must be pause/resume/kill/reset ({KILL_RESET_PHRASE} for reset)")
    if path == "/api/v1/omega/evolution/rollback":
        from muse.evolution_guard import EvolutionVersionLedger
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        if payload.get("confirmation") != f"ROLLBACK-{version}":
            raise ValueError(f"explicit ROLLBACK-{version} confirmation required")
        # C5: rolling back rewrites project files → high-risk Commander ticket; a user:/human: approval in
        # the Commander panel runs it. The confirmation phrase alone no longer touches files.
        from muse.commander_routes import HANDLER_ROLLBACK, submit_and_wait
        routed = submit_and_wait(workspace, handler=HANDLER_ROLLBACK, route_args={"version": version, "workspace": str(workspace)},
                                 title=f"Evolution rollback v{version}", objective=f"回滚进化版本 {version}（改写项目文件）",
                                 request_key=f"rollback:{version}", risk_override="high")
        return 202, {"result": {"status": routed["status"], "version": version},
                     "commander": {k: routed.get(k) for k in ("ticket_id", "status", "risk")}}
    if path == "/api/v1/omega/xuanshu/fuse":
        from muse.xuanshu_fusion import fuse
        from muse.xuanshu_symbolic_fallback import today_in_shanghai
        # External chart engines are optional; the stdlib symbolic fallback keeps the lab usable everywhere.
        return 200, {"result": fuse(str(payload.get("question", "")), seed=str(payload.get("seed", "")),
                                    symbolic_fallback=True, moment=today_in_shanghai())}
    if path == "/api/v1/omega/feedback/upload":
        from muse.feedback_bridge import FeedbackBridge
        from muse.research_closed_loop import ClaimDossierStore
        with ClaimDossierStore(p["claims"]) as claims, FeedbackBridge(p["feedback"], claims) as bridge:
            result = bridge.upload(filename=payload.get("filename", ""), content_base64=payload.get("content_base64", ""),
                                   declared_sha256=payload.get("declared_sha256", ""))
        return (201 if result["status"] == "accepted" else 200), {"result": result}
    if path == "/api/v1/omega/research/investigate":
        from muse.research_closed_loop import ClaimDossierStore, ResearchClosedLoop, Source

        def sources(key: str, stance: str) -> list[Source]:
            items = payload.get(key) or []
            if not isinstance(items, list) or len(items) > 40:
                raise ValueError(f"{key} must be a list of at most 40 sources")
            out = []
            for item in items:
                if not isinstance(item, dict) or not str(item.get("url", "")).startswith(("https://", "http://")):
                    raise ValueError("each source needs an http(s) url")
                out.append(Source(url=item["url"], title=str(item.get("title", ""))[:300], text=str(item.get("text", ""))[:20000],
                                  publisher=str(item.get("publisher", ""))[:120], stance=stance))
            return out

        support, counter = sources("support_sources", "supports"), sources("counter_sources", "refutes")
        with ClaimDossierStore(p["claims"]) as claims:
            loop = ResearchClosedLoop(support_search=lambda q: support, counter_search=lambda q: counter, store=claims)
            return 201, {"dossier": loop.investigate(str(payload.get("claim_id", "")), str(payload.get("claim", "")),
                                                      citations=payload.get("citations") or [])}
    if path == "/api/v1/omega/unit-economics/simulate":
        from muse.unit_economics import simulate
        return 200, {"simulation": simulate(payload.get("hypothesis") or {})}
    if path == "/api/v1/omega/quotes/extract":
        from muse.quote_ingest import extract_csv, extract_pdf_text, extract_xlsx
        name = str(payload.get("filename", ""))
        if not re.fullmatch(r"[\w\u4e00-\u9fff .()-]{1,120}\.(csv|xlsx|pdf)", name, re.I):
            raise ValueError("filename must be a plain .csv/.xlsx/.pdf name")
        data = _b64(payload.get("content_base64"))
        extractor = {"csv": extract_csv, "xlsx": extract_xlsx, "pdf": extract_pdf_text}[name.rsplit(".", 1)[1].casefold()]
        return 200, {"extraction": extractor(data, name)}
    if path == "/api/v1/omega/film/plan":
        from muse.cinematic_factory import CinematicFactory, request_from_payload
        return 200, {"manifest": CinematicFactory().plan(request_from_payload(payload))}
    if path == "/api/v1/omega/film/retention":
        from muse.cinematic_factory import import_retention_curve, retention_to_agent_track
        from muse.memory import MemoryStore
        csv_text = payload.get("csv_text")
        if not isinstance(csv_text, str) or len(csv_text) > 2 * 1024 * 1024:
            raise ValueError("csv_text must be at most 2 MiB")
        curve = import_retention_curve(csv_text, duration_seconds=payload.get("duration_seconds"))
        hook_id, video_id = str(payload.get("hook_id", "unknown-hook"))[:80], str(payload.get("video_id", "video"))[:80]
        with MemoryStore(p["memory"]) as store:
            proposals = retention_to_agent_track(curve, hook_id=hook_id, video_id=video_id, memory_store=store)
        return 201, {"curve": curve, "agent_track_candidates": len(proposals)}
    raise OmegaNotFound("unknown omega route")


def handle_omega(method: str, path: str, query: str, body: bytes, workspace: str | Path) -> tuple[int, dict[str, Any]]:
    """Return ``(http_status, json_body)``; never raises."""
    root = Path(workspace)
    try:
        if method == "GET":
            parse_qs(query or "")
            return 200, {"api_version": 1, **_get(path, query, root)}
        if method == "POST":
            if len(body) > max_body_bytes(path):
                return 413, {"error": "request body too large"}
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 400, {"error": "request body must be UTF-8 JSON"}
            if not isinstance(payload, dict):
                return 400, {"error": "request body must be a JSON object"}
            status, result = _post(path, payload, root)
            return status, {"api_version": 1, **result}
        return 405, {"error": "method not allowed"}
    except OmegaNotFound as exc:
        return 404, {"error": str(exc)}
    except PermissionError as exc:
        return 409, {"error": str(exc)[:300]}
    except (ValueError, TypeError, KeyError) as exc:
        return 400, {"error": str(exc)[:300]}
    except Exception as exc:
        return 503, {"error": f"omega operation unavailable ({type(exc).__name__})"}
