"""Topic/script/URL intake for Muse's Commander-owned video production pipeline.

This module prepares reviewable, immutable creative artifacts and one Commander
approval Ticket. It never starts ComfyUI, TTS, ASR, a GPU job, or publishing.
"""
from __future__ import annotations

import hashlib
import html
import html.parser
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from muse.autonomous_research import _queue_creator_video_approval

from muse.initiative_engine import InitiativeStore
from muse.narration_generator import NarrationGenerator
from muse.story_bible import validate_story_bible, validate_story_continuity
from muse.task_queue import TaskQueue
from muse.video_prompt_composer import CINEMATIC_DEFAULT_NEGATIVE_PROMPT
from muse.video_prompt_studio import compose_video_studio_package
from muse.video_workflow import build_video_commander_plan, validate_storyboard


MIN_DURATION_SECONDS = 30
MAX_DURATION_SECONDS = 60
MAX_SOURCE_BYTES = 1_048_576
MAX_SOURCE_TEXT_CHARS = 5_000
MAX_SCRIPT_FILE_CHARS = 12_000
SCRIPT_STYLE = "yujie_canto"
DEFAULT_PIPELINE = "animated_explainer"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json_once(workspace: Path, relative_path: str, value: Any) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("video artifact paths must be safe workspace-relative paths")
    root = workspace.resolve()
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("video artifact directory must not contain symlinks")
        if cursor.exists() and not cursor.is_dir():
            raise ValueError("video artifact parent path is not a directory")
        if not cursor.exists():
            cursor.mkdir()
    target = cursor / relative.name
    if target.is_symlink():
        raise ValueError("video artifact target must not be a symlink")
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    if target.exists():
        if not target.is_file() or target.read_bytes() != encoded:
            raise FileExistsError(f"refusing to overwrite a different video artifact: {relative_path}")
        return target
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if not target.is_file() or target.read_bytes() != encoded:
                raise FileExistsError(f"concurrent writer created a different artifact: {relative_path}")
        return target
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class _PageTextParser(html.parser.HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self.skip_depth += 1
        elif tag == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag == "title":
            self.in_title = False
        elif tag in {"p", "div", "article", "section", "li", "h1", "h2", "h3", "br"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self.in_title:
            self.title_parts.append(cleaned)
        else:
            self.text_parts.append(cleaned)


def _validate_public_https_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("URL must be non-empty text of at most 2048 characters")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise ValueError("URL is malformed") from None
    if (
        parsed.scheme.lower() != "https" or not parsed.hostname
        or parsed.username or parsed.password or (port is not None and port != 443)
    ):
        raise ValueError("URL intake only accepts public HTTPS URLs without credentials or custom ports")
    try:
        answers = socket.getaddrinfo(parsed.hostname, port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"URL host could not be resolved: {exc}") from exc
    addresses = {answer[4][0].split("%", 1)[0] for answer in answers}
    if not addresses:
        raise ValueError("URL host did not resolve to a public address")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise ValueError("URL host resolved to an invalid IP address") from None
        if not ip.is_global:
            raise ValueError("URL intake blocks private, loopback, link-local and reserved IP addresses")
    return value.strip()


class _PublicHttpsRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_public_https_url(url: str) -> dict[str, str]:
    safe_url = _validate_public_https_url(url)
    opener = build_opener(
        ProxyHandler({}),
        _PublicHttpsRedirectHandler(),
        HTTPSHandler(context=ssl.create_default_context()),
    )
    request = Request(
        safe_url,
        headers={
            "User-Agent": "MuseVideoFactory/1.0",
            "Accept": "text/html,text/plain;q=0.9",
        },
    )
    try:
        with opener.open(request, timeout=12) as response:
            final_url = _validate_public_https_url(response.geturl())
            content_type = response.headers.get_content_type().lower()
            if content_type not in {"text/html", "text/plain"}:
                raise ValueError(f"URL content type is not supported: {content_type}")
            body = response.read(MAX_SOURCE_BYTES + 1)
            if len(body) > MAX_SOURCE_BYTES:
                raise ValueError("URL source exceeds the 1 MiB extraction limit")
            charset = response.headers.get_content_charset() or "utf-8"
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"URL source retrieval failed: {type(exc).__name__}: {exc}") from exc
    decoded = body.decode(charset, errors="replace")
    if content_type == "text/html":
        parser = _PageTextParser()
        parser.feed(decoded)
        title = " ".join(parser.title_parts).strip()
        text = " ".join(" ".join(parser.text_parts).split())
    else:
        title = ""
        text = " ".join(decoded.split())
    text = html.unescape(text)[:MAX_SOURCE_TEXT_CHARS]
    if len(text) < 80:
        raise ValueError("URL page did not contain enough readable text to create a video brief")
    return {"url": final_url, "title": title[:160], "text": text}


def _split_user_script(value: str, maximum_segments: int = 16) -> list[str]:
    clean = " ".join(value.replace("\r", "\n").split())
    if not clean:
        raise ValueError("script file is empty")
    sentences = [
        item.strip() for item in re.split(r"(?<=[。！？!?；;])\s*|\n+", clean)
        if item.strip()
    ]
    if not sentences:
        sentences = [clean]
    count = max(3, min(maximum_segments, len(sentences)))
    if len(sentences) >= count:
        buckets: list[list[str]] = [[] for _ in range(count)]
        for index, sentence in enumerate(sentences):
            bucket = min(count - 1, index * count // len(sentences))
            buckets[bucket].append(sentence)
        segments = ["".join(bucket) for bucket in buckets]
    else:
        segments = [
            clean[index * len(clean) // count:(index + 1) * len(clean) // count].strip()
            for index in range(count)
        ]
    if any(not item for item in segments):
        raise ValueError("script could not be divided into at least three non-empty narration segments")
    if any(len(item) > 600 for item in segments):
        raise ValueError("script is too long for the current 60-second video worker; shorten it before intake")
    return segments


def _build_script(
    title: str, research_text: str, inspiration: str, segment_count: int, *,
    source_kind: str, source_text: str = "",
) -> dict[str, Any]:
    if source_kind == "script":
        texts = _split_user_script(source_text, maximum_segments=16)
        segment_count = len(texts)

        def planner(_prompt: str) -> dict[str, Any]:
            return {"segments": [{"text": text, "emotion": "neutral"} for text in texts]}
    else:
        excerpt = " ".join(source_text.split())[:70]
        if source_kind == "url":
            lines = [
                f"講到「{title}」，先唔急住落結論。",
                "呢篇資料提供咗一條線索，但仍然需要交叉核對。",
                "將呢條線索放返入現實場景，睇下可能影響邊啲人。",
                "再由近鏡睇清楚，變化背後有咩條件同限制。",
                "機會之外，成本、風險同未知因素都要一齊考慮。",
                f"關於「{title}」，你會點樣判斷？",
            ]
        else:
            lines = [
                f"講到「{title}」，你可能已經聽過好多說法。",
                "今次先放低結論，換個角度睇清楚件事。",
                "最值得追問嘅，係佢點樣進入普通人嘅日常。",
                "將鏡頭拉近，想像一個具體而可信嘅生活場景。",
                "機會之外，成本、限制同未知因素都要一齊睇。",
                f"所以，關於「{title}」，你會點樣判斷？",
            ]
        while len(lines) < segment_count:
            insert_at = max(2, len(lines) - 1)
            lines.insert(insert_at, "先觀察具體情境，再分清楚已知、推測同仍然未知嘅部分。")
        texts = lines[:segment_count]

        def planner(_prompt: str) -> dict[str, Any]:
            return {"segments": [{"text": text, "emotion": "neutral"} for text in texts]}

    generator = NarrationGenerator(planner=planner)
    script = generator.generate(
        title,
        research_text or title,
        inspiration,
        SCRIPT_STYLE,
        segments=segment_count,
        claim_ids_by_segment=[[] for _ in range(segment_count)],
        factual_by_segment=[False for _ in range(segment_count)],
    )
    script["source"] = (
        "user_supplied_script" if source_kind == "script" else "video_factory_local_template_v1"
    )
    return script


def _build_story_bible(title: str, audience: str, scene_ids: list[str]) -> dict[str, Any]:
    bible = {
        "schema_version": 1,
        "title": title,
        "logline": f"以問題式探索呈現「{title}」，不把未核實推測包裝成事實。",
        "genre": "cinematic_explainer",
        "target_audience": audience,
        "characters": [{
            "id": "protagonist",
            "name": "主角",
            "description": "同一位虛構成年主角，短黑髮、深藍色無標識外套、自然造型；臉型、髮型、身形與服裝全片保持一致，不模仿任何真實人物。",
            "outfits": [{
                "id": "base",
                "description": "深藍色簡約外套、素色內搭、深色長褲，無品牌、無圖案，所有鏡頭保持一致。",
            }],
        }],
        "locations": [
            {"id": "studio", "name": "現代工作空間", "description": "整潔、自然採光的現代工作空間，桌面簡約，窗外城市背景柔和虛化。"},
            {"id": "street", "name": "城市日常街道", "description": "現代城市街道，真實比例與自然人流，背景不出現可辨識品牌或可讀招牌。"},
            {"id": "home", "name": "安靜居家空間", "description": "簡潔、溫暖的居家角落，光線自然，材質與物件符合真實生活。"},
        ],
        "props": [
            {"id": "phone", "name": "手機", "description": "無品牌智能手機，螢幕內容保持抽象且不可讀，不生成虛構文字或數據。"},
            {"id": "notebook", "name": "筆記本", "description": "素色紙本筆記本，頁面不出現可讀文字、圖表或品牌。"},
        ],
        "style_guide": {
            "visual_style": "電影級寫實，可信的人體比例、自然皮膚與布料材質、真實景深與光線反射。",
            "palette": ["深藍", "暖灰", "自然木色", "柔和金色"],
            "lighting": "自然主光方向一致，保留接觸陰影、可信高光與自然反射。",
            "camera_language": "35mm與50mm電影攝影語言，單鏡頭單一主要動作，運鏡克制而有敘事目的。",
            "audio_mood": "克制的環境氛圍僅作視覺情緒參考，實際配樂由後續音頻階段獨立設計。",
            "dialogue_language": "zh-HK",
        },
        "continuity": {
            "rules": [
                "主角臉型、短黑髮、身形、深藍外套和內搭全片一致，不換人、不換裝。",
                "同一場景的光線方向、色溫、時間感與空間佈局保持連續。",
                "手機與筆記本不出現可讀文字、虛構數據、商標或浮水印。",
                "每個鏡頭只保留一個清晰主體和一個主要動作，人體接觸與重心符合物理規律。",
                "避免手部畸形、面部漂移、肢體增生、閃爍、重影、凍結與不合理鏡頭跳變。",
            ],
            "scene_states": [],
        },
    }
    for index, scene_id in enumerate(scene_ids):
        location_id = "studio" if index in {0, 1, 2} else ("street" if index in {3, 4} else "home")
        prop_states = []
        if index in {1, 2}:
            prop_states.append({"prop_id": "phone", "state_note": "螢幕不可讀，握持與手指接觸自然。"})
        if index in {3, 4}:
            prop_states.append({"prop_id": "notebook", "state_note": "頁面無可讀文字，紙張與手部接觸可信。"})
        bible["continuity"]["scene_states"].append({
            "scene_id": scene_id,
            "location_id": location_id,
            "character_states": [{
                "character_id": "protagonist",
                "outfit_id": "base",
                "state_note": "同一張臉、同一髮型與服裝，表情自然。",
            }],
            "prop_states": prop_states,
        })
    return validate_story_bible(bible)


def _build_prompt_spec(
    title: str, audience: str, aspect_ratio: str, duration_seconds: int,
    source_url: str | None, scene_ids: list[str],
) -> dict[str, Any]:
    actions = [
        f"建立「{title}」的現實情境：同一位主角置身現代城市空間，鏡頭緩慢推近，畫面清楚而有懸念。",
        f"同一位主角在工作桌前觀察與「{title}」相關的日常物件，過肩中景，螢幕與紙面不可讀。",
        f"以主角的手部、手機和具體物件呈現「{title}」的視覺隱喻，接觸關係自然，材質可信。",
        f"同一位主角走進城市日常環境，以穩定跟拍呈現「{title}」可能涉及的生活場景，不虛構數據。",
        f"主角短暫停下思考，以光線和環境層次呈現「{title}」的機會與限制，情緒克制。",
        f"主角回到安靜居家空間，以自然眼神和一個簡單動作收束「{title}」，留出思考空間。",
    ]
    cameras = [
        "wide establishing shot, slow dolly-in",
        "medium over-the-shoulder shot, controlled push-in",
        "macro insert, stable close-up, natural hand movement",
        "medium tracking shot, steady lateral movement",
        "medium close-up, gentle locked-off camera",
        "wide-to-medium slow dolly-out, clean negative space",
    ]
    lights = [
        "soft morning daylight, consistent key-light direction",
        "window daylight with natural contact shadows",
        "soft side light revealing realistic material texture",
        "natural city daylight with consistent white balance",
        "soft directional light, restrained contrast",
        "warm late-afternoon natural light, continuous color temperature",
    ]
    scenes = []
    for index, scene_id in enumerate(scene_ids):
        template_index = index % len(actions)
        scenes.append({
            "scene_id": scene_id,
            "action": actions[template_index],
            "duration_seconds": 5,
            "beat": ("hook", "context", "detail", "scenario", "counterpoint", "closing")[template_index],
            "camera": cameras[template_index],
            "lighting": lights[template_index],
            "depends_on": [],
        })
    source_refs = []
    if source_url:
        source_refs.append({
            "source_id": "user_url_source",
            "author": "user-provided URL",
            "source_url": source_url,
            "license": "reference-only; not a media asset license",
            "completeness": "excerpt",
            "category": "research_reference",
            "summary": f"User-supplied source page for the topic: {title[:300]}",
            "method_tags": ["user_supplied_url", "reference_only"],
        })
    return {
        "title": title[:160],
        "creative_intent": (
            f"製作一條{duration_seconds}秒、{aspect_ratio}的電影感短片，圍繞「{title}」以問題式敘事探索；"
            "不添加未核實的數字、人物、引語、品牌或因果結論。"
        ),
        "scenes": scenes,
        "aspect_ratio": aspect_ratio,
        "fps": 30,
        "platform": "YouTube Shorts" if aspect_ratio == "9:16" else "YouTube",
        "target_audience": audience[:160],
        "visual_style": "cinematic photorealism, natural skin and fabric texture, physically plausible motion, controlled depth of field",
        "camera_language": "35mm and 50mm cinematic coverage, motivated camera movement, stable horizon, one clear action per shot",
        "lighting": "consistent natural key light, realistic contact shadows, restrained highlights and reflections",
        "audio_mood": "",
        "dialogue_language": "zh-HK",
        "narration_language": "zh-HK",
        "negative_prompt": CINEMATIC_DEFAULT_NEGATIVE_PROMPT,
        "master_constraints": [
            "主角外觀錨點固定：成年虛構人物、短黑髮、深藍色無標識外套、深色長褲；所有鏡頭保持同一臉型、髮型、體型與服裝。",
            "同一位虛構主角全片外貌與服裝一致。",
            "不出現可讀字幕、標牌、螢幕文字、商標、浮水印或虛構數據。",
            "每個鏡頭只保留一個清晰主體和一個主要動作。",
            "人物比例、手部、接觸關係、重心和運動模糊符合物理規律。",
            "保持鏡頭間光線方向、色溫、材質與空間連續。",
            "任何未經核實的內容以問題或假設呈現，不當作事實。",
        ],
        "source_refs": source_refs,
        "model_family": "wan21",
        "capture_profile": "cinematic",
    }


def _workspace_paths(workspace: str | Path) -> tuple[Path, Path, Path]:
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("workspace must be an existing project directory")
    return (
        root,
        root / "data" / "initiatives" / "muse.sqlite3",
        root / "data" / "tasks" / "muse.sqlite3",
    )


def create_video_task(
    workspace: str | Path, *, commander_planner: Any, topic: str | None = None, script_file: str | Path | None = None,
    source_url: str | None = None, title: str | None = None, audience: str = "一般观众",
    aspect_ratio: str = "9:16", duration_seconds: int = MIN_DURATION_SECONDS,
    pipeline_id: str = DEFAULT_PIPELINE,
) -> dict[str, Any]:
    """Create immutable artifacts and one approval Ticket from a strict model DAG."""
    from muse.commander_planner import CommanderPlanner
    if not isinstance(commander_planner, CommanderPlanner) or commander_planner.strict_model is not True:
        raise TypeError("video factory intake requires an explicitly injected strict CommanderPlanner")
    root, initiative_db, task_db = _workspace_paths(workspace)
    supplied = sum(value is not None for value in (topic, script_file, source_url))
    if supplied != 1:
        raise ValueError("provide exactly one input: topic, script_file or source_url")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, int):
        raise ValueError("duration_seconds must be an integer")
    if not MIN_DURATION_SECONDS <= duration_seconds <= MAX_DURATION_SECONDS or duration_seconds % 5:
        raise ValueError("duration_seconds must be a 5-second multiple from 30 to 60")
    if aspect_ratio not in {"9:16", "16:9", "1:1"}:
        raise ValueError("aspect_ratio must be 9:16, 16:9 or 1:1")
    if not isinstance(audience, str) or not audience.strip() or len(audience) > 160:
        raise ValueError("audience must contain 1-160 characters")

    source_kind = "topic"
    resolved_source_url = None
    source_text = ""
    if topic is not None:
        resolved_title = title or topic
        if not isinstance(topic, str) or not topic.strip() or len(topic.strip()) > 160:
            raise ValueError("topic must contain 1-160 characters")
        resolved_title = topic.strip()
        source_text = (
            f"主题：{resolved_title}。未提供已核实资料；本稿只提出问题和创作情境，"
            "不作未经证实的事实断言。"
        )
    elif script_file is not None:
        source_kind = "script"
        source_path = Path(script_file).expanduser()
        if not source_path.is_file() or source_path.is_symlink():
            raise ValueError("script_file must be an existing regular file and not a symlink")
        raw = source_path.read_text(encoding="utf-8-sig")
        if not raw.strip() or len(raw) > MAX_SCRIPT_FILE_CHARS:
            raise ValueError(f"script_file must contain 1-{MAX_SCRIPT_FILE_CHARS} characters")
        resolved_title = (title or source_path.stem).strip()
        source_text = raw
    else:
        source_kind = "url"
        fetched = _fetch_public_https_url(str(source_url))
        resolved_source_url = fetched["url"]
        resolved_title = (title or fetched["title"] or urlsplit(resolved_source_url).hostname or "来源视频").strip()
        source_text = fetched["text"]

    if not resolved_title or len(resolved_title) > 160:
        raise ValueError("resolved title must contain 1-160 characters")
    if source_kind == "script":
        segments = _split_user_script(source_text)
        script_segment_count = len(segments)
        planner = lambda _prompt: {"segments": [{"text": item, "emotion": "neutral"} for item in segments]}
    else:
        script_segment_count = min(12, max(6, duration_seconds // 5))
        planner = None
    inspiration = (
        "以沉稳、清晰的香港粤语旁白提出问题和具体情境；不把估算或未经核实的推测说成事实。"
    )
    if planner is None:
        script = _build_script(
            resolved_title, source_text, inspiration, script_segment_count,
            source_kind=source_kind, source_text=source_text,
        )
    else:
        generator = NarrationGenerator(planner=planner)
        script = generator.generate(
            resolved_title, source_text, inspiration, SCRIPT_STYLE,
            segments=script_segment_count,
            claim_ids_by_segment=[[] for _ in range(script_segment_count)],
            factual_by_segment=[False for _ in range(script_segment_count)],
        )
        script["source"] = "user_supplied_script"
    script_duration = sum(float(segment["estimated_duration_seconds"]) for segment in script["segments"])
    if script_duration > MAX_DURATION_SECONDS:
        raise ValueError(
            f"narration is estimated at {script_duration:.1f}s, exceeding the 60s worker limit; shorten the script"
        )
    planned_duration = max(duration_seconds, int(math.ceil(script_duration / 5.0) * 5))
    planned_duration = min(MAX_DURATION_SECONDS, planned_duration)
    scene_ids = [f"scene_{index:02d}" for index in range(1, planned_duration // 5 + 1)]
    story_bible = _build_story_bible(resolved_title, audience, scene_ids)
    prompt_spec = _build_prompt_spec(
        resolved_title, audience, aspect_ratio, planned_duration, resolved_source_url, scene_ids,
    )
    # Keep the storyboard handoff audio-neutral; the persisted Story Bible is continuity-validated below, while audio is a separate later pipeline stage.
    package = compose_video_studio_package(prompt_spec, pipeline_id, None)
    handoff = package.get("worker_handoff", {})
    if handoff.get("status") != "ready_for_review" or not isinstance(handoff.get("creator_brief_video"), dict):
        raise ValueError(
            "video storyboard is not eligible for Commander review: "
            + ",".join(handoff.get("blockers", ["worker_handoff_unavailable"]))
        )
    storyboard = validate_storyboard(package["composition"]["storyboard"])
    continuity = validate_story_continuity(story_bible, storyboard)
    video_brief = dict(handoff["creator_brief_video"])

    script_sha256 = _json_sha256(script)
    storyboard_sha256 = _json_sha256(storyboard)
    story_bible_sha256 = _json_sha256(story_bible)
    source_text_sha256 = _sha256_bytes(source_text.encode("utf-8"))
    prompt_spec_sha256 = _json_sha256(prompt_spec)
    recipe_sha256 = _json_sha256({
        "source_kind": source_kind,
        "source_url": resolved_source_url or "",
        "title": resolved_title,
        "source_text_sha256": source_text_sha256,
        "script_sha256": script_sha256,
        "storyboard_sha256": storyboard_sha256,
        "story_bible_sha256": story_bible_sha256,
        "prompt_spec_sha256": prompt_spec_sha256,
    })
    fingerprint = recipe_sha256
    artifact_dir = Path("artifacts") / "video_factory" / fingerprint
    script_rel = (artifact_dir / "script.json").as_posix()
    storyboard_rel = (artifact_dir / "storyboard.json").as_posix()
    story_bible_rel = (artifact_dir / "story_bible.json").as_posix()
    brief_rel = (artifact_dir / "video_brief.json").as_posix()
    manifest_rel = (artifact_dir / "manifest.json").as_posix()

    video_brief.update({
        "recipe_sha256": recipe_sha256,
        "execution_policy": {
            "cloud_paid_enabled": False,
            "gpu_max_concurrent_jobs": 1,
            "missing_capability": "block",
            "auto_publish": False,
        },
        "quality_policy": {
            "mode": "strict",
            "reject_nonconforming_shots": True,
            "max_retries_per_shot": 2,
            "allow_quality_downgrade": False,
            "required_shot_checks": [
                "subject_consistency", "deformation", "motion", "composition",
                "lighting", "material_realism",
            ],
            "required_file_checks": ["ffprobe_metadata", "full_decode"],
        },
        "source_kind": source_kind,
        "source_url": resolved_source_url or "",
        "script_path": script_rel,
        "script_sha256": script_sha256,
        "storyboard_path": storyboard_rel,
        "storyboard_sha256": storyboard_sha256,
        "story_bible_path": story_bible_rel,
        "story_bible_sha256": story_bible_sha256,
        "narration": {
            "enabled": True,
            "language": "zh-HK",
            "style": SCRIPT_STYLE,
            "segments": len(script["segments"]),
            "research_text": resolved_title,
            "inspiration": inspiration,
            "claim_grounded": False,
            "asr_enabled": False,
            "script_path": script_rel,
            "script_sha256": script_sha256,
        },
    })
    video_brief_sha256 = _json_sha256(video_brief)
    brief_wrapper = {
        "schema_version": 1,
        "video_brief_sha256": video_brief_sha256,
        "video_brief": video_brief,
    }
    concept = {
        "creator_brief": {
            "video": {
                "brief_path": brief_rel,
                "brief_sha256": video_brief_sha256,
            },
        },
        "video_factory_package": {
            "schema_version": 1,
            "fingerprint": fingerprint,
            "script_path": script_rel,
            "storyboard_path": storyboard_rel,
            "story_bible_path": story_bible_rel,
            "manifest_path": manifest_rel,
            "script_sha256": script_sha256,
            "storyboard_sha256": storyboard_sha256,
            "story_bible_sha256": story_bible_sha256,
            "video_brief_sha256": video_brief_sha256,
        },
    }
    concept_text = json.dumps(concept, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(concept_text) > 4000:
        raise ValueError("video package reference exceeds the initiative concept size limit")

    _write_json_once(root, script_rel, {
        "schema_version": 1,
        "source_kind": source_kind,
        "source_url": resolved_source_url or "",
        "topic": resolved_title,
        "source_text_sha256": source_text_sha256,
        "requires_fact_check": True,
        "script_sha256": script_sha256,
        "script": script,
    })
    _write_json_once(root, storyboard_rel, {
        "schema_version": 1,
        "storyboard_sha256": storyboard_sha256,
        "storyboard": storyboard,
    })
    _write_json_once(root, story_bible_rel, {
        "schema_version": 1,
        "story_bible_sha256": story_bible_sha256,
        "story_bible": story_bible,
    })
    _write_json_once(root, brief_rel, brief_wrapper)

    initiative_title = f"Muse 视频创作：{resolved_title[:80]} [{fingerprint[:8]}]"
    with InitiativeStore(initiative_db) as store:
        initiative = store.propose(
            initiative_title,
            kind="creative_output",
            creator_value="生成可审阅、可追踪的电影感短视频创作包",
            concept=concept_text,
            first_step="审阅已生成的脚本、分镜、角色设定与来源说明，并批准单次视频生成。",
            expected_output=f"{planned_duration}秒、{aspect_ratio}短视频初稿及逐镜质检报告。",
            interest_signals=["video_factory", f"topic:{resolved_title[:120]}"],
            inspiration_refs=[resolved_source_url] if resolved_source_url else [],
            capability_gap="ComfyUI/Wan、TTS、ASR 必须在执行阶段进行实时能力探测；未知或缺失时阻断。",
            priority=70,
            autonomy_level="prototype",
        )
        try:
            existing_concept = json.loads(initiative["concept"])
        except (TypeError, json.JSONDecodeError, KeyError):
            raise ValueError("existing initiative concept is not valid JSON") from None
        if existing_concept != concept:
            raise ValueError("initiative idempotency conflict: existing initiative has a different creative package")
        shot_ids=[f"shot_{index:03d}" for index in range(1,len(storyboard.get('scenes',[]))+1)]
        if not shot_ids:
            raise ValueError("video factory storyboard must contain at least one scene for Commander planning")
        plan_goal=(
            f"Produce one approved video asset from the Creator Brief: {resolved_title[:120]}. "
            f"Return exactly these shot steps: {', '.join(shot_ids)}. Use only operator/video_preflight, developer/comfyui_generation, operator/ffmpeg+video_assembly, reviewer/media_review. "
            "Include preflight (operator, video_preflight), each shot step (developer, comfyui_generation, no dependencies), "
            "assemble (operator, ffmpeg and video_assembly, depends on all shots), and quality_review (reviewer, media_review, depends on assemble). "
            "The Worker performs preflight before generation; do not make shots depend on preflight. No extra roles or capabilities."
        )
        commander_plan=commander_planner.plan(plan_goal)
        if commander_plan.source != "model":
            raise ValueError("video factory approval requires a Commander model-authored plan")
        plan_snapshot=commander_plan.to_snapshot()
        dag_sha256=hashlib.sha256(json.dumps(plan_snapshot,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest()
        topic_key = "video-factory:" + fingerprint
        approval = _queue_creator_video_approval(
            store, task_db,
            initiative=initiative,
            topic={"key": topic_key},
            video_brief=video_brief,
            recipe_sha256=recipe_sha256,
            evolution_cycle_id="cycle_" + fingerprint[:32],
            commander_plan_snapshot=plan_snapshot,
            dag_sha256=dag_sha256,
        )
        attempt_id = approval["attempt_id"]
        approval_ticket = approval["ticket"]

    workflow_plan = build_video_commander_plan(storyboard)
    workflow_dag = {
        "steps": [{
            "step_id": step.step_id,
            "role": step.role,
            "objective": step.objective,
            "depends_on": list(step.depends_on),
            "required_capabilities": list(step.required_capabilities),
        } for step in workflow_plan.steps],
        "parallel_waves": [list(wave) for wave in workflow_plan.parallel_waves],
    }
    workflow_dag_sha256 = _json_sha256(workflow_dag)
    manifest = {
        "schema_version": 1,
        "status": "approval_required",
        "execution": "not_started",
        "dispatch_authority": "Muse Commander",
        "auto_publish": False,
        "execution_policy": video_brief["execution_policy"],
        "quality_policy": video_brief["quality_policy"],
        "source_kind": source_kind,
        "source_url": resolved_source_url or "",
        "topic": resolved_title,
        "audience": audience,
        "aspect_ratio": aspect_ratio,
        "requested_duration_seconds": duration_seconds,
        "planned_duration_seconds": planned_duration,
        "script_source": script["source"],
        "requires_fact_check": True,
        "script_path": script_rel,
        "storyboard_path": storyboard_rel,
        "story_bible_path": story_bible_rel,
        "video_brief_path": brief_rel,
        "manifest_path": manifest_rel,
        "script_sha256": script_sha256,
        "storyboard_sha256": storyboard_sha256,
        "story_bible_sha256": story_bible_sha256,
        "video_brief_sha256": video_brief_sha256,
        "recipe_sha256": recipe_sha256,
        "video_workflow_dag_sha256": workflow_dag_sha256,
        "video_workflow_step_ids": [step["step_id"] for step in workflow_dag["steps"]],
        "video_workflow_parallel_waves": workflow_dag["parallel_waves"],
        "initiative_id": initiative["id"],
        "attempt_id": attempt_id,
        "approval_task_id": approval_ticket["id"],
        "commander_plan_id": plan_snapshot["plan_id"],
        "commander_plan_source": plan_snapshot["source"],
        "dag_sha256": dag_sha256,
        "worker_handoff_status": handoff["status"],
        "worker_handoff_blockers": handoff.get("blockers", []),
        "continuity_validation": continuity,
    }
    _write_json_once(root, manifest_rel, manifest)
    return {
        "status": "approval_required",
        "execution": "not_started",
        "dispatch_authority": "Muse Commander",
        "initiative_id": initiative["id"],
        "attempt_id": attempt_id,
        "approval_task_id": approval_ticket["id"],
        "approval_status": approval_ticket["status"],
        "video_brief_sha256": video_brief_sha256,
        "recipe_sha256": recipe_sha256,
        "worker_handoff_status": handoff["status"],
        "source_kind": source_kind,
        "script_source": script["source"],
        "duration_seconds": planned_duration,
        "scene_count": len(storyboard["scenes"]),
        "narration_segment_count": len(script["segments"]),
        "artifact_paths": {
            "script": script_rel,
            "storyboard": storyboard_rel,
            "story_bible": story_bible_rel,
            "video_brief": brief_rel,
            "manifest": manifest_rel,
        },
        "auto_publish": False,
    }


def queue_approved_video_pipeline(
    workspace: str | Path, approval_task_id: str,
) -> dict[str, Any]:
    """Convert one exact approved creator-video Ticket into a Commander execution Ticket."""
    root, initiative_db, task_db = _workspace_paths(workspace)
    if not isinstance(approval_task_id, str) or not approval_task_id.strip():
        raise ValueError("approval_task_id must be non-empty text")
    with TaskQueue(task_db) as queue:
        approval_ticket = queue.get(approval_task_id)
        if approval_ticket is None:
            raise KeyError(approval_task_id)
        if approval_ticket.get("status") != "queued":
            raise ValueError("creator video approval Ticket must be approved and queued before execution can be queued")
        payload = approval_ticket.get("payload")
        if (
            not isinstance(payload, dict)
            or payload.get("kind") != "creator_initiative_video_approval"
            or payload.get("_approved") is not True
            or payload.get("approval_scope") != "one_video_attempt_only"
            or payload.get("auto_publish") is not False
        ):
            raise ValueError("Ticket is not an approved one-attempt creator video authorization")
        approval = payload.get("_approval")
        if (
            not isinstance(approval, dict)
            or not isinstance(approval.get("approved_by"), str)
            or not approval["approved_by"].strip()
            or not isinstance(approval.get("approved_at"), str)
            or not approval["approved_at"].strip()
        ):
            raise ValueError("approved video Ticket is missing persisted human approval metadata")
        decisions = [
            event for event in queue.events(approval_task_id, limit=500)
            if event.get("event_type") == "human.decision"
        ]
        latest = decisions[-1] if decisions else None
        if (
            not isinstance(latest, dict)
            or latest.get("actor") != approval["approved_by"]
            or not isinstance(latest.get("payload"), dict)
            or latest["payload"].get("decision") != "approved"
            or not TaskQueue.approval_timestamp_matches_event(
                approval["approved_at"], latest.get("created_at"),
            )
        ):
            raise ValueError("latest human decision does not match the persisted video approval")
        approval_event_id = latest.get("event_id")
        if not isinstance(approval_event_id, str) or not approval_event_id:
            raise ValueError("approved video Ticket is missing its human decision event ID")

        initiative_id = payload.get("initiative_id")
        attempt_id = payload.get("attempt_id")
        brief_sha256 = payload.get("video_brief_sha256")
        plan_snapshot = payload.get("commander_plan_snapshot")
        dag_sha256 = payload.get("dag_sha256")
        plan_id = payload.get("commander_plan_id")
        plan_source = payload.get("commander_plan_source")
        if not all(isinstance(value, str) and value.strip() for value in (
            initiative_id, attempt_id, brief_sha256, dag_sha256, plan_id, plan_source,
        )) or not isinstance(plan_snapshot, dict):
            raise ValueError("approved video Ticket is missing Commander plan or attempt bindings")
        plan_digest = hashlib.sha256(json.dumps(plan_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        if plan_digest != dag_sha256 or plan_snapshot.get("plan_id") != plan_id or plan_snapshot.get("source") != plan_source:
            raise ValueError("approved video Ticket Commander plan snapshot or digest is invalid")

        with InitiativeStore(initiative_db) as store:
            initiative = store.get_by_id(initiative_id)
            attempt = store.get_video_attempt(attempt_id)
            if initiative is None or initiative.get("status") in {"rejected", "parked"}:
                raise ValueError("creator initiative is unavailable or no longer eligible")
            if (
                attempt is None
                or attempt.get("initiative_id") != initiative_id
                or attempt.get("approval_task_id") != approval_task_id
            ):
                raise ValueError("video attempt is not bound to this exact initiative and approval Ticket")
            try:
                concept = json.loads(initiative["concept"])
                reference = concept["creator_brief"]["video"]
            except (TypeError, KeyError, json.JSONDecodeError):
                raise ValueError("initiative is missing its video brief artifact reference") from None
            if not isinstance(reference, dict) or reference.get("brief_sha256") != brief_sha256:
                raise ValueError("initiative video brief reference does not match the approved Ticket")
            from muse.video_task_worker import _load_video_brief_reference
            video_brief = _load_video_brief_reference(root, reference)
            if _json_sha256(video_brief) != brief_sha256:
                raise ValueError("video brief artifact digest does not match the approved Ticket")
            if payload.get("video_brief") != video_brief:
                raise ValueError("Commander approval Ticket video brief differs from its immutable artifact")
            if attempt.get("recipe_sha256") != video_brief.get("recipe_sha256"):
                raise ValueError("video attempt recipe digest differs from the approved video brief")

        execution_payload = {
            "kind": "video_pipeline",
            "approval_task_id": approval_task_id,
            "approval_event_id": approval_event_id,
            "approved_by": approval["approved_by"],
            "attempt_id": attempt_id,
            "initiative_id": initiative_id,
            "brief_sha256": brief_sha256,
            "commander_plan_id": plan_id,
            "commander_plan_source": plan_source,
            "commander_plan_snapshot": plan_snapshot,
            "dag_sha256": dag_sha256,
        }
        execution_ticket = queue.enqueue(
            f"执行已批准视频生成：{video_brief.get('title', '')[:80]}",
            execution_payload,
            priority=max(0, min(100, int(approval_ticket.get("priority", 70) or 70))),
            recovery_policy="manual",
            idempotency_key=f"video-pipeline-execution:{attempt_id}",
            workload_class="media",
        )
        stored = execution_ticket.get("payload", {})
        if any(stored.get(field) != value for field, value in execution_payload.items()):
            raise ValueError("Commander execution Ticket idempotency key is already bound to different video data")
        return {
            "status": "queued",
            "execution": "not_started",
            "dispatch_authority": "Muse Commander",
            "execution_task_id": execution_ticket["id"],
            "execution_task_status": execution_ticket["status"],
            "approval_task_id": approval_task_id,
            "attempt_id": attempt_id,
            "initiative_id": initiative_id,
            "gpu_execution": "not_started",
            "auto_publish": False,
        }
