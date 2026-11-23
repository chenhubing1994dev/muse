"""Create reviewable static prototypes from approved local initiatives.

This first-stage builder never executes generated code, invokes a shell, installs
packages, or makes network requests. It writes a deterministic, self-contained
HTML artifact and records its integrity manifest under data/prototypes/.
"""
from __future__ import annotations

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from muse.initiative_engine import InitiativeStore


class PrototypeBuildError(RuntimeError):
    """A prototype could not be built or verified without overwriting data."""


class _StaticHTMLValidator(HTMLParser):
    FORBIDDEN_TAGS = {"script", "iframe", "object", "embed", "form", "base"}
    RESOURCE_ATTRIBUTES = {
        "src", "srcset", "action", "formaction", "poster", "data",
        "background", "manifest", "ping", "xlink:href",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.errors: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        self.tags.add(tag)
        if tag in self.FORBIDDEN_TAGS:
            self.errors.append("forbidden tag: " + tag)
        if tag == "style":
            self._in_style = True
        attr_map = {name.casefold(): value for name, value in attrs}
        for name, value in attr_map.items():
            if name.startswith("on"):
                self.errors.append("event handler attribute: " + name)
            if name in self.RESOURCE_ATTRIBUTES and value:
                self.errors.append("resource loading attribute: " + name)
            if name == "style" and value:
                self.errors.append("inline style attribute is not permitted")
            if name == "href" and value and not value.startswith("#"):
                self.errors.append("non-local hyperlink")
        if attr_map.get("http-equiv", "").casefold() == "refresh":
            self.errors.append("meta refresh is not permitted")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "style":
            self._in_style = False

    def handle_data(self, data: str) -> None:
        if self._in_style and re.search(
            r"(?:url\s*\(|@import|expression\s*\(|javascript\s*:)",
            data,
            flags=re.IGNORECASE,
        ):
            self.errors.append("active or remote CSS content is not permitted")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

class StaticPrototypeBuilder:
    """Build deterministic, inert HTML prototypes from InitiativeStore records."""

    BUILDER_VERSION = 3

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise PrototypeBuildError("workspace must be an existing directory")

    @staticmethod
    def _canonical_initiative(initiative: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "id", "title", "kind", "creator_value", "concept", "first_step",
            "expected_output", "interest_signals", "inspiration_refs",
            "capability_gap", "priority", "autonomy_level",
        )
        return {key: initiative.get(key) for key in fields}

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _prototype_root(self) -> Path:
        data_root = self.workspace / "data"
        prototype_root = data_root / "prototypes"
        for candidate in (data_root, prototype_root):
            if candidate.is_symlink():
                raise PrototypeBuildError("prototype output path must not contain symlinks")
        resolved = prototype_root.resolve()
        if not resolved.is_relative_to(self.workspace):
            raise PrototypeBuildError("prototype output escapes the workspace")
        return prototype_root

    @staticmethod
    def _assert_no_symlink(path: Path, root: Path) -> None:
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise PrototypeBuildError("prototype path escapes output root") from exc
        current = root
        if current.is_symlink():
            raise PrototypeBuildError("prototype output root is a symlink")
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise PrototypeBuildError("prototype path contains a symlink")

    @staticmethod
    def _render_html(item: dict[str, Any]) -> str:
        esc = lambda value: html.escape(str(value or ""), quote=True)
        interest = item.get("interest_signals") or []
        inspiration = item.get("inspiration_refs") or []
        interest_html = "".join(f"<li>{esc(value)}</li>" for value in interest) or "<li>尚未记录</li>"

        def display_reference(value: Any) -> str:
            reference = str(value or "")
            windows_path = PureWindowsPath(reference)
            if windows_path.is_absolute() and (windows_path.drive or windows_path.root):
                return windows_path.name or "本地文件引用"
            return reference
        inspiration_html = "".join(
            f"<li>{esc(display_reference(value))}</li>" for value in inspiration
        ) or "<li>尚未记录</li>"
        raw_concept = str(item.get("concept") or "")
        try:
            concept_data = json.loads(raw_concept)
        except (TypeError, json.JSONDecodeError):
            concept_data = None
        concept_sections: list[str] = []
        if isinstance(concept_data, dict):
            question = concept_data.get("research_question")
            if isinstance(question, str) and question.strip():
                concept_sections.append(
                    f"<section><h2>研究问题</h2><p>{esc(question)}</p></section>"
                )
            discoveries = concept_data.get("key_discoveries")
            if isinstance(discoveries, dict):
                summary = discoveries.get("executive_summary")
                if isinstance(summary, str) and summary.strip():
                    concept_sections.append(
                        f"<section><h2>研究摘要（待审）</h2><p>{esc(summary)}</p></section>"
                    )
                for key, heading in (
                    ("creator_inspired_hypotheses", "待验证假设"),
                    ("counter_evidence", "反证与限制"),
                    ("design_implications_for_muse", "对 Muse 的设计启发"),
                    ("testable_experiments", "建议实验"),
                    ("uncertainties", "仍未确定"),
                ):
                    values = discoveries.get(key)
                    if isinstance(values, list):
                        safe_values = [value for value in values if isinstance(value, (str, int, float)) and str(value).strip()]
                        if safe_values:
                            items_html = "".join(f"<li>{esc(value)}</li>" for value in safe_values)
                            concept_sections.append(
                                f"<section><h2>{heading}</h2><ul>{items_html}</ul></section>"
                            )
        if not concept_sections:
            concept_sections.append(
                f"<section><h2>构想</h2><p>{esc(raw_concept)}</p></section>"
            )
        concept_html = "\n".join(concept_sections)
        template = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:dark;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#10131d;color:#edf0f7}
body{margin:0;padding:2rem;background:radial-gradient(circle at top right,#28324a,#10131d 55%)}
main{max-width:900px;margin:auto}.eyebrow{color:#a8b7d8;letter-spacing:.12em;text-transform:uppercase}
section{background:#1a2030;border:1px solid #39445d;border-radius:14px;padding:1.2rem;margin:1rem 0}
h1{font-size:2rem;margin:.3rem 0 1rem}h2{font-size:1.1rem;color:#c8d6f4}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}
small,.muted{color:#aab4c8}.pill{display:inline-block;border:1px solid #60749a;border-radius:99px;padding:.2rem .7rem;margin:.2rem}
footer{border-top:1px solid #39445d;margin-top:2rem;padding-top:1rem;color:#aab4c8;font-size:.9rem}
</style>
</head>
<body><main>
<p class="eyebrow">Muse · Local Initiative Prototype</p>
<h1>__TITLE__</h1>
<p class="muted">提案类型：__KIND__ · 优先级：__PRIORITY__</p>
<section id="overview"><h2>创造者价值</h2><p>__CREATOR_VALUE__</p></section>
__CONCEPT_BLOCK__
<div class="grid">
<section><h2>第一步</h2><p>__FIRST_STEP__</p></section>
<section><h2>预期产物</h2><p>__EXPECTED_OUTPUT__</p></section>
<section><h2>能力缺口</h2><p>__CAPABILITY_GAP__</p></section>
</div>
<section><h2>兴趣线索</h2><ul>__INTEREST__</ul></section>
<section><h2>灵感与研究引用</h2><ul>__INSPIRATION__</ul></section>
<footer>静态本地原型 · 仅用于审阅与反馈。未执行任意代码、未加载外部资源、未访问网络；尚未通过人工验收，不代表已发布。</footer>
</main></body></html>
"""
        replacements = {
            "__TITLE__": esc(item.get("title")),
            "__KIND__": esc(item.get("kind")),
            "__PRIORITY__": esc(item.get("priority")),
            "__CREATOR_VALUE__": esc(item.get("creator_value")),
            "__CONCEPT_BLOCK__": concept_html,
            "__FIRST_STEP__": esc(item.get("first_step")),
            "__EXPECTED_OUTPUT__": esc(item.get("expected_output")),
            "__CAPABILITY_GAP__": esc(item.get("capability_gap")),
            "__INTEREST__": interest_html,
            "__INSPIRATION__": inspiration_html,
        }
        for marker, value in replacements.items():
            template = template.replace(marker, value)
        return template
    @staticmethod
    def _render_readme(item: dict[str, Any], input_hash: str) -> str:
        return (
            f"# {item['title']}\n\n"
            f"- Initiative ID: {item['id']}\n"
            f"- Input SHA-256: {input_hash}\n"
            f"- Kind: {item['kind']}\n"
            f"- Priority: {item['priority']}\n\n"
            "## Prototype boundary\n\n"
            "This is a static, self-contained HTML prototype. It does not execute generated code, "
            "load remote assets, access the network, invoke a shell, or install dependencies.\n\n"
            "## Acceptance checklist\n\n"
            "- [ ] Creator value and proposal scope are represented accurately.\n"
            "- [ ] All user-controlled text is HTML-escaped.\n"
            "- [ ] No script, form, iframe, object, embed, or external resource is present.\n"
            "- [ ] A human reviewer has checked usefulness and requested changes.\n"
        )

    @staticmethod
    def _validate_html(content: str) -> None:
        parser = _StaticHTMLValidator()
        try:
            parser.feed(content)
            parser.close()
        except Exception as exc:
            raise PrototypeBuildError("generated HTML could not be parsed") from exc
        required = {"html", "head", "body", "main", "h1"}
        if not required.issubset(parser.tags):
            raise PrototypeBuildError("generated HTML is missing required structure")
        if parser.errors:
            raise PrototypeBuildError(
                "generated HTML violates the static-only policy: " + "; ".join(parser.errors)
            )

    def _verify_existing(self, target: Path, input_hash: str) -> dict[str, Any]:
        manifest_path = target / "manifest.json"
        if target.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
            raise PrototypeBuildError("existing prototype is incomplete or unsafe; refusing to overwrite")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PrototypeBuildError("existing prototype manifest is unreadable") from exc
        if manifest.get("input_sha256") != input_hash:
            raise PrototypeBuildError("existing prototype input hash does not match")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != {"index.html", "README.md"}:
            raise PrototypeBuildError("existing prototype manifest has an invalid file list")
        for name, expected_hash in files.items():
            path = target / name
            if path.is_symlink() or not path.is_file():
                raise PrototypeBuildError("existing prototype artifact is missing or unsafe")
            if self._sha256(path.read_bytes()) != expected_hash:
                raise PrototypeBuildError("existing prototype artifact was modified; refusing to overwrite")
        self._validate_html((target / "index.html").read_text(encoding="utf-8"))
        return {
            "relative_path": str(target.relative_to(self.workspace)),
            "created": False,
            "validated": True,
            "input_sha256": input_hash,
            "status": "testing",
        }

    def build(self, store: InitiativeStore, initiative_id: str) -> dict[str, Any]:
        if not isinstance(initiative_id, str) or not re.fullmatch(r"[0-9a-f]{32}", initiative_id):
            raise ValueError("initiative_id must be a 32-character lowercase UUID hex string")
        initiative = store.get_by_id(initiative_id)
        if initiative is None:
            raise KeyError(initiative_id)
        if initiative.get("status") in {"parked", "rejected", "shipped"}:
            raise PrototypeBuildError("initiative status does not permit prototype generation")
        item = self._canonical_initiative(initiative)
        canonical = json.dumps(
            {"builder_version": self.BUILDER_VERSION, "initiative": item},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        input_hash = self._sha256(canonical.encode("utf-8"))
        root = self._prototype_root()
        parent = root / initiative_id
        target = parent / input_hash[:20]
        self._assert_no_symlink(parent, root)
        self._assert_no_symlink(target, root)
        if target.exists():
            result = self._verify_existing(target, input_hash)
        else:
            parent.mkdir(parents=True, exist_ok=True)
            self._assert_no_symlink(parent, root)
            temporary = Path(tempfile.mkdtemp(prefix=".prototype-", dir=parent))
            try:
                html_text = self._render_html(item)
                readme_text = self._render_readme(item, input_hash)
                self._validate_html(html_text)
                html_bytes = html_text.encode("utf-8")
                readme_bytes = readme_text.encode("utf-8")
                (temporary / "index.html").write_bytes(html_bytes)
                (temporary / "README.md").write_bytes(readme_bytes)
                manifest = {
                    "schema_version": 1,
                    "builder_version": self.BUILDER_VERSION,
                    "initiative_id": initiative_id,
                    "input_sha256": input_hash,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "files": {
                        "index.html": self._sha256(html_bytes),
                        "README.md": self._sha256(readme_bytes),
                    },
                    "execution": {
                        "arbitrary_code_executed": False,
                        "network_access": False,
                        "shell_invoked": False,
                        "dependencies_installed": False,
                    },
                    "validation": {"html_structure": "passed", "static_only_policy": "passed"},
                }
                (temporary / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                try:
                    temporary.rename(target)
                except OSError:
                    if target.exists():
                        result = self._verify_existing(target, input_hash)
                    else:
                        raise
                else:
                    result = {
                        "relative_path": str(target.relative_to(self.workspace)),
                        "created": True,
                        "validated": True,
                        "input_sha256": input_hash,
                        "status": "testing",
                    }
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary, ignore_errors=True)
        if initiative.get("status") != "testing":
            store.update(
                initiative_id,
                status="testing",
                outcome=f"Static prototype validated at {result['relative_path']}; awaiting human review.",
            )
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an inert local prototype from a Muse initiative"
    )
    parser.add_argument("initiative_id", help="Exact 32-character initiative UUID hex ID")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--db", default="")
    args = parser.parse_args(argv)
    workspace = Path(args.workspace).resolve()
    database = Path(args.db) if args.db else workspace / "data" / "initiatives" / "muse.sqlite3"
    with InitiativeStore(database) as store:
        result = StaticPrototypeBuilder(workspace).build(store, args.initiative_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
