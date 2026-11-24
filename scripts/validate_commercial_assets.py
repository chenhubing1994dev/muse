from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.hrefs = []
        self.srcs = []
        self.forms = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if "href" in values:
            self.hrefs.append(values["href"])
        if "src" in values:
            self.srcs.append(values["src"])
        if tag == "form":
            self.forms.append(values)


def validate_site():
    html_path = ROOT / "ui-prototype" / "showcase.html"
    html = html_path.read_text(encoding="utf-8")
    parser = AssetParser()
    parser.feed(html)
    parser.close()
    if len(parser.ids) != len(set(parser.ids)):
        raise AssertionError("duplicate HTML id values")
    for href in parser.hrefs:
        if href.startswith("#") and href[1:] not in parser.ids:
            raise AssertionError(f"broken in-page link: {href}")
        if href.startswith("./") and not (html_path.parent / href[2:]).is_file():
            raise AssertionError(f"missing local linked asset: {href}")
    for src in parser.srcs:
        if src.startswith("./") and not (html_path.parent / src[2:]).is_file():
            raise AssertionError(f"missing local script asset: {src}")
    if "\\n" in html or not html.rstrip().endswith("</html>"):
        raise AssertionError("HTML contains a literal escaped newline or lacks closing html tag")
    if 'id="inquiry-form"' not in html or "不会把资料上传或发送给第三方" not in html:
        raise AssertionError("local inquiry form disclosure is missing")
    if not parser.forms or len(parser.forms) != 1 or parser.forms[0].get("action"):
        raise AssertionError("inquiry form must remain same-origin with no external action URL")
    if 'name="consent"' not in html or 'name="website"' not in html or 'name="contact"' not in html:
        raise AssertionError("explicit consent, honeypot, and contact fields are required")
    js_path = html_path.parent / "commerce.js"
    js = js_path.read_text(encoding="utf-8")
    if 'fetch("/api/leads"' not in js or 'credentials: "same-origin"' not in js:
        raise AssertionError("inquiry JS must submit only to its same-origin local API")
    for forbidden in ("XMLHttpRequest", "sendBeacon(", "https://", "http://"):
        if forbidden in js:
            raise AssertionError(f"unexpected external submission path: {forbidden}")
    server_path = ROOT / "scripts" / "muse-lead-server.py"
    server = server_path.read_text(encoding="utf-8")
    lead_source = (ROOT / "src" / "muse" / "lead_intake.py").read_text(encoding="utf-8")
    if 'choices=("127.0.0.1",)' not in server or 'default="data/commerce/leads.sqlite3"' not in server:
        raise AssertionError("lead server must bind to loopback and use the local inbox")
    if 'MAX_REQUEST_BYTES = 8192' not in lead_source or 'CONSENT_VERSION' not in lead_source or 'LeadRateLimiter' not in lead_source:
        raise AssertionError("local lead intake safety controls are missing")
    products_path = html_path.parent / "products.html"
    products_html = products_path.read_text(encoding="utf-8")
    product_parser = AssetParser()
    product_parser.feed(products_html)
    product_parser.close()
    if len(product_parser.ids) != len(set(product_parser.ids)):
        raise AssertionError("product page contains duplicate HTML id values")
    for href in product_parser.hrefs:
        if href.startswith("#") and href[1:] not in product_parser.ids:
            raise AssertionError(f"broken product-page anchor: {href}")
        if href.startswith("./"):
            local_target = href[2:].split("#", 1)[0]
            if local_target and not (products_path.parent / local_target).is_file():
                raise AssertionError(f"missing product-page asset: {href}")
    if "尚未开放购买" not in products_html or "暂未销售" not in products_html or "US$9 测试假设" not in products_html:        raise AssertionError("product page must disclose draft-only status and unvalidated test pricing")
    if "NIST 未审阅、认证或背书本产品" not in products_html:
        raise AssertionError("product page must disclose no NIST endorsement")
    products_css = (html_path.parent / "products.css").read_text(encoding="utf-8")
    if "@media(max-width:800px)" not in products_css:
        raise AssertionError("product page responsive layout rule is missing")
    print("Product preview page, local assets, draft pricing, release and endorsement disclosures: PASS")
    css = (html_path.parent / "commerce.css").read_text(encoding="utf-8")
    if "@media(max-width:800px)" not in css or "prefers-reduced-motion" not in (html_path.parent / "showcase.css").read_text(encoding="utf-8"):
        raise AssertionError("responsive or reduced-motion CSS requirement missing")
    node = shutil.which("node")
    if node:
        result = subprocess.run([node, "--check", str(js_path)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr or result.stdout or "node --check failed")
        print("JavaScript syntax: PASS (node --check)")
    else:
        print("JavaScript syntax: NOT CHECKED (Node.js not installed)")
    print("Landing page assets, links, inquiry consent, loopback API and local-only storage guard: PASS")


def main():
    validate_site()
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tests"))
    test_module = importlib.import_module("test_social_campaign_tracking")
    suite = unittest.defaultTestLoader.loadTestsFromModule(test_module)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f"Social campaign tracking tests: {result.testsRun}/{result.testsRun} PASS")


if __name__ == "__main__":
    main()
