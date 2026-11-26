"""Muse desktop sidecar engine (``muse-engine``): same-origin gateway for the Tauri v2 client.

One process, two loopback listeners:

* an **inner** Muse status API (``muse.status_api.create_status_server``) on a private dynamic
  port, authenticated with an inner bearer that never leaves this process;
* a **gateway** on ``127.0.0.1:<dynamic port>`` that serves the multi-page Vite build (``dist/``)
  *and* proxies every ``/api/*`` route from the same origin, so the strict CSP
  (``script-src 'self'``) and the ``Origin`` same-origin checks hold inside the desktop webview.

Session auth (single desktop session): the Tauri shell passes ``MUSE_SESSION_TOKEN`` and a
single-use ``MUSE_LAUNCH_NONCE`` through the environment (never argv). The webview opens
``/__muse/bootstrap?nonce=…`` once, receives an ``HttpOnly; SameSite=Strict`` cookie and is
redirected to ``/``. API callers may alternatively use ``Authorization: Bearer <session token>``.

Runtime data never comes from the developer checkout: the default data root is the per-user
application directory (``%LOCALAPPDATA%\\Muse`` / ``~/Library/Application Support/com.muse.consciousness``
/ ``~/.local/share/muse``), or ``./muse-data`` next to the executable when a ``MUSE_PORTABLE``
marker file is present.

``--self-test`` builds a brand-new temporary data root, checks health, the ten-dimension
consciousness API, the symbolic Xuanshu fallback, auth/origin rejection and the cookie bootstrap,
then exits 0 (pass) or 1 (fail).
"""
from __future__ import annotations

import argparse
import hmac
import http.client
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

APP_ID = "com.muse.consciousness"
SIDECAR_VERSION = "1.0.3"
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

COOKIE_NAME = "muse_session"
MAX_PROXY_BODY = 96 * 1024 * 1024
PORTABLE_MARKER = "MUSE_PORTABLE"
SQLITE_SUFFIXES = (".sqlite3", ".sqlite", ".db")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; font-src 'self' data:; connect-src 'self' ipc: http://ipc.localhost; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
FORWARD_REQUEST_HEADERS = frozenset({"content-type", "accept", "accept-language", "last-event-id", "range",
                                     "if-none-match", "if-modified-since"})
HOP_BY_HOP = frozenset({"connection", "keep-alive", "transfer-encoding", "content-length", "server", "date",
                        "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade"})
DATA_LAYOUT = ("data/tasks", "data/progress", "data/capabilities", "data/memory", "data/autonomy",
               "data/evolution", "data/agents", "data/research", "data/studio", "data/plugins", "data/companion",
               "logs", "inbox", "backups")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/wasm", ".wasm")


# --------------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------------
def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SRC_DIR.parent


def default_data_dir(*, platform: str | None = None, env: Mapping[str, str] | None = None,
                     exe_dir: Path | None = None, home: Path | None = None) -> Path:
    """Per-user runtime data root. Never the developer checkout's ``data/``."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    exe_dir = exe_dir or executable_dir()
    override = env.get("MUSE_DATA_DIR", "").strip()
    if override:
        return Path(override)
    if (exe_dir / PORTABLE_MARKER).is_file():
        return exe_dir / "muse-data"
    home = home or Path.home()
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else home / "AppData" / "Local") / "Muse"
    if platform == "darwin":
        return home / "Library" / "Application Support" / APP_ID
    xdg = env.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg) if xdg else home / ".local" / "share") / "muse"


def resolve_ui_dist() -> Path | None:
    override = os.environ.get("MUSE_UI_DIST", "").strip()
    candidates = [Path(override)] if override else []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "ui_dist")
    candidates.append(SRC_DIR.parent / "ui-prototype" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def prepare_data_root(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    for rel in DATA_LAYOUT:
        (root / rel).mkdir(parents=True, exist_ok=True)
    data = root / "data"
    reports_dir = data / "research" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return {
        "task_db": data / "tasks" / "muse.sqlite3",
        "progress_db": data / "progress" / "muse.sqlite3",
        "capability_db": data / "capabilities" / "muse.sqlite3",
        "memory_db": data / "memory" / "muse.sqlite3",
        "agent_db": data / "agents" / "registry.sqlite3",
        "improvement_db": data / "evolution" / "improvements.sqlite3",
        "knowledge_db": data / "research" / "knowledge.sqlite3",
        "reports_dir": reports_dir,
    }

def safe_static_path(ui_dist: Path, request_path: str) -> Path | None:
    """Map a URL path to a file inside ``ui_dist``; ``None`` on traversal or absence."""
    rel = unquote(request_path.split("?", 1)[0]).replace("\\", "/").lstrip("/")
    if "\x00" in rel or any(part == ".." for part in rel.split("/")):
        return None
    if rel == "":
        rel = "index.html"
    root = ui_dist.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if candidate.is_file():
        return candidate
    if "." not in rel.rsplit("/", 1)[-1]:
        html = candidate.with_suffix(".html")
        if html.is_file():
            return html
    return None


def checkpoint_sqlite(root: Path) -> int:
    """Fold every WAL file under ``root/data`` back into its database before exit."""
    done = 0
    data = root / "data"
    if not data.is_dir():
        return 0
    for path in data.rglob("*"):
        if path.suffix.lower() not in SQLITE_SUFFIXES or not path.is_file():
            continue
        if not Path(str(path) + "-wal").exists():
            continue
        try:
            conn = sqlite3.connect(str(path), timeout=2)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                done += 1
            finally:
                conn.close()
        except sqlite3.Error:
            continue
    return done


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------------------------
# Quick-note page (served by the gateway; external JS/CSS only, CSP-clean)
# --------------------------------------------------------------------------------------------
QUICK_NOTE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>快速记灵感 · Muse</title><link rel="stylesheet" href="/__muse/quick-note.css"></head>
<body><main><h1>✶ 快速记灵感</h1>
<textarea id="note" maxlength="4000" placeholder="此刻的灵感、画面、标题或反转……" autofocus></textarea>
<input id="tags" maxlength="200" placeholder="标签（可选，用逗号分隔）">
<button id="save" type="button">保存到灵感收件箱</button><p id="status" role="status"></p>
<p class="hint">仅保存在本机用户数据目录 inbox/inspirations.jsonl；标记为 symbolic_inspiration / unverified_hypothesis。</p>
</main><script src="/__muse/quick-note.js"></script></body></html>
"""
QUICK_NOTE_CSS = """:root{color-scheme:dark}body{margin:0;background:radial-gradient(circle at 30% 20%,#1b2559,#05060f 70%);
color:#e8ecff;font:14px/1.6 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}main{padding:18px;display:flex;flex-direction:column;gap:10px}
h1{font-size:18px;margin:0;color:#9fd8ff}textarea{min-height:150px;resize:vertical}textarea,input{background:#0b1030;color:#e8ecff;
border:1px solid #2d3b80;border-radius:10px;padding:10px;font:inherit}button{background:linear-gradient(90deg,#3b82f6,#a855f7);border:0;color:#fff;
padding:10px;border-radius:10px;font-weight:600;cursor:pointer}button:disabled{opacity:.6}#status{min-height:1.4em;margin:0}.hint{opacity:.6;font-size:12px;margin:0}
"""
QUICK_NOTE_JS = """(function(){var b=document.getElementById('save'),n=document.getElementById('note'),t=document.getElementById('tags'),s=document.getElementById('status');
b.addEventListener('click',function(){var text=n.value.trim();if(!text){s.textContent='请先写下灵感';return;}b.disabled=true;s.textContent='保存中…';
fetch('/__muse/quick-note',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','Accept':'application/json'},
body:JSON.stringify({text:text,tags:t.value.split(/[,，]/).map(function(x){return x.trim();}).filter(Boolean).slice(0,12)})})
.then(function(r){return r.json().then(function(d){if(!r.ok)throw new Error(d.error||('HTTP '+r.status));return d;});})
.then(function(){s.textContent='✓ 已保存（仅本机）';n.value='';t.value='';n.focus();})
.catch(function(e){s.textContent='保存失败：'+e.message;}).finally(function(){b.disabled=false;});});})();
"""


# --------------------------------------------------------------------------------------------
# Gateway
# --------------------------------------------------------------------------------------------
class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 64


def _make_gateway_handler(engine: "MuseEngine") -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MuseEngine/" + SIDECAR_VERSION
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # keep stdout clean for the ready line
            if engine.verbose:
                sys.stderr.write("gateway: " + (fmt % args) + "\n")

        # ---- helpers -------------------------------------------------------------------
        def _security_headers(self) -> None:
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")

        def _json(self, status: int, body: Mapping[str, Any], extra: Mapping[str, str] | None = None) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

        def _bytes(self, status: int, raw: bytes, content_type: str, *, cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", cache)
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            return host in engine.allowed_hosts

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is not None and origin.strip().lower() not in engine.allowed_origins:
                return False
            return (self.headers.get("Sec-Fetch-Site") or "same-origin").lower() in {"same-origin", "none"}

        def _cookie_token(self) -> str:
            raw = self.headers.get("Cookie")
            if not raw:
                return ""
            try:
                jar = SimpleCookie()
                jar.load(raw)
            except Exception:
                return ""
            morsel = jar.get(COOKIE_NAME)
            return morsel.value if morsel is not None else ""

        def _authorized(self) -> bool:
            expected = engine.session_token
            auth = self.headers.get("Authorization", "")
            if auth and hmac.compare_digest(auth.encode(), ("Bearer " + expected).encode()):
                return True
            cookie = self._cookie_token()
            return bool(cookie) and hmac.compare_digest(cookie.encode(), expected.encode())

        def _read_body(self, limit: int) -> bytes | None:
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                return None
            if length < 0 or length > limit:
                return None
            data = self.rfile.read(length) if length else b""
            return data if len(data) == length else None

        # ---- dispatch ------------------------------------------------------------------
        def _dispatch(self, method: str) -> None:
            if not self._host_ok():
                self._json(421, {"error": "host not allowed"})
                return
            path = urlsplit(self.path).path
            if path == "/__muse/health" and method in {"GET", "HEAD"}:
                stores = {key: dict(value) for key, value in engine.store_health.items()}
                healthy = bool(stores) and all(value.get("status") == "healthy" for value in stores.values())
                self._json(200 if healthy else 503, {
                    "service": "muse-engine", "status": "ok" if healthy else "degraded",
                    "version": SIDECAR_VERSION, "agent_registry_configured": stores.get("agents", {}).get("status") == "healthy",
                    "stores_healthy": healthy, "stores": stores,
                })
                return
            if path == "/__muse/bootstrap" and method == "GET":
                self._bootstrap()
                return
            if not self._origin_ok():
                self._json(403, {"error": "cross-origin request rejected"})
                return
            if not self._authorized():
                self._json(401, {"error": "unauthorized: open Muse from the desktop client"})
                return
            if path == "/__muse/shutdown" and method == "POST":
                checkpoints = checkpoint_sqlite(engine.data_root)
                self._json(200, {"status": "stopping", "wal_checkpoints": checkpoints})
                threading.Thread(target=engine.stop, name="muse-shutdown", daemon=True).start()
                return
            if path == "/__muse/session" and method == "GET":
                self._json(200, {"status": "ok", "data_dir_name": engine.data_root.name,
                                 "ui_available": engine.ui_dist is not None, "version": SIDECAR_VERSION})
                return
            if path == "/__muse/quick-note" and method == "POST":
                self._quick_note()
                return
            if path in {"/__muse/quick-note.html", "/__muse/quick-note.js", "/__muse/quick-note.css"} \
                    and method in {"GET", "HEAD"}:
                kind = path.rsplit(".", 1)[-1]
                body, ctype = {"html": (QUICK_NOTE_HTML, "text/html; charset=utf-8"),
                               "js": (QUICK_NOTE_JS, "text/javascript; charset=utf-8"),
                               "css": (QUICK_NOTE_CSS, "text/css; charset=utf-8")}[kind]
                self._bytes(200, body.encode("utf-8"), ctype)
                return
            if path.startswith("/api/"):
                self._proxy(method)
                return
            if method not in {"GET", "HEAD"}:
                self._json(405, {"error": "method not allowed"})
                return
            self._static(path)

        def _bootstrap(self) -> None:
            nonce = parse_qs(urlsplit(self.path).query).get("nonce", [""])[0]
            if not engine.consume_nonce(nonce):
                self._bytes(403, "<!doctype html><meta charset=utf-8><title>Muse</title><p>会话链接已失效，请从 Muse 桌面客户端重新打开。</p>"
                            .encode("utf-8"), "text/html; charset=utf-8")
                return
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"{COOKIE_NAME}={engine.session_token}; HttpOnly; SameSite=Strict; Path=/")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self._security_headers()
            self.end_headers()

        def _quick_note(self) -> None:
            if (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
                self._json(415, {"error": "application/json required"})
                return
            raw = self._read_body(64 * 1024)
            if raw is None:
                self._json(413, {"error": "request body size not allowed"})
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "request body must be UTF-8 JSON"})
                return
            text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
            if not 1 <= len(text) <= 4000:
                self._json(400, {"error": "text must be 1-4000 characters"})
                return
            tags = payload.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            record = {"id": secrets.token_hex(16), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                      "text": text, "tags": [str(t)[:40] for t in tags[:12]], "source": "desktop_tray_quick_note",
                      "epistemic_labels": ["symbolic_inspiration", "unverified_hypothesis"]}
            inbox = engine.data_root / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            with engine.note_lock, open(inbox / "inspirations.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._json(201, {"status": "saved", "id": record["id"]})

        def _proxy(self, method: str) -> None:
            body = self._read_body(MAX_PROXY_BODY) if method in {"POST", "PUT", "PATCH", "DELETE"} else b""
            if body is None:
                self._json(413, {"error": "request body size not allowed"})
                return
            headers = {key: value for key, value in self.headers.items() if key.lower() in FORWARD_REQUEST_HEADERS}
            headers["Authorization"] = "Bearer " + engine.inner_token
            headers["Host"] = f"127.0.0.1:{engine.inner_port}"
            if body:
                headers["Content-Length"] = str(len(body))
            conn = http.client.HTTPConnection("127.0.0.1", engine.inner_port, timeout=330)
            try:
                conn.request(method, self.path, body=body if body else None, headers=headers)
                resp = conn.getresponse()
                streaming = (resp.getheader("Content-Type") or "").startswith("text/event-stream")
                self.send_response(resp.status)
                for key, value in resp.getheaders():
                    if key.lower() not in HOP_BY_HOP:
                        self.send_header(key, value)
                if streaming:
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.close_connection = True
                    while True:
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(data)
            except (ConnectionError, OSError, http.client.HTTPException) as exc:
                try:
                    self._json(502, {"error": f"muse status api unavailable ({type(exc).__name__})"})
                except OSError:
                    pass
            finally:
                conn.close()

        def _static(self, path: str) -> None:
            if engine.ui_dist is None:
                self._bytes(503, "<!doctype html><meta charset=utf-8><title>Muse</title><p>界面资源缺失：请先运行 npm run build。</p>"
                            .encode("utf-8"), "text/html; charset=utf-8")
                return
            target = safe_static_path(engine.ui_dist, path)
            if target is None:
                self._json(404, {"error": "not found"})
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in {"application/json", "application/manifest+json"}:
                ctype += "; charset=utf-8"
            cache = "no-store" if target.suffix == ".html" else "private, max-age=3600"
            self._bytes(200, target.read_bytes(), ctype, cache=cache)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_HEAD(self) -> None:  # noqa: N802
            self._dispatch("HEAD")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def do_PATCH(self) -> None:  # noqa: N802
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch("DELETE")

    return Handler


class MuseEngine:
    """Inner status API + same-origin gateway, both on loopback."""

    def __init__(self, *, data_root: Path, port: int = 0, session_token: str | None = None,
                 launch_nonce: str | None = None, ui_dist: Path | None = None, verbose: bool = False) -> None:
        self.data_root = Path(data_root).resolve()
        self.requested_port = port
        self.session_token = session_token or secrets.token_urlsafe(32)
        if len(self.session_token) < 32:
            raise ValueError("session token must contain at least 32 characters")
        self.launch_nonce = launch_nonce or secrets.token_urlsafe(24)
        self.inner_token = secrets.token_urlsafe(32)
        self.ui_dist = ui_dist
        self.verbose = verbose
        self.note_lock = threading.Lock()
        self._nonce_lock = threading.Lock()
        self._nonce_used = False
        self._stopped = threading.Event()
        self._inner = None
        self._gateway: GatewayServer | None = None
        self._threads: list[threading.Thread] = []
        self.inner_port = 0
        self.port = 0
        self.allowed_hosts: frozenset[str] = frozenset()
        self.allowed_origins: frozenset[str] = frozenset()
        self.store_health: dict[str, dict[str, str]] = {}

    def consume_nonce(self, nonce: str) -> bool:
        if not nonce:
            return False
        with self._nonce_lock:
            if self._nonce_used or not hmac.compare_digest(nonce.encode(), self.launch_nonce.encode()):
                return False
            self._nonce_used = True
            return True

    def start(self) -> "MuseEngine":
        from muse.status_api import create_status_server
        paths = prepare_data_root(self.data_root)
        from muse.store_health import validate_store_registry
        store_health = validate_store_registry({
            "feedback": paths["improvement_db"],
            "inspiration": paths["knowledge_db"],
            "artifacts": paths["reports_dir"],
            "agents": paths["agent_db"],
        })
        self.store_health = store_health
        inner, _ = create_status_server(
            task_db=paths["task_db"], progress_db=paths["progress_db"], capability_db=paths["capability_db"],
            memory_db=paths["memory_db"], improvement_db=paths["improvement_db"],
            agent_db=paths["agent_db"],
            knowledge_db=paths["knowledge_db"], report_dir=paths["reports_dir"],
            store_health=store_health,
            port=0, token=self.inner_token,
        )
        self._inner = inner
        self.inner_port = int(inner.server_address[1])
        gateway = GatewayServer(("127.0.0.1", self.requested_port), _make_gateway_handler(self))
        self._gateway = gateway
        self.port = int(gateway.server_address[1])
        self.allowed_hosts = frozenset({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})
        self.allowed_origins = frozenset({f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"})
        for name, server in (("muse-inner-api", inner), ("muse-gateway", gateway)):
            thread = threading.Thread(target=server.serve_forever, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        self._run_authenticated_startup_api_check()
        return self

    def _run_authenticated_startup_api_check(self) -> None:
        """Exercise the actual authenticated gateway routes before declaring the engine started."""
        results: dict[str, Any] = {}
        for endpoint in ("/api/v1/agents", "/api/v1/health", "/api/v1/store-health"):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
            try:
                connection.request(
                    "GET", endpoint,
                    headers={
                        "Host": f"127.0.0.1:{self.port}",
                        "Authorization": "Bearer " + self.session_token,
                        "Accept": "application/json",
                    },
                )
                response = connection.getresponse()
                raw = response.read()
                try:
                    payload: Any = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {"unparsed_response": raw.decode("utf-8", errors="replace")[:2000]}
                results[endpoint] = {"http_status": response.status, "body": payload}
                if response.status != 200:
                    raise RuntimeError(
                        f"startup API self-check failed for {endpoint}: HTTP {response.status}; "
                        f"response={json.dumps(payload, ensure_ascii=False)[:1000]}"
                    )
            finally:
                connection.close()
        agents = results["/api/v1/agents"]["body"]
        if not isinstance(agents, dict) or agents.get("registry_configured") is not True:
            raise RuntimeError("startup API self-check failed: /api/v1/agents reports registry_configured != true")
        for endpoint in ("/api/v1/health", "/api/v1/store-health"):
            body = results[endpoint]["body"]
            if not isinstance(body, dict) or body.get("stores_healthy") is not True:
                raise RuntimeError(f"startup API self-check failed: {endpoint} does not report stores_healthy=true")
        report_path = self.data_root / "logs" / "startup-api-acceptance.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"status": "passed", "version": SIDECAR_VERSION, "checks": results},
                       ensure_ascii=False, indent=2) + chr(10),
            encoding="utf-8",
        )
    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        checkpoint_sqlite(self.data_root)
        for server in (self._gateway, self._inner):
            if server is None:
                continue
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass

    def wait(self, parent_pid: int | None = None) -> None:
        while not self._stopped.wait(2.0):
            if parent_pid and not pid_alive(parent_pid):
                self.stop()


# --------------------------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------------------------
def _http(port: int, method: str, path: str, *, headers: Mapping[str, str] | None = None,
          body: Any = None, timeout: float = 30.0) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        payload = None
        hdrs = dict(headers or {})
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    finally:
        conn.close()


def run_self_test(*, require_ui: bool | None = None, emit: Callable[[str], None] = print) -> int:
    require_ui = bool(getattr(sys, "frozen", False)) if require_ui is None else require_ui
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="muse-selftest-", ignore_cleanup_errors=True) as tmp:
        engine = None
        try:
            engine = MuseEngine(data_root=Path(tmp) / "muse-data", ui_dist=resolve_ui_dist()).start()
            port, token = engine.port, engine.session_token
            bearer = {"Authorization": "Bearer " + token}
            status, _, raw = _http(port, "GET", "/api/v1/health", headers=bearer)
            check("health", status == 200 and json.loads(raw).get("status") == "ok", f"HTTP {status}")
            status, _, raw = _http(port, "GET", "/api/v1/omega/consciousness", headers=bearer)
            check("consciousness_ten_dimensions", status == 200, f"HTTP {status} {raw[:120]!r}")
            status, _, raw = _http(port, "POST", "/api/v1/omega/xuanshu/fuse", headers=bearer,
                                   body={"question": "新电脑开箱自检", "seed": "self-test"})
            computed: list[str] = []
            if status == 200:
                computed = json.loads(raw).get("result", {}).get("computed_lenses", [])
            check("xuanshu_symbolic_fallback", status == 200 and {"ziwei", "qimen", "liuren"} <= set(computed),
                  f"HTTP {status} computed={computed}")
            status, _, _ = _http(port, "GET", "/api/v1/health")
            check("rejects_unauthenticated", status == 401, f"HTTP {status}")
            status, _, _ = _http(port, "GET", "/api/v1/health", headers={**bearer, "Origin": "https://evil.example"})
            check("rejects_cross_origin", status == 403, f"HTTP {status}")
            status, _, _ = _http(port, "GET", "/api/v1/health", headers={**bearer, "Host": "evil.example"})
            check("rejects_foreign_host", status == 421, f"HTTP {status}")
            status, hdrs, _ = _http(port, "GET", "/__muse/bootstrap?nonce=" + engine.launch_nonce)
            cookie = hdrs.get("set-cookie", "")
            check("cookie_bootstrap", status == 302 and "HttpOnly" in cookie and "SameSite=Strict" in cookie,
                  f"HTTP {status}")
            status, _, _ = _http(port, "GET", "/__muse/bootstrap?nonce=" + engine.launch_nonce)
            check("bootstrap_nonce_single_use", status == 403, f"HTTP {status}")
            session_cookie = {"Cookie": f"{COOKIE_NAME}={token}"}
            status, _, _ = _http(port, "POST", "/__muse/quick-note", headers=session_cookie,
                                 body={"text": "自检灵感", "tags": ["self-test"]})
            check("quick_note_inbox", status == 201, f"HTTP {status}")
            if engine.ui_dist is not None:
                status, hdrs, raw = _http(port, "GET", "/", headers=session_cookie)
                check("static_index_same_origin", status == 200 and b"<" in raw[:200]
                      and "script-src 'self'" in hdrs.get("content-security-policy", ""), f"HTTP {status}")
                status, _, _ = _http(port, "GET", "/review-desk.html", headers=session_cookie)
                check("static_review_desk", status == 200, f"HTTP {status}")
                status, _, _ = _http(port, "GET", "/..%2f..%2fpyproject.toml", headers=session_cookie)
                check("static_rejects_traversal", status == 404, f"HTTP {status}")
            else:
                check("static_ui_present", not require_ui, "ui dist not found")
            check("isolated_data_root", Path(engine.data_root).is_relative_to(Path(tmp).resolve()), engine.data_root)
        except Exception as exc:  # pragma: no cover - reported as a failed check
            check("engine_start", False, f"{type(exc).__name__}: {exc}")
        finally:
            if engine is not None:
                engine.stop()
    passed = bool(checks) and all(item["ok"] for item in checks)
    emit(json.dumps({"self_test": "passed" if passed else "failed", "seconds": round(time.monotonic() - started, 2),
                     "checks": checks}, ensure_ascii=False))
    return 0 if passed else 1


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse-engine", description="Muse desktop sidecar engine")
    parser.add_argument("--self-test", action="store_true", help="run the isolated startup self-test and exit")
    parser.add_argument("--serve", action="store_true", help="serve (default when no other mode is given)")
    parser.add_argument("--port", type=int, default=0, help="gateway port (0 = dynamic free port)")
    parser.add_argument("--data-dir", help="runtime data root (default: per-user application directory)")
    parser.add_argument("--parent-pid", type=int, default=0, help="exit automatically when this process dies")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    if args.version:
        print(json.dumps({"service": "muse-engine", "version": SIDECAR_VERSION}))
        return 0
    if args.self_test:
        return run_self_test()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    token = os.environ.pop("MUSE_SESSION_TOKEN", "") or None
    nonce = os.environ.pop("MUSE_LAUNCH_NONCE", "") or None
    interactive = token is None
    data_root = Path(args.data_dir) if args.data_dir else default_data_dir()
    engine = MuseEngine(data_root=data_root, port=args.port, session_token=token, launch_nonce=nonce,
                        ui_dist=resolve_ui_dist(), verbose=args.verbose).start()
    ready = {"event": "ready", "port": engine.port, "pid": os.getpid(), "version": SIDECAR_VERSION,
             "ui": engine.ui_dist is not None}
    if interactive:
        ready["open"] = f"{engine.base_url}__muse/bootstrap?nonce={engine.launch_nonce}"
    print(json.dumps(ready), flush=True)
    try:
        engine.wait(parent_pid=args.parent_pid or None)
    except KeyboardInterrupt:
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
