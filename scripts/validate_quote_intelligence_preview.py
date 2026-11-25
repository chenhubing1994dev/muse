from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "outputs/quote_intelligence_launch_preview_20261125/index.html"
README = ROOT / "outputs/quote_intelligence_launch_preview_20261125/README.md"
REPORT = ROOT / "outputs/quote_intelligence-demo.md"

class Audit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.links = []
    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))

def main() -> int:
    for p in (PAGE, README, REPORT):
        if not p.is_file():
            raise SystemExit(f"missing required file: {p.relative_to(ROOT)}")
    source = PAGE.read_text(encoding="utf-8")
    parser = Audit()
    parser.feed(source)
    parser.close()
    required = [
        "lang=\"zh-Hans\"", "US$9", "尚未通过真实访客", "合成样例",
        "没有结账", "不自动选供应商", "PDF/XLSX/OCR",
    ]
    missing = [x for x in required if x not in source]
    if missing:
        raise SystemExit(f"missing disclosure/content: {missing}")
    if "form" in parser.tags or "script" in parser.tags or "iframe" in parser.tags:
        raise SystemExit("unexpected form/script/iframe in static preview")
    for href in parser.links:
        if href.startswith(("http://", "https://", "#")):
            continue
        target = (PAGE.parent / href).resolve()
        if not target.is_file():
            raise SystemExit(f"broken local link: {href}")
    print("QUOTE_PREVIEW_VALIDATION=PASS")
    print(f"html_tags={len(parser.tags)} local_links={len(parser.links)}")
    for path in (PAGE, README):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"SHA256 {path.relative_to(ROOT)} {digest}")
    print("checkout=false tracking=false lead_collection=false public_deploy=false")
    return 0

if __name__ == "__main__":
    sys.exit(main())
