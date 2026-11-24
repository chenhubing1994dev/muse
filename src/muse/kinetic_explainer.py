from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
HEIGHT = 1920
FPS = 24
SECONDS_PER_SCENE = 8
SCENE_FRAMES = FPS * SECONDS_PER_SCENE

SLIDES = (
    {
        "eyebrow": "MUSE FIELD GUIDE 001",
        "title": "AI 答得好肯定，\n就代表真係啱？",
        "body": "流暢嘅答案，可能包含錯誤引用、過期數字，甚至不存在嘅來源。",
        "tag": "流暢 ≠ 正確",
    },
    {
        "eyebrow": "01 / DECOMPOSE",
        "title": "第一步：拆開\n每一條主張",
        "body": "圈出人名、日期、數字、研究結果。將一大段答案拆成可以逐項核對嘅小主張。",
        "tag": "唔好一次過照單全收",
    },
    {
        "eyebrow": "02 / TRACE",
        "title": "第二步：追返\n原始來源",
        "body": "優先查原始研究、官方文件、原始數據。核對作者、日期、版本同適用範圍。",
        "tag": "原文有冇真正支持個講法？",
    },
    {
        "eyebrow": "03 / CHALLENGE",
        "title": "第三步：主動\n搵反面證據",
        "body": "搜尋相反結果、勘誤、撤回同其他解釋。唔好只搵支持自己想法嘅資料。",
        "tag": "試下推翻自己嘅假設",
    },
    {
        "eyebrow": "04 / UNCERTAINTY",
        "title": "證據不足，\n就講未證實",
        "body": "分清已支持、部分支持、互相矛盾同未知。唔好用猜測填補資料空白。",
        "tag": "保留範圍與不確定性",
    },
    {
        "eyebrow": "SOURCE NOTE",
        "title": "NIST 都提醒：\nAI 可能自信地出錯",
        "body": "NIST AI 600-1 討論生成式 AI 可能自信地產生虛構或錯誤內容。呢套三步流程係 Muse 原創整理，並非 NIST 官方方法或背書。",
        "tag": "來源：NIST AI 600-1 · 2024-07-26",
    },
    {
        "eyebrow": "YOUR NEXT STEP",
        "title": "下一次問 AI，\n記住做呢三件事",
        "body": "拆主張 → 查原文 → 搵反證。每一步都留下來源、日期同判斷理由。",
        "tag": "先核對，再採納",
    },
    {
        "eyebrow": "FREE FIELD CHECKLIST",
        "title": "免費下載\nAI 三步查證卡",
        "body": "一頁清單，幫你記錄主張、原始來源、反面證據同未解決嘅問題。",
        "tag": "MUSE · 證據先於確定性",
    },
)


SOCIAL_COPY = """# Muse AI 三步查證｜五平台發布草稿

> 本片為 64 秒靜音動態圖文。AI 輔助策劃與文案，畫面由程序繪製；沒有旁白、第三方音樂或第三方影片。發布前仍需核對平台披露要求及素材權利。

## YouTube Shorts
**標題：** AI 答得好肯定，就代表真係啱？
**說明：** AI 答得流暢，唔代表每句都正確。記住三步：拆開主張、查原始來源、主動搵反證。證據不足，就標記未證實。免費查證卡連結會在網站正式部署後補上。
**標籤：** #AI查證 #人工智能 #FactChecking #Muse

## TikTok
**文案：** AI 越講得肯定，越要睇證據。三步：拆主張、查原文、搵反證。唔肯定就唔好扮肯定。
**CTA：** 留言講下你遇過最離譜嘅 AI 錯誤答案。
**標籤：** #AI工具 #AI技巧 #查證 #學習方法

## Instagram Reels
**文案：** 流暢 ≠ 正確。下次用 AI，試下逐條拆開主張、追返原始來源，再主動搜尋相反證據。保留不確定性，先係負責任嘅答案。
**CTA：** 收藏呢三步，下次查答案時用。
**標籤：** #AIliteracy #FactCheck #ResponsibleAI #Muse

## Bilibili
**標題：** AI 回答得很肯定？先用這三步查證
**简介：** 拆解具體主張、追查原始來源、主動尋找反面證據。NIST AI 600-1 討論生成式 AI 的 confabulation 風險；本片三步流程為 Muse 原創編輯整理，並非 NIST 官方方法或背書。
**标签：** AI,人工智能,知识科普,信息检索,事实核查

## 小红书（简体中文编辑版）
**标题：** AI说得很肯定，就一定是真的吗？
**正文：** 别把流畅当成正确。检查 AI 答案，可以先做三件事：1. 拆开人名、日期、数字等具体主张；2. 回到原始研究、官方文件或原始数据；3. 主动寻找相反结果、勘误和其他解释。证据不足，就标记“未证实”。这套流程是 Muse 的原创编辑整理，不是 NIST 官方方法或认证。
**CTA：** 收藏这份三步清单，下次提问后逐项核对。
**标签：** #AI工具 #效率提升 #知识管理 #信息辨别

## 发布前检查
- 替换为已确认归属项目方的 HTTPS 免费清单网址；当前不放占位链接。
- 检查字幕/封面/手机端安全区及平台当前视频规格。
- 确认字体授权、编辑审校与 NIST 来源说明。
- 不声称已发布、已获平台推荐、已有流量或收入。
- 只有完成授权与披露检查后，才可从各平台官方界面手动发布。
"""
def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
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
    return lines


FONT_SOURCE_URL = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/Variable/TTF/NotoSansCJKhk-VF.ttf"
FONT_LICENSE_URL = "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE"


def _font_path(bold: bool = False) -> Path:
    del bold
    project_root = Path(__file__).resolve().parents[2]
    font_path = project_root / "assets" / "fonts" / "NotoSansCJKhk-VF.ttf"
    license_path = project_root / "assets" / "fonts" / "NotoCJK-OFL.txt"
    if not font_path.is_file():
        raise FileNotFoundError(f"Bundled Noto Sans CJK HK font is missing: {font_path}")
    if not license_path.is_file():
        raise FileNotFoundError(f"Bundled Noto CJK OFL license is missing: {license_path}")
    license_text = license_path.read_text(encoding="utf-8")
    if "SIL OPEN FONT LICENSE" not in license_text or "Version 1.1" not in license_text:
        raise RuntimeError("Bundled Noto font license must be verified as SIL OFL 1.1")
    return font_path


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_font_path()), size)
    axes = font.get_variation_axes()
    weight_index = next(
        (
            index for index, axis in enumerate(axes)
            if (
                (axis_name := axis.get("name", ""))
                and (
                    axis_name.decode("ascii", errors="ignore") if isinstance(axis_name, bytes) else str(axis_name)
                ).lower() in {"weight", "wght"}
            )
        ),
        None,
    )
    if weight_index is None:
        raise RuntimeError("Bundled Noto Sans CJK HK font does not expose a weight variation axis")
    values = [axis["default"] for axis in axes]
    values[weight_index] = 700 if bold else 400
    font.set_variation_by_axes(values)
    return font


def render_scene(index: int, slide: dict[str, str], destination: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = image.load()
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        base = (7 + int(9 * t), 12 + int(12 * t), 29 + int(28 * t))
        for x in range(WIDTH):
            glow = max(0.0, 1.0 - (((x - 170) / 760) ** 2 + ((y - 340) / 850) ** 2) ** 0.5)
            pixels[x, y] = (
                min(255, base[0] + int(14 * glow)),
                min(255, base[1] + int(27 * glow)),
                min(255, base[2] + int(43 * glow)),
            )

    draw = ImageDraw.Draw(image, "RGBA")
    gold = (233, 199, 123, 230)
    cyan = (124, 232, 239, 235)
    muted = (187, 201, 225, 245)
    white = (241, 246, 255, 255)

    # Original vector decoration; no third-party footage, photos, or stock media.
    for radius, alpha in ((390, 42), (300, 55), (210, 35)):
        draw.ellipse((WIDTH - 160 - radius, 170 - radius, WIDTH - 160 + radius, 170 + radius),
                     outline=(124, 232, 239, alpha), width=2)
    draw.arc((80, 1040, 1000, 1960), 188, 332, fill=(233, 199, 123, 90), width=3)
    for n in range(36):
        x = (n * 197 + 83) % WIDTH
        y = (n * 313 + 119) % HEIGHT
        draw.ellipse((x, y, x + 3, y + 3), fill=(220, 234, 255, 105))

    small = _load_font(26, bold=True)
    title_font = _load_font(76, bold=True)
    body_font = _load_font(39, bold=False)
    tag_font = _load_font(27, bold=True)
    footer_font = _load_font(22, bold=False)

    draw.rounded_rectangle((72, 94, 1008, 1810), radius=34, outline=(137, 174, 255, 48), width=2)
    draw.text((116, 150), slide["eyebrow"], font=small, fill=cyan, spacing=8)
    draw.line((116, 220, 964, 220), fill=(124, 232, 239, 105), width=2)

    title_lines = wrap_text(draw, slide["title"], title_font, 830)
    y = 360
    for line in title_lines:
        draw.text((116, y), line, font=title_font, fill=white, stroke_width=1, stroke_fill=(12, 22, 43, 255))
        y += 112

    body_lines = wrap_text(draw, slide["body"], body_font, 820)
    y += 88
    for line in body_lines:
        draw.text((120, y), line, font=body_font, fill=muted)
        y += 72

    draw.rounded_rectangle((116, 1455, 964, 1570), radius=18, fill=(12, 29, 51, 225),
                           outline=(233, 199, 123, 110), width=2)
    tag_lines = wrap_text(draw, slide["tag"], tag_font, 770)
    tag_y = 1492
    for line in tag_lines[:2]:
        draw.text((150, tag_y), line, font=tag_font, fill=gold)
        tag_y += 40

    draw.text((116, 1702), "MUSE  /  PROMETHEUS", font=footer_font, fill=(166, 179, 207, 220))
    draw.text((900, 1702), f"{index + 1:02d} / {len(SLIDES):02d}", font=footer_font, fill=cyan)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)


def _probe(ffprobe: str, video: Path) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,avg_frame_rate,codec_name",
         "-of", "json", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=True,
    )
    return json.loads(result.stdout)


def build(output: Path, workspace: Path) -> dict:
    output = output.resolve()
    allowed_root = (workspace / "outputs").resolve()
    if output == allowed_root or allowed_root not in output.parents:
        raise ValueError("Output must be a new child directory under outputs/")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required")
    output.mkdir(parents=True)
    font_source = _font_path()
    license_source = font_source.with_name("NotoCJK-OFL.txt")
    font_bundle_dir = output / "assets" / "fonts"
    font_bundle_dir.mkdir(parents=True)
    font_bundle_path = font_bundle_dir / font_source.name
    license_bundle_path = font_bundle_dir / license_source.name
    shutil.copy2(font_source, font_bundle_path)
    shutil.copy2(license_source, license_bundle_path)
    font_sha256 = hashlib.sha256(font_source.read_bytes()).hexdigest()
    font_license_sha256 = hashlib.sha256(license_source.read_bytes()).hexdigest()

    scenes_dir = output / "scenes"
    scenes_dir.mkdir()
    scenes: list[Path] = []
    for index, slide in enumerate(SLIDES):
        path = scenes_dir / f"scene-{index + 1:02d}.png"
        render_scene(index, slide, path)
        scenes.append(path)

    video = output / "muse-ai-fact-check-64s-silent.mp4"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for path in scenes:
        command += ["-i", str(path)]
    filters = []
    labels = []
    for index in range(len(scenes)):
        label = f"v{index}"
        filters.append(
            f"[{index}:v]zoompan=z='min(zoom+0.00013,1.025)':d={SCENE_FRAMES}:"
            f"s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1,format=yuv420p[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append("".join(labels) + f"concat=n={len(scenes)}:v=1:a=0[outv]")
    command += [
        "-filter_complex", ";".join(filters), "-map", "[outv]", "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video),
    ]
    subprocess.run(command, cwd=workspace, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=1800, check=True)

    probe = _probe(ffprobe, video)
    streams = probe.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    duration = float(probe.get("format", {}).get("duration", 0))
    if video_stream.get("width") != WIDTH or video_stream.get("height") != HEIGHT:
        raise RuntimeError("Rendered video dimensions do not match 1080x1920")
    if not 63.0 <= duration <= 65.0:
        raise RuntimeError(f"Rendered duration outside expected 64s window: {duration}")
    if any(stream.get("codec_type") == "audio" for stream in streams):
        raise RuntimeError("Silent typography master unexpectedly contains an audio stream")

    readme = output / "README.md"
    readme.write_text(
        "# Muse AI 三步查證｜64 秒竖屏动态图文样片\n\n"
        "- 画幅：1080×1920（9:16）；24 FPS；H.264 MP4。\n"
        "- 声音：无旁白、无音乐；这是刻意制作的静音动态图文母版，需配合平台字幕/说明使用。\n"
        "- 画面：Muse 原创文字与程序绘制的矢量背景；没有第三方视频、照片、音乐或网络素材。\n"
        "- 社媒：附 YouTube Shorts、TikTok、Instagram Reels、Bilibili、小红书五平台逐条文案草稿。\n"
        "- 字体：Noto Sans CJK HK Variable；随包附带 SIL Open Font License 1.1 正文与字体文件。\n"
        "- 字体来源：Noto CJK 上游项目；字体 SHA-256 与许可证 SHA-256 已写入 rights-manifest.json。\n"
        "- NIST AI 600-1 仅作来源参考；Muse 三步流程不是 NIST 官方方法或背书。\n"
        "- 状态：internal review only；需要人工检查字形、排版、内容准确性与字体/商用权利后才可发布。\n",
        encoding="utf-8",    )
    rights = {
        "status": "review_required",
        "third_party_video": [],
        "third_party_photography": [],
        "third_party_music": [],
        "original_text_and_vector_art": "muse_authored",
        "font": {
            "family": "Noto Sans CJK HK",
            "source": FONT_SOURCE_URL,
            "license": "SIL Open Font License 1.1",
            "license_source": FONT_LICENSE_URL,
            "font_file": "assets/fonts/NotoSansCJKhk-VF.ttf",
            "license_file": "assets/fonts/NotoCJK-OFL.txt",
            "font_sha256": font_sha256,
            "license_sha256": font_license_sha256,
            "license_verified": True,
        },
        "external_media_assets": [],
        "commercial_use_allowed": False,
        "reason": "The bundled font license is verified as SIL OFL 1.1; final editorial and source review are still required, so no commercial release is authorized.",
    }
    (output / "rights-manifest.json").write_text(json.dumps(rights, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    qa = {
        "status": "technical_pass_editorial_review_required",
        "duration_seconds": round(duration, 3),
        "width": WIDTH,
        "height": HEIGHT,
        "fps": FPS,
        "video_codec": video_stream.get("codec_name"),
        "audio_stream": False,
        "decode_check": "ffprobe_stream_metadata_passed",
        "scenes": len(scenes),
        "font_license_status": "verified_sil_ofl_1_1",
        "human_review_required": True,
        "ready_to_publish": False,
    }
    (output / "qa-report.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "social-copy.md").write_text(SOCIAL_COPY, encoding="utf-8")
    payload_files = [
        video, readme, output / "rights-manifest.json", output / "qa-report.json",
        output / "social-copy.md", font_bundle_path, license_bundle_path, *scenes,
    ]
    hashes = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in payload_files}
    manifest = {
        "product_id": "muse-ai-fact-check-kinetic-64s-v4-noto-ofl",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration, 3),
        "dimensions": [WIDTH, HEIGHT],
        "fps": FPS,
        "voiceover": {"status": "not_used", "reason": "Silent kinetic typography master; narration is intentionally omitted."},        "font": {
            "family": "Noto Sans CJK HK",
            "license": "SIL Open Font License 1.1",
            "font_file": "assets/fonts/NotoSansCJKhk-VF.ttf",
            "license_file": "assets/fonts/NotoCJK-OFL.txt",
            "font_sha256": font_sha256,
            "license_sha256": font_license_sha256,
        },
        "source_assets": [],
        "files": hashes,
        "release_state": "draft_only",
        "ready_to_publish": False,
        "rights_review_required": True,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package = output / "delivery-package.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in payload_files:
            archive.write(path, path.relative_to(output).as_posix())
        archive.write(output / "manifest.json", "manifest.json")
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Delivery ZIP CRC validation failed")
    package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
    (output / "package.sha256").write_text(f"{package_sha}  delivery-package.zip\n", encoding="ascii")
    return {
        "output": str(output),
        "video": str(video),
        "duration_seconds": round(duration, 3),
        "width": WIDTH,
        "height": HEIGHT,
        "fps": FPS,
        "scenes": len(scenes),
        "video_bytes": video.stat().st_size,
        "package": str(package),
        "package_bytes": package.stat().st_size,
        "package_sha256": package_sha,
        "ready_to_publish": False,
    }
