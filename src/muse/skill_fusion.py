"""Safe source-to-capability fusion for Muse.

External source code is inspected as data only. This module never imports, executes,
installs, or activates code from an uploaded or downloaded archive.
"""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import ipaddress
import io
import json
import os
import re
import socket
import sqlite3
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from muse.capabilities import CapabilityRegistry

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_FILES = 300
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_TEXT_FILE_BYTES = 512 * 1024
MAX_TEXT_TOTAL_BYTES = 2 * 1024 * 1024
MAX_URL_LENGTH = 2048
MAX_RETAINED_ARCHIVES = 50
MAX_RETAINED_ARCHIVE_BYTES = 400 * 1024 * 1024
_ALLOWED_HOSTS = frozenset({"github.com", "api.github.com", "codeload.github.com", "gitlab.com"})
_TEXT_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rs", ".go",
    ".java", ".kt", ".cs", ".cpp", ".c", ".h", ".hpp", ".sh", ".ps1", ".bat",
    ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".xml", ".ini",
    ".cfg", ".sql", ".html", ".css", ".svelte", ".vue",
})
_IGNORED_PARTS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    "target", ".next", ".cache", "vendor", "coverage",
})
_CAPABILITY_RULES: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("web_research", "research", "Web research", "Search, crawl and extract web information", (r"scrapling", r"crawler", r"scraper", r"web.?research", r"browser automation")),
    ("video_editing", "media", "Video editing", "Compose, transform and inspect video assets", (r"ffmpeg", r"video.?edit", r"timeline", r"remotion", r"video.?render")),
    ("speech_synthesis", "audio", "Speech synthesis", "Generate or transform speech and voice audio", (r"tts", r"text.?to.?speech", r"speech.?synth", r"voice.?clone")),
    ("image_generation", "visual", "Image generation", "Generate or transform raster and vector imagery", (r"image.?generation", r"diffusion", r"stable.?diffusion", r"comfyui")),
    ("document_processing", "documents", "Document processing", "Read, transform and generate structured documents", (r"pdf", r"docx", r"document.?parser", r"ocr")),
    ("code_analysis", "development", "Code analysis", "Inspect source code, symbols and dependencies", (r"ast", r"parser", r"lsp", r"static.?analysis", r"code.?analysis")),
    ("workflow_automation", "automation", "Workflow automation", "Compose reusable multi-step automation workflows", (r"workflow", r"orchestrat", r"automation", r"agent.?framework")),
    ("knowledge_graph", "knowledge", "Knowledge graph", "Connect entities, evidence and reusable knowledge", (r"knowledge.?graph", r"vector.?store", r"embedding", r"graph.?database")),
    ("data_analysis", "data", "Data analysis", "Transform, query and analyze structured datasets", (r"pandas", r"dataframe", r"analytics", r"sql.?engine")),
)


class FusionError(ValueError):
    """A source cannot be safely inspected or fused."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_public_host(host: str) -> None:
    if host not in _ALLOWED_HOSTS:
        raise FusionError("URL host is not on the supported source allowlist")
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise FusionError("source host could not be resolved") from exc
    addresses = {record[4][0].split("%", 1)[0] for record in records}
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise FusionError("source host resolved to a non-public network address")


def _fetch_https(url: str, *, max_bytes: int) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect)
    current = url
    for _ in range(4):
        parsed = urllib.parse.urlsplit(current)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not parsed.hostname
                or parsed.fragment or len(current) > MAX_URL_LENGTH):
            raise FusionError("only public HTTPS source URLs are supported")
        host = parsed.hostname.lower().rstrip(".")
        _validate_public_host(host)
        request = urllib.request.Request(
            current, headers={"User-Agent": "Muse-Skill-Fusion/1.0", "Accept": "application/zip, application/json"},
        )
        try:
            with opener.open(request, timeout=12) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise FusionError("remote source exceeds the download size limit")
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise FusionError("remote source exceeds the download size limit")
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise FusionError(f"source host returned HTTP {exc.code}") from exc
            location = exc.headers.get("Location")
            if not location:
                raise FusionError("source redirect did not include a destination")
            current = urllib.parse.urljoin(current, location)
        except (OSError, TimeoutError) as exc:
            raise FusionError("source download failed or timed out") from exc
    raise FusionError("source redirected too many times")


def _download_repository(url: str) -> tuple[bytes, str]:
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL_LENGTH:
        raise FusionError("source_url must be a valid URL of at most 2048 characters")
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise FusionError("repository sources must use public HTTPS")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _ALLOWED_HOSTS:
        raise FusionError("URL host is not on the supported source allowlist")
    path = parsed.path.strip("/")
    if host == "github.com":
        parts = path.removesuffix(".git").split("/")
        if len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", part) for part in parts):
            raise FusionError("use a GitHub repository URL in the form github.com/owner/repository")
        owner, repo = parts
        metadata_url = f"https://api.github.com/repos/{owner}/{repo}"
        metadata = json.loads(_fetch_https(metadata_url, max_bytes=256 * 1024).decode("utf-8"))
        branch = metadata.get("default_branch")
        if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", branch) or ".." in branch.split("/"):
            raise FusionError("GitHub repository did not provide a safe default branch")
        archive_url = (
            f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/"
            + urllib.parse.quote(branch, safe="/")
        )
        return _fetch_https(archive_url, max_bytes=MAX_ARCHIVE_BYTES), f"github.com/{owner}/{repo}"
    if host == "codeload.github.com" or (host == "gitlab.com" and "/-/archive/" in parsed.path):
        return _fetch_https(url.strip(), max_bytes=MAX_ARCHIVE_BYTES), host + "/" + path[:180]
    raise FusionError("supported URLs are GitHub repository pages or direct GitHub/GitLab ZIP archives")


def _read_archive(archive_bytes: bytes) -> tuple[list[str], dict[str, str], dict[str, int]]:
    if not isinstance(archive_bytes, bytes) or not 1 <= len(archive_bytes) <= MAX_ARCHIVE_BYTES:
        raise FusionError("archive must contain 1 byte to 8 MiB")
    paths: list[str] = []
    text_files: dict[str, str] = {}
    languages: dict[str, int] = {}
    total_uncompressed = 0
    total_text = 0
    seen: set[str] = set()
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except (zipfile.BadZipFile, OSError) as exc:
        raise FusionError("source archive is not a valid ZIP file") from exc
    with archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ARCHIVE_FILES:
            raise FusionError("archive must contain between 1 and 300 entries")
        for entry in entries:
            raw_name = entry.filename.replace("\\", "/")
            if entry.flag_bits & 0x1:
                raise FusionError("encrypted ZIP entries are not supported")
            path = PurePosixPath(raw_name)
            mode = (entry.external_attr >> 16) & 0xFFFF
            mode_type = stat.S_IFMT(mode)
            if (not raw_name or any(ord(char) < 32 or ord(char) == 127 for char in raw_name)
                    or raw_name.startswith("/") or re.match(r"^[A-Za-z]:", raw_name)
                    or any(part in {"", ".", ".."} for part in raw_name.rstrip("/").split("/"))
                    or stat.S_ISLNK(mode) or (mode_type and mode_type not in {stat.S_IFREG, stat.S_IFDIR})):
                raise FusionError("archive contains an unsafe path or special file")
            normalized = path.as_posix().casefold()
            if normalized in seen:
                raise FusionError("archive contains duplicate paths")
            seen.add(normalized)
            if entry.is_dir():
                continue
            total_uncompressed += entry.file_size
            if entry.file_size < 0 or total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise FusionError("archive expands beyond the 32 MiB safety limit")
            if entry.compress_size == 0 and entry.file_size > 0:
                raise FusionError("archive contains an invalid compressed entry")
            if entry.compress_size and entry.file_size / entry.compress_size > 1000:
                raise FusionError("archive contains a suspicious compression ratio")
            if any(part.casefold() in _IGNORED_PARTS for part in path.parts):
                continue
            paths.append(path.as_posix())
            suffix = path.suffix.lower()
            if suffix in {".py", ".pyi"}:
                languages["Python"] = languages.get("Python", 0) + 1
            elif suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}:
                languages["JavaScript / TypeScript"] = languages.get("JavaScript / TypeScript", 0) + 1
            elif suffix == ".rs":
                languages["Rust"] = languages.get("Rust", 0) + 1
            elif suffix == ".go":
                languages["Go"] = languages.get("Go", 0) + 1
            elif suffix in {".md", ".rst"}:
                languages["Documentation"] = languages.get("Documentation", 0) + 1
            is_text = suffix in _TEXT_SUFFIXES or path.name.casefold() in {
                "dockerfile", "makefile", "license", "license.md", "readme", "readme.md",
            }
            if is_text and entry.file_size <= MAX_TEXT_FILE_BYTES and total_text < MAX_TEXT_TOTAL_BYTES:
                read_limit = min(entry.file_size, MAX_TEXT_FILE_BYTES, MAX_TEXT_TOTAL_BYTES - total_text)
                try:
                    with archive.open(entry, "r") as source:
                        raw = source.read(read_limit + 1)
                except (RuntimeError, OSError, NotImplementedError, zipfile.BadZipFile) as exc:
                    raise FusionError("archive contains an unreadable source file") from exc
                if len(raw) <= read_limit and b"\x00" not in raw:
                    text_files[path.as_posix()] = raw.decode("utf-8-sig", errors="replace")
                    total_text += len(raw)
    if not paths:
        raise FusionError("archive contains no inspectable project files")
    return paths[:MAX_ARCHIVE_FILES], text_files, languages


def _extract_capabilities(paths: list[str], text_files: dict[str, str]) -> list[dict[str, Any]]:
    corpus = "\n".join(paths) + "\n" + "\n".join(text_files.values())
    results: list[dict[str, Any]] = []
    for capability_id, domain, name, description, patterns in _CAPABILITY_RULES:
        hits = [pattern for pattern in patterns if re.search(pattern, corpus, re.IGNORECASE)]
        if not hits:
            continue
        evidence = [
            path for path in paths
            if any(re.search(pattern, path, re.IGNORECASE) for pattern in patterns)
        ][:6]
        results.append({
            "id": capability_id, "domain": domain, "name": name, "description": description,
            "confidence": round(min(0.95, 0.55 + 0.1 * len(hits)), 2),
            "signals": evidence or ["repository text matched capability signals"],
            "execution_state": "not_executed",
        })
    return results


class SkillFusionService:
    """Persist source analysis and proposed cross-capability links without executing source code."""

    def __init__(self, workspace_root: str | Path, capability_db: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        data_dir = self.workspace_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.database = data_dir / "skill-fusions.sqlite3"
        self.archive_dir = data_dir / "fusion_sources"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        if not self.archive_dir.resolve().is_relative_to(self.workspace_root):
            raise FusionError("fusion archive storage must remain inside the Muse workspace")
        self.capability_db = Path(capability_db)
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS fusion_submissions (
                    fusion_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source_label TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL UNIQUE, status TEXT NOT NULL, report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, approved_at TEXT, approved_by TEXT
                )"""
            )
            connection.commit()

    @staticmethod
    def _decode_archive(encoded: str) -> bytes:
        if not isinstance(encoded, str) or len(encoded) > ((MAX_ARCHIVE_BYTES + 2) // 3) * 4:
            raise FusionError("archive_base64 exceeds the 8 MiB upload limit")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise FusionError("archive_base64 is invalid") from exc
        if not 1 <= len(payload) <= MAX_ARCHIVE_BYTES:
            raise FusionError("uploaded archive must be between 1 byte and 8 MiB")
        return payload

    def _register_candidates(self, report: dict[str, Any]) -> list[str]:
        created: list[str] = []
        try:
            with CapabilityRegistry(self.capability_db) as registry:
                for candidate in report["analysis"]["capabilities"]:
                    capability_id = f"fusion.{report['fusion_id'][:12]}.{candidate['id']}"
                    registry.register(
                        capability_id=capability_id, domain=candidate["domain"], name=candidate["name"],
                        description=candidate["description"], status="unverified", executable=None,
                        capabilities=(candidate["id"],), requirements=(), trust="unverified",
                        license="unknown", metadata={
                            "fusion_id": report["fusion_id"],
                            "source_sha256": report["source"]["sha256"],
                            "source_label": report["source"]["label"],
                            "execution_policy": "never_execute_untrusted_source",
                            "integration_edges": [
                                edge for edge in report["analysis"]["integration_edges"]
                                if edge["from"] == f"external:{candidate['id']}"
                            ],
                        },
                    )
                    created.append(capability_id)
        except Exception as exc:
            if created:
                self._disable_capabilities(
                    report["fusion_id"], created, rolled_back_by="system",
                    reason="candidate_registration_failed",
                )
            raise FusionError("candidate capability registration failed safely") from exc
        return created

    def _disable_capabilities(
        self, fusion_id: str, capability_ids: list[str], *, rolled_back_by: str, reason: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with CapabilityRegistry(self.capability_db) as registry:
            entries = [registry.get(capability_id) for capability_id in capability_ids]
            if any(
                item is None or item.get("metadata", {}).get("fusion_id") != fusion_id
                for item in entries
            ):
                raise FusionError("capability registry changed; rollback preflight failed")
            for item in entries:
                registry.register(
                    capability_id=item["capability_id"], domain=item["domain"], name=item["name"],
                    description=item["description"], status="disabled", executable=None,
                    capabilities=item["capabilities"], requirements=item["requirements"],
                    trust=item["trust"], license=item["license"], version=item["version"],
                    metadata={
                        **item["metadata"],
                        "rollback": {"rolled_back_at": now, "rolled_back_by": rolled_back_by, "reason": reason},
                        "execution_policy": "disabled_by_fusion_rollback" if rolled_back_by != "system" else "disabled_after_fusion_failure",
                    },
                )

    def submit(
        self, *, source_url: str | None = None, archive_base64: str | None = None,
        file_name: str | None = None,
    ) -> dict[str, Any]:
        if bool(source_url) == bool(archive_base64):
            raise FusionError("provide exactly one source_url or archive_base64")
        if source_url:
            archive_bytes, source_label = _download_repository(source_url)
            source_kind = "url"
        else:
            archive_bytes = self._decode_archive(archive_base64 or "")
            if file_name is not None and (not isinstance(file_name, str) or len(file_name) > 260):
                raise FusionError("file_name must be text of at most 260 characters")
            safe_name = PurePosixPath((file_name or "uploaded-source.zip").replace("\\", "/")).name
            source_label = "".join(char for char in safe_name if ord(char) >= 32 and ord(char) != 127).strip()[:160]
            if not source_label:
                source_label = "uploaded-source.zip"
            source_kind = "archive"
        digest = hashlib.sha256(archive_bytes).hexdigest()
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            existing = connection.execute(
                "SELECT * FROM fusion_submissions WHERE source_sha256=?", (digest,),
            ).fetchone()
            if existing:
                report = json.loads(existing["report_json"])
                report["duplicate"] = True
                return {"api_version": 1, "fusion": report}


        paths, text_files, languages = _read_archive(archive_bytes)
        candidates = _extract_capabilities(paths, text_files)
        existing_capabilities: list[dict[str, Any]] = []
        with CapabilityRegistry(self.capability_db) as registry:
            registered = registry.list_all()
        for candidate in candidates:
            candidate_terms = {candidate["id"], candidate["domain"]}
            for item in registered:
                overlap = candidate_terms.intersection(set(item.get("capabilities", [])) | {item.get("domain", "")})
                if overlap:
                    existing_capabilities.append({
                        "candidate_id": candidate["id"],
                        "capability_id": item["capability_id"],
                        "name": item["name"],
                        "status": item["status"],
                        "relationship": "overlap" if candidate["id"] in item.get("capabilities", []) else "domain_related",
                    })
        integration_edges = [
            {
                "from": f"external:{candidate['id']}",
                "to": f"muse:{item['capability_id']}",
                "relationship": item["relationship"],
                "activation": "requires_review_and_verification",
            }
            for candidate in candidates for item in existing_capabilities
            if item["candidate_id"] == candidate["id"]
        ]
        manifest_names = {
            "pyproject.toml", "package.json", "cargo.toml", "go.mod", "requirements.txt",
            "pom.xml", "composer.json", "dockerfile", "makefile",
        }
        manifests = sorted({
            Path(path).name for path in paths if Path(path).name.casefold() in manifest_names
        })
        license_present = any(Path(path).name.casefold().startswith("license") for path in paths)
        report = {
            "fusion_id": uuid.uuid4().hex,
            "source": {"kind": source_kind, "label": source_label, "sha256": digest},
            "status": "integrated_unverified",
            "analysis": {
                "file_count": len(paths), "languages": languages, "manifests": manifests,
                "license_file_present": license_present,
                "license_status": "license_file_present_unclassified" if license_present else "unknown",
                "capabilities": candidates,
                "integration_edges": integration_edges,
                "matched_existing_capabilities": existing_capabilities[:80],
            },
            "safety": {
                "archive_validated": True, "path_traversal_checked": True,
                "symlinks_and_special_files_rejected": True, "size_and_ratio_limits_enforced": True,
                "external_code_executed": False, "dependencies_installed": False,
                "network_access": "only public allowlisted HTTPS source hosts",
                "activation": "blocked_until_independent_verification",
            },
            "next_step": "Candidates are automatically linked into the Muse unverified capability graph; run independent tests and trust review before activation.",
            "duplicate": False,
        }
        report["safety"]["archive_retained"] = True
        archive_path = self.archive_dir / f"{report['fusion_id']}.zip"
        retained_archives = list(self.archive_dir.glob("*.zip"))
        retained_bytes = sum(item.stat().st_size for item in retained_archives if item.is_file())
        if len(retained_archives) >= MAX_RETAINED_ARCHIVES or retained_bytes + len(archive_bytes) > MAX_RETAINED_ARCHIVE_BYTES:
            raise FusionError("fusion archive retention quota reached; no new source was stored")
        temporary_path = self.archive_dir / f".{report['fusion_id']}.tmp"
        try:
            with temporary_path.open("xb") as stored:
                stored.write(archive_bytes)
                stored.flush()
                os.fsync(stored.fileno())
            os.replace(temporary_path, archive_path)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise FusionError("validated source archive could not be retained safely") from exc
        try:
            registered_ids = self._register_candidates(report)
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        report["registered_capability_ids"] = registered_ids
        report["status"] = "integrated_unverified" if registered_ids else "analyzed_no_capabilities"
        report["activation"] = "blocked_until_tests_and_trust_review"
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with closing(sqlite3.connect(self.database, timeout=10)) as connection:
                connection.execute(
                    """INSERT INTO fusion_submissions
                       (fusion_id,source_kind,source_label,source_sha256,status,report_json,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (report["fusion_id"], source_kind, source_label, digest, report["status"],
                     json.dumps(report, ensure_ascii=False, sort_keys=True), created_at),
                )
                connection.commit()
        except Exception:
            if registered_ids:
                self._disable_capabilities(
                    report["fusion_id"], registered_ids, rolled_back_by="system",
                    reason="fusion_record_write_failed",
                )
            archive_path.unlink(missing_ok=True)
            raise
        return {"api_version": 1, "fusion": report}

    def get(self, fusion_id: str) -> dict[str, Any] | None:
        if not isinstance(fusion_id, str) or not re.fullmatch(r"[a-f0-9]{32}", fusion_id):
            raise FusionError("fusion id is invalid")
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            row = connection.execute(
                "SELECT report_json FROM fusion_submissions WHERE fusion_id=?", (fusion_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise FusionError("limit must be between 1 and 50")
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            rows = connection.execute(
                "SELECT report_json FROM fusion_submissions ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def approve(self, fusion_id: str, *, approved_by: str) -> dict[str, Any]:
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 120:
            raise FusionError("approved_by must be non-empty text of at most 120 characters")
        report = self.get(fusion_id)
        if report is None:
            raise FusionError("fusion submission was not found")
        if report["status"] == "approved_unverified":
            return {"api_version": 1, "fusion": report}
        if report["status"] != "integrated_unverified":
            raise FusionError("only an integrated unverified fusion can be reviewed")
        capability_ids = report.get("registered_capability_ids", [])
        if not capability_ids:
            raise FusionError("fusion has no registered capability entries to review")
        approved_at = datetime.now(timezone.utc).isoformat()
        with CapabilityRegistry(self.capability_db) as registry:
            entries = [registry.get(capability_id) for capability_id in capability_ids]
            if any(
                item is None or item.get("metadata", {}).get("fusion_id") != fusion_id
                for item in entries
            ):
                raise FusionError("capability registry changed; review preflight failed")
            for item in entries:
                registry.register(
                    capability_id=item["capability_id"], domain=item["domain"], name=item["name"],
                    description=item["description"], status="unverified", executable=None,
                    capabilities=item["capabilities"], requirements=item["requirements"],
                    trust=item["trust"], license=item["license"], version=item["version"],
                    metadata={
                        **item["metadata"],
                        "human_review": {"reviewed_at": approved_at, "reviewed_by": approved_by.strip()},
                        "execution_policy": "never_execute_untrusted_source",
                    },
                )
        report["status"] = "approved_unverified"
        report["approved_by"] = approved_by.strip()
        report["approved_at"] = approved_at
        report["activation"] = "blocked_until_tests_and_trust_review"
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.execute(
                "UPDATE fusion_submissions SET status=?,report_json=?,approved_at=?,approved_by=? WHERE fusion_id=?",
                ("approved_unverified", json.dumps(report, ensure_ascii=False, sort_keys=True),
                 approved_at, approved_by.strip(), fusion_id),
            )
            connection.commit()
        return {"api_version": 1, "fusion": report}



    def rollback(self, fusion_id: str, *, rolled_back_by: str) -> dict[str, Any]:
        if not isinstance(rolled_back_by, str) or not rolled_back_by.strip() or len(rolled_back_by) > 120:
            raise FusionError("rolled_back_by must be non-empty text of at most 120 characters")
        report = self.get(fusion_id)
        if report is None:
            raise FusionError("fusion submission was not found")
        if report["status"] not in {"integrated_unverified", "approved_unverified"}:
            raise FusionError("only an integrated unverified fusion can be rolled back")
        capability_ids = report.get("registered_capability_ids", [])
        if not capability_ids:
            raise FusionError("fusion has no registered capability entries to roll back")
        now = datetime.now(timezone.utc).isoformat()
        self._disable_capabilities(
            fusion_id, capability_ids, rolled_back_by=rolled_back_by.strip(),
            reason="user_requested_rollback",
        )
        report["status"] = "rolled_back"
        report["rolled_back_by"] = rolled_back_by.strip()
        report["rolled_back_at"] = now
        report["activation"] = "rolled_back_disabled"
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.execute(
                "UPDATE fusion_submissions SET status=?,report_json=? WHERE fusion_id=?",
                ("rolled_back", json.dumps(report, ensure_ascii=False, sort_keys=True), fusion_id),
            )
            connection.commit()
        return {"api_version": 1, "fusion": report}
