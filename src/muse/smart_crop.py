"""Focus-aware deterministic image cropping and Ken Burns parameter planning."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _pillow():
    try:
        from PIL import Image, ImageFilter, ImageStat
    except ImportError as exc:
        raise RuntimeError("Pillow is required for smart_crop; it is an optional dependency") from exc
    return Image, ImageFilter, ImageStat


def _ratio(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("target_ratio must be a positive number")
    ratio = float(value)
    if ratio <= 0 or ratio > 100:
        raise ValueError("target_ratio must be between 0 and 100")
    return ratio


def smart_crop(src: str | Path, dest: str | Path, target_ratio: float, *, focus_x: float = 0.5, focus_y: float = 0.5) -> dict[str, Any]:
    """Crop the source image without modifying it, favoring focus and saliency."""
    Image, ImageFilter, ImageStat = _pillow()
    ratio = _ratio(target_ratio)
    for name, value in (("focus_x", focus_x), ("focus_y", focus_y)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
    source = Path(src)
    target = Path(dest)
    if source.resolve() == target.resolve():
        raise ValueError("source and destination must differ")
    if not source.is_file():
        raise FileNotFoundError(source)
    if not target.parent.is_dir():
        raise FileNotFoundError(target.parent)
    if target.exists():
        raise FileExistsError(target)
    with Image.open(source) as image:
        image.load()
        width, height = image.size
        if width < 16 or height < 16:
            raise ValueError("source image is too small for smart cropping")

        # Maximal inscribed rectangle with the requested aspect ratio.
        if width / height > ratio:
            crop_h, crop_w = height, int(round(height * ratio))
        else:
            crop_w, crop_h = width, int(round(width / ratio))
        crop_w = max(16, min(crop_w, width))
        crop_h = max(16, min(crop_h, height))

        # Saliency proxy: strong local contrast, downsampled for cheap scanning.
        small = image.convert("L")
        small.thumbnail((160, 160))
        edges = small.filter(ImageFilter.FIND_EDGES)
        pixels = edges.load()
        sw, sh = edges.size
        step_x = max(1, width // sw)
        step_y = max(1, height // sh)
        candidates = []
        for tile_x in range(0, max(1, width - crop_w + 1), max(1, crop_w // 8)):
            for tile_y in range(0, max(1, height - crop_h + 1), max(1, crop_h // 8)):
                saliency = 0.0
                for sy in range(sh):
                    for sx in range(sw):
                        px = (sx + 0.5) * step_x
                        py = (sy + 0.5) * step_y
                        if tile_x <= px <= tile_x + crop_w and tile_y <= py <= tile_y + crop_h:
                            saliency += pixels[sx, sy]
                focus_penalty = (
                    abs((tile_x + crop_w / 2) / width - focus_x)
                    + abs((tile_y + crop_h / 2) / height - focus_y)
                )
                candidates.append((saliency / max(1, sw * sh) - 1.5 * focus_penalty, tile_x, tile_y))
        _, left, top = max(candidates, key=lambda item: (item[0], -item[1], -item[2]))
        left = int(max(0, min(left, width - crop_w)))
        top = int(max(0, min(top, height - crop_h)))
        box = (left, top, left + crop_w, top + crop_h)
        image.crop(box).save(target)
    return {"crop_box": box, "output": str(target), "source": str(source.resolve()),
            "target_ratio": ratio, "focus": (focus_x, focus_y), "pillow_required": True}


def ken_burns_filter(effect: str, duration: float, *, fps: int = 30) -> str:
    frames = max(2, round(float(duration) * fps))
    if effect not in {"push_in", "pull_out", "drift_left", "drift_right"}:
        raise ValueError("effect must be push_in, pull_out, drift_left or drift_right")
    if effect == "push_in":
        return f"/* push_in */ zoompan=z='min(zoom+0.0015,1.25)':d={frames}:s=1920x1080:fps={fps}"
    if effect == "pull_out":
        return f"/* pull_out */ zoompan=z='if(eq(on,1),1.25,max(zoom-0.0015,1.0))':d={frames}:s=1920x1080:fps={fps}"
    if effect == "drift_left":
        return f"/* drift_left */ zoompan=z='1.08':x='iw/2-(iw/zoom/2)-(on/{frames})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={fps}"
    if effect == "drift_right":
        return f"/* drift_right */ zoompan=z='1.08':x='(on/{frames})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps={fps}"
    raise ValueError("effect must be push_in, pull_out, drift_left or drift_right")


def to_ken_burns(asset: Any, duration: float, effect: str = "push_in", *, fps: int = 30) -> dict[str, Any]:
    """Return a bounded FFmpeg zoompan plan; no files or processes are touched."""
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 1 <= duration <= 60:
        raise ValueError("duration must be between 1 and 60 seconds")
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= 60:
        raise ValueError("fps must be between 1 and 60")
    url = getattr(asset, "url", asset if isinstance(asset, str) else "")
    return {
        "effect": effect,
        "source_url": str(url)[:4096],
        "options": [effect],
        "duration_seconds": float(duration),
        "fps": fps,
        "frames": max(2, round(float(duration) * fps)),
        "filter": ken_burns_filter(effect, float(duration), fps=fps),
    }
