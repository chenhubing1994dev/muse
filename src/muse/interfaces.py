"""Typed boundaries for future integrations; no external actions occur here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: str
    summary: str


class ModelProvider(Protocol):
    def respond(self, prompt: str, *, context: str = "") -> str: ...


class NotificationChannel(Protocol):
    def send(self, message: str) -> TaskResult: ...


class AuthorizedTool(Protocol):
    def execute(self, action: str, arguments: dict[str, object]) -> TaskResult: ...
