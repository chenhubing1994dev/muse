from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ASSETS = ("showcase.css", "products.css", "commerce.css", "commerce.js")

def patch_navigation(source: str) -> str:
    marker = '<a href="./products.html">数字产品</a>'
    if marker not in source:
        raise ValueError("Showcase navigation marker not found")
    if 'href="./portfolio.html"' not in source:
        source = source.replace(marker, '<a href="./portfolio.html">作品样片</a>' + marker, 1)
    return source

def build_portfolio_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#050711">
<meta name="robots" content="noindex,nofollow">
<meta name="description" content="Muse 原创 AI 查证动态图文样片。使用 Noto Sans CJK HK，SIL OFL 1.1；内容仍待人工审校。">
<title>MUSE · 创作样片预览</title>
<link rel="stylesheet" href="./showcase.css">
</head>
<body>
<header class="topbar">
  <a class="brand" href="./index.html"><span class="brand-mark">M</span><span>MUSE<small>PROJECT PROMETHEUS</small></span></a>
  <nav><a href="./index.html">Muse 主站</a><a href="./products.html">数字产品</a></nav>
</header>
<main style="max-width:1100px;margin:0 auto;padding:64px 24px">
  <div class="eyebrow">CREATION FORGE / INTERNAL PREVIEW</div>
  <h1>AI 三步查证｜64 秒动态图文样片</h1>
  <p>原创文案与程序绘制画面。静音母版，无旁白、无第三方音乐、无第三方视频素材。</p>
  <video controls playsinline preload="metadata" style="display:block;width:min(100%,520px);max-height:82vh;margin:28px auto;border:1px solid #7ce8ef55;border-radius:16px;background:#080d1d">
    <source src="./media/muse-ai-fact-check-64s-silent.mp4" type="video/mp4">
    你的浏览器不支持 HTML5 视频。
  </video>
  <section style="max-width:760px;margin:32px auto;padding:24px;border:1px solid #e9c77b55;border-radius:14px;background:#101a31">
    <h2>发布状态：内部审核稿</h2>
    <p>本片使用 Noto Sans CJK HK，字体文件及 SIL OFL 1.1 许可证随交付包提供。字体许可已核验；内容、来源与编辑审校仍未完成，因此不得公开发布或作为已获商业发布批准的客户案例。</p>
    <p>五平台标题、说明、标签与发布前检查见随附 social-copy.md。</p>
    <p><a href="./products.html">查看数字产品预览 ↗</a> · <a href="./index.html#services">查看合作服务 ↗</a></p>
  </section>
</main>
<footer><span>MUSE · PROMETHEUS</span><span>Internal preview only · No public release</span></footer>
</body>
</html>
"""

class _LocalReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.references.append(value)

def local_html_references(html: str) -> list[str]:
    parser = _LocalReferenceParser()
    parser.feed(html)
    return [
        ref for ref in parser.references
        if not ref.startswith(("http://", "https://", "mailto:", "tel:", "data:", "#", "javascript:"))
    ]

def build_preview(output: Path, workspace: Path) -> dict:
    output = output.resolve()
    workspace = workspace.resolve(strict=True)
    allowed_root = (workspace / "outputs").resolve()
    if output == allowed_root or allowed_root not in output.parents:
        raise ValueError("Output must be a new child directory under outputs/")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing preview: {output}")

    source = workspace / "ui-prototype"
    kinetic = workspace / "outputs" / "kinetic_ai_fact_check_20261124_v4_noto_ofl"
    output.mkdir(parents=True)
    media_dir = output / "media"
    media_dir.mkdir()

    showcase = patch_navigation((source / "showcase.html").read_text(encoding="utf-8"))
    products = (source / "products.html").read_text(encoding="utf-8")
    products_marker = '<a href="#catalog">数字产品</a>'
    if products_marker in products and 'href="./portfolio.html"' not in products:
        products = products.replace(products_marker, '<a href="./portfolio.html">作品样片</a>' + products_marker, 1)

    (output / "index.html").write_text(showcase, encoding="utf-8")
    (output / "showcase.html").write_text(showcase, encoding="utf-8")
    (output / "products.html").write_text(products, encoding="utf-8")
    (output / "portfolio.html").write_text(build_portfolio_page(), encoding="utf-8")
    for asset in ASSETS:
        shutil.copy2(source / asset, output / asset)
    shutil.copy2(kinetic / "muse-ai-fact-check-64s-silent.mp4", media_dir / "muse-ai-fact-check-64s-silent.mp4")
    shutil.copy2(kinetic / "social-copy.md", output / "social-copy.md")

    (output / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (output / "README.md").write_text(
        "# Muse Static Site Preview\n\n"
        "- Pages: home/showcase, products, portfolio sample.\n"
        "- Local static assets only; no backend, analytics, payment processing or customer submission endpoint.\n"
        "- Inquiry form only prepares a local text draft and copies it when browser permission allows.\n"
        "- robots.txt disallows crawling; all pages remain a preview and must not be treated as a public launch.\n"
        "- Font: Noto Sans CJK HK under SIL OFL 1.1; the rendered MP4 is included. Font file/license are bundled in the video delivery package, not duplicated in this site ZIP.\n"
        "- Before deployment: complete editorial/source review, review legal/privacy copy, configure an owner-controlled contact channel, confirm domain/HTTPS, and deliberately remove the crawl block only after approval.\n"
        "- Payments are not enabled; no price is represented as a live offer.\n",
        encoding="utf-8",
    )

    payload = sorted(path for path in output.rglob("*") if path.is_file())
    for page in (output / "index.html", output / "products.html", output / "portfolio.html"):
        for reference in local_html_references(page.read_text(encoding="utf-8")):
            relative = reference.split("#", 1)[0].split("?", 1)[0]
            if relative and not (output / relative).is_file():
                raise FileNotFoundError(f"Broken local site link in {page.name}: {reference}")
    hashes = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in payload}
    manifest = {        "product": "muse-public-site-preview",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pages": ["index.html", "showcase.html", "products.html", "portfolio.html"],
        "assets": hashes,
        "status": "preview_only",
        "public_deployment_allowed": False,
        "crawl_allowed": False,
        "contact_channel_configured": False,
        "payments_enabled": False,
        "commercial_rights_review_required": True,
    }
    (output / "site-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive_path = output / "site-preview.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in payload:
            archive.write(path, path.relative_to(output).as_posix())
        archive.write(output / "site-manifest.json", "site-manifest.json")
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Site preview ZIP CRC failed")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Site preview ZIP contains duplicate member names")
        for name, digest in hashes.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise RuntimeError(f"Site preview hash mismatch: {name}")

    return {
        "output": str(output),
        "archive": str(archive_path),
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "pages": manifest["pages"],
        "asset_count": len(hashes),
        "public_deployment_allowed": False,
    }
