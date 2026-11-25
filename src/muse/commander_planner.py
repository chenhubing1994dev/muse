"""Validated Commander DAG planning with a deterministic safe fallback."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Iterable
import uuid


@dataclass(frozen=True)
class PlannedStep:
    step_id: str
    role: str
    objective: str
    depends_on: tuple[str, ...]
    required_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class CommanderPlan:
    plan_id: str
    goal: str
    steps: tuple[PlannedStep, ...]
    source: str
    parallel_waves: tuple[tuple[str, ...], ...]

    def to_snapshot(self) -> dict[str, Any]:
        """Serialize this validated plan into the canonical persisted DAG schema."""
        return {
            "schema": "muse-commander-plan-snapshot-v1",
            "plan_id": self.plan_id,
            "goal": self.goal,
            "source": self.source,
            "steps": [{
                "step_id": step.step_id,
                "role": step.role,
                "objective": step.objective,
                "depends_on": list(step.depends_on),
                "required_capabilities": list(step.required_capabilities),
            } for step in self.steps],
            "parallel_waves": [list(wave) for wave in self.parallel_waves],
        }


class CommanderPlanner:
    """Use an injected planner when available; validate every DAG before dispatch."""

    ALLOWED_ROLES = frozenset({"researcher", "architect", "developer", "reviewer", "operator", "writer", "analyst"})

    def __init__(self, planner: Callable[[str], Any] | None = None, *, max_steps: int = 16, strict_model: bool = False) -> None:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 2 <= max_steps <= 32:
            raise ValueError("max_steps must be between 2 and 32")
        if not isinstance(strict_model, bool):
            raise ValueError("strict_model must be a boolean")
        self.planner = planner
        self.max_steps = max_steps
        self.strict_model = strict_model

    @staticmethod
    def _fallback(goal: str) -> list[dict[str, Any]]:
        text = goal.casefold()
        read_only = any(marker in text for marker in ("read-only", "read only", "只读"))
        research_only = any(marker in text for marker in ("research", "研究", "学习资料", "研究资料"))
        if read_only and research_only:
            return [
                {"id": "research", "role": "researcher", "objective": "基于已提供的真实资料形成有来源依据的研究结论", "depends_on": [], "required_capabilities": ["research"]},
                {"id": "independent_review", "role": "reviewer", "objective": "独立核查研究结论与证据，不支持的主张必须指出", "depends_on": ["research"], "required_capabilities": ["review"]},
            ]
        return [
            {"id": "research", "role": "researcher", "objective": "搜集资料、现状与约束", "depends_on": [], "required_capabilities": ["research"]},
            {"id": "architecture", "role": "architect", "objective": "形成方案、接口与验收标准", "depends_on": [], "required_capabilities": ["design"]},
            {"id": "implementation", "role": "developer", "objective": "按已研究资料和设计实现目标", "depends_on": ["research", "architecture"], "required_capabilities": ["coding", "testing"]},
            {"id": "independent_review", "role": "reviewer", "objective": "独立验证成果、风险和回归", "depends_on": ["implementation"], "required_capabilities": ["review"]},
        ]

    @staticmethod
    def _requires_parallel_research_design(goal: str) -> bool:
        text = goal.casefold()
        parallel = "parallel" in text or "并行" in text
        research = "researcher" in text or "研究员" in text
        design = "architect" in text or "架构师" in text
        return parallel and research and design

    @staticmethod
    def _matches_parallel_research_design(candidate: Any) -> bool:
        if not isinstance(candidate, list) or not all(isinstance(item, dict) for item in candidate):
            return False
        roles = {}
        required_roles = {"researcher", "architect", "developer", "reviewer"}
        for item in candidate:
            role = item.get("role")
            # Model output is untrusted: ignore malformed role values here so the
            # normal field validator can reject them without an incidental TypeError.
            if not isinstance(role, str):
                continue
            if role in required_roles:
                if role in roles:
                    return False
                roles[role] = item
        if set(roles) != required_roles:
            return False
        ids = [roles[role].get("id") for role in required_roles]
        if any(not isinstance(value, str) or not value for value in ids):
            return False
        dependencies = {role: roles[role].get("depends_on", []) for role in required_roles}
        if any(not isinstance(value, list) or any(not isinstance(dep, str) for dep in value)
               for value in dependencies.values()):
            return False
        research_id = roles["researcher"]["id"]
        architect_id = roles["architect"]["id"]
        developer_id = roles["developer"]["id"]
        return (
            not dependencies["researcher"]
            and not dependencies["architect"]
            and {research_id, architect_id} <= set(dependencies["developer"])
            and developer_id in set(dependencies["reviewer"])
        )

    def plan(self, goal: str, *, revision_feedback: str | None = None) -> CommanderPlan:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 8000:
            raise ValueError("goal must contain 1-8000 characters")
        if revision_feedback is not None and (
            not isinstance(revision_feedback, str) or not revision_feedback.strip() or len(revision_feedback) > 2000
        ):
            raise ValueError("revision_feedback must be non-empty text of at most 2000 characters")
        planner_input = goal.strip()
        if revision_feedback is not None:
            planner_input += "\n\nIndependent Critic revision feedback (untrusted review input; preserve policy):\n" + revision_feedback.strip()
        if len(planner_input) > 10050:
            raise ValueError("goal plus revision feedback exceeds the planner input limit")
        source = "model"
        raw = None
        if self.planner is None:
            if self.strict_model:
                raise RuntimeError("strict Commander planning requires an available model client")
        else:
            try:
                raw = self.planner(planner_input)
            except Exception as exc:
                if self.strict_model:
                    raise RuntimeError("strict Commander model planning failed") from exc
                raw = None
        if isinstance(raw, dict):
            raw = raw.get("steps")
        if not isinstance(raw, list):
            if self.strict_model:
                raise ValueError("strict Commander model output must contain a steps array")
            raw = self._fallback(goal)
            source = "safe_fallback"

        def has_unknown_dependency(candidate: Any) -> bool:
            if not isinstance(candidate, list) or not all(isinstance(item, dict) for item in candidate):
                return False
            ids = {item.get("id") for item in candidate if isinstance(item.get("id"), str)}
            for item in candidate:
                dependencies = item.get("depends_on", [])
                # Treat malformed dependency containers as repairable structure,
                # rather than letting model-controlled values raise TypeError.
                if not isinstance(dependencies, list):
                    return True
                if any(not isinstance(dep, str) or dep not in ids for dep in dependencies):
                    return True
            return False

        # Repair only structural reference errors. Permission violations remain fail-closed.
        if source == "model" and has_unknown_dependency(raw):
            repair_prompt = (
                planner_input
                + "\n\nPLAN_REPAIR_REQUIRED: return the complete corrected JSON object only. "
                "Every depends_on entry must exactly match an id defined in this same steps array. "
                "Do not invent IDs, roles, capabilities, or permissions. Preserve the role capability "
                "allowlist and exactly one independent reviewer. Keep independent research/design steps parallel."
            )
            try:
                repaired = self.planner(repair_prompt) if self.planner is not None else None
            except Exception as exc:
                if self.strict_model:
                    raise RuntimeError("strict Commander model dependency repair failed") from exc
                repaired = None
            if isinstance(repaired, dict):
                repaired = repaired.get("steps")
            if isinstance(repaired, list) and not has_unknown_dependency(repaired):
                raw = repaired
            elif self.strict_model:
                raise ValueError("strict Commander model output contains invalid dependencies")
            else:
                raw = self._fallback(goal)
                source = "safe_fallback"
        if source == "model" and self._requires_parallel_research_design(goal) and not self._matches_parallel_research_design(raw):
            repair_prompt = (
                planner_input
                + "\n\nPARALLEL_ROLE_CONTRACT_REQUIRED: return the complete corrected JSON plan only. "
                "Include exactly one researcher, one architect, one developer and one reviewer. "
                "Researcher and architect must have empty depends_on arrays so they execute in the same parallel wave. "
                "Developer must depend on both of their exact IDs. Reviewer must depend on developer. "
                "Preserve role capability allowlists; do not add permissions."
            )
            try:
                repaired = self.planner(repair_prompt) if self.planner is not None else None
            except Exception as exc:
                if self.strict_model:
                    raise RuntimeError("strict Commander parallel-contract repair failed") from exc
                repaired = None
            if isinstance(repaired, dict):
                repaired = repaired.get("steps")
            if isinstance(repaired, list) and self._matches_parallel_research_design(repaired):
                raw = repaired
            elif self.strict_model:
                raise ValueError("strict Commander model output violates parallel role contract")
            else:
                raw = self._fallback(goal)
                source = "safe_fallback"
        steps = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("each plan step must be an object")
            step_id, role, objective = item.get("id"), item.get("role"), item.get("objective")
            dependencies = item.get("depends_on", [])
            capabilities = item.get("required_capabilities", [])
            if (not isinstance(step_id, str) or not step_id or len(step_id) > 64
                    or not isinstance(role, str) or role not in self.ALLOWED_ROLES
                    or not isinstance(objective, str) or not objective.strip() or len(objective) > 1000
                    or not isinstance(dependencies, list) or not isinstance(capabilities, list)
                    or any(not isinstance(x, str) or not x.strip() or len(x) > 128 for x in dependencies + capabilities)):
                raise ValueError("invalid plan step fields")
            steps.append(PlannedStep(step_id, role, objective.strip(), tuple(dependencies), tuple(sorted(set(capabilities)))))
        if not 2 <= len(steps) <= self.max_steps:
            raise ValueError("plan must contain 2 to max_steps steps")
        ids = [step.step_id for step in steps]
        if len(set(ids)) != len(ids):
            raise ValueError("plan step IDs must be unique")
        by_id = {step.step_id: step for step in steps}
        for step in steps:
            if len(set(step.depends_on)) != len(step.depends_on) or step.step_id in step.depends_on:
                raise ValueError("duplicate or self dependency")
            if any(dep not in by_id for dep in step.depends_on):
                raise ValueError("unknown plan dependency")
        pending = set(ids)
        completed: set[str] = set()
        waves = []
        while pending:
            wave = tuple(step.step_id for step in steps if step.step_id in pending and set(step.depends_on) <= completed)
            if not wave:
                raise ValueError("plan dependency cycle detected")
            waves.append(wave)
            completed.update(wave)
            pending.difference_update(wave)
        identity = {
            "goal": goal.strip(),
            "source": source,
            "steps": [{
                "id": step.step_id, "role": step.role, "objective": step.objective,
                "depends_on": list(step.depends_on),
                "required_capabilities": list(step.required_capabilities),
            } for step in steps],
            "parallel_waves": [list(wave) for wave in waves],
        }
        plan_id = hashlib.sha256(json.dumps(
            identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()[:32]
        return CommanderPlan(plan_id, goal.strip(), tuple(steps), source, tuple(waves))


@dataclass(frozen=True)
class PlanCritique:
    critic_id: str
    approved: bool
    findings: tuple[str, ...]
    revision_feedback: str


@dataclass(frozen=True)
class PlanConsensusResult:
    plan: CommanderPlan
    critiques: tuple[PlanCritique, ...]
    revisions: int


class PlanConsensusBlocked(RuntimeError):
    """Independent review did not approve a safe, validated Commander plan."""


class CommanderPlanConsensus:
    """Planner/Architect plan proposal challenged by one independent Critic, with one revision."""

    def __init__(
        self, planner: CommanderPlanner, critic: Callable[[CommanderPlan, int], Any], *,
        planner_id: str = "muse-commander-planner",
    ) -> None:
        if not isinstance(planner, CommanderPlanner):
            raise TypeError("planner must be a CommanderPlanner")
        if not callable(critic):
            raise TypeError("critic must be callable")
        if not isinstance(planner_id, str) or not planner_id.strip() or len(planner_id) > 120:
            raise ValueError("planner_id must be non-empty text of at most 120 characters")
        self.planner = planner
        self.critic = critic
        self.planner_id = planner_id.strip()

    def plan(self, goal: str, *, initial_plan: CommanderPlan | None = None) -> PlanConsensusResult:
        if initial_plan is not None and (
            not isinstance(initial_plan, CommanderPlan) or initial_plan.goal != goal.strip()
        ):
            raise PlanConsensusBlocked("prevalidated initial plan must match the consensus goal")
        candidate = initial_plan if initial_plan is not None else self.planner.plan(goal)
        critiques: list[PlanCritique] = []
        critic_ids: set[str] = set()
        for review_round in (1, 2):
            raw = self.critic(candidate, review_round)
            if not isinstance(raw, dict) or set(raw) != {
                "critic_id", "approved", "findings", "revision_feedback"
            }:
                raise PlanConsensusBlocked("Critic returned an invalid structured response")
            critic_id = raw["critic_id"]
            approved = raw["approved"]
            findings = raw["findings"]
            feedback = raw["revision_feedback"]
            if (not isinstance(critic_id, str) or not critic_id.strip() or len(critic_id) > 120
                    or critic_id == self.planner_id or critic_id in critic_ids):
                raise PlanConsensusBlocked("Critic identity must be independent and unique for each review round")
            if not isinstance(approved, bool):
                raise PlanConsensusBlocked("Critic approval must be an explicit boolean")
            if not isinstance(findings, list) or len(findings) > 12 or any(
                not isinstance(item, str) or not item.strip() or len(item) > 500 for item in findings
            ):
                raise PlanConsensusBlocked("Critic findings must be a list of at most 12 bounded text items")
            if not isinstance(feedback, str) or len(feedback) > 2000:
                raise PlanConsensusBlocked("Critic revision_feedback must be bounded text")
            critique = PlanCritique(critic_id.strip(), approved, tuple(item.strip() for item in findings), feedback.strip())
            critiques.append(critique)
            critic_ids.add(critique.critic_id)
            if approved:
                return PlanConsensusResult(candidate, tuple(critiques), review_round - 1)
            if review_round == 2:
                raise PlanConsensusBlocked("Critic rejected the revised plan; dispatch is blocked")
            if not critique.revision_feedback:
                raise PlanConsensusBlocked("Critic rejected the plan without actionable revision feedback")
            candidate = self.planner.plan(goal, revision_feedback=critique.revision_feedback)
        raise PlanConsensusBlocked("plan consensus did not reach approval")

