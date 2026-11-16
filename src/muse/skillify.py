"""Evidence-backed Skillify candidates using Muse's existing Agent memory track.

Candidates stay pending and invisible to search until deterministic evaluation and
explicit human approval both succeed. Skills are guidance, never tool grants.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import re
from typing import Any

from muse.memory import MemoryStore


_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class SkillifyBlocked(RuntimeError):
    """A skill candidate lacks evidence, evaluation or explicit approval."""


class SkillifyService:
    def __init__(self, memory: MemoryStore) -> None:
        if not isinstance(memory, MemoryStore):
            raise TypeError("memory must be the existing MemoryStore")
        self.memory = memory
        memory._connection.execute(
            """CREATE TABLE IF NOT EXISTS skill_candidate_evaluations (
                candidate_id INTEGER PRIMARY KEY,
                evaluator_id TEXT NOT NULL,
                passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES memory_candidates(id)
            )"""
        )
        memory._connection.commit()

    @staticmethod
    def _text(value: Any, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise ValueError(f"{field} must be non-empty text of at most {limit} characters")
        return value.strip()

    def propose_completed_run(
        self, *, run_id: str, agent_id: str, name: str, summary: str,
        steps: tuple[str, ...], test_cases: tuple[Mapping[str, str], ...],
        evidence_refs: tuple[str, ...], run_status: str,
        project: str = "muse", confidence: float = 0.8,
    ) -> dict[str, Any]:
        if run_status != "succeeded":
            raise SkillifyBlocked("only completed successful runs can produce Skill candidates")
        run_id = self._text(run_id, "run_id", 120)
        agent_id = self._text(agent_id, "agent_id", 120)
        name = self._text(name, "name", 64)
        if not _SKILL_NAME.fullmatch(name):
            raise ValueError("name must be a lowercase skill identifier")
        summary = self._text(summary, "summary", 1000)
        if not isinstance(steps, tuple) or not 1 <= len(steps) <= 12:
            raise ValueError("steps must contain 1-12 instructions")
        clean_steps = [self._text(step, "step", 800) for step in steps]
        if not isinstance(test_cases, tuple) or not 1 <= len(test_cases) <= 20:
            raise ValueError("test_cases must contain 1-20 test cases")
        clean_tests = []
        for case in test_cases:
            if not isinstance(case, Mapping) or set(case) - {"input", "expected_contains", "expected_not_contains"}:
                raise ValueError("each test case has an invalid shape")
            if "input" not in case or "expected_contains" not in case:
                raise ValueError("each test case requires input and expected_contains")
            clean = {
                "input": self._text(case["input"], "test input", 1000),
                "expected_contains": self._text(case["expected_contains"], "expected_contains", 500),
            }
            if "expected_not_contains" in case:
                clean["expected_not_contains"] = self._text(
                    case["expected_not_contains"], "expected_not_contains", 500
                )
            clean_tests.append(clean)
        if not isinstance(evidence_refs, tuple) or not 1 <= len(evidence_refs) <= 16:
            raise ValueError("evidence_refs must contain 1-16 references")
        clean_evidence = [self._text(ref, "evidence reference", 240) for ref in evidence_refs]
        confidence = self.memory._validate_confidence(confidence)
        document = {
            "schema_version": 1,
            "name": name,
            "summary": summary,
            "steps": clean_steps,
            "test_cases": clean_tests,
            "source_run_id": run_id,
            "source_agent_id": agent_id,
            "evidence_refs": clean_evidence,
            "permissions": [],
            "notice": "Guidance only. This skill grants no tools, capabilities or execution permissions.",
        }
        content = json.dumps(document, ensure_ascii=False, sort_keys=True)
        if len(content.encode("utf-8")) > 12000:
            raise ValueError("skill candidate exceeds 12 KiB")
        source = f"completed-run:{run_id};agent:{agent_id};evidence:{'|'.join(clean_evidence)}"
        return self.memory.propose(
            content, project=self._text(project, "project", 120), source=source,
            confidence=confidence, kind="skill", track="agent",
        )

    def evaluate_candidate(
        self, candidate_id: int, *, evaluator_id: str,
        runner: Callable[[Mapping[str, Any], Mapping[str, str]], str],
    ) -> dict[str, Any]:
        evaluator_id = self._text(evaluator_id, "evaluator_id", 120)
        if not callable(runner):
            raise TypeError("runner must be callable")
        candidate = self.memory.get_candidate(candidate_id)
        if candidate is None or candidate["kind"] != "skill" or candidate["track"] != "agent":
            raise SkillifyBlocked("candidate is not an Agent Track Skill")
        if candidate["status"] != "pending":
            raise SkillifyBlocked("only pending Skill candidates can be evaluated")
        document = json.loads(candidate["content"])
        failures = []
        passed_count = 0
        for index, case in enumerate(document["test_cases"]):
            try:
                output = runner(document, case)
                if not isinstance(output, str) or len(output) > 4000:
                    raise ValueError("skill evaluator output must be text of at most 4000 characters")
                normalized = output.casefold()
                passed = case["expected_contains"].casefold() in normalized
                if "expected_not_contains" in case:
                    passed = passed and case["expected_not_contains"].casefold() not in normalized
                if passed:
                    passed_count += 1
                else:
                    failures.append({"case": index, "reason": "expectation_mismatch"})
            except Exception as exc:
                failures.append({"case": index, "reason": type(exc).__name__})
        passed = passed_count == len(document["test_cases"])
        report = {
            "candidate_id": candidate_id, "evaluator_id": evaluator_id,
            "passed": passed, "total": len(document["test_cases"]),
            "passed_count": passed_count, "failures": failures,
        }
        now = datetime.now(timezone.utc).isoformat()
        self.memory._connection.execute(
            """INSERT INTO skill_candidate_evaluations
               (candidate_id,evaluator_id,passed,report_json,created_at) VALUES (?,?,?,?,?)
               ON CONFLICT(candidate_id) DO UPDATE SET evaluator_id=excluded.evaluator_id,
               passed=excluded.passed,report_json=excluded.report_json,created_at=excluded.created_at""",
            (candidate_id, evaluator_id, int(passed),
             json.dumps(report, ensure_ascii=False, sort_keys=True), now),
        )
        self.memory._connection.commit()
        return {**report, "created_at": now}

    def approve_candidate(self, candidate_id: int, *, approved_by: str) -> dict[str, Any]:
        row = self.memory._connection.execute(
            "SELECT passed FROM skill_candidate_evaluations WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None or row["passed"] != 1:
            raise SkillifyBlocked("Skill must pass its evaluation suite before human approval")
        return self.memory.approve_candidate(candidate_id, approved_by=approved_by)

    def reject_candidate(self, candidate_id: int, *, rejected_by: str, reason: str = "") -> dict[str, Any]:
        return self.memory.reject_candidate(candidate_id, rejected_by=rejected_by, reason=reason)

    def evaluation(self, candidate_id: int) -> dict[str, Any] | None:
        row = self.memory._connection.execute(
            "SELECT evaluator_id,passed,report_json,created_at "
            "FROM skill_candidate_evaluations WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "evaluator_id": row["evaluator_id"], "passed": bool(row["passed"]),
            "report": json.loads(row["report_json"]), "created_at": row["created_at"],
        }

    def search_active_skills(self, query: str, *, project: str = "muse", limit: int = 20) -> list[dict[str, Any]]:
        return self.memory.search(query, project=project, kind="skill", track="agent", limit=limit)
