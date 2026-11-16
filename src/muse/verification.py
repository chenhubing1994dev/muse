"""Bounded verify→fix→reverify primitive; never schedules work or widens authority."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable

from muse.workflow_profiles import VerificationPolicy


@dataclass(frozen=True)
class VerificationResult:
    approved: bool
    retryable: bool
    findings: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.approved, bool) or not isinstance(self.retryable, bool):
            raise ValueError("verification decisions must be explicit booleans")
        if not isinstance(self.findings, tuple) or len(self.findings) > 12 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 500
            for item in self.findings
        ):
            raise ValueError("findings must be a tuple of at most 12 bounded strings")
        if not self.approved and not self.findings:
            raise ValueError("rejected verification must provide actionable findings")
        if not isinstance(self.evidence_refs, tuple) or len(self.evidence_refs) > 16 or any(
            not isinstance(ref, str) or not ref.strip() or len(ref) > 240
            for ref in self.evidence_refs
        ):
            raise ValueError("evidence_refs must be a tuple of at most 16 bounded references")


@dataclass(frozen=True)
class VerificationOutcome:
    artifact: Any
    attempts: int
    results: tuple[VerificationResult, ...]


class VerificationBlocked(RuntimeError):
    def __init__(self, reason: str, *, attempts: int, findings: tuple[str, ...]) -> None:
        self.attempts = attempts
        self.findings = findings
        super().__init__(reason)


class BoundedVerificationLoop:
    """Run verification and an idempotent repair at most the profile's attempt/time ceilings."""

    def __init__(
        self, policy: VerificationPolicy, *, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(policy, VerificationPolicy):
            raise TypeError("policy must be a VerificationPolicy")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.policy = policy
        self.clock = clock

    def run(
        self, artifact: Any, *,
        verify: Callable[[Any, int], VerificationResult],
        fix: Callable[[Any, tuple[str, ...], int], Any],
        idempotent: bool,
        on_attempt: Callable[[int, VerificationResult], None] | None = None,
    ) -> VerificationOutcome:
        if not callable(verify) or not callable(fix):
            raise TypeError("verify and fix must be callable")
        if not isinstance(idempotent, bool):
            raise ValueError("idempotent must be an explicit boolean")
        if on_attempt is not None and not callable(on_attempt):
            raise TypeError("on_attempt must be callable")
        started = self.clock()
        current = artifact
        results: list[VerificationResult] = []
        for attempt in range(1, self.policy.max_attempts + 1):
            if self.clock() - started >= self.policy.max_runtime_seconds:
                raise VerificationBlocked(
                    "verification runtime ceiling exceeded",
                    attempts=len(results), findings=results[-1].findings if results else (),
                )
            result = verify(current, attempt)
            if not isinstance(result, VerificationResult):
                raise VerificationBlocked(
                    "verifier returned an invalid structured result",
                    attempts=len(results), findings=results[-1].findings if results else (),
                )
            results.append(result)
            if on_attempt is not None:
                on_attempt(attempt, result)
            if self.clock() - started >= self.policy.max_runtime_seconds:
                raise VerificationBlocked(
                    "verification runtime ceiling exceeded after review",
                    attempts=attempt, findings=result.findings,
                )
            if result.approved:
                return VerificationOutcome(current, attempt, tuple(results))
            if not result.retryable:
                raise VerificationBlocked(
                    "review rejected the artifact as non-retryable",
                    attempts=attempt, findings=result.findings,
                )
            if not idempotent:
                raise VerificationBlocked(
                    "automatic repair is forbidden for non-idempotent work",
                    attempts=attempt, findings=result.findings,
                )
            if attempt >= self.policy.max_attempts:
                raise VerificationBlocked(
                    "verification attempt ceiling reached",
                    attempts=attempt, findings=result.findings,
                )
            if self.clock() - started >= self.policy.max_runtime_seconds:
                raise VerificationBlocked(
                    "verification runtime ceiling exceeded before repair",
                    attempts=attempt, findings=result.findings,
                )
            current = fix(current, result.findings, attempt)
        raise VerificationBlocked(
            "verification did not reach approval",
            attempts=len(results), findings=results[-1].findings if results else (),
        )
