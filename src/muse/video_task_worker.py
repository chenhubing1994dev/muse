"""Bounded executor for durable Creator Initiative video attempts.

The worker claims at most one task per invocation. Wan generation obtains its own
cross-process GPU lease in wan_video; this module never starts or stops services.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from muse.initiative_engine import InitiativeStore


def _runtime_is_paused(workspace: Path) -> bool:
    from muse.autonomous_runtime import RuntimeStateStore
    with RuntimeStateStore(workspace / "data" / "autonomy" / "runtime.sqlite3") as state:
        return state.status().get("state") in {"paused", "stopping"}


class VideoTaskWorker:
    def __init__(
        self, workspace: str | Path, *, builder=None, worker_id: str | None = None,
        pause_check: Callable[[], bool] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.worker_id = worker_id or ("video-" + uuid.uuid4().hex[:16])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.worker_id):
            raise ValueError("worker_id must be 1-64 safe identifier characters")
        self.builder = builder
        self.pause_check = pause_check or (lambda: False)
        self.database = self.workspace / "data" / "initiatives" / "muse.sqlite3"
        self.task_database = self.workspace / "data" / "tasks" / "muse.sqlite3"

    @staticmethod
    def _commander_approval_is_valid(store, queue, attempt) -> bool:
        """Require a live, persisted human approval bound to this exact brief and attempt."""
        task_id = attempt.get("approval_task_id")
        initiative_id = attempt.get("initiative_id")
        if not isinstance(task_id, str) or not task_id or not isinstance(initiative_id, str):
            return False
        initiative = store.get_by_id(initiative_id)
        if initiative is None or initiative.get("status") in {"rejected", "parked"}:
            return False
        try:
            concept = json.loads(initiative["concept"])
        except (TypeError, json.JSONDecodeError):
            return False
        brief = concept.get("creator_brief", {}).get("video") if isinstance(concept, dict) else None
        if not isinstance(brief, dict):
            return False
        digest = hashlib.sha256(json.dumps(brief, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        ticket = queue.get(task_id)
        if ticket is None or ticket.get("status") != "queued":
            return False
        payload = ticket.get("payload", {})
        if not isinstance(payload, dict):
            return False
        approval = payload.get("_approval", {})
        approved_by = approval.get("approved_by") if isinstance(approval, dict) else None
        if (payload.get("kind") != "business_opportunity_video_approval"
                or payload.get("initiative_id") != initiative_id
                or payload.get("attempt_id") != attempt.get("attempt_id")
                or payload.get("approval_scope") != "one_video_attempt_only"
                or payload.get("_approved") is not True
                or payload.get("video_brief_sha256") != digest
                or not isinstance(payload.get("video_brief"), dict)
                or hashlib.sha256(json.dumps(payload["video_brief"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != digest
                or not isinstance(approved_by, str) or not approved_by.strip()):
            return False
        return any(
            event.get("event_type") == "human.decision"
            and event.get("actor") == approved_by
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("decision") == "approved"
            for event in queue.events(task_id, limit=500)
        )

    def _builder(self):
        if self.builder is None:
            from muse.video_product_builder import VideoProductBuilder
            self.builder = VideoProductBuilder(self.workspace)
        return self.builder

    def run_once(self) -> dict[str, Any]:
        """Run one queued prompt-to-video task; never loops or auto-publishes."""
        if self.pause_check():
            return {"status": "paused", "claimed": False}
        with InitiativeStore(self.database) as store:
            candidates = store.list_queued_approval_video_attempts(limit=200)
            if not candidates or self.task_database.is_symlink() or not self.task_database.is_file():
                return {"status": "idle", "claimed": False, "approval_required": True}
            from muse.task_queue import TaskQueue
            attempt = None
            blocked = 0
            with TaskQueue(self.task_database) as approvals:
                for candidate in candidates:
                    try:
                        valid = self._commander_approval_is_valid(store, approvals, candidate)
                    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError):
                        valid = False
                    if not valid:
                        blocked += 1
                        continue
                    attempt = store.claim_next_video_attempt(
                        self.worker_id, lease_seconds=3600, attempt_id=candidate["attempt_id"],
                    )
                    if attempt is not None:
                        break
            if attempt is None:
                return {"status": "idle", "claimed": False, "approval_required": True, "approval_blocked": blocked}
            attempt_id = attempt["attempt_id"]
            if self.pause_check():
                store.release_video_attempt(attempt_id, worker_id=self.worker_id, reason="runtime_paused")
                return {"status": "paused", "claimed": False, "attempt_id": attempt_id}
            try:
                with TaskQueue(self.task_database) as approvals:
                    if not self._commander_approval_is_valid(store, approvals, attempt):
                        store.release_video_attempt(attempt_id, worker_id=self.worker_id, reason="commander_approval_invalidated")
                        return {"status": "blocked_approval", "claimed": False, "attempt_id": attempt_id}
                if attempt["mode"] != "prompt_to_video":
                    raise ValueError("video worker currently supports prompt_to_video attempts only")
                initiative = store.get_by_id(attempt["initiative_id"])
                if initiative is None:
                    raise KeyError("video attempt initiative is missing")
                concept = json.loads(initiative["concept"])
                brief = concept.get("creator_brief") if isinstance(concept, dict) else None
                video = brief.get("video") if isinstance(brief, dict) else None
                if not isinstance(video, dict):
                    raise ValueError("initiative has no valid creator_brief.video")
                title = video.get("title")
                prompt = video.get("prompt")
                if not isinstance(title, str) or not title.strip() or not isinstance(prompt, str) or not prompt.strip():
                    raise ValueError("video brief requires a non-empty title and prompt")
                aspect_ratio = video.get("aspect_ratio", "9:16")
                duration = video.get("duration_seconds", 2.0)
                steps = video.get("steps", 8)
                scenes = video.get("scenes")
                if aspect_ratio not in {"16:9", "9:16", "1:1"}:
                    raise ValueError("unsupported video aspect ratio in creator brief")
                if scenes is None:
                    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 1 <= duration <= 5:
                        raise ValueError("creator brief duration must be between 1 and 5 seconds")
                elif not isinstance(scenes, list) or not 2 <= len(scenes) <= 20:
                    raise ValueError("creator brief scenes must contain between 2 and 20 scene specifications")
                if isinstance(steps, bool) or not isinstance(steps, int) or not 8 <= steps <= 30:
                    raise ValueError("creator brief steps must be between 8 and 30")
                normalized_scenes = None
                if scenes is not None:
                    normalized_scenes = []
                    total_scene_duration = 0.0
                    for index, scene in enumerate(scenes, start=1):
                        if not isinstance(scene, dict) or set(scene) - {"prompt", "duration_seconds", "seed"}:
                            raise ValueError(f"scene {index} contains unsupported fields")
                        scene_prompt = scene.get("prompt")
                        scene_duration = scene.get("duration_seconds")
                        scene_seed = scene.get("seed")
                        if not isinstance(scene_prompt, str) or not scene_prompt.strip() or len(scene_prompt.strip()) > 2000:
                            raise ValueError(f"scene {index} prompt must contain 1-2000 characters")
                        if (isinstance(scene_duration, bool) or not isinstance(scene_duration, (int, float))
                                or not 1 <= scene_duration <= 5):
                            raise ValueError(f"scene {index} duration must be between 1 and 5 seconds")
                        if scene_seed is None:
                            scene_seed = int(hashlib.sha256(
                                f"{attempt_id}:scene:{index}".encode("utf-8")
                            ).hexdigest()[:8], 16)
                        if isinstance(scene_seed, bool) or not isinstance(scene_seed, int) or not 0 <= scene_seed <= 2**32 - 1:
                            raise ValueError(f"scene {index} seed must be an unsigned 32-bit integer")
                        total_scene_duration += float(scene_duration)
                        normalized_scenes.append({
                            "prompt": scene_prompt.strip(),
                            "duration_seconds": float(scene_duration),
                            "seed": scene_seed,
                        })
                    if total_scene_duration > 60:
                        raise ValueError("creator brief scene duration must not exceed 60 seconds")
                narration_script = None
                narration_model_dir = None
                narration_asr_model_dir = None
                pending_evidence_contract = None
                narration = video.get("narration")
                if isinstance(narration, dict) and narration.get("enabled") is True:
                    if narration.get("language", "zh-HK") != "zh-HK":
                        raise ValueError("video narration currently supports zh-HK only")
                    style = narration.get("style", "yujie_canto")
                    segments = narration.get("segments", 3)
                    if isinstance(segments, bool) or not isinstance(segments, int) or not 3 <= segments <= 16:
                        raise ValueError("Cantonese narration segments must be between 3 and 16")
                    tts_packages = self.workspace / ".muse-canto-tts-py314"
                    tts_model = self.workspace / "data" / "models" / "canto-tts-nano"
                    if (tts_packages.is_symlink() or not (tts_packages / "canto_tts").is_dir()
                            or (tts_packages / "canto_tts").is_symlink()
                            or tts_model.is_symlink() or not (tts_model / "browser_poc_manifest.json").is_file()):
                        raise RuntimeError("Cantonese narration was requested but its pinned local TTS runtime/model is unavailable; video generation was not started")
                    from muse.cantonese_tts import preflight_cantonese_tts_runtime
                    preflight_cantonese_tts_runtime(tts_packages, tts_model)
                    from muse.narration_generator import NarrationGenerator
                    research_text = narration.get("research_text") or prompt.strip()
                    inspiration = narration.get("inspiration") or "以清晰、克制、可信的香港粤语介绍，不把估算说成已验证成果"
                    claim_grounded = narration.get("claim_grounded") is True
                    generator_options = {}
                    if claim_grounded:
                        claim_mapping = narration.get("claim_ids_by_segment")
                        if not isinstance(claim_mapping, list):
                            raise ValueError("claim-grounded narration requires claim_ids_by_segment")
                        factual_mapping = narration.get("factual_by_segment", [True] * segments)
                        generator_options = {
                            "claim_ids_by_segment": claim_mapping,
                            "factual_by_segment": factual_mapping,
                        }
                    generated_script = NarrationGenerator().generate(
                        title.strip(), research_text, inspiration, style, segments=segments,
                        **generator_options,
                    )
                    narration_script = "\n".join(segment["text"] for segment in generated_script["segments"])
                    if claim_grounded:
                        knowledge_db = self.workspace / "data" / "research" / "knowledge.sqlite3"
                        if not knowledge_db.is_file():
                            raise RuntimeError("claim-grounded narration requires the existing ResearchKnowledgeStore database")
                        from muse.narration_evidence_pipeline import persist_claim_grounded_narration_run
                        from muse.narration_sync import NarrationSyncStore
                        from muse.research_knowledge import ResearchKnowledgeStore
                        with ResearchKnowledgeStore(knowledge_db) as research_store:
                            with NarrationSyncStore(
                                self.workspace / "data" / "narration" / "narration.sqlite3"
                            ) as narration_store:
                                persisted = persist_claim_grounded_narration_run(
                                    generated_script, research_store, narration_store,
                                )
                        evidence_contract = persisted["evidence_contract"]
                        if evidence_contract.get("publishable") is not True:
                            raise RuntimeError(
                                "claim-grounded narration blocked before GPU generation: "
                                + ",".join(sorted({
                                    reason
                                    for segment in evidence_contract["segments"]
                                    for reason in segment["blocking_reasons"]
                                }))
                            )
                        pending_evidence_contract = evidence_contract
                    narration_model_dir = tts_model
                    if narration.get("asr_enabled") is True:
                        if sys.version_info[:2] != (3, 14):
                            raise RuntimeError("Cantonese ASR currently requires the Python 3.14 main runtime; video generation was not started")
                        package_path = str(tts_packages.resolve())
                        if package_path not in sys.path:
                            sys.path.insert(0, package_path)
                        asr_model = self.workspace / "models" / "whisper-small-cantonese" / "cts"
                        if asr_model.is_symlink() or not (asr_model / "model.bin").is_file():
                            raise RuntimeError("Cantonese narration ASR was requested but the pinned local model is unavailable")
                        narration_asr_model_dir = asr_model
                elif narration is not None and not isinstance(narration, dict):
                    raise ValueError("creator_brief.video.narration must be an object")
                build_options = {
                    "negative_prompt": str(video.get("negative_prompt", ""))[:1000],
                    "aspect_ratio": aspect_ratio, "steps": steps,
                    "timeout_seconds": 1800, "motion_profile": "none",
                    "creator_initiative_id": attempt["initiative_id"],
                    "evolution_cycle_id": attempt["evolution_cycle_id"] or None,
                }
                if narration_script is not None:
                    build_options.update({
                        "narration_script": narration_script,
                        "narration_model_dir": narration_model_dir,
                        "narration_asr_model_dir": narration_asr_model_dir,
                    })
                    if narration.get("claim_grounded") is True:
                        build_options["narration_evidence_contract"] = pending_evidence_contract
                if normalized_scenes is not None:
                    build_options["scenes"] = normalized_scenes
                    result = self._builder().build_from_scenes(title.strip(), **build_options)
                else:
                    build_options.update({
                        "duration_seconds": float(duration),
                        "seed": int(hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:8], 16),
                    })
                    result = self._builder().build_from_prompt(title.strip(), prompt.strip(), **build_options)
                if result.get("validated") is not True:
                    raise RuntimeError("video product builder did not verify its output package")
                required = ("initiative_id", "version_id", "mp4_sha256", "package_sha256")
                if any(not isinstance(result.get(key), str) or not result[key] for key in required):
                    raise RuntimeError("video product builder returned incomplete artifact identity")
                completed = store.complete_video_attempt(
                    attempt_id, product_id=result["initiative_id"], version_id=result["version_id"],
                    mp4_sha256=result["mp4_sha256"], package_sha256=result["package_sha256"],
                )
                return {
                    "status": completed["status"], "attempt_id": attempt_id,
                    "product_id": completed["product_id"], "version_id": completed["version_id"],
                    "quality_gate": result.get("quality_gate", {"status": "not_reported"}),
                    "content_review_required": True, "auto_publish": False,
                    "narration_status": (
                        result.get("narration_synthesis", {}).get("status", "not_requested")
                        if isinstance(result.get("narration_synthesis"), dict) else "not_requested"
                    ),
                }
            except Exception as exc:
                failed = store.fail_video_attempt(
                    attempt_id, error_type=type(exc).__name__, failure_code="video.worker_execution_failed",
                )
                return {
                    "status": failed["status"], "attempt_id": attempt_id,
                    "error_type": type(exc).__name__, "failure_code": failed["failure_code"],
                }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded Muse video task or explicitly retry a failed attempt")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--retry-attempt", default=None)
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args(argv)
    workspace = Path(args.workspace).resolve()
    try:
        database = workspace / "data" / "initiatives" / "muse.sqlite3"
        if args.retry_attempt:
            with InitiativeStore(database) as store:
                result = store.retry_failed_video_attempt(args.retry_attempt, max_attempts=args.max_attempts)
            print(json.dumps({"status": result["status"], "attempt_id": result["attempt_id"]}, ensure_ascii=False))
        else:
            result = VideoTaskWorker(
                workspace, worker_id=args.worker_id, pause_check=lambda: _runtime_is_paused(workspace),
            ).run_once()
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
