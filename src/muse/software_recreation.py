"""Plan-only software study/recreation workflow.

This module creates a bounded, authorization-aware plan. It does not dispatch
tasks, launch tools, inspect targets, write project files, or mark products ready.
Muse Commander remains the only scheduler for any later execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import parse_qsl, urlsplit


TARGET_KINDS = frozenset({
    "website", "desktop_app", "mobile_app", "game", "source_project",
    "binary", "file", "database", "firmware",
})
MODES = frozenset({"clean_room", "deep"})


@dataclass(frozen=True)
class RecreationRequest:
    target: str
    target_kind: str
    authorization_confirmed: bool = False
    mode: str = "clean_room"
    authorization_scope: str | None = None
    desired_outcome: str = "Implement an independently built equivalent of the requested functionality."
    max_refine_rounds: int = 3


@dataclass(frozen=True)
class RecreationStep:
    step_id: str
    title: str
    owner: str
    depends_on: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    requires_approval: bool = False
    max_attempts: int = 1


@dataclass(frozen=True)
class SoftwareRecreationPlan:
    plan_id: str
    idempotency_key: str
    target: str
    target_kind: str
    authorization_confirmed: bool
    mode: str
    authorization_scope: str | None
    desired_outcome: str
    max_refine_rounds: int
    dispatcher: str
    execution_started: bool
    steps: tuple[RecreationStep, ...]
    guardrails: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "idempotency_key": self.idempotency_key,
            "target": self.target,
            "target_kind": self.target_kind,
            "authorization_confirmed": self.authorization_confirmed,
            "mode": self.mode,
            "authorization_scope": self.authorization_scope,
            "desired_outcome": self.desired_outcome,
            "max_refine_rounds": self.max_refine_rounds,
            "dispatcher": self.dispatcher,
            "execution_started": self.execution_started,
            "steps": [
                {
                    "step_id": step.step_id,
                    "title": step.title,
                    "owner": step.owner,
                    "depends_on": list(step.depends_on),
                    "required_capabilities": list(step.required_capabilities),
                    "requires_approval": step.requires_approval,
                    "max_attempts": step.max_attempts,
                }
                for step in self.steps
            ],
            "guardrails": list(self.guardrails),
        }


def _validate_url(target: str) -> None:
    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("web/repository targets must be absolute HTTP(S) URLs")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("target URLs must not contain embedded credentials")
    sensitive_keys = {"token", "access_token", "api_key", "apikey", "password", "secret", "session"}
    if any(key.casefold() in sensitive_keys for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise ValueError("target URL query appears to contain credentials; remove them before planning")


def _validate_local_path(target: str) -> None:
    win = PureWindowsPath(target)
    posix = PurePosixPath(target)
    if not (win.is_absolute() or posix.is_absolute()):
        raise ValueError("local targets must use an absolute path")
    if ".." in win.parts or ".." in posix.parts:
        raise ValueError("local target paths must not contain parent traversal segments")


def _validate_request(request: RecreationRequest) -> None:
    if not isinstance(request, RecreationRequest):
        raise TypeError("request must be a RecreationRequest")
    if not isinstance(request.target, str) or not request.target.strip() or len(request.target) > 4096:
        raise ValueError("target must contain 1-4096 characters")
    if any(ord(char) < 32 for char in request.target):
        raise ValueError("target must not contain control characters")
    if not isinstance(request.target_kind, str) or request.target_kind not in TARGET_KINDS:
        raise ValueError("unsupported target_kind")
    if not isinstance(request.mode, str) or request.mode not in MODES:
        raise ValueError("mode must be clean_room or deep")
    if request.authorization_confirmed is not True:
        raise PermissionError("the target must be owned by the user or explicitly authorized for examination")
    if not isinstance(request.desired_outcome, str) or not request.desired_outcome.strip() or len(request.desired_outcome) > 1000:
        raise ValueError("desired_outcome must contain 1-1000 characters")
    if isinstance(request.max_refine_rounds, bool) or not isinstance(request.max_refine_rounds, int) or not 1 <= request.max_refine_rounds <= 5:
        raise ValueError("max_refine_rounds must be between 1 and 5")
    if request.mode == "deep":
        scope = request.authorization_scope
        if not isinstance(scope, str) or not 3 <= len(scope.strip()) <= 500:
            raise PermissionError("deep mode requires an explicit authorization_scope of 3-500 characters")

    target = request.target.strip()
    if request.target_kind == "website":
        _validate_url(target)
    elif request.target_kind == "source_project" and target.startswith(("https://", "http://")):
        _validate_url(target)
    else:
        _validate_local_path(target)


def build_recreation_plan(request: RecreationRequest) -> SoftwareRecreationPlan:
    """Return a deterministic plan without starting any execution."""
    _validate_request(request)
    target = request.target.strip()
    scope = request.authorization_scope.strip() if isinstance(request.authorization_scope, str) else None
    canonical = {
        "target": target,
        "target_kind": request.target_kind,
        "authorization_confirmed": request.authorization_confirmed,
        "mode": request.mode,
        "authorization_scope": scope,
        "desired_outcome": request.desired_outcome.strip(),
        "max_refine_rounds": request.max_refine_rounds,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    if request.mode == "deep":
        study_capabilities = ("rea.deep_analysis", "craft_anything.deep_study")
        study_approval = True
    else:
        study_capabilities = ("craft_anything.clean_room_study", "rea.passive_visible_observation")
        study_approval = False

    steps = (
        RecreationStep("study", "Inspect target and record observations", "muse_commander", (), study_capabilities, study_approval),
        RecreationStep("evidence", "Normalize evidence, provenance, unknowns and counter-evidence", "muse_research_knowledge", ("study",), ("research.evidence_normalization",)),
        RecreationStep("specify", "Produce feature map, requirements and acceptance tests", "muse_commander", ("evidence",), ("commander.plan",)),
        RecreationStep("build", "Implement an independent equivalent", "muse_commander", ("specify",), ("agent.code_change",), True),
        RecreationStep("compare", "Compare implementation with observed requirements", "muse_commander", ("build",), ("craft_anything.check", "comparison.evidence")),
        RecreationStep("refine", "Resolve supported gaps within a bounded iteration budget", "muse_commander", ("compare",), ("agent.code_change",), True, request.max_refine_rounds),
        RecreationStep("verify", "Run functional, regression and artifact-integrity tests", "muse_test_runner", ("refine",), ("tests.execute", "artifacts.verify")),
        RecreationStep("review", "Obtain independent review and rights/scope review", "muse_review_gate", ("verify",), ("review.independent", "rights.review")),
        RecreationStep("package", "Package a versioned deliverable for user acceptance", "muse_product_builder", ("review",), ("product.package",)),
    )
    guardrails = (
        "authorized_targets_only", "clean_room_is_default", "deep_mode_requires_explicit_scope",
        "no_drm_login_or_anti_cheat_bypass", "no_unlicensed_code_text_branding_or_media_copy",
        "no_unreviewed_dependency_install_or_arbitrary_shell", "muse_commander_is_the_only_scheduler",
        "bounded_refinement", "independent_review_before_ready", "human_acceptance_before_release",
        "unknowns_and_limitations_remain_visible",
    )
    return SoftwareRecreationPlan(
        plan_id="recreate_" + digest[:24],
        idempotency_key=digest,
        target=target,
        target_kind=request.target_kind,
        authorization_confirmed=request.authorization_confirmed,
        mode=request.mode,
        authorization_scope=scope,
        desired_outcome=request.desired_outcome.strip(),
        max_refine_rounds=request.max_refine_rounds,
        dispatcher="muse_commander_only",
        execution_started=False,
        steps=steps,
        guardrails=guardrails,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a safe, plan-only Muse software study/recreation workflow.")
    parser.add_argument("target")
    parser.add_argument("--kind", required=True, choices=sorted(TARGET_KINDS))
    parser.add_argument("--mode", choices=sorted(MODES), default="clean_room")
    parser.add_argument("--authorized", action="store_true", help="confirm that you own or may examine this target")
    parser.add_argument("--authorization-scope", help="required in deep mode; describe the permitted examination scope")
    parser.add_argument("--goal", default="Implement an independently built equivalent of the requested functionality.")
    parser.add_argument("--max-refine-rounds", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        plan = build_recreation_plan(RecreationRequest(
            target=args.target, target_kind=args.kind, authorization_confirmed=args.authorized,
            mode=args.mode, authorization_scope=args.authorization_scope,
            desired_outcome=args.goal, max_refine_rounds=args.max_refine_rounds,
        ))
    except (TypeError, ValueError, PermissionError) as exc:
        parser.exit(2, "Muse recreation plan rejected: " + str(exc) + "\n")
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
