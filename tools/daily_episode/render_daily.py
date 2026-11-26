"""Muse daily episode renderer — standalone, no subprocess/network.

Usage (inside an unattended run, on-device Python with write access):
    script = r"<MuseRoot>\\tools\\daily_episode\\render_daily.py"
    ns = {"__file__": script}
    exec(open(script, encoding="utf-8").read(), ns)
    result = ns["render_episode"](r"<MuseRoot>\\outputs\\daily\\ep-007\\spec.json")

The Muse root is taken from MUSE_ROOT, else derived from __file__ (tools/daily_episode/ -> root).

spec.json:
{
  "episode": 7, "title": "...", "steps": ["5 short labels, <=4 chars each"],
  "slides": [{"eyebrow": "...", "title": "line1\\nline2", "body": "...", "tag": "..."} x5]
}
Writes scene-01..05.png (1080x1920) + preview.png next to spec.json. Never overwrites
existing PNGs. Visual style matches episodes 1-6 (original typography, Noto Sans CJK, SIL OFL).
"""
import json
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

def _muse_root() -> Path:
    override = os.environ.get("MUSE_ROOT", "").strip()
    if override:
        return Path(override)
    here = globals().get("__file__")
    if here:
        return Path(here).resolve().parents[2]
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / "tools" / "daily_episode" / "render_daily.py").is_file():
            return candidate
    return Path.cwd()


MUSE_ROOT = _muse_root()
FONT = MUSE_ROOT / "assets" / "fonts" / "NotoSansCJKhk-VF.ttf"
LICENSE = MUSE_ROOT / "assets" / "fonts" / "NotoCJK-OFL.txt"
WIDTH, HEIGHT = 1080, 1920
NOSTART = "。，、；：？！」』）》〉…%,.;:?!)"
NOEND = "「『（《〈"
_FONT_CACHE = {}


def _load_font(size, bold=False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    if "SIL OPEN FONT LICENSE" not in LICENSE.read_text(encoding="utf-8"):
        raise RuntimeError("font licence check failed")
    font = ImageFont.truetype(str(FONT), size)
    axes = font.get_variation_axes()
    idx = None
    for i, axis in enumerate(axes):
        name = axis.get("name", "")
        name = name.decode("ascii", "ignore") if isinstance(name, bytes) else str(name)
        if name.lower() in {"weight", "wght"}:
            idx = i
    if idx is None:
        raise RuntimeError("font has no weight axis")
    values = [axis["default"] for axis in axes]
    values[idx] = 700 if bold else 400
    font.set_variation_by_axes(values)
    _FONT_CACHE[key] = font
    return font


def wrap_text(draw, text, font, max_width):
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        lines.append(current)
    out = []
    for line in lines:  # kinsoku: no closing punctuation at line start
        while out and line and line[0] in NOSTART:
            out[-1] += line[0]
            line = line[1:]
        if line:
            out.append(line)
    for i in range(len(out) - 1):  # no opening bracket at line end
        if out[i] and out[i][-1] in NOEND:
            out[i + 1] = out[i][-1] + out[i + 1]
            out[i] = out[i][:-1]
    return out


def _background():
    grad = Image.new("RGB", (1, HEIGHT))
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        grad.putpixel((0, y), (7 + int(9 * t), 12 + int(12 * t), 29 + int(28 * t)))
    image = grad.resize((WIDTH, HEIGHT))
    # soft radial glow: draw small, blur, upscale (smooth, fast)
    small = Image.new("L", (WIDTH // 10, HEIGHT // 10), 0)
    ImageDraw.Draw(small).ellipse((17 - 60, 34 - 70, 17 + 60, 34 + 70), fill=70)
    glow = small.filter(ImageFilter.GaussianBlur(18)).resize((WIDTH, HEIGHT), Image.BICUBIC)
    tint = Image.new("RGB", (WIDTH, HEIGHT), (21, 41, 66))
    return Image.composite(tint, image, glow)


def render_scene(index, total, slide, steps, destination):
    image = _background()
    draw = ImageDraw.Draw(image, "RGBA")
    gold = (233, 199, 123, 230)
    cyan = (124, 232, 239, 235)
    muted = (187, 201, 225, 245)
    white = (241, 246, 255, 255)
    for radius, alpha in ((390, 42), (300, 55), (210, 35)):
        draw.ellipse((WIDTH - 160 - radius, 170 - radius, WIDTH - 160 + radius, 170 + radius),
                     outline=(124, 232, 239, alpha), width=2)
    draw.arc((80, 1040, 1000, 1960), 188, 332, fill=(233, 199, 123, 90), width=3)
    for n in range(36):
        x = (n * 197 + 83) % WIDTH
        y = (n * 313 + 119) % HEIGHT
        draw.ellipse((x, y, x + 3, y + 3), fill=(220, 234, 255, 105))
    small, title_font = _load_font(26, True), _load_font(76, True)
    body_font, tag_font, footer_font = _load_font(39), _load_font(27, True), _load_font(22)
    draw.rounded_rectangle((72, 94, 1008, 1810), radius=34, outline=(137, 174, 255, 48), width=2)
    draw.text((116, 150), slide["eyebrow"], font=small, fill=cyan)
    draw.line((116, 220, 964, 220), fill=(124, 232, 239, 105), width=2)
    y = 360
    for line in wrap_text(draw, slide["title"], title_font, 830)[:3]:
        draw.text((116, y), line, font=title_font, fill=white, stroke_width=1, stroke_fill=(12, 22, 43, 255))
        y += 112
    y += 88
    for line in wrap_text(draw, slide["body"], body_font, 820)[:5]:
        draw.text((120, y), line, font=body_font, fill=muted)
        y += 72
    # 5-step progress motif (same grammar as episodes 4-6)
    xs = [150 + i * 195 for i in range(5)]
    py = 1180
    draw.line((xs[0], py, xs[-1], py), fill=(124, 232, 239, 90), width=4)
    draw.line((xs[0], py, xs[min(index, 4)], py), fill=gold, width=6)
    lf, nf = _load_font(28, True), _load_font(26, True)
    for i, x in enumerate(xs):
        on, done = i == index, i < index
        fill = (233, 199, 123, 240) if on else ((40, 70, 110, 230) if done else (12, 29, 51, 235))
        draw.ellipse((x - 34, py - 34, x + 34, py + 34), fill=fill,
                     outline=gold if (on or done) else (150, 165, 195, 200), width=3)
        num = str(i + 1)
        draw.text((x - draw.textlength(num, font=nf) / 2, py - 17), num, font=nf,
                  fill=(20, 16, 6) if on else (cyan if done else (150, 165, 195, 200)))
        label = steps[i][:5]
        draw.text((x - draw.textlength(label, font=lf) / 2, py + 52), label, font=lf,
                  fill=gold if on else (150, 165, 195, 200))
    draw.rounded_rectangle((116, 1455, 964, 1570), radius=18, fill=(12, 29, 51, 225),
                           outline=(233, 199, 123, 110), width=2)
    ty = 1492
    for line in wrap_text(draw, slide["tag"], tag_font, 770)[:2]:
        draw.text((150, ty), line, font=tag_font, fill=gold)
        ty += 40
    draw.text((116, 1702), "MUSE  /  AI 查证系列", font=footer_font, fill=(166, 179, 207, 220))
    draw.text((900, 1702), "%02d / %02d" % (index + 1, total), font=footer_font, fill=cyan)
    image.save(destination, format="PNG", optimize=True)


def validate_spec(spec):
    problems = []
    slides = spec.get("slides")
    if not isinstance(slides, list) or len(slides) != 5:
        problems.append("need exactly 5 slides")
        return problems
    steps = spec.get("steps")
    if not isinstance(steps, list) or len(steps) != 5 or any(not isinstance(s, str) or not s or len(s) > 5 for s in steps):
        problems.append("steps: 5 labels, 1-5 chars each")
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for i, s in enumerate(slides):
        for k in ("eyebrow", "title", "body", "tag"):
            if not isinstance(s.get(k), str) or not s[k].strip():
                problems.append("slide %d missing %s" % (i + 1, k))
        if problems:
            continue
        if len(wrap_text(probe, s["title"], _load_font(76, True), 830)) > 2:
            problems.append("slide %d title wraps to >2 lines; shorten" % (i + 1))
        if len(wrap_text(probe, s["body"], _load_font(39), 820)) > 4:
            problems.append("slide %d body wraps to >4 lines; shorten" % (i + 1))
        if len(wrap_text(probe, s["tag"], _load_font(27, True), 770)) > 1:
            problems.append("slide %d tag wraps to >1 line; shorten" % (i + 1))
    return problems


def render_episode(spec_path):
    spec_path = Path(spec_path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    problems = validate_spec(spec)
    if problems:
        return {"status": "invalid_spec", "problems": problems}
    out = spec_path.parent
    paths = []
    for i, slide in enumerate(spec["slides"]):
        p = out / ("scene-%02d.png" % (i + 1))
        if p.exists():
            return {"status": "refused_overwrite", "path": str(p)}
        render_scene(i, 5, slide, spec["steps"], p)
        paths.append(p)
    w, h = 216, 384
    sheet = Image.new("RGB", (w * 5, h), "black")
    for i, p in enumerate(paths):
        sheet.paste(Image.open(p).resize((w, h)), (i * w, 0))
    sheet.save(out / "preview.png")
    return {"status": "rendered", "files": [p.name for p in paths] + ["preview.png"], "folder": str(out)}
