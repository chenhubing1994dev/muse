"""Legacy video pipeline compatibility surface; direct subprocess dispatch is disabled."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from muse.task_queue import TaskQueue


class PipelineError(RuntimeError):
    """The requested pipeline action is invalid or unavailable."""


RECIPES: dict[str, dict[str, str]] = {
    "high_value_short_v2": {
        "label": "研究型高价值竖屏短片（NIST AI 查证法）",
        "script": "scripts/muse-high-value-short.py",
        "output": "data/prometheus/high-value-short-v2",
        "description": "复用已验证的 6 段 Wan 镜头 + 原创配乐 + 中文字幕 + 封面 + 来源笔记，技术 QA 与交付 ZIP。",
    },
    "cantonese_pilot_v2": {
        "label": "粤语旁白试验版（本地 TTS + Whisper ASR）",
        "script": "scripts/muse-high-value-short.py",
        "output": "data/prometheus/high-value-short-cantonese-pilot-v2",
        "description": "本地 zh-HK 合成旁白、Whisper 对稿（CER 门槛 0.10）、按语音时间戳字幕与旁白驱动 BGM ducking；当前中性默认声线，仍须人工听审。",
    },
    "inspiration_viral_9x16_v1": {
        "label": "灵感进化旗舰竖屏（抖音/视频号/Shorts 9:16 1080P）",
        "script": "scripts/muse-inspiration-flagship-video.py",
        "output": "data/prometheus/inspiration-viral-9x16-v1",
        "description": "由灵感深研与自我改进闭环编译 6 幕高留存分镜，Remotion 原生 1080×1920 30fps 动态图表 + 语音解说 + 侧链配乐 + -16 LUFS + 抖音/视频号/Shorts 多平台发布包。",
    },
    "inspiration_viral_16x9_v1": {
        "label": "灵感进化旗舰横屏（YouTube/视频号/B站 16:9 1080P）",
        "script": "scripts/muse-inspiration-flagship-video.py",
        "output": "data/prometheus/inspiration-viral-16x9-v1",
        "description": "由灵感深研与自我改进闭环编译 6 幕双栏 keynote 视觉，Remotion 原生 1920×1080 30fps + 语音解说 + -16 LUFS + YouTube/视频号/B站发布包。",
    },
}


CANTONESE_RECIPE_ID = "cantonese_pilot_v2"
FLAGSHIP_RECIPE_IDS = frozenset({"inspiration_viral_9x16_v1", "inspiration_viral_16x9_v1"})
LEGACY_DISPATCH_DISABLED_REASON = "Legacy video_pipeline dispatch is disabled; use the Commander-approved VideoTaskWorker path."


class PipelineController:
    """Thin API adapter over the authoritative Commander TaskQueue."""

    def __init__(self, workspace: str | Path, *, task_db: str | Path | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.task_db = Path(task_db) if task_db is not None else self.workspace / "data" / "tasks" / "muse.sqlite3"

    def _cantonese_unavailable_reason(self) -> str | None:
        target = self.workspace / ".muse-canto-tts-py314"
        tts_model = self.workspace / "data" / "models" / "canto-tts-nano"
        asr_model = self.workspace / "models" / "whisper-small-cantonese" / "cts" / "model.bin"
        required_packages = ("canto_tts", "faster_whisper", "ctranslate2", "onnxruntime")
        missing = [name for name in required_packages if not (target / name).is_dir()]
        if missing:
            return "本地 Cantonese TTS/ASR 运行依赖未齐备：" + ", ".join(missing)
        if not (tts_model / "browser_poc_manifest.json").is_file():
            return "固定版本 Cantonese TTS ONNX 模型尚未就绪"
        if not asr_model.is_file() or asr_model.stat().st_size != 967_072_097:
            return "固定版本 Cantonese Whisper ASR 模型缺失或大小不符"
        return None

    def _flagship_unavailable_reason(self, recipe_id: str) -> str | None:
        script = self.workspace / RECIPES[recipe_id]["script"]
        if not script.is_file():
            return "旗舰灵感视频脚本尚未就绪"
        cli = self.workspace / "video-motion" / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
        if not cli.is_file():
            return "本地 Remotion 渲染引擎尚未安装"
        return None

    def _recipe_unavailable_reason(self, recipe_id: str) -> str | None:
        if recipe_id == CANTONESE_RECIPE_ID:
            return self._cantonese_unavailable_reason()
        if recipe_id in FLAGSHIP_RECIPE_IDS:
            return self._flagship_unavailable_reason(recipe_id)
        return None

    def recipes(self) -> list[dict[str, Any]]:
        items = []
        for key, value in RECIPES.items():
            reason = self._recipe_unavailable_reason(key) or LEGACY_DISPATCH_DISABLED_REASON
            items.append({"id": key, "available": False, **value, "reason": reason})
        return items
    @staticmethod
    def _public_job(task: dict[str, Any]) -> dict[str, Any] | None:
        payload = task.get("payload")
        if not isinstance(payload, dict) or payload.get("kind") != "video_pipeline":
            return None
        recipe_id = payload.get("recipe_id")
        recipe = RECIPES.get(recipe_id)
        if recipe is None:
            return None
        status = task.get("status", "unknown")
        result = payload.get("_result")
        return {
            "job_id": task["id"],
            "recipe_id": recipe_id,
            "recipe_label": recipe["label"],
            "title": payload.get("title") or recipe["label"],
            "status": status,
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "output_dir": payload.get("output_dir") or recipe["output"],
            "progress": {
                "queued": "旧版渲染路径已停用；请使用 Commander VideoTaskWorker",
                "running": "旧版渲染路径已停用；不会由此入口继续渲染",
                "waiting_approval": "旧版渲染路径已停用；审批不会授权此路径执行",
                "succeeded": "历史交付包已生成并通过脚本校验",
                "failed": "历史流水线失败，可查看任务账本",
                "cancelled": "历史任务已取消",
            }.get(status, status),
            "result": result,
            "error": (
                str(
                    result.get("message")
                    or result.get("error")
                    or result.get("error_code")
                    or "流水线执行失败"
                )
                if status == "failed" and isinstance(result, dict)
                else None
            ),
        }
    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with TaskQueue(self.task_db) as queue:
            task = queue.get(job_id)
        return self._public_job(task) if task else None

    def list_jobs(self, *, limit: int = 30) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise PipelineError("limit must be between 1 and 100")
        with TaskQueue(self.task_db) as queue:
            tasks = queue.list_tasks(kind="video_pipeline", limit=limit)
        return [job for task in tasks if (job := self._public_job(task)) is not None]

    def submit(
        self,
        recipe_id: str,
        *,
        title: str | None = None,
        inspiration_id: str | None = None,
        blueprint_path: str | None = None,
    ) -> dict[str, Any]:
        recipe = RECIPES.get(recipe_id)
        if recipe is None:
            raise PipelineError("unsupported pipeline recipe")
        unavailable_reason = self._recipe_unavailable_reason(recipe_id)
        if unavailable_reason:
            raise PipelineError(unavailable_reason)
        script = (self.workspace / recipe["script"]).resolve()
        if self.workspace not in script.parents or not script.is_file():
            raise PipelineError("pipeline script is unavailable or outside workspace")
        raise PipelineError(LEGACY_DISPATCH_DISABLED_REASON)

class PipelineTaskHandler:
    """Fail-closed compatibility tombstone; never runs a render subprocess."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise PipelineError(LEGACY_DISPATCH_DISABLED_REASON)