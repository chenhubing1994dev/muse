"""Stable, privacy-conscious failure classification for Muse task execution."""

from __future__ import annotations

import re
import sqlite3
import subprocess
from dataclasses import dataclass


_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CATEGORIES = frozenset({
    "authorization", "configuration", "dependency", "execution", "input",
    "provider", "review", "storage", "system", "handler", "worker",
})


@dataclass(frozen=True)
class FailureDescriptor:
    code: str
    category: str
    retryable: bool
    guidance: str

    def as_result(self) -> dict[str, object]:
        return {
            "error_code": self.code,
            "failure_category": self.category,
            "retryable": self.retryable,
            "recovery_guidance": self.guidance,
        }


class MuseTaskError(Exception):
    """A handler may raise this to provide a validated, stable failure classification."""

    def __init__(
        self, code: str, *, category: str, retryable: bool, guidance: str
    ) -> None:
        if not isinstance(code, str) or len(code) > 80 or not _CODE_PATTERN.fullmatch(code):
            raise ValueError("failure code must be a bounded lowercase identifier")
        if not isinstance(category, str) or category not in _CATEGORIES:
            raise ValueError("unsupported failure category")
        if not isinstance(retryable, bool):
            raise ValueError("retryable must be a boolean")
        if not isinstance(guidance, str) or not guidance.strip() or len(guidance) > 240:
            raise ValueError("recovery guidance must be non-empty text of at most 240 characters")
        self.descriptor = FailureDescriptor(code, category, retryable, guidance.strip())
        super().__init__(guidance.strip())


def classify_failure(error: BaseException) -> FailureDescriptor:
    """Map an exception type to stable metadata; never persist its message."""
    if isinstance(error, MuseTaskError):
        return error.descriptor
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return FailureDescriptor(
            "execution.timeout", "execution", True,
            "Check the configured timeout and resource limits before retrying.",
        )
    if isinstance(error, PermissionError):
        return FailureDescriptor(
            "authorization.denied", "authorization", False,
            "Review the required approval and registered capability before retrying.",
        )
    if isinstance(error, FileNotFoundError):
        return FailureDescriptor(
            "dependency.missing", "dependency", False,
            "Verify that the required executable or input resource is installed and registered.",
        )
    if isinstance(error, ConnectionError):
        return FailureDescriptor(
            "provider.unavailable", "provider", True,
            "Check the configured endpoint and retry only after the provider is reachable.",
        )
    if isinstance(error, sqlite3.OperationalError):
        if any(token in str(error).lower() for token in ("locked", "busy")):
            return FailureDescriptor(
                "storage.busy", "storage", True,
                "Wait for the database lock to clear, then retry within the bounded recovery policy.",
            )
        return FailureDescriptor(
            "storage.operation_failed", "storage", False,
            "Inspect the local database state before attempting a manual recovery.",
        )
    if isinstance(error, sqlite3.DatabaseError):
        return FailureDescriptor(
            "storage.invalid_state", "storage", False,
            "Inspect the local database and restore from a verified backup if required.",
        )
    if isinstance(error, subprocess.CalledProcessError):
        return FailureDescriptor(
            "execution.failed", "execution", False,
            "Review the registered tool's exit status and bounded diagnostic output.",
        )
    if isinstance(error, (ValueError, TypeError)):
        return FailureDescriptor(
            "input.invalid", "input", False,
            "Validate the task schema and required fields before creating a new task.",
        )
    if isinstance(error, OSError):
        return FailureDescriptor(
            "system.io_error", "system", False,
            "Check local resource availability and filesystem permissions.",
        )
    return FailureDescriptor(
        "handler.unexpected", "handler", False,
        "Inspect the handler implementation and reproduce with a bounded regression test.",
    )
