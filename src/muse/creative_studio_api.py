"""Creative Studio API backing functions for the Muse video studio.

Design contract (docs/VISION.md + muse-video-studio-plan.md):
- All work is local and bounded. No paid API, no model download, no shell.
- The API only validates, plans and records; heavy synthesis/rendering is done by
  the dedicated Phase 2/3 modules through injectable callables, so this module
  keeps working (fail-closed) when those optional engines are not provisioned.
- Render jobs are persisted in SQLite and exposed as display-safe summaries.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


STYLE_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "hk-retro",
        "label": "复古港风",
        "sub_label": "霓虹 · 街景",
        "palette": ["#ff2e88", "#ffd76a", "#1a103c", "#00e5ff"],
        "description": "竖排繁体霓虹、VHS 扫描线、黄字白边港片字幕与街景暖色调。",
        "vfx": "embers",
        "camera_moves": ["push_in", "drift_left", "rise"],
    },
    {
        "id": "pixel",
        "label": "像素复古",
        "sub_label": "8-bit · CGA",
        "palette": ["#ff0000", "#00ffff", "#ffff00", "#0a0a2e"],
        "description": "CGA/EGA 像素转场、粒子飘散与像素风标题卡。",
        "vfx": "none",
        "camera_moves": ["push_in", "drift_right"],
    },
    {
        "id": "cosmic",
        "label": "赛博宇宙",
        "sub_label": "星云 · 光速",
        "palette": ["#7b2fff", "#00d4ff", "#ff4fd8", "#04030f"],
        "description": "星空粒子、星云渐变、光速隧道与全息信息框。",
        "vfx": "moonlight",
        "camera_moves": ["pull_out", "rise", "drift_left"],
    },
    {
        "id": "mix",
        "label": "混搭自定",
        "sub_label": "三风融合",
        "palette": ["#ff2e88", "#00e5ff", "#ffd76a", "#7b2fff"],
        "description": "按场景在港风、像素、宇宙三套预设间切换。",
        "vfx": "mist",
        "camera_moves": ["push_in", "pull_out", "drift_left", "drift_right", "rise"],
    },
)
STYLE_IDS = frozenset(item["id"] for item in STYLE_PRESETS)

NARRATION_STYLES: tuple[dict[str, str], ...] = (
    {"id": "yujie_canto", "label": "粤语御姐 · 成熟知性", "voice_style": "yujie"},
    {"id": "cute_canto", "label": "粤语活泼 · 俏皮可爱", "voice_style": "cute"},
    {"id": "news_canto", "label": "粤语播报 · 正式稳重", "voice_style": "news"},
    {"id": "none", "label": "无解说 · 纯 BGM", "voice_style": "none"},
)
NARRATION_STYLE_IDS = frozenset(item["id"] for item in NARRATION_STYLES)


def list_styles() -> dict[str, Any]:
    """Return display-safe style and narration choices."""
    return {
        "api_version": 1,
        "styles": [dict(item) for item in STYLE_PRESETS],
        "narration_styles": [dict(item) for item in NARRATION_STYLES],
        "capabilities": {
            "search_media": "local_optional",
            "generate_script": "local_deterministic",
            "render": "local_bounded",
        },
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CreativeStudioStore:
    """SQLite ledger for render jobs; stores only bounded, display-safe summaries."""

    def __init__(self, database: str | Path) -> None:
        self.path = Path(database).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS studio_render_jobs(
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                style TEXT NOT NULL,
                narration_style TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT 'queued',
                request_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def create_job(self, *, title: str, style: str, narration_style: str, request: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = _now()
        request_json = json.dumps(request, ensure_ascii=False, sort_keys=True, allow_nan=False)[:6000]
        self.connection.execute(
            "INSERT INTO studio_render_jobs(id,title,style,narration_style,status,progress,stage,request_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, title[:200], style, narration_style, "queued", 0, "queued", request_json, now, now),
        )
        self.connection.commit()
        return self.get_job(job_id)  # type: ignore[return-value]

    def update_job(self, job_id: str, *, status: str | None = None, progress: int | None = None,
                   stage: str | None = None, result: dict[str, Any] | None = None,
                   error: str | None = None) -> dict[str, Any] | None:
        fields: list[str] = []
        values: list[Any] = []
        for column, value in (("status", status), ("progress", progress), ("stage", stage), ("error", error)):
            if value is not None:
                fields.append(f"{column}=?")
                values.append(value)
        if result is not None:
            fields.append("result_json=?")
            values.append(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)[:6000])
        if not fields:
            return self.get_job(job_id)
        fields.append("updated_at=?")
        values.append(_now())
        values.append(job_id)
        self.connection.execute(f"UPDATE studio_render_jobs SET {','.join(fields)} WHERE id=?", values)
        self.connection.commit()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM studio_render_jobs WHERE id=?", (job_id,)).fetchone()
        return self._public(row) if row else None

    def list_jobs(self, *, limit: int = 30) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = self.connection.execute(
            "SELECT * FROM studio_render_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._public(row) for row in rows]

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "style": row["style"],
            "narration_style": row["narration_style"], "status": row["status"],
            "progress": row["progress"], "stage": row["stage"],
            "result": json.loads(row["result_json"] or "{}"),
            "error": row["error"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "CreativeStudioStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def validate_render_request(payload: Any) -> dict[str, Any]:
    """Validate and normalize a render request. Rejects unknown keys and unsafe values."""
    if not isinstance(payload, dict):
        raise ValueError("render request must be an object")
    allowed = {"title", "topic", "research_text", "inspiration", "style", "narration_style", "query", "aspect_ratio", "fps"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError("unexpected render fields: " + ", ".join(sorted(unknown)))
    topic = str(payload.get("topic") or payload.get("title") or "").strip()
    if not topic or len(topic) > 200:
        raise ValueError("topic must contain 1-200 characters")
    style = str(payload.get("style", "hk-retro"))
    if style not in STYLE_IDS:
        raise ValueError("unsupported style preset")
    narration_style = str(payload.get("narration_style", "yujie_canto"))
    if narration_style not in NARRATION_STYLE_IDS:
        raise ValueError("unsupported narration style")
    research_text = str(payload.get("research_text", ""))[:8000]
    inspiration = str(payload.get("inspiration", ""))[:2000]
    query = str(payload.get("query") or topic)[:300]
    aspect_ratio = str(payload.get("aspect_ratio", "9:16"))
    if aspect_ratio not in {"9:16", "16:9", "1:1"}:
        raise ValueError("unsupported aspect ratio")
    fps = payload.get("fps", 30)
    if isinstance(fps, bool) or not isinstance(fps, int) or fps not in {24, 25, 30, 60}:
        raise ValueError("fps must be one of 24, 25, 30, 60")
    return {
        "title": topic[:200], "topic": topic, "research_text": research_text,
        "inspiration": inspiration, "style": style, "narration_style": narration_style,
        "query": query, "aspect_ratio": aspect_ratio, "fps": fps,
    }


def plan_render(request: dict[str, Any], *, style_catalog: tuple[dict[str, Any], ...] = STYLE_PRESETS) -> dict[str, Any]:
    """Build a deterministic, display-safe pipeline plan without executing tools."""
    preset = next(item for item in style_catalog if item["id"] == request["style"])
    stages = [
        {"order": 1, "stage": "inspiration", "label": "Muse 哲学灵感注入", "weight": 10},
        {"order": 2, "stage": "script", "label": "粤语解说脚本生成", "weight": 20},
        {"order": 3, "stage": "media", "label": "素材搜索与智能裁剪", "weight": 25},
        {"order": 4, "stage": "narration", "label": "御姐音色 TTS 与字幕同步", "weight": 20},
        {"order": 5, "stage": "render", "label": "Remotion 风格动画合成", "weight": 25},
    ]
    return {
        "api_version": 1, "status": "planned", "request": request,
        "style": {"id": preset["id"], "label": preset["label"], "palette": preset["palette"]},
        "narration": {"style": request["narration_style"], "synth_required": request["narration_style"] != "none"},
        "stages": stages,
        "execution": "not_started",
    }


def generate_script(request: dict[str, Any], *, generator: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Generate a narration script via the Phase 2 generator, fail-closed if absent."""
    style_map = {"yujie_canto": "YUJIE_CANTO", "cute_canto": "CUTE_CANTO", "news_canto": "NEWS_CANTO"}
    if request["narration_style"] == "none":
        return {"api_version": 1, "status": "skipped", "segments": [], "reason": "narration disabled"}
    if generator is None:
        try:
            from muse.narration_generator import NarrationGenerator, NarrationStyle  # type: ignore
            generator = NarrationGenerator().generate
            style_value = NarrationStyle[style_map[request["narration_style"]]]
        except (ImportError, KeyError, AttributeError):
            return {
                "api_version": 1, "status": "blocked", "segments": [],
                "reason": "local narration generator is not available in this installation",
            }
    else:
        style_value = style_map.get(request["narration_style"], "YUJIE_CANTO")
    # The generator requires non-empty research text; derive a minimal local
    # brief from the topic when the creator left the field blank.
    research = request["research_text"].strip() or (
        f"围绕「{request['topic']}」整理三点要点：它是什么、为什么重要、可以怎样开始。"
    )
    inspiration = request["inspiration"].strip() or "让灵感成为可验证、可制作的创作"
    try:
        result = generator(
            request["topic"], research, inspiration,
            style=style_value,
        )
    except Exception as exc:  # optional engine is untrusted
        return {"api_version": 1, "status": "blocked", "segments": [], "reason": f"{type(exc).__name__}: {exc}"[:500]}
    segments = result if isinstance(result, list) else result.get("segments", []) if isinstance(result, dict) else []
    safe_segments = [
        {"index": int(seg.get("index", i)), "text": str(seg.get("text", ""))[:1000],
         "estimated_duration_seconds": float(seg.get("estimated_duration_seconds", 3.0))}
        for i, seg in enumerate(segments[:60]) if isinstance(seg, dict)
    ]
    return {"api_version": 1, "status": "generated", "segments": safe_segments, "segment_count": len(safe_segments)}


def search_media(request: dict[str, Any], *, engine: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Search media via the Phase 3 engine, fail-closed if absent or unavailable."""
    # The media engine accepts hk-retro/pixel/cosmic only; "mix" falls back to cosmic.
    engine_style = "cosmic" if request["style"] == "mix" else request["style"]
    if engine is None:
        try:
            from muse.media_search import MediaSearchEngine  # type: ignore
            engine = MediaSearchEngine().search_images
        except (ImportError, AttributeError):
            return {
                "api_version": 1, "status": "blocked", "assets": [],
                "reason": "local media search engine is not configured (SearXNG endpoint required)",
            }
    try:
        result = engine(request["query"], engine_style, limit=20)
    except Exception as exc:
        return {"api_version": 1, "status": "blocked", "assets": [], "reason": f"{type(exc).__name__}: {exc}"[:500]}

    def as_dict(asset: Any) -> dict[str, Any]:
        if isinstance(asset, dict):
            return asset
        if hasattr(asset, "__dataclass_fields__"):
            from dataclasses import asdict
            return asdict(asset)
        return {}

    assets = result if isinstance(result, list) else result.get("assets", []) if isinstance(result, dict) else []
    safe = [
        {"url": str(a.get("url", ""))[:2000], "title": str(a.get("title", ""))[:300],
         "license": str(a.get("license", "unknown"))[:80], "source_ref": str(a.get("source_ref", ""))[:500]}
        for a in (as_dict(item) for item in assets[:20])
        if str(a.get("url", "")).startswith(("http://", "https://"))
    ]
    return {"api_version": 1, "status": "found", "assets": safe, "asset_count": len(safe)}


def philosophy_inject(request: dict[str, Any], *, engine: Callable[..., Any] | None = None,
                      philosophy_db: str | Path | None = None) -> dict[str, Any]:
    """Query the Muse consciousness engine for an inspiration brief (hypothesis-labeled)."""
    owns_store = False
    store = None
    if engine is None:
        try:
            from muse.consciousness_engine import ConsciousnessEngine  # type: ignore
            from muse.philosophy import PhilosophyStore  # type: ignore
            default_db = Path(philosophy_db) if philosophy_db is not None else Path("data/philosophy/muse.sqlite3")
            store = PhilosophyStore(default_db)  # type: ignore[assignment]
            owns_store = True
            engine = ConsciousnessEngine(store).inject_inspiration
        except (ImportError, AttributeError, OSError):
            if store is not None:
                store.close()  # type: ignore[union-attr]
            return {
                "api_version": 1, "status": "blocked", "principles": [], "note": "",
                "reason": "local philosophy store is not available",
            }
    try:
        result = engine(request["topic"], symbolic_lenses=())
    except Exception as exc:
        return {"api_version": 1, "status": "blocked", "principles": [], "note": "", "reason": f"{type(exc).__name__}: {exc}"[:500]}
    finally:
        if owns_store and store is not None:
            store.close()  # type: ignore[union-attr]
    if isinstance(result, dict):
        return {"api_version": 1, "status": "injected", **{
            k: result[k] for k in ("principles", "note", "muse_note", "quotes") if k in result
        }}
    return {"api_version": 1, "status": "injected", "brief": str(result)[:2000]}


def submit_render(payload: Any, *, store: CreativeStudioStore | None = None,
                  execute: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Validate, plan and enqueue a render job. Execution is injected and bounded."""
    request = validate_render_request(payload)
    plan = plan_render(request)
    owns_store = store is None
    store = store or CreativeStudioStore(Path("data/studio/render-jobs.sqlite3"))
    try:
        job = store.create_job(
            title=request["title"], style=request["style"], narration_style=request["narration_style"],
            request=request,
        )
        job_id = str(job["id"])
        if execute is not None:
            try:
                outcome = execute(job_id, request)
                job = store.update_job(
                    job_id,
                    status=str(outcome.get("status", "failed")),
                    progress=int(outcome.get("progress", 100)),
                    stage=str(outcome.get("stage", "done")),
                    result=outcome if isinstance(outcome, dict) else {},
                    error=str(outcome.get("error", "")),
                ) or job
            except Exception as exc:
                job = store.update_job(job_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:500]) or job
        return {"api_version": 1, "job": job, "plan": plan}
    finally:
        if owns_store:
            store.close()


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Muse creative studio API helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("styles")
    jobs = sub.add_parser("jobs")
    jobs.add_argument("--db", default="data/studio/render-jobs.sqlite3")
    args = parser.parse_args()
    if args.command == "styles":
        print(json.dumps(list_styles(), ensure_ascii=False, indent=2))
    else:
        with CreativeStudioStore(args.db) as store:
            print(json.dumps({"api_version": 1, "jobs": store.list_jobs()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
