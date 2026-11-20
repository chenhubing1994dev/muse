"""Director-style video prompt composition for Muse.

The composer keeps the creative brief separate from Muse's strict storyboard schema.
It emits bounded per-clip prompts and sidecar timing/audio/source metadata; it never
calls a model, downloads references, schedules work, or starts media generation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite
from urllib.parse import urlsplit
from typing import Any, Mapping

from muse.prompt_engine import resolve_profile
from muse.story_bible import validate_story_bible, validate_story_continuity
from muse.video_workflow import validate_storyboard

VIDEO_PROMPT_SPEC_VERSION = 1
MAX_SCENES = 24
MAX_PROMPT_CHARS = 2000


@dataclass(frozen=True)
class PromptSourceRef:
    """Attribution metadata only; raw upstream prompt text is intentionally excluded."""

    source_id: str
    author: str
    source_url: str
    license: str
    completeness: str
    category: str
    method_tags: tuple[str, ...] = ()
    summary: str = ""
    captured_at: str = ""

    def __post_init__(self) -> None:
        for name in ("source_id", "author", "license", "category"):
            _required_text(getattr(self, name), name, 160)
        parsed = urlsplit(self.source_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("source_url must be an absolute HTTPS URL")
        if self.completeness not in {"full", "excerpt"}:
            raise ValueError("completeness must be full or excerpt")
        if len(self.method_tags) > 20 or any(not tag.strip() or len(tag) > 64 for tag in self.method_tags):
            raise ValueError("method_tags must contain at most 20 short non-empty labels")
        if len(self.summary) > 500:
            raise ValueError("summary must be at most 500 characters")
        if self.captured_at:
            try:
                date.fromisoformat(self.captured_at)
            except ValueError:
                raise ValueError("captured_at must be an ISO date (YYYY-MM-DD)") from None


@dataclass(frozen=True)
class VideoPromptScene:
    scene_id: str
    action: str
    duration_seconds: float
    beat: str = ""
    camera: str = ""
    lighting: str = ""
    dialogue: str = ""
    narration: str = ""
    sound_effects: str = ""
    music_cue: str = ""
    negative_prompt: str = ""
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required_text(self.scene_id, "scene_id", 40)
        if not self.scene_id.replace("_", "").replace("-", "").isalnum() or not self.scene_id[0].islower():
            raise ValueError("scene_id must be a lowercase storyboard-safe identifier")
        _required_text(self.action, "action", 1600)
        if isinstance(self.duration_seconds, bool) or not isinstance(self.duration_seconds, (int, float)):
            raise ValueError("duration_seconds must be a finite number from 1 to 20")
        if not isfinite(self.duration_seconds) or not 1 <= self.duration_seconds <= 20:
            raise ValueError("duration_seconds must be a finite number from 1 to 20")
        for name, maximum in (
            ("beat", 240), ("camera", 160), ("lighting", 160),
            ("dialogue", 500), ("narration", 700), ("sound_effects", 240),
            ("music_cue", 240), ("negative_prompt", 1000),
        ):
            _optional_text(getattr(self, name), name, maximum)
        if len(self.depends_on) > 12 or any(not item.strip() for item in self.depends_on):
            raise ValueError("depends_on must contain at most 12 non-empty scene IDs")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("depends_on cannot contain duplicates")


@dataclass(frozen=True)
class VideoPromptSpec:
    title: str
    creative_intent: str
    scenes: tuple[VideoPromptScene, ...]
    aspect_ratio: str = "16:9"
    fps: int = 24
    platform: str = "通用"
    target_audience: str = ""
    visual_style: str = ""
    camera_language: str = ""
    lighting: str = ""
    audio_mood: str = ""
    dialogue_language: str = "粤语"
    narration_language: str = "粤语"
    negative_prompt: str = ""
    master_constraints: tuple[str, ...] = ()
    source_refs: tuple[PromptSourceRef, ...] = ()
    model_family: str = "generic"

    def __post_init__(self) -> None:
        _required_text(self.title, "title", 160)
        _required_text(self.creative_intent, "creative_intent", 1200)
        if not 1 <= len(self.scenes) <= MAX_SCENES:
            raise ValueError(f"scenes must contain 1-{MAX_SCENES} entries")
        if len({scene.scene_id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("scene IDs must be unique")
        if self.aspect_ratio not in {"16:9", "9:16", "1:1"}:
            raise ValueError("aspect_ratio must be 16:9, 9:16 or 1:1")
        if isinstance(self.fps, bool) or self.fps not in {24, 25, 30, 50, 60}:
            raise ValueError("fps must be one of 24, 25, 30, 50 or 60")
        for name, maximum in (
            ("platform", 80), ("target_audience", 160), ("visual_style", 400),
            ("camera_language", 160), ("lighting", 160), ("audio_mood", 160),
            ("dialogue_language", 32), ("narration_language", 32),
            ("negative_prompt", 1000), ("model_family", 80),
        ):
            _optional_text(getattr(self, name), name, maximum)
        if len(self.master_constraints) > 24 or any(not item.strip() or len(item) > 240 for item in self.master_constraints):
            raise ValueError("master_constraints must contain at most 24 short non-empty rules")
        if len(self.source_refs) > 40:
            raise ValueError("source_refs must contain at most 40 attribution records")
        ids = {scene.scene_id for scene in self.scenes}
        for scene in self.scenes:
            if scene.scene_id in scene.depends_on or set(scene.depends_on) - ids:
                raise ValueError(f"scene {scene.scene_id} has self or unknown dependencies")


def _required_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{name} must contain 1-{maximum} characters")
    return value.strip()


def _optional_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{name} must be text of at most {maximum} characters")
    return value.strip()


def _story_bible_master_lines(bible: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    style = bible["style_guide"]
    lines.extend((
        f"视觉风格锁定：{style['visual_style']}",
        f"色彩：{'、'.join(style['palette'])}",
        f"光线：{style['lighting']}",
        f"摄影语言：{style['camera_language']}",
        f"声音氛围：{style['audio_mood']}",
        f"对白语言：{style['dialogue_language']}",
    ))
    for character in bible["characters"]:
        outfit_text = "；".join(f"{item['id']}={item['description']}" for item in character["outfits"])
        asset = character.get("canonical_asset_id", "")
        lines.append(
            f"角色锁定 {character['id']}（{character['name']}）：{character['description'][:180]}"
            + (f"；标准参考资产 {asset}" if asset else "")
            + (f"；服装 {outfit_text[:240]}" if outfit_text else "")
        )
    for location in bible["locations"]:
        lines.append(f"场景锁定 {location['id']}：{location['name']}，{location['description'][:140]}")
    for prop in bible["props"]:
        lines.append(f"道具锁定 {prop['id']}：{prop['name']}，{prop['description'][:120]}")
    lines.extend(f"连续性规则：{rule}" for rule in bible["continuity"]["rules"])
    return lines


def _story_bible_clip_lock_lines(bible: Mapping[str, Any] | None) -> list[str]:
    """Repeat concise canonical identity locks in every clip, not only the master brief."""
    if bible is None:
        return []
    lines: list[str] = []
    for character in bible["characters"]:
        asset = character.get("canonical_asset_id")
        lines.append(
            f"角色一致性锁定：{character['id']}／{character['name']}；"
            f"{character['description'][:110]}"
            + (f"；参考资产 {asset}" if asset else "")
        )
    for location in bible["locations"]:
        lines.append(f"地点一致性锁定：{location['id']}／{location['name']}；{location['description'][:80]}")
    for prop in bible["props"]:
        lines.append(f"道具一致性锁定：{prop['id']}／{prop['name']}；{prop['description'][:80]}")
    lines.extend(f"连续性规则：{rule}" for rule in bible["continuity"]["rules"])
    return lines


def _scene_continuity_lines(bible: Mapping[str, Any] | None, scene_id: str) -> list[str]:
    if bible is None:
        return []
    states = {item["scene_id"]: item for item in bible["continuity"]["scene_states"]}
    state = states.get(scene_id)
    if state is None:
        return []
    locations = {item["id"]: item for item in bible["locations"]}
    characters = {item["id"]: item for item in bible["characters"]}
    props = {item["id"]: item for item in bible["props"]}
    lines: list[str] = []
    if state.get("location_id"):
        location = locations[state["location_id"]]
        lines.append(f"本镜头地点：{location['name']}（{location['id']}）")
    for item in state["character_states"]:
        character = characters[item["character_id"]]
        outfit = next((o for o in character["outfits"] if o["id"] == item.get("outfit_id")), None)
        lines.append(f"出镜角色：{character['name']}；外观必须一致：{character['description'][:160]}")
        if outfit:
            lines.append(f"指定服装：{outfit['description']}")
        if item.get("state_note"):
            lines.append(f"角色状态：{item['state_note']}")
    for item in state["prop_states"]:
        prop = props[item["prop_id"]]
        lines.append(f"道具状态：{prop['name']}；{item.get('state_note', prop['description'][:120])}")
    return lines


def compile_video_prompt(
    spec: VideoPromptSpec,
    story_bible: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a director brief into the existing storyboard v1 plus sidecar metadata."""
    bible = validate_story_bible(story_bible) if story_bible is not None else None
    model_profile = resolve_profile(spec.model_family)

    master_lines = [
        f"创作目标：{spec.creative_intent}",
        f"目标平台：{spec.platform}",
        f"目标受众：{spec.target_audience or '未限定'}",
        f"画幅：{spec.aspect_ratio}；帧率：{spec.fps}fps",
        f"整体视觉风格：{spec.visual_style or '遵循 Story Bible 与场景描述'}",
        f"整体摄影语言：{spec.camera_language or '服务于叙事，不做无意义运镜'}",
        f"整体光线：{spec.lighting or '保持镜头间光线方向与色温连续'}",
    ]
    if bible is not None:
        master_lines.extend(_story_bible_master_lines(bible))
    master_lines.extend(f"全片约束：{rule}" for rule in spec.master_constraints)
    master_prompt = "\n".join(master_lines)

    scenes: list[dict[str, Any]] = []
    clip_manifest: list[dict[str, Any]] = []
    audio_cues: list[dict[str, str]] = []
    cursor = 0.0
    for scene in spec.scenes:
        bible_style = bible["style_guide"] if bible is not None else {}
        camera = scene.camera or spec.camera_language or bible_style.get("camera_language", "")
        lighting = scene.lighting or spec.lighting or bible_style.get("lighting", "")
        if len(camera) > 160:
            raise ValueError(f"scene {scene.scene_id} camera exceeds storyboard limit")
        prompt_parts = [
            f"全片统一意图：{spec.creative_intent}",
            f"视觉风格：{spec.visual_style or bible_style.get('visual_style', '遵循全片 Master Prompt')}",
            f"全局连续性约束：{'；'.join(spec.master_constraints)}" if spec.master_constraints else "",
            *_story_bible_clip_lock_lines(bible),
            *_scene_continuity_lines(bible, scene.scene_id),
            f"动作与叙事：{scene.action}",
            f"节拍：{scene.beat}" if scene.beat else "",
            f"摄影机：{camera}" if camera else "",
            f"光线：{lighting}" if lighting else "",
        ]
        clip_prompt = "\n".join(part for part in prompt_parts if part)
        if len(clip_prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"compiled prompt for {scene.scene_id} exceeds {MAX_PROMPT_CHARS} characters")
        scene_negative = scene.negative_prompt
        scenes.append({
            "id": scene.scene_id,
            "prompt": clip_prompt,
            "duration_seconds": float(scene.duration_seconds),
            "camera": camera,
            "negative_prompt": scene_negative,
            "depends_on": list(scene.depends_on),
        })
        end = cursor + float(scene.duration_seconds)
        clip_manifest.append({
            "scene_id": scene.scene_id,
            "start_seconds": round(cursor, 3),
            "end_seconds": round(end, 3),
            "duration_seconds": float(scene.duration_seconds),
            "beat": scene.beat,
            "action": scene.action,
            "camera": camera,
            "depends_on": list(scene.depends_on),
        })
        if any((scene.dialogue, scene.narration, scene.sound_effects, scene.music_cue)):
            audio_cues.append({
                "scene_id": scene.scene_id,
                "dialogue_language": spec.dialogue_language,
                "dialogue": scene.dialogue,
                "narration_language": spec.narration_language,
                "narration": scene.narration,
                "sound_effects": scene.sound_effects,
                "music_cue": scene.music_cue,
            })
        cursor = end

    style = spec.visual_style
    if bible is not None:
        style = bible["style_guide"]["visual_style"] + (f"；{style}" if style else "")
    storyboard = validate_storyboard({
        "schema_version": 1,
        "title": spec.title,
        "aspect_ratio": spec.aspect_ratio,
        "fps": spec.fps,
        "style": style,
        "negative_prompt": spec.negative_prompt,
        "scenes": scenes,
    })
    continuity = validate_story_continuity(bible, storyboard) if bible is not None else None
    return {
        "video_prompt_spec_version": VIDEO_PROMPT_SPEC_VERSION,
        "master_prompt": master_prompt,
        "storyboard": storyboard,
        "clip_manifest": clip_manifest,
        "audio_plan": {
            "audio_mood": spec.audio_mood or (bible["style_guide"]["audio_mood"] if bible else ""),
            "dialogue_language": spec.dialogue_language,
            "narration_language": spec.narration_language,
            "cues": audio_cues,
            "note": "音频为独立 sidecar；不假设当前视频模型支持原生对白或音乐生成。",
        },
        "model_guidance": {
            "family": model_profile.family,
            "style": model_profile.style,
            "notes": list(model_profile.notes),
            "provider_parameters_applied": [],
            "note": "模型档案仅提供提示词组织启发；未知能力保持 unknown，不猜测或强塞参数。",
        },
        "source_ledger": [asdict(ref) for ref in spec.source_refs],
        "continuity_validation": continuity,
    }
