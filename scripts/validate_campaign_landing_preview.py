from __future__ import annotations

import re
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "outputs/campaign_videos_20261124_v2/campaign-landing-preview.html"
README = ROOT / "outputs/campaign_videos_20261124_v2/LANDING-PREVIEW-README.md"

class StructureCheck(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []
    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected closing tag: {tag}")
            if tag in self.stack:
                self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
        else:
            self.stack.pop()

assert PAGE.is_file(), f"missing page: {PAGE}"
assert README.is_file(), f"missing README: {README}"
html = PAGE.read_text(encoding="utf-8")
parser = StructureCheck()
parser.feed(html)
parser.close()
assert not parser.errors, parser.errors
assert not parser.stack, f"unclosed tags: {parser.stack}"
assert '<html lang="zh-Hant">' in html
assert 'id="download"' in html and 'createObjectURL' in html
assert 'disabled aria-disabled="true"' in html
assert "尚未開放購買" in html
assert "<form" not in html.lower()
assert not re.search(r"\bfetch\s*\(|XMLHttpRequest|sendBeacon", html)
assert not re.search(r"https?://", html.replace("NIST AI 600-1", ""))
scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
assert len(scripts) == 1, f"expected one inline script, got {len(scripts)}"
node = subprocess.run(["node", "--version"], capture_output=True, text=True)
assert node.returncode == 0, "Node.js unavailable for JS syntax check"
with tempfile.TemporaryDirectory() as tmp:
    js_path = Path(tmp) / "campaign-landing-preview.mjs"
    js_path.write_text(scripts[0], encoding="utf-8")
    check = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
print("campaign landing preview: HTML structure PASS")
print("local checklist download behavior PASS")
print("no forms / analytics / external fetch PASS")
print("paid checkout remains disabled PASS")
print("inline JavaScript syntax PASS")
