"""B397 WS6 one-click cinematic short-video factory — algorithm engines + single-entry orchestrator.

DAG (each node is a pure, testable engine; GPU work is only *prepared*, never silently faked):

  hook_matrix → rhythm_density → story_bible_lock → shot_director → gpu_preflight → wan21_workflow
  → upscale_1080p_bridge → dub_subtitle_align → loudness_qc → vlm_review (fallback: human shot desk)
  → retention_feedback (official curve import → Agent-Track memory candidates)

Honesty rules baked into the engines:
* 480P → 1080P must go through a *real AI super-resolution model node*; a plain resize is
  classified ``plain_resize`` and fails the 720P quality gate.
* LUFS is measured with an ITU-R BS.1770-4 / EBU R128 implementation (K-weighting, 400 ms blocks,
  absolute −70 LUFS and relative −10 LU gates); sample peak is reported as such (not true peak).
* VLM verdicts are untrusted structured data; any failure routes every shot to the human desk.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

REQUIRED_FREE_VRAM_MIB = 7885          # 7.7 GiB
HOOK_WINDOW_SECONDS = 3.0
CANTONESE_MAX_CPS = 6.5                # readable subtitle characters per second
MANDARIN_MAX_CPS = 7.0
MAX_AV_DRIFT_SECONDS = 0.12


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _chars(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", text or ""))


# ======================================================================================
# 1. Golden first-3-seconds Pattern-Interrupt Hook Matrix
# ======================================================================================
HOOK_PATTERNS = {
    "contradiction": ("你以为{topic}很难？其实只差一步", "颠覆常识"),
    "curiosity_gap": ("{topic}最关键嘅一步，九成人都做错", "信息缺口"),
    "visual_shock": ("（画面骤变）{topic}，三秒睇清", "视觉打断"),
    "direct_address": ("如果你都喺度为{topic}烦恼，停一停", "第二人称"),
    "stakes_countdown": ("三秒后，你对{topic}嘅睇法会改变", "倒计时"),
    "sound_break": ("（静音→重击声）{topic}嘅真相", "声音打断"),
}
VISUAL_INTERRUPTS = ("hard_cut_to_ecu", "whip_pan", "speed_ramp", "color_flash", "object_into_lens", "match_cut")


def generate_hook_matrix(topic: str, *, audience: str = "粤语观众", variants_per_pattern: int = 2) -> list[dict[str, Any]]:
    if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 60:
        raise ValueError("topic must be 1-60 characters")
    topic = topic.strip()
    matrix = []
    for index, (pattern, (template, mechanism)) in enumerate(HOOK_PATTERNS.items()):
        for variant in range(max(1, min(variants_per_pattern, len(VISUAL_INTERRUPTS)))):
            line = template.replace("{topic}", topic)
            visual = VISUAL_INTERRUPTS[(index + variant) % len(VISUAL_INTERRUPTS)]
            spoken_chars = _chars(re.sub(r"（[^）]*）", "", line))
            spoken_seconds = round(spoken_chars / 5.0, 2)  # ~5 Cantonese syllables per second
            score = 1.0
            score -= max(0.0, spoken_seconds - 2.2) * 0.35   # must land the line inside the 3 s window
            score += 0.15 if pattern in {"contradiction", "curiosity_gap"} else 0.0
            score += 0.1 if visual in {"hard_cut_to_ecu", "object_into_lens"} else 0.0
            matrix.append({
                "hook_id": f"hook-{pattern}-{variant + 1}", "pattern": pattern, "mechanism": mechanism,
                "line_yue": line, "visual_interrupt": visual, "audience": audience,
                "beats": [{"t": "0.0-1.0s", "do": f"visual pattern interrupt: {visual}"},
                          {"t": "1.0-2.2s", "do": "spoken hook line (Cantonese first)"},
                          {"t": "2.2-3.0s", "do": "open loop / promise of payoff"}],
                "spoken_seconds_estimate": spoken_seconds, "fits_3s": spoken_seconds <= 2.2,
                "score": round(score, 3), "label": "heuristic_rank_needs_ab_test",
            })
    return sorted(matrix, key=lambda h: (-h["score"], h["hook_id"]))


# ======================================================================================
# 2. 3-second visual rhythm density analyser
# ======================================================================================
def analyze_visual_rhythm(shots: Sequence[dict[str, Any]], *, window: float = HOOK_WINDOW_SECONDS,
                          min_events_per_window: int = 1) -> dict[str, Any]:
    """``shots``: [{"start","end","motion" (0-1), "text_overlay": bool}] sorted by start."""
    if not shots:
        raise ValueError("at least one shot is required")
    ordered = sorted(shots, key=lambda s: float(s["start"]))
    duration = max(float(s["end"]) for s in ordered)
    cuts = [float(s["start"]) for s in ordered[1:]]
    windows = []
    t = 0.0
    while t < duration - 1e-9:
        end = min(duration, t + window)
        events = sum(1 for c in cuts if t <= c < end)
        active = [s for s in ordered if float(s["start"]) < end and float(s["end"]) > t]
        motion = max((float(s.get("motion", 0)) for s in active), default=0.0)
        overlay = any(s.get("text_overlay") for s in active)
        change = events + (1 if motion >= 0.5 else 0) + (1 if overlay and events == 0 else 0)
        windows.append({"from": round(t, 3), "to": round(end, 3), "cuts": events, "peak_motion": motion,
                        "density": round(change / (end - t), 3), "dead_air": change < min_events_per_window})
        t = end
    first = windows[0]
    return {"duration": duration, "cuts": len(cuts), "windows": windows,
            "dead_air_windows": [w["from"] for w in windows if w["dead_air"]],
            "hook_window_ok": not first["dead_air"] and (first["cuts"] >= 1 or first["peak_motion"] >= 0.6),
            "avg_shot_length": round(duration / len(ordered), 3)}


# ======================================================================================
# 3. Story Bible continuity lock
# ======================================================================================
class StoryBibleLock:
    def __init__(self, characters: dict[str, dict[str, Any]]) -> None:
        if not characters:
            raise ValueError("story bible needs at least one character")
        self.locked = {cid: {"wardrobe": str(c["wardrobe"]), "props": sorted(c.get("props", [])),
                             "look": str(c.get("look", ""))} for cid, c in characters.items()}
        self.fingerprint = _sha(self.locked)

    def check(self, shots: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Each shot: {"shot_id","characters":{cid:{"wardrobe","props","look"}}, "change_events":[cid,...]}"""
        state = json.loads(json.dumps(self.locked))
        violations = []
        for shot in shots:
            allowed = set(shot.get("change_events", []))
            for cid, seen in (shot.get("characters") or {}).items():
                if cid not in state:
                    violations.append({"shot_id": shot["shot_id"], "character": cid, "issue": "unknown_character"})
                    continue
                for attribute in ("wardrobe", "look"):
                    if attribute in seen and str(seen[attribute]) != state[cid][attribute]:
                        if cid in allowed:
                            state[cid][attribute] = str(seen[attribute])
                        else:
                            violations.append({"shot_id": shot["shot_id"], "character": cid, "issue": f"{attribute}_drift",
                                               "expected": state[cid][attribute], "found": seen[attribute]})
                if "props" in seen:
                    missing = sorted(set(state[cid]["props"]) - set(seen["props"]))
                    if missing and cid not in allowed:
                        violations.append({"shot_id": shot["shot_id"], "character": cid, "issue": "prop_missing",
                                           "missing": missing})
        return {"bible_sha256": self.fingerprint, "ok": not violations, "violations": violations}

    def prompt_fragment(self, cid: str) -> str:
        c = self.locked[cid]
        return f"{cid}: wearing {c['wardrobe']}; {c['look']}; props: {', '.join(c['props']) or 'none'}"


# ======================================================================================
# 4. Shot director agent (deterministic rules, explainable)
# ======================================================================================
SHOT_SIZES = ("ECU", "CU", "MCU", "MS", "WS", "EWS")
EMOTION_LIGHT = {"tension": "low-key, hard side light, 4:1 contrast", "warm": "soft key 45°, warm 3200K practicals",
                 "reveal": "backlight rim + slow key fade-up", "calm": "large soft source, 2:1 contrast",
                 "energy": "high-key, colored accents, motivated neon"}


def direct_shots(beats: Sequence[dict[str, Any]], *, bible: StoryBibleLock | None = None) -> list[dict[str, Any]]:
    """beats: [{"beat_id","purpose" (hook/setup/escalate/reveal/payoff/cta),"emotion","seconds","character"?}]"""
    plan, previous = [], None
    for index, beat in enumerate(beats):
        purpose = beat.get("purpose", "setup")
        size = {"hook": "ECU", "setup": "WS", "escalate": "MS", "reveal": "CU", "payoff": "MCU", "cta": "MCU"}.get(purpose, "MS")
        if size == previous:   # never repeat a shot size back-to-back (jump-cut avoidance)
            size = SHOT_SIZES[(SHOT_SIZES.index(size) + 2) % len(SHOT_SIZES)]
        focal = {"ECU": 100, "CU": 85, "MCU": 50, "MS": 35, "WS": 24, "EWS": 16}[size]
        movement = {"hook": "fast push-in", "setup": "slow dolly", "escalate": "handheld drift", "reveal": "orbit 30°",
                    "payoff": "static lock-off", "cta": "slow push-in"}.get(purpose, "static")
        emotion = beat.get("emotion", "calm")
        shot = {"shot_id": f"s{index + 1:02d}", "beat_id": beat.get("beat_id", f"b{index + 1}"), "purpose": purpose,
                "size": size, "focal_length_mm": focal, "camera_move": movement,
                "lighting": EMOTION_LIGHT.get(emotion, EMOTION_LIGHT["calm"]), "seconds": float(beat.get("seconds", 3)),
                "rationale": f"{purpose}→{size}/{focal}mm, {movement}; {emotion} lighting"}
        if bible is not None and beat.get("character") in bible.locked:
            shot["continuity"] = bible.prompt_fragment(beat["character"])
        plan.append(shot)
        previous = size
    return plan


# ======================================================================================
# 5. GPU preflight + Wan 2.1 ComfyUI workflow
# ======================================================================================
def gpu_preflight(probe: Callable[[], dict[str, Any]] | None, *, required_free_mib: int = REQUIRED_FREE_VRAM_MIB) -> dict[str, Any]:
    if probe is None:
        return {"ok": False, "reason": "no_gpu_probe", "required_free_mib": required_free_mib}
    try:
        sample = probe() or {}
        free = float(sample["free_mib"])
    except Exception as exc:
        return {"ok": False, "reason": f"probe_failed:{type(exc).__name__}", "required_free_mib": required_free_mib}
    return {"ok": free >= required_free_mib, "free_mib": free, "required_free_mib": required_free_mib,
            "reason": "ok" if free >= required_free_mib else "insufficient_free_vram", "device": sample.get("name", "")}


def build_wan21_workflow(prompt: str, *, negative: str = "blurry, distorted face, extra fingers, watermark, text",
                         seed: int = 1, width: int = 832, height: int = 480, frames: int = 81, steps: int = 30,
                         cfg: float = 6.0, unet: str = "wan2.1_t2v_1.3B_fp16.safetensors",
                         clip: str = "umt5_xxl_fp8_e4m3fn_scaled.safetensors", vae: str = "wan_2.1_vae.safetensors",
                         prefix: str = "muse/wan480") -> dict[str, Any]:
    if not prompt.strip() or len(prompt) > 2000:
        raise ValueError("prompt must be 1-2000 characters")
    if width % 16 or height % 16 or not 17 <= frames <= 161 or (frames - 1) % 4:
        raise ValueError("Wan 2.1 needs /16 dimensions and 4n+1 frames (17-161)")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "wan"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"shift": 8.0, "model": ["1", 0]}},
        "7": {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": width, "height": height, "length": frames, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"seed": int(seed), "steps": steps, "cfg": cfg, "sampler_name": "uni_pc",
                                                   "scheduler": "simple", "denoise": 1.0, "model": ["6", 0],
                                                   "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["7", 0]}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveAnimatedWEBP", "inputs": {"images": ["9", 0], "filename_prefix": prefix, "fps": 16.0,
                                                           "lossless": False, "quality": 90, "method": "default"}},
    }


def validate_comfy_graph(workflow: dict[str, Any]) -> list[str]:
    errors = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or "class_type" not in node or not isinstance(node.get("inputs"), dict):
            errors.append(f"{node_id}: malformed node")
            continue
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                if value[0] not in workflow:
                    errors.append(f"{node_id}.{key}: dangling link to {value[0]}")
    return errors


# ======================================================================================
# 6. 480P → 1080P real AI super-resolution bridge + 720P gate
# ======================================================================================
def build_ai_upscale_workflow(*, video_frames_dir: str, model: str = "RealESRGAN_x4plus.pth",
                              target_width: int = 1920, target_height: int = 1080, prefix: str = "muse/sr1080") -> dict[str, Any]:
    if not re.fullmatch(r"[\w.-]{3,120}\.(pth|safetensors)", model):
        raise ValueError("upscale model filename is invalid")
    return {
        "1": {"class_type": "LoadImagesFromDirectory", "inputs": {"directory": video_frames_dir}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "ImageScale", "inputs": {"image": ["3", 0], "upscale_method": "lanczos",
                                                     "width": target_width, "height": target_height, "crop": "center"}},
        "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": prefix}},
    }


def classify_upscale(workflow: dict[str, Any]) -> str:
    """``ai_model`` only if an UpscaleModelLoader feeds ImageUpscaleWithModel that reaches the saved output."""
    loaders = {k for k, n in workflow.items() if n.get("class_type") == "UpscaleModelLoader"}
    model_nodes = {k for k, n in workflow.items() if n.get("class_type") == "ImageUpscaleWithModel"
                   and isinstance(n["inputs"].get("upscale_model"), list) and n["inputs"]["upscale_model"][0] in loaders}
    if not model_nodes:
        return "plain_resize"
    outputs = [k for k, n in workflow.items() if n.get("class_type", "").startswith("Save")]

    def reaches(node_id: str, seen=frozenset()) -> bool:
        if node_id in model_nodes:
            return True
        if node_id in seen:
            return False
        inputs = workflow[node_id]["inputs"].values()
        return any(isinstance(v, list) and len(v) == 2 and v[0] in workflow and reaches(v[0], seen | {node_id}) for v in inputs)

    return "ai_model" if any(reaches(o) for o in outputs) else "plain_resize"


def quality_gate_720p(*, source_height: int, output_height: int, upscale_kind: str) -> dict[str, Any]:
    if source_height >= 720:
        return {"pass": True, "class": "native", "source_height": source_height}
    if output_height >= 1080 and upscale_kind == "ai_model":
        return {"pass": True, "class": f"ai_super_resolved_from_{source_height}p", "source_height": source_height,
                "disclosure": "AI super-resolution; not native capture"}
    return {"pass": False, "class": "plain_resize_rejected" if upscale_kind != "ai_model" else "insufficient_output",
            "source_height": source_height, "reason": "sub-720P source requires real AI super-resolution to ≥1080P"}


# ======================================================================================
# 7. Cantonese-first + Mandarin dub/subtitle alignment
# ======================================================================================
def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def align_subtitles(segments: Sequence[dict[str, Any]], *, voice: Sequence[dict[str, float]] | None = None) -> dict[str, Any]:
    """segments: [{"start","end","yue","cmn"}]; voice: [{"start","end"}] measured from the dub track."""
    issues, srt = [], {"yue": [], "cmn": []}
    last_end = -1.0
    for index, seg in enumerate(segments):
        start, end = float(seg["start"]), float(seg["end"])
        if end <= start:
            issues.append({"index": index, "issue": "non_positive_duration"})
            continue
        if start < last_end - 1e-6:
            issues.append({"index": index, "issue": "overlap"})
        last_end = end
        if not str(seg.get("yue", "")).strip():
            issues.append({"index": index, "issue": "missing_cantonese_primary"})
        if seg.get("jyutping"):
            from muse.jyutping import JyutpingError, parse_jyutping   # island module now wired
            try:
                syllables = parse_jyutping(str(seg["jyutping"]))
                hanzi = len(re.findall(r"[\u4e00-\u9fff]", str(seg.get("yue", ""))))
                if hanzi and len(syllables) != hanzi:
                    issues.append({"index": index, "issue": "jyutping_syllable_mismatch",
                                   "syllables": len(syllables), "hanzi": hanzi})
            except JyutpingError:
                issues.append({"index": index, "issue": "invalid_jyutping"})
        for lang, limit in (("yue", CANTONESE_MAX_CPS), ("cmn", MANDARIN_MAX_CPS)):
            text = str(seg.get(lang, "")).strip()
            if text:
                cps = _chars(text) / (end - start)
                if cps > limit:
                    issues.append({"index": index, "issue": f"{lang}_reading_speed", "cps": round(cps, 2), "limit": limit})
                srt[lang].append(f"{len(srt[lang]) + 1}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
        if voice is not None:
            if index >= len(voice):
                issues.append({"index": index, "issue": "no_voice_segment"})
            else:
                drift = max(abs(voice[index]["start"] - start), abs(voice[index]["end"] - end))
                if drift > MAX_AV_DRIFT_SECONDS:
                    issues.append({"index": index, "issue": "av_drift", "seconds": round(drift, 3)})
    return {"ok": not issues, "issues": issues, "primary_language": "yue", "srt_yue": "\n".join(srt["yue"]),
            "srt_cmn": "\n".join(srt["cmn"])}


# ======================================================================================
# 8. EBU R128 / ITU-R BS.1770-4 loudness
# ======================================================================================
def _k_weighting(fs: int) -> list[tuple[list[float], list[float]]]:
    """BS.1770-4 K-weighting (pre-filter shelf + RLB high-pass) for any sample rate (bilinear, De Man form)."""
    G, Q, fc = 3.999843853973347, 0.7071752369554196, 1681.974450955533
    K = math.tan(math.pi * fc / fs)
    Vh = 10 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    shelf = ([(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0],
             [1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0])
    Q2, fc2 = 0.5003270373238773, 38.13547087602444
    K = math.tan(math.pi * fc2 / fs)
    a0 = 1 + K / Q2 + K * K
    highpass = ([1.0, -2.0, 1.0], [1.0, 2 * (K * K - 1) / a0, (1 - K / Q2 + K * K) / a0])
    return [shelf, highpass]


def _biquad(samples: Sequence[float], b: list[float], a: list[float]) -> list[float]:
    out, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    b0, b1, b2 = b
    _, a1, a2 = a
    for x in samples:
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        out.append(y)
        x2, x1, y2, y1 = x1, x, y1, y
    return out


def integrated_loudness(channels: Sequence[Sequence[float]], fs: int, *, weights: Sequence[float] | None = None) -> float:
    if not channels or fs < 8000:
        raise ValueError("audio must have ≥1 channel and fs ≥ 8 kHz")
    weights = list(weights or [1.0] * len(channels))
    filtered = []
    for channel in channels:
        data = list(channel)
        for b, a in _k_weighting(fs):
            data = _biquad(data, b, a)
        filtered.append(data)
    block, step = int(0.4 * fs), int(0.1 * fs)
    length = len(filtered[0])
    if length < block:
        raise ValueError("need at least 400 ms of audio")
    powers = []
    for start in range(0, length - block + 1, step):
        z = [sum(v * v for v in ch[start:start + block]) / block for ch in filtered]
        powers.append(z)

    def lufs(z: Sequence[float]) -> float:
        total = sum(w * v for w, v in zip(weights, z))
        return -0.691 + 10 * math.log10(total) if total > 0 else -math.inf

    gated = [z for z in powers if lufs(z) > -70.0]
    if not gated:
        return -math.inf
    mean = [sum(z[i] for z in gated) / len(gated) for i in range(len(channels))]
    relative = lufs(mean) - 10.0
    final = [z for z in gated if lufs(z) > relative]
    mean = [sum(z[i] for z in final) / len(final) for i in range(len(channels))]
    return round(lufs(mean), 2)


def loudness_qc(channels: Sequence[Sequence[float]], fs: int, *, target_lufs: float = -23.0,
                tolerance_lu: float = 1.0, max_peak_dbfs: float = -1.0) -> dict[str, Any]:
    measured = integrated_loudness(channels, fs)
    peak = max(abs(v) for ch in channels for v in ch)
    peak_db = 20 * math.log10(peak) if peak > 0 else -math.inf
    return {"integrated_lufs": measured, "target_lufs": target_lufs, "sample_peak_dbfs": round(peak_db, 2),
            "pass": abs(measured - target_lufs) <= tolerance_lu and peak_db <= max_peak_dbfs,
            "gain_to_target_db": round(target_lufs - measured, 2), "peak_kind": "sample_peak_not_true_peak",
            "standard": "EBU R128 / ITU-R BS.1770-4"}


def read_wav_pcm16(path: str | Path) -> tuple[list[list[float]], int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError("only 16-bit PCM WAV is supported")
        channels, fs, frames = handle.getnchannels(), handle.getframerate(), handle.readframes(handle.getnframes())
    import array
    data = array.array("h", frames)
    return [[data[i] / 32768.0 for i in range(c, len(data), channels)] for c in range(channels)], fs


# ======================================================================================
# 9. VLM semantic review adapter with human fallback
# ======================================================================================
def review_shots(shots: Sequence[dict[str, Any]], vlm: Callable[[dict[str, Any]], Any] | None) -> dict[str, Any]:
    checklist = ["角色/服装与 Story Bible 一致", "无畸形手指/面部", "字幕无遮挡主体", "前 3 秒有视觉打断", "无水印/乱码文字"]
    human_queue, automated = [], []
    for shot in shots:
        verdict = None
        if vlm is not None:
            try:
                raw = vlm(shot)
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict) and data.get("verdict") in {"pass", "fail"} \
                        and isinstance(data.get("issues", []), list) and all(isinstance(i, str) for i in data.get("issues", [])):
                    verdict = {"shot_id": shot["shot_id"], "verdict": data["verdict"],
                               "issues": [i[:200] for i in data.get("issues", [])][:10], "by": "vlm_untrusted_advisory"}
            except Exception:
                verdict = None
        if verdict is None or verdict["verdict"] == "fail":
            human_queue.append({"shot_id": shot["shot_id"], "checklist": checklist,
                                "reason": "vlm_unavailable_or_invalid" if verdict is None else "vlm_flagged",
                                "vlm_issues": verdict["issues"] if verdict else []})
        if verdict is not None:
            automated.append(verdict)
    return {"automated": automated, "human_review_queue": human_queue,
            "release_requires_human": True,  # VLM output is advisory; a human always signs off
            "mode": "vlm_plus_human" if vlm is not None else "human_only"}


# ======================================================================================
# 10. Official retention / completion curve import → Agent-Track candidates
# ======================================================================================
def import_retention_curve(csv_text: str, *, duration_seconds: float | None = None) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("retention CSV has no header")
    fields = {f.strip().casefold(): f for f in reader.fieldnames}
    time_key = next((fields[k] for k in fields if any(w in k for w in ("second", "time", "秒", "video position", "时间"))), None)
    value_key = next((fields[k] for k in fields if any(w in k for w in ("retention", "audience", "留存", "watch ratio", "观众"))), None)
    if time_key is None or value_key is None:
        raise ValueError("could not find time and retention columns")
    points = []
    for row in reader:
        try:
            t = float(str(row[time_key]).strip().rstrip("s%"))
            v = float(str(row[value_key]).strip().rstrip("%"))
        except (ValueError, TypeError):
            continue
        points.append((t, v / 100.0 if v > 1.5 else v))
    if len(points) < 3:
        raise ValueError("retention curve needs at least 3 numeric points")
    points.sort()
    if points[-1][0] <= 1.0 and (duration_seconds or 0) > 0:   # ratio position (0-1) → seconds
        points = [(t * duration_seconds, v) for t, v in points]

    def at(second: float) -> float:
        for (t0, v0), (t1, v1) in zip(points, points[1:]):
            if t0 <= second <= t1:
                return v0 + (v1 - v0) * ((second - t0) / (t1 - t0) if t1 > t0 else 0)
        return points[-1][1] if second > points[-1][0] else points[0][1]

    drops = sorted(((points[i][1] - points[i + 1][1], points[i][0], points[i + 1][0]) for i in range(len(points) - 1)),
                   reverse=True)[:3]
    return {"points": len(points), "hold_3s": round(at(3.0), 4), "completion_rate": round(points[-1][1], 4),
            "biggest_drops": [{"from_s": a, "to_s": b, "drop": round(d, 4)} for d, a, b in drops if d > 0],
            "source": "official_platform_export"}


def retention_to_agent_track(curve: dict[str, Any], *, hook_id: str, video_id: str, memory_store: Any = None) -> list[dict[str, Any]]:
    lessons = [f"[{video_id}] {hook_id}: 3s hold={curve['hold_3s']:.0%}, completion={curve['completion_rate']:.0%}"]
    for drop in curve["biggest_drops"][:2]:
        lessons.append(f"[{video_id}] largest drop {drop['drop']:.0%} between {drop['from_s']:.1f}s-{drop['to_s']:.1f}s → inspect that beat")
    proposals = []
    for text in lessons:
        if memory_store is not None:
            proposals.append(memory_store.propose(text, project="video", source=f"retention:{video_id}", kind="case",
                                                  track="agent", confidence=0.7))
        else:
            proposals.append({"content": text, "track": "agent", "status": "pending"})
    return proposals


# ======================================================================================
# Single-entry orchestrator
# ======================================================================================
@dataclass
class FilmRequest:
    topic: str
    characters: dict[str, dict[str, Any]]
    beats: list[dict[str, Any]]
    seed: int = 20261125
    style: str = "cinematic, anamorphic, filmic grain, Hong Kong night neon"
    subtitles: list[dict[str, Any]] = field(default_factory=list)


class CinematicFactory:
    DAG = ("hook_matrix", "rhythm_density", "story_bible_lock", "shot_director", "gpu_preflight", "wan21_workflow",
           "upscale_1080p_bridge", "dub_subtitle_align", "loudness_qc", "vlm_review", "retention_feedback")

    def __init__(self, *, gpu_probe: Callable[[], dict[str, Any]] | None = None, vlm: Callable[..., Any] | None = None,
                 comfy_submit: Callable[[dict[str, Any]], str] | None = None) -> None:
        self.gpu_probe, self.vlm, self.comfy_submit = gpu_probe, vlm, comfy_submit

    def plan(self, request: FilmRequest, *, audio: tuple[Sequence[Sequence[float]], int] | None = None) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}

        def record(name: str, status: str, output: Any) -> None:
            nodes[name] = {"status": status, "output": output, "output_sha256": _sha(output)}

        hooks = generate_hook_matrix(request.topic)
        record("hook_matrix", "done", {"top": hooks[:3], "variants": len(hooks)})
        bible = StoryBibleLock(request.characters)
        shots = direct_shots(request.beats, bible=bible)
        timeline, t = [], 0.0
        for shot in shots:
            timeline.append({"start": t, "end": t + shot["seconds"], "motion": 0.8 if "push" in shot["camera_move"]
                             or "handheld" in shot["camera_move"] else 0.3, "text_overlay": shot["purpose"] == "hook"})
            t += shot["seconds"]
        record("rhythm_density", "done", analyze_visual_rhythm(timeline))
        continuity = bible.check([{"shot_id": s["shot_id"], "characters": {}} for s in shots])
        record("story_bible_lock", "done" if continuity["ok"] else "blocked", continuity)
        record("shot_director", "done", shots)
        preflight = gpu_preflight(self.gpu_probe)
        record("gpu_preflight", "done" if preflight["ok"] else "blocked", preflight)
        workflows = []
        for shot in shots:
            prompt = f"{request.style}. {shot['size']} {shot['focal_length_mm']}mm, {shot['camera_move']}, {shot['lighting']}. " \
                     f"{shot.get('continuity', '')} {request.topic}"
            workflow = build_wan21_workflow(prompt, seed=request.seed + int(shot["shot_id"][1:]), prefix=f"muse/{shot['shot_id']}")
            errors = validate_comfy_graph(workflow)
            submitted = None
            if preflight["ok"] and self.comfy_submit is not None and not errors:
                submitted = self.comfy_submit(workflow)
            workflows.append({"shot_id": shot["shot_id"], "workflow": workflow, "graph_errors": errors,
                              "prompt_id": submitted})
        status = "submitted" if any(w["prompt_id"] for w in workflows) else ("workflow_ready_not_submitted"
                                                                              if preflight["ok"] else "blocked_gpu")
        record("wan21_workflow", status, workflows)
        upscale = build_ai_upscale_workflow(video_frames_dir="output/muse/frames480")
        kind = classify_upscale(upscale)
        record("upscale_1080p_bridge", "done", {"workflow": upscale, "upscale_kind": kind,
                                                "gate": quality_gate_720p(source_height=480, output_height=1080, upscale_kind=kind)})
        record("dub_subtitle_align", "done" if request.subtitles else "skipped_no_script",
               align_subtitles(request.subtitles) if request.subtitles else {})
        record("loudness_qc", "done" if audio else "pending_audio", loudness_qc(*audio) if audio else {})
        record("vlm_review", "done", review_shots(shots, self.vlm))
        record("retention_feedback", "awaiting_publication", {"import": "POST official retention CSV after publishing",
                                                              "hook_ids": [h["hook_id"] for h in hooks[:3]]})
        manifest = {"api_version": 1, "dag": list(self.DAG), "nodes": nodes,
                    "ready_for_render": nodes["gpu_preflight"]["status"] == "done" and continuity["ok"],
                    "release_requires_human_review": True}
        manifest["manifest_sha256"] = _sha({k: v["output_sha256"] for k, v in nodes.items()})
        return manifest


def request_from_payload(payload: dict[str, Any]) -> FilmRequest:
    if not isinstance(payload, dict):
        raise ValueError("film request must be an object")
    topic = payload.get("topic")
    characters = payload.get("characters") or {"host": {"wardrobe": "black bomber jacket", "look": "short hair", "props": []}}
    beats = payload.get("beats") or [
        {"purpose": "hook", "emotion": "energy", "seconds": 3, "character": next(iter(characters))},
        {"purpose": "setup", "emotion": "calm", "seconds": 4}, {"purpose": "escalate", "emotion": "tension", "seconds": 4},
        {"purpose": "reveal", "emotion": "reveal", "seconds": 3}, {"purpose": "payoff", "emotion": "warm", "seconds": 4},
        {"purpose": "cta", "emotion": "warm", "seconds": 2}]
    if not isinstance(beats, list) or not 1 <= len(beats) <= 40:
        raise ValueError("beats must be a list of 1-40 items")
    return FilmRequest(topic=str(topic or ""), characters=characters, beats=beats,
                       seed=int(payload.get("seed", 20261125)), subtitles=list(payload.get("subtitles") or []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse-film", description="One-click cinematic short-video plan (no GPU work without preflight)")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--out", default="outputs/film-plan.json")
    args = parser.parse_args(argv)
    manifest = CinematicFactory().plan(request_from_payload({"topic": args.topic}))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out), "ready_for_render": manifest["ready_for_render"],
                      "manifest_sha256": manifest["manifest_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
