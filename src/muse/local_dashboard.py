"""Authenticated LAN-capable browser host for Muse's local dashboard."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Sequence
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from muse.status_api import create_status_server

def _local_ipv4_addresses() -> list[str]:
    addresses = set()
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    route_address = None
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        route_address = probe.getsockname()[0]
    except OSError:
        pass
    finally:
        probe.close()
    if route_address:
        addresses.add(route_address)
    private = []
    for value in addresses:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            continue
        if (ip.version == 4 and ip.is_private and not ip.is_loopback
                and not ip.is_link_local and not ip.is_reserved
                and ip not in ipaddress.ip_network("198.18.0.0/15")):
            private.append(value)
    ordered = ([route_address] if route_address in private else []) + sorted(
        value for value in private if value != route_address
    )
    return ["127.0.0.1", *ordered]

class LocalDashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        """Keep normal browser disconnects from producing noisy server tracebacks."""
        _exc_type, exc, _traceback = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)

    def __init__(self, address, handler, *, api_server, bearer, dist_dir,
                 lan_mode=False, pairing_code=None):
        self.api_server = api_server
        self.bearer = bearer
        self.dist_dir = Path(dist_dir).resolve()
        self.lan_mode = bool(lan_mode)
        self.pairing_code = pairing_code
        self.allowed_hosts = set(_local_ipv4_addresses()) | {"localhost", "::1"}
        self.sessions: dict[str, float] = {}
        self.sessions_lock = threading.Lock()
        self.pair_attempts: dict[str, list[float]] = {}
        self.lan_urls: list[str] = []
        super().__init__(address, handler)


def create_local_dashboard_server(
    dist_dir: str | Path, *, api_server, bearer: str, port: int = 0,
    host: str = "127.0.0.1", lan_mode: bool = False, pairing_code: str | None = None,
    advertise_ip: str | None = None,
):
    """Create a same-origin dashboard; LAN access is protected by a pairing code."""
    directory = Path(dist_dir).resolve()
    if not (directory / "index.html").is_file():
        raise FileNotFoundError("Muse UI build is missing; run npm run build in ui-prototype")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("dashboard port must be between 0 and 65535")
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("dashboard host must be loopback or 0.0.0.0")
    if lan_mode and (host != "0.0.0.0" or not isinstance(pairing_code, str)
                     or len(pairing_code) != 8 or not pairing_code.isdigit()):
        raise ValueError("LAN mode requires 0.0.0.0 and an eight-digit pairing code")
    if not isinstance(bearer, str) or len(bearer) < 32:
        raise ValueError("dashboard bearer token is invalid")
    if advertise_ip is not None:
        try:
            advertised = ipaddress.ip_address(advertise_ip)
        except ValueError as exc:
            raise ValueError("advertise_ip must be a valid IPv4 address") from exc
        if advertised.version != 4 or not advertised.is_private or advertise_ip not in _local_ipv4_addresses():
            raise ValueError("advertise_ip must be a private IPv4 address assigned to this host")

    class Handler(SimpleHTTPRequestHandler):
        server_version = "MuseDashboard/1"
        sys_version = ""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def log_message(self, _format, *_args):
            return

        def _send_bytes(self, status, body, content_type, *, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            if headers:
                for key, value in headers.items():
                    if key.lower() in {"content-disposition", "location", "set-cookie"}:
                        self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _is_loopback(self) -> bool:
            try:
                return ipaddress.ip_address(self.client_address[0]).is_loopback
            except (ValueError, IndexError):
                return False

        def _paired(self) -> bool:
            if self._is_loopback():
                return True
            cookie = self.headers.get("Cookie", "")
            token = ""
            for item in cookie.split(";"):
                key, separator, value = item.strip().partition("=")
                if separator and key == "muse_dashboard":
                    token = value
                    break
            if not token:
                return False
            now = time.monotonic()
            with self.server.sessions_lock:
                expiry = self.server.sessions.get(token, 0)
                if expiry <= now:
                    self.server.sessions.pop(token, None)
                    return False
                return True

        def _trusted_browser_request(self) -> bool:
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").lower()
            try:
                parsed_host = urlsplit("//" + host)
                hostname = parsed_host.hostname
                valid_port = parsed_host.port == port
            except ValueError:
                hostname, valid_port = None, False
            if hostname not in self.server.allowed_hosts or not valid_port:
                self._send_bytes(421, b"invalid host", "text/plain; charset=utf-8")
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed_origin = urlsplit(origin)
                if (parsed_origin.scheme != "http" or parsed_origin.netloc.lower() != host
                        or parsed_origin.username is not None or parsed_origin.password is not None):
                    self._send_bytes(403, b"origin denied", "text/plain; charset=utf-8")
                    return False
            fetch_site = self.headers.get("Sec-Fetch-Site")
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                self._send_bytes(403, b"cross-site request denied", "text/plain; charset=utf-8")
                return False
            if not self._is_loopback():
                try:
                    remote = ipaddress.ip_address(self.client_address[0])
                except (ValueError, IndexError):
                    self._send_bytes(403, b"invalid client address", "text/plain; charset=utf-8")
                    return False
                if not self.server.lan_mode or not remote.is_private:
                    self._send_bytes(403, b"private LAN clients only", "text/plain; charset=utf-8")
                    return False
            return True

        def _login_page(self):
            page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Muse 设备配对</title><body style="margin:0;background:#0b1220;color:#e5edf7;font:16px system-ui;display:grid;min-height:100vh;place-items:center">
<form method="post" action="/__muse__/pair" style="width:min(360px,85vw);padding:28px;background:#121e30;border:1px solid #34445d;border-radius:16px">
<h2>Muse 局域网设备配对</h2><p style="color:#aab8cc">这是给手机、平板等其他局域网设备使用的一次性配对页。</p><p style="color:#c7f4ee">如果你现在就在 Muse 主电脑上，请打开 <b>http://127.0.0.1:8766/</b>，不要在主电脑上输入配对码。配对码显示在主电脑的“灵感工坊”顶部。</p><p style="color:#aab8cc">其他设备请输入主电脑控制台显示的 8 位配对码，授权后即可提交灵感、查看历史和下载报告。</p>
<label>设备配对码<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{8}" maxlength="8" required style="box-sizing:border-box;width:100%;margin:12px 0;padding:12px;background:#0b1220;color:white;border:1px solid #50617a;font-size:22px;letter-spacing:5px"></label>
<button style="width:100%;padding:12px;background:#24556a;color:white;border:0;border-radius:8px;font-size:16px">配对并打开 Muse</button>
</form></body></html>"""
            self._send_bytes(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def _proxy_api(self):
            if not self._paired():
                self._send_bytes(401, b'{"error":"device pairing required"}', "application/json; charset=utf-8")
                return
            path = self.path
            if not path.startswith("/api/v1/") or len(path) > 2048:
                self._send_bytes(404, b'{"error":"not found"}', "application/json; charset=utf-8")
                return
            body = None
            headers = {"Authorization": "Bearer " + self.server.bearer, "Accept": self.headers.get("Accept", "application/json")}
            if self.command == "POST":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send_bytes(400, b'{"error":"invalid content length"}', "application/json; charset=utf-8")
                    return
                if length > 16384:
                    self._send_bytes(413, b'{"error":"request too large"}', "application/json; charset=utf-8")
                    return
                body = self.rfile.read(length) if length else b""
                if self.headers.get("Content-Type"):
                    headers["Content-Type"] = self.headers["Content-Type"]
            request = Request(
                "http://127.0.0.1:" + str(self.server.api_server.server_address[1]) + path,
                data=body, headers=headers, method=self.command,
            )
            response = None
            binary_product_route = (
                self.command == "GET"
                and re.fullmatch(
                    r"/api/v1/products/[0-9a-f]{32}/[0-9a-f]{20}/(?:download|preview|video)",
                    urlsplit(path).path,
                ) is not None
            )
            streaming_started = False
            try:
                # This upstream is always the local status API; never send it through
                # an environment-configured corporate/HTTP proxy.
                response = build_opener(ProxyHandler({})).open(request, timeout=8)
                status = response.status
                response_headers = response.headers
                if binary_product_route and status == 200:
                    try:
                        size = int(response_headers.get("Content-Length", "-1"))
                    except (TypeError, ValueError):
                        size = -1
                    if not 0 <= size <= 2 * 1024 * 1024 * 1024:
                        self._send_bytes(502, b'{"error":"invalid binary response size"}', "application/json; charset=utf-8")
                        return
                    content_type = response_headers.get("Content-Type") or "application/octet-stream"
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(size))
                    for name in ("Content-Disposition",):
                        if response_headers.get(name):
                            self.send_header(name, response_headers[name])
                    self.end_headers()
                    streaming_started = True
                    remaining = size
                    while remaining:
                        chunk = response.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError("upstream binary response ended before Content-Length")
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                    return
                payload = response.read(10 * 1024 * 1024 + 1)
            except HTTPError as exc:
                status = exc.code
                payload = exc.read(1024 * 1024)
                response_headers = exc.headers
            except OSError:
                if streaming_started:
                    return
                self._send_bytes(503, b'{"error":"local API unavailable"}', "application/json; charset=utf-8")
                return
            finally:
                if response is not None:
                    response.close()
            if len(payload) > 10 * 1024 * 1024:
                self._send_bytes(413, b'{"error":"response too large"}', "application/json; charset=utf-8")
                return
            outgoing = {}
            for name in ("Content-Disposition",):
                if response_headers and response_headers.get(name):
                    outgoing[name] = response_headers[name]
            self._send_bytes(status, payload, (response_headers.get("Content-Type") if response_headers else None)
                             or "application/octet-stream", headers=outgoing)
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            super().end_headers()

        def do_GET(self):
            if not self._trusted_browser_request():
                return
            path = urlsplit(self.path).path
            if not self._paired():
                if path == "/":
                    self._login_page()
                else:
                    self._send_bytes(401, b"device pairing required", "text/plain; charset=utf-8")
                return
            if path.startswith("/api/v1/"):
                self._proxy_api()
                return
            if path == "/__muse__/health":
                body = json.dumps({
                    "status": "ready", "pid": os.getpid(),
                    "dashboard_port": self.server.server_address[1],
                    "api_port": self.server.api_server.server_address[1],
                    "lan_mode": self.server.lan_mode,
                    "lan_urls": self.server.lan_urls,
                }, separators=(",", ":")).encode("utf-8")
                self._send_bytes(200, body, "application/json; charset=utf-8")
                return
            if path == "/__muse__/config.js":
                host = self.headers.get("Host", "").lower()
                config = {
                    "baseUrl": "http://" + host + "/",
                    "token": self.server.bearer,
                    "pairingCode": self.server.pairing_code if self._is_loopback() else None,
                    "lanUrls": self.server.lan_urls if self._is_loopback() else [],
                }
                body = ("window.__MUSE_LOCAL_CONNECTION__ = Object.freeze("
                        + json.dumps(config, separators=(",", ":")) + ");").encode("utf-8")
                self._send_bytes(200, body, "application/javascript; charset=utf-8")
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/":
                index = (self.server.dist_dir / "index.html").read_text(encoding="utf-8")
                marker = "</head>"
                if marker not in index:
                    self._send_bytes(500, b"invalid Muse UI entry point", "text/plain; charset=utf-8")
                    return
                index = index.replace(marker, '<script src="/__muse__/config.js"></script>\n' + marker, 1)
                self._send_bytes(200, index.encode("utf-8"), "text/html; charset=utf-8")
                return
            if path.startswith("/__muse__/"):
                self._send_bytes(404, b"not found", "text/plain; charset=utf-8")
                return
            super().do_GET()

        def do_POST(self):
            if not self._trusted_browser_request():
                return
            path = urlsplit(self.path).path
            if path == "/__muse__/pair":
                if self._is_loopback() or not self.server.lan_mode:
                    self._send_bytes(400, b"pairing is only for remote LAN devices", "text/plain; charset=utf-8")
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
                    self._send_bytes(415, b"form encoding required", "text/plain; charset=utf-8")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 256:
                        raise ValueError
                    values = parse_qs(self.rfile.read(length).decode("ascii"), strict_parsing=True)
                    code = values.get("code", [""])[0]
                except (ValueError, UnicodeDecodeError):
                    self._send_bytes(400, b"invalid pairing request", "text/plain; charset=utf-8")
                    return
                client_ip = self.client_address[0]
                now = time.monotonic()
                with self.server.sessions_lock:
                    attempts = [stamp for stamp in self.server.pair_attempts.get(client_ip, []) if now - stamp < 60]
                    if len(attempts) >= 5:
                        self.server.pair_attempts[client_ip] = attempts
                        self._send_bytes(429, b"too many pairing attempts; retry in one minute", "text/plain; charset=utf-8")
                        return
                    attempts.append(now)
                    self.server.pair_attempts[client_ip] = attempts
                if not secrets.compare_digest(code, self.server.pairing_code or ""):
                    self._send_bytes(403, b"pairing code incorrect", "text/plain; charset=utf-8")
                    return
                with self.server.sessions_lock:
                    self.server.pair_attempts.pop(client_ip, None)
                session = secrets.token_urlsafe(32)
                with self.server.sessions_lock:
                    self.server.sessions[session] = time.monotonic() + 12 * 60 * 60
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", "muse_dashboard=" + session
                                 + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.startswith("/api/v1/"):
                self._proxy_api()
                return
            self._send_bytes(404, b"not found", "text/plain; charset=utf-8")

        def do_OPTIONS(self):
            self._send_bytes(405, b"method not allowed", "text/plain; charset=utf-8")

    server = LocalDashboardServer(
        (host, port), Handler, api_server=api_server, bearer=bearer, dist_dir=directory,
        lan_mode=lan_mode, pairing_code=pairing_code,
    )
    if lan_mode:
        addresses = [advertise_ip] if advertise_ip else [
            address for address in _local_ipv4_addresses() if address != "127.0.0.1"
        ]
        server.lan_urls = [f"http://{address}:{server.server_address[1]}/" for address in addresses]
    return server


def _write_state(path: Path, url: str, dashboard_port: int, api_port: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(), "url": url, "dashboard_port": dashboard_port,
        "api_port": api_port, "started_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Muse's local dashboard; optional private-LAN pairing")
    parser.add_argument("--root", default=".")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--state-file", default="data/desktop/dashboard.json")
    parser.add_argument("--lan", action="store_true", help="allow private LAN clients after pairing")
    parser.add_argument("--advertise-ip", default=None, help="single private LAN IPv4 address to display")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    dist = root / "ui-prototype" / "dist"
    if not (root / "pyproject.toml").is_file():
        parser.error("--root must point to the Muse project directory")
    if not (dist / "index.html").is_file():
        parser.error("Muse UI build is missing; run npm run build in ui-prototype")
    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = root / state_path
    api_server = dashboard = api_thread = None
    try:
        api_server, bearer = create_status_server(
            task_db=root / "data/tasks/muse.sqlite3",
            progress_db=root / "data/tasks/muse.sqlite3",
            capability_db=root / "data/capabilities/muse.sqlite3",
            agent_db=root / "data/agents/muse.sqlite3",
            improvement_db=root / "data/improvements/muse.sqlite3",
            knowledge_db=root / "data/research/knowledge.sqlite3",
            report_dir=root / "data/research/reports",
            products_dir=root / "data/products",
            port=0,
        )
        pairing_code = f"{secrets.randbelow(100_000_000):08d}" if args.lan else None
        dashboard = create_local_dashboard_server(
            dist, api_server=api_server, bearer=bearer, port=args.port,
            host="0.0.0.0" if args.lan else "127.0.0.1",
            lan_mode=args.lan, pairing_code=pairing_code, advertise_ip=args.advertise_ip,
        )
        api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
        api_thread.start()
        dashboard_port = dashboard.server_address[1]
        api_port = api_server.server_address[1]
        url = f"http://127.0.0.1:{dashboard_port}/"
        _write_state(state_path, url, dashboard_port, api_port)
        print(json.dumps({
            "status": "ready", "url": url, "pid": os.getpid(),
            "lan_urls": dashboard.lan_urls, "lan_pairing_enabled": bool(args.lan),
        }), flush=True)
        dashboard.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"Muse dashboard startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if dashboard is not None:
            dashboard.server_close()
        if api_server is not None:
            api_server.shutdown()
            api_server.server_close()
        if api_thread is not None:
            api_thread.join(timeout=2)
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("pid") == os.getpid():
                state_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
