"""Bounded executor for durable Creator Initiative video attempts.

The worker claims at most one task per invocation. Wan generation obtains its own
cross-process GPU lease in wan_video; this module never starts or stops services.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
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
            attempt = store.claim_next_video_attempt(self.worker_id, lease_seconds=3600)
            if attempt is None:
                return {"status": "idle", "claimed": False}
            attempt_id = attempt["attempt_id"]
            if self.pause_check():
                store.release_video_attempt(attempt_id, worker_id=self.worker_id, reason="runtime_paused")
                return {"status": "paused", "claimed": False, "attempt_id": attempt_id}
            try:
                if attempt["mode"] != "prompt_to_video":                    raise ValueError("video worker currently supports prompt_to_video attempts only")
                initiative = next((item for item in store.list(limit=200) if item["id"] == attempt["initiative_id"]), None)
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
                if aspect_ratio not in {"16:9", "9:16", "1:1"}:
                    raise ValueError("unsupported video aspect ratio in creator brief")
                if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 1 <= duration <= 5:
                    raise ValueError("creator brief duration must be between 1 and 5 seconds")
                if isinstance(steps, bool) or not isinstance(steps, int) or not 8 <= steps <= 30:
                    raise ValueError("creator brief steps must be between 8 and 30")
                seed = int(hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:8], 16)
                result = self._builder().build_from_prompt(
                    title.strip(), prompt.strip(),
                    negative_prompt=str(video.get("negative_prompt", ""))[:1000],
                    aspect_ratio=aspect_ratio, duration_seconds=float(duration), steps=steps,
                    seed=seed, timeout_seconds=1800, motion_profile="none",
                    creator_initiative_id=attempt["initiative_id"],
                    evolution_cycle_id=attempt["evolution_cycle_id"] or None,
                )
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
