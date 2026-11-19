"""Commander-queued, allowlisted short-video pipeline tasks.

This module defines recipes and an execution handler only. TaskQueue owns job
state, and the existing Muse Worker/Commander owns scheduling and recovery.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
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
            reason = self._recipe_unavailable_reason(key)
            items.append({"id": key, "available": reason is None, **value,
                          **({"reason": reason} if reason else {})})
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
        return {
            "job_id": task["id"], "recipe_id": recipe_id,
            "recipe_label": recipe["label"], "title": payload.get("title") or recipe["label"],
            "status": status, "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "output_dir": payload.get("output_dir") or recipe["output"],
            "progress": {"queued": "等待 Muse Worker 执行", "running": "正在本地渲染（FFmpeg / 已验证镜头）…",
                         "waiting_approval": "等待人工审批/恢复审查", "succeeded": "交付包已生成并通过脚本校验",
                         "failed": "流水线失败，可查看任务账本", "cancelled": "任务已取消"}.get(status, status),
            "result": payload.get("_result"),
            "error": (
                str((payload.get("_result") or {}).get("message")
                    or (payload.get("_result") or {}).get("error")
                    or (payload.get("_result") or {}).get("error_code")
                    or "流水线执行失败")
                if status == "failed" and isinstance(payload.get("_result"), dict) else None
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
        run_id = uuid.uuid4().hex
        output_relative = Path("data/prometheus/runs") / recipe_id / run_id
        output = (self.workspace / output_relative).resolve()
        if self.workspace not in output.parents or output.exists():
            raise PipelineError("unique pipeline output path is unsafe or already exists")
        task_payload: dict[str, Any] = {
            "kind": "video_pipeline",
            "recipe_id": recipe_id,
            "title": (title or recipe["label"])[:200],
            "output_dir": output_relative.as_posix(),
        }
        if isinstance(inspiration_id, str) and inspiration_id.strip():
            task_payload["inspiration_id"] = inspiration_id.strip()[:64]
        if isinstance(blueprint_path, str) and blueprint_path.strip():
            task_payload["blueprint_path"] = blueprint_path.strip()[:300]
        try:
            with TaskQueue(self.task_db) as queue:
                task = queue.enqueue(
                    f"Video pipeline: {(title or recipe['label'])[:200]}",
                    task_payload,
                    workload_class="media", estimated_seconds=1800,
                    recovery_policy="manual", priority=60,
                )
        except (OSError, ValueError) as exc:
            raise PipelineError(str(exc)) from exc
        job = self._public_job(task)
        assert job is not None
        return job


class PipelineTaskHandler:
    """Trusted Worker handler for the finite video recipe allowlist."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("kind") != "video_pipeline":
            raise PipelineError("invalid video pipeline task payload")
        if set(payload) - {"kind", "recipe_id", "title", "output_dir", "inspiration_id", "blueprint_path"}:
            raise PipelineError("video pipeline task contains unsupported fields")
        recipe_id = payload.get("recipe_id")
        recipe = RECIPES.get(recipe_id)
        if recipe is None:
            raise PipelineError("unsupported pipeline recipe")
        unavailable_reason = PipelineController(self.workspace)._recipe_unavailable_reason(recipe_id)
        if unavailable_reason:
            raise PipelineError(unavailable_reason)
        script = (self.workspace / recipe["script"]).resolve()
        output_value = payload.get("output_dir", recipe["output"])
        if not isinstance(output_value, str) or not output_value.strip():
            raise PipelineError("pipeline output_dir must be a non-empty relative path")
        output_relative = Path(output_value)
        if output_relative.is_absolute() or ".." in output_relative.parts:
            raise PipelineError("pipeline output_dir must be a safe relative path")
        output = (self.workspace / output_relative).resolve()
        if self.workspace not in script.parents or not script.is_file():
            raise PipelineError("pipeline script is unavailable or outside workspace")
        if self.workspace not in output.parents or output.exists():
            raise PipelineError("pipeline output is unsafe or already exists")
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["MUSE_PIPELINE_OUTPUT_DIR"] = str(output)
        env["MUSE_PIPELINE_TITLE"] = str(payload.get("title") or recipe["label"])[:160]
        env["MUSE_PIPELINE_RECIPE_ID"] = recipe_id
        if isinstance(payload.get("inspiration_id"), str) and payload["inspiration_id"].strip():
            env["MUSE_PIPELINE_INSPIRATION_ID"] = payload["inspiration_id"].strip()
        if isinstance(payload.get("blueprint_path"), str) and payload["blueprint_path"].strip():
            env["MUSE_PIPELINE_BLUEPRINT"] = payload["blueprint_path"].strip()
        if recipe_id == "inspiration_viral_16x9_v1":
            env["MUSE_PIPELINE_ASPECT_RATIO"] = "16:9"
        elif recipe_id == "inspiration_viral_9x16_v1":
            env["MUSE_PIPELINE_ASPECT_RATIO"] = "9:16"
        python_paths = [str(self.workspace / "src")]
        tts_target = self.workspace / ".muse-canto-tts-py314"
        if tts_target.is_dir():
            python_paths.append(str(tts_target))
        if env.get("PYTHONPATH"):
            python_paths.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_paths)
        try:
            proc = subprocess.run(
                [sys.executable or "python", str(script)], cwd=self.workspace,
                env=env, capture_output=True, text=True, encoding="utf-8",
                timeout=1800, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PipelineError("video pipeline exceeded its 30-minute execution budget") from exc
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        try:
            result = json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            raise PipelineError("video pipeline returned invalid JSON") from exc
        if proc.returncode != 0 or not isinstance(result, dict) or result.get("status") != "completed":
            message = result.get("message") or result.get("error") if isinstance(result, dict) else ""
            raise PipelineError(str(message or stderr or f"pipeline exited with code {proc.returncode}")[:2000])
        for artifact_key in ("mp4", "bundle"):
            artifact = result.get(artifact_key)
            relative = artifact.get("path") if isinstance(artifact, dict) else None
            if not isinstance(relative, str) or not relative.strip():
                raise PipelineError(f"completed pipeline result is missing {artifact_key} artifact path")
            artifact_path = (self.workspace / relative).resolve()
            if output not in artifact_path.parents or not artifact_path.is_file() or artifact_path.stat().st_size <= 0:
                raise PipelineError(f"completed pipeline {artifact_key} artifact is missing, empty, or outside recipe output")
        return result
