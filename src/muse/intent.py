"""Conservative natural-language intent routing for Muse.

Classification creates a reviewable plan only. It never executes tools or sends data.
"""

from __future__ import annotations

import re
from typing import Any


_RULES = (
    ("reminders", "reminder_capture", (
        "提醒我", "設定提醒", "设置提醒", "建立提醒", "创建提醒", "remind me", "set a reminder",
    )),
    ("video", "video_edit", (
        "剪影片", "剪片", "剪視頻", "剪视频", "宣傳片", "短片", "影片剪輯", "視頻剪輯",
        "video edit", "edit video", "video editing", "montage",
    )),
    ("video", "video_generate", (
        "生成影片", "生成視頻", "影片素材", "視頻素材", "video generation",
        "generate video", "video assets",
    )),
    ("audio", "audio_edit", (
        "音訊", "音频", "混音", "降噪", "配音", "聲音", "声音", "audio edit",
        "audio mixing", "voiceover",
    )),
    ("images", "image_design", (
        "生成圖片", "生成图片", "修圖", "修图", "圖片素材", "图片素材", "海報", "海报",
        "image generation", "image design",
    )),
    ("documents", "report", (
        "報告", "报告", "簡報", "简报", "proposal", "business report", "presentation",
    )),
    ("writing", "copywriting", (
        "文案", "宣傳語", "宣传语", "廣告詞", "广告词", "copywriting", "social post",
    )),
    ("web", "website", (
        "網站", "网站", "網頁", "网页", "web design", "website", "landing page",
    )),
    ("software", "coding", (
        "寫程式", "写程序", "寫代碼", "写代码", "編程", "编程", "coding", "debug",
        "build an app", "開發程式", "开发程序",
    )),
    ("research", "research", (
        "研究", "搜尋資料", "搜索资料", "搵資料", "找資料", "找资料", "research",
        "compare projects", "開源項目", "开源项目",
    )),
)

_EXTERNAL_ACTION = re.compile(
    r"發送|发送|寄出|寄送|發布|发布|上架|公開|公开|提交|付款|購買|购买|刪除|删除|"
    r"email|send to|publish|post online|upload|pay|purchase|delete|submit",
    re.IGNORECASE,
)


def classify_request(
    request: str, *, inventory: Any | None = None, registry: Any | None = None
) -> dict[str, Any]:
    if not isinstance(request, str) or not request.strip():
        raise ValueError("request must be non-empty text")
    normalized = request.casefold()
    matches = [
        (domain, capability, phrase)
        for domain, capability, phrases in _RULES
        for phrase in phrases
        if phrase.casefold() in normalized
    ]
    if not matches:
        deterministic = {
            "domain": "unknown", "capability": "unknown", "confidence": 0.0,
            "matched_phrase": None, "approval_required": bool(_EXTERNAL_ACTION.search(request)),
        }
        # The optional backend stays unloaded unless explicitly enabled.
        from muse.gemma_intent import assist_unknown_request
        return assist_unknown_request(request, deterministic)
    # Prefer the longest phrase: it is generally the most specific signal.
    domain, capability, phrase = max(matches, key=lambda item: len(item[2]))
    available = None
    if inventory is not None:
        available = {tool.tool_id for tool in getattr(inventory, "tools", ())}
    tool_map = {
        "reminder_capture": (None, "Parse a supported local reminder phrase; only the explicit capture command saves it."),
        "video_edit": ("ffmpeg", "Use FFmpeg for deterministic cuts, filters and assembly; a timeline editor is a separate capability."),
        "video_generate": (None, "Requires a verified local video-generation workflow; do not assume one is installed."),
        "audio_edit": ("audacity", "Use the installed audio editor for waveform cleanup and editing."),
        "image_design": (None, "Requires a verified local image-generation or design workflow."),
        "report": (None, "Draft and structure the report; connect an approved document renderer when available."),
        "copywriting": (None, "Draft copy and variants; no external publishing is performed."),
        "website": (None, "Create a local web project and preview; publishing is a separate approval-gated action."),
        "coding": (None, "Use the project workspace, tests and approved local development tools."),
        "research": (None, "Research public sources and preserve source provenance; do not upload private files."),
    }
    tool, guidance = tool_map[capability]
    tool_status = "unverified" if tool is None else (
        "ready" if available is None or tool in available else "missing"
    )
    if registry is not None:
        required = {
            "video_edit": ("video_filter", "video_transcode"),
            "audio_edit": ("audio_edit", "audio_cleanup"),
        }.get(capability, ())
        registered = [item for feature in required for item in registry.find_by_capability(feature)]
        ready = next((item for item in registered if item["status"] == "ready"), None)
        if ready is not None:
            tool = ready["capability_id"].split(".", 1)[-1]
            tool_status = "ready"
            guidance = f"Use verified registry capability {ready['capability_id']} ({', '.join(ready['capabilities'])})."
        else:
            tool = None
            tool_status = "unverified"
            guidance = "No ready local capability is registered for this task; do not assume a tool is installed."
    return {
        "domain": domain, "capability": capability,
        "confidence": 0.9 if len(phrase) >= 3 else 0.75,
        "matched_phrase": phrase, "tool": tool, "tool_status": tool_status,
        "guidance": guidance,
        "approval_required": bool(_EXTERNAL_ACTION.search(request)),
    }

def build_execution_plan(classification: dict[str, Any]) -> dict[str, Any]:
    if classification.get("capability") == "reminder_capture":
        return {
            "status": "ready_for_local_capture", "domain": "reminders",
            "capability": "reminder_capture", "approval_required": False,
            "execution_started": False,
            "steps": [{"step": 1, "action": "parse_local_reminder_text", "tool": "muse-reminders capture",
                       "description": "Parse a supported local Chinese/English time phrase; preview the result before saving."}],
            "blockers": [],
        }
    domain = classification.get("domain")
    if domain == "unknown":
        return {
            "status": "needs_clarification", "domain": domain, "steps": [],
            "approval_required": bool(classification.get("approval_required")),
            "blockers": ["The request does not match a sufficiently clear supported intent."],
        }
    if classification.get("tool_status") == "missing":
        return {
            "status": "blocked", "domain": domain, "steps": [],
            "approval_required": bool(classification.get("approval_required")),
            "blockers": [f"Required local tool '{classification.get('tool')}' is not confirmed installed."],
        }
    steps = [{
        "step": 1, "action": "prepare_and_validate_inputs",
        "tool": classification.get("tool") or "Muse capability workflow",
        "description": classification.get("guidance", ""),
    }, {
        "step": 2, "action": "execute_after_plan_review",
        "tool": classification.get("tool") or "approved capability adapter",
        "description": "Run only the reviewed scope; keep private inputs local by default.",
    }, {
        "step": 3, "action": "verify_and_deliver",
        "tool": "Muse quality checks",
        "description": "Verify output files, report errors and record a privacy-safe progress event.",
    }]
    approval = bool(classification.get("approval_required"))
    if approval:
        status = "waiting_approval"
    elif classification.get("tool_status") == "unverified":
        status = "needs_capability"
    else:
        status = "needs_review"
    return {
        "status": status, "domain": domain,
        "capability": classification.get("capability"),
        "confidence": classification.get("confidence", 0.0),
        "steps": steps, "approval_required": approval,
        "blockers": [] if status != "needs_capability" else [
            "A suitable installed execution adapter has not yet been verified."
        ],
        "execution_started": False,
    }
