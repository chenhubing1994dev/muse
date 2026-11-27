"""B391-P2 L2.5 front-end ↔ back-end contract and DOM/CSP cross-check (stdlib only).

Runs anywhere Python 3.11+ and the Muse ``src`` tree are available (cloud sandbox or the Windows
workstation). It never touches ``data/``: a real Status API is started on 127.0.0.1 with throw-away
SQLite stores inside a temporary directory, then every ``call(...)`` / ``soft(...)`` site found in
``ui-prototype/os.js`` and ``ui-prototype/portal.js`` is replayed against it.

A call site fails when the backend answers with an *unknown route* (404 "not found" / "unknown control
route") or 405. Resource-level answers (404 "task not found", 400 validation, 409 confirmation gate,
503 store not configured) prove the route exists and are accepted.

DOM: every ``$("id")`` / ``getElementById("id")`` / ``data-focus`` reference must exist in the page, ids
must be unique, and pages must have no inline ``<script>``, ``<style>``, ``style="…"`` or ``on*=`` handlers.

Exit code 0 = all green; 1 = at least one contract or DOM/CSP violation. JSON report on stdout.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui-prototype"
HEX32 = "0" * 31 + "a"
UNKNOWN_MARKERS = (b'"not found"', b"unknown control route", b"unknown omega route")
CALL_RE = re.compile(r"\b(call|soft)\(\s*(\"[^\"]*\"|'[^']*'|`[^`]*`)\s*(,\s*\{([^{}]|\{[^{}]*\})*\})?")


def call_sites(js: str) -> list[dict]:
    sites = []
    for match in CALL_RE.finditer(js):
        literal = match.group(2)[1:-1]
        options = match.group(3) or ""
        method = "POST" if "body" in options else "GET"
        explicit = re.search(r'method:\s*"(\w+)"', options)
        if explicit:
            method = explicit.group(1)
        sites.append({"line": js[: match.start()].count("\n") + 1, "method": method, "path": literal,
                      "phrases": sorted(set(re.findall(r'"([A-Z]+(?:-[A-Z]+)+)"', options)))})
    return sites


def concretize(path: str) -> str:
    # memory ids are integers; everything else in the routes is a 32-hex id
    path = re.sub(r"(/memory/(?:candidates/)?)\$\{[^}]*\}", r"\g<1>1", path)
    path = re.sub(r"\$\{state\.track\}", "user", path)
    path = re.sub(r"\$\{q \?[^}]*\}", "", path)
    path = re.sub(r"\$\{[^}]*\}", HEX32, path)
    if path.endswith("/"):
        path += "1" if path.endswith("/memory/") else HEX32
    return path


def dom_report(js: str, html: str) -> dict:
    ids = re.findall(r'\bid="([^"]+)"', html)
    refs = set(re.findall(r'\$\(\s*"([^"]+)"\s*\)', js)) | set(re.findall(r'getElementById\(\s*"([^"]+)"', js))
    refs |= set(re.findall(r'data-focus="([^"]+)"', html))
    return {
        "references": len(refs),
        "missing_ids": sorted(r for r in refs if r not in set(ids)),
        "duplicate_ids": sorted({i for i in ids if ids.count(i) > 1}),
        "inline_script": len(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)),
        "inline_style_tag": len(re.findall(r"<style\b", html)),
        "style_attributes": len(re.findall(r'\sstyle="', html)),
        "inline_handlers": len(re.findall(r"\son[a-z]+=\"", html)),
        "js_inner_html": len(re.findall(r"\.innerHTML\s*=", js)),
        "js_style_attribute": len(re.findall(r"setAttribute\(\s*[\"']style", js)),
    }


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from muse.status_api import create_status_server  # noqa: E402

    report: dict = {"routes": [], "dom": {}, "ok": True}
    with tempfile.TemporaryDirectory(prefix="muse-l25-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        data = root / "data"
        for name in ("tasks", "progress", "capabilities", "agents", "improvements", "research", "memory", "products"):
            (data / name).mkdir(parents=True, exist_ok=True)
        server, token = create_status_server(
            task_db=data / "tasks" / "muse.sqlite3", progress_db=data / "progress" / "p.sqlite3",
            capability_db=data / "capabilities" / "c.sqlite3", agent_db=data / "agents" / "a.sqlite3",
            improvement_db=data / "improvements" / "i.sqlite3", knowledge_db=data / "research" / "k.sqlite3",
            products_dir=data / "products", memory_db=data / "memory" / "m.sqlite3")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for page in ("os", "portal"):
                js = (UI / f"{page}.js").read_text(encoding="utf-8")
                html = (UI / f"{page}.html").read_text(encoding="utf-8")
                dom = dom_report(js, html)
                report["dom"][page] = dom
                if dom["missing_ids"] or dom["duplicate_ids"] or any(dom[k] for k in (
                        "inline_script", "inline_style_tag", "style_attributes", "inline_handlers",
                        "js_inner_html", "js_style_attribute")):
                    report["ok"] = False
                for site in call_sites(js):
                    probe = "/api/v1" + concretize(site["path"])
                    body = b"{}" if site["method"] == "POST" else None
                    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                    request = Request(f"http://127.0.0.1:{server.server_address[1]}{probe}", data=body,
                                      method=site["method"], headers=headers)
                    try:
                        with urlopen(request, timeout=30) as response:
                            status, raw = response.status, response.read()
                    except HTTPError as exc:
                        status, raw = exc.code, exc.read()
                        exc.close()
                    known = not (status == 405 or (status == 404 and any(m in raw for m in UNKNOWN_MARKERS)))
                    report["routes"].append({"page": page, **site, "probe": probe, "status": status,
                                             "route_known": known, "body": raw[:140].decode("utf-8", "replace")})
                    if not known:
                        report["ok"] = False
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    ui_phrases: set[str] = set()
    for page in ("os", "portal"):
        js = (UI / f"{page}.js").read_text(encoding="utf-8")
        ui_phrases |= set(re.findall(r'phrase:\s*"([A-Z]+(?:-[A-Z]+)+)"', js))
        ui_phrases |= {p for r in report["routes"] if r["page"] == page for p in r["phrases"]}
    backend_text = "".join((ROOT / "src" / "muse" / name).read_text(encoding="utf-8")
                           for name in ("control_center_api.py", "status_api.py", "omega_api.py"))
    backend_phrases = set(re.findall(r'"([A-Z]+(?:-[A-Z]+)+)"', backend_text))
    ui_only = {"PAUSE-AUTONOMY", "RESUME-AUTONOMY"}  # documented UI-side mis-click guards
    unmatched = sorted(ui_phrases - backend_phrases - ui_only)
    if unmatched:
        report["ok"] = False
    report["summary"] = {
        "call_sites": len(report["routes"]),
        "unknown_routes": [r["probe"] for r in report["routes"] if not r["route_known"]],
        "confirmation_phrases_ui": sorted(ui_phrases),
        "confirmation_phrases_without_backend_check": unmatched,
        "ui_only_guards": sorted(ui_only & ui_phrases),
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
