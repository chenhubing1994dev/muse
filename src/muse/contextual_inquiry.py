"""Privacy-conscious, evidence-aware context inquiry contracts.

This module defines immutable data structures only. It does not diagnose,
persist personal cases, call providers, schedule work, or execute actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Tuple


class SourceKind(str, Enum):
    USER_QUOTE = "user_quote"
    OBSERVABLE_FACT = "observable_fact"
    THIRD_PARTY_STATEMENT = "third_party_statement"
    SYSTEM_RECORD = "system_record"


class SafetyRoute(str, Enum):
    NORMAL = "normal"
    PROFESSIONAL_SUPPORT = "professional_support"
    URGENT_SUPPORT = "urgent_support"
    OUT_OF_SCOPE = "out_of_scope"


class ViewStatus(str, Enum):
    EXPRESSED = "expressed"
    UNKNOWN = "unknown"


class FeedbackOutcome(str, Enum):
    OBSERVED = "observed"
    PARTIAL = "partial"
    NO_EFFECT = "no_effect"
    ADVERSE = "adverse"
    UNKNOWN = "unknown"


def _text(value: str, field_name: str, limit: int = 1200) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field_name} must be non-empty text of at most {limit} characters")


def _refs(values: Tuple[str, ...], field_name: str, maximum: int = 32) -> None:
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValueError(f"{field_name} must be a tuple with at most {maximum} items")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 160 for item in values):
        raise ValueError(f"{field_name} items must be non-empty identifiers of at most 160 characters")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicate identifiers")


def _texts(values: Tuple[str, ...], field_name: str, maximum: int = 32) -> None:
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValueError(f"{field_name} must be a tuple with at most {maximum} items")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 1200 for item in values):
        raise ValueError(f"{field_name} items must be non-empty text of at most 1200 characters")


@dataclass(frozen=True)
class Observation:
    observation_id: str
    source_kind: SourceKind
    content: str
    source_ref: str
    observed_at: str = ""

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id", 160)
        _text(self.content, "content")
        _text(self.source_ref, "source_ref", 500)
        if not isinstance(self.source_kind, SourceKind):
            raise ValueError("source_kind must be a SourceKind")
        if not isinstance(self.observed_at, str):
            raise ValueError("observed_at must be ISO date/time text")


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    statement: str
    source_refs: Tuple[str, ...]
    counter_refs: Tuple[str, ...] = ()
    confidence: float = 0.25
    unsourced: bool = False
    falsification_condition: str = ""

    def __post_init__(self) -> None:
        _text(self.hypothesis_id, "hypothesis_id", 160)
        _text(self.statement, "statement")
        _refs(self.source_refs, "source_refs")
        _refs(self.counter_refs, "counter_refs")
        _text(self.falsification_condition, "falsification_condition")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("confidence must be a number between 0 and 1")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.unsourced, bool):
            raise ValueError("unsourced must be a boolean")
        if not self.source_refs and not self.unsourced:
            raise ValueError("a hypothesis without source_refs must be marked unsourced")
        if self.unsourced and (self.source_refs or self.counter_refs or self.confidence > 0.35):
            raise ValueError("unsourced hypotheses cannot cite evidence or exceed 0.35 confidence")
        if set(self.source_refs).intersection(self.counter_refs):
            raise ValueError("source_refs and counter_refs must be disjoint")


@dataclass(frozen=True)
class StakeholderView:
    stakeholder_id: str
    status: ViewStatus
    statement: str = ""
    observation_ref: str = ""

    def __post_init__(self) -> None:
        _text(self.stakeholder_id, "stakeholder_id", 160)
        if not isinstance(self.statement, str) or not isinstance(self.observation_ref, str):
            raise ValueError("stakeholder statement and observation_ref must be text")
        if not isinstance(self.status, ViewStatus):
            raise ValueError("status must be a ViewStatus")
        if self.status is ViewStatus.EXPRESSED:
            _text(self.statement, "statement")
            _text(self.observation_ref, "observation_ref", 160)
        elif self.statement or self.observation_ref:
            raise ValueError("unknown stakeholder views must not invent a statement or source")


@dataclass(frozen=True)
class ActionExperiment:
    experiment_id: str
    owner: str
    due_date: str
    resource_constraints: Tuple[str, ...]
    expected_signal: str
    stop_condition: str
    reversible: bool = True

    def __post_init__(self) -> None:
        _text(self.experiment_id, "experiment_id", 160)
        _text(self.owner, "owner", 160)
        _text(self.due_date, "due_date", 32)
        try:
            date.fromisoformat(self.due_date)
        except ValueError as exc:
            raise ValueError("due_date must be an ISO YYYY-MM-DD date") from exc
        _texts(self.resource_constraints, "resource_constraints", maximum=12)
        _text(self.expected_signal, "expected_signal")
        _text(self.stop_condition, "stop_condition")
        if self.reversible is not True:
            raise ValueError("only reversible action experiments are permitted")


@dataclass(frozen=True)
class ActionFeedback:
    experiment_id: str
    outcome: FeedbackOutcome
    observed_signal: str
    side_effects: str = ""
    supported_hypothesis_ids: Tuple[str, ...] = ()
    contradicted_hypothesis_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.experiment_id, "experiment_id", 160)
        if not isinstance(self.outcome, FeedbackOutcome):
            raise ValueError("outcome must be a FeedbackOutcome")
        _text(self.observed_signal, "observed_signal")
        if not isinstance(self.side_effects, str) or len(self.side_effects) > 1200:
            raise ValueError("side_effects must be text of at most 1200 characters")
        _refs(self.supported_hypothesis_ids, "supported_hypothesis_ids")
        _refs(self.contradicted_hypothesis_ids, "contradicted_hypothesis_ids")


@dataclass(frozen=True)
class ContextualInquiry:
    inquiry_id: str
    user_goal: str
    observations: Tuple[Observation, ...] = ()
    hypotheses: Tuple[Hypothesis, ...] = ()
    unknowns: Tuple[str, ...] = ()
    stakeholder_views: Tuple[StakeholderView, ...] = ()
    action_experiments: Tuple[ActionExperiment, ...] = ()
    safety_route: SafetyRoute = SafetyRoute.NORMAL
    consent_to_store: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        _text(self.inquiry_id, "inquiry_id", 160)
        _text(self.user_goal, "user_goal")
        if (isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int)
                or self.schema_version != 1):
            raise ValueError("unsupported contextual inquiry schema_version")
        if not isinstance(self.safety_route, SafetyRoute):
            raise ValueError("safety_route must be a SafetyRoute")
        if not isinstance(self.consent_to_store, bool):
            raise ValueError("consent_to_store must be a boolean")
        for field_name, values, expected_type, maximum in (
            ("observations", self.observations, Observation, 64),
            ("hypotheses", self.hypotheses, Hypothesis, 32),
            ("stakeholder_views", self.stakeholder_views, StakeholderView, 32),
            ("action_experiments", self.action_experiments, ActionExperiment, 16),
        ):
            if not isinstance(values, tuple) or len(values) > maximum:
                raise ValueError(f"{field_name} must be a tuple with at most {maximum} items")
            if any(not isinstance(item, expected_type) for item in values):
                raise ValueError(f"{field_name} contains an invalid item type")
        _texts(self.unknowns, "unknowns", maximum=32)
        observation_ids = {item.observation_id for item in self.observations}
        hypothesis_ids = {item.hypothesis_id for item in self.hypotheses}
        experiment_ids = {item.experiment_id for item in self.action_experiments}
        if len(observation_ids) != len(self.observations):
            raise ValueError("observation identifiers must be unique")
        if len(hypothesis_ids) != len(self.hypotheses):
            raise ValueError("hypothesis identifiers must be unique")
        if len(experiment_ids) != len(self.action_experiments):
            raise ValueError("experiment identifiers must be unique")
        for hypothesis in self.hypotheses:
            refs = set(hypothesis.source_refs + hypothesis.counter_refs)
            if not refs.issubset(observation_ids):
                raise ValueError("hypothesis references an unknown observation")
        for view in self.stakeholder_views:
            if view.status is ViewStatus.EXPRESSED:
                if view.observation_ref not in observation_ids:
                    raise ValueError("stakeholder view references an unknown observation")
                source = next(item for item in self.observations
                              if item.observation_id == view.observation_ref)
                if source.source_kind is not SourceKind.THIRD_PARTY_STATEMENT:
                    raise ValueError("expressed stakeholder views require a third-party statement source")
        if self.safety_route is not SafetyRoute.NORMAL and self.action_experiments:
            raise ValueError("non-normal safety routes cannot include ordinary action experiments")

    def assert_persistable(self) -> None:
        """Require explicit consent before a caller persists this case elsewhere."""
        if not self.consent_to_store:
            raise PermissionError("explicit consent_to_store is required before persistence")
