"""Fail-closed routing policy for explicitly verified free provider quotas."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Iterable


class NoFreeProviderAvailable(RuntimeError):
    """No enabled provider has a verified, fresh, positive free quota."""


@dataclass(frozen=True)
class ProviderBudget:
    provider_id: str
    enabled: bool
    free_tier_confirmed: bool
    quota_verified: bool
    remaining_units: int | None
    unit_name: str = "requests"
    quota_checked_at: str = ""
    quota_source: str = ""

    @property
    def eligible(self) -> bool:
        return (
            bool(self.provider_id.strip())
            and self.enabled
            and self.free_tier_confirmed
            and self.quota_verified
            and self.remaining_units is not None
            and self.remaining_units > 0
        )

    def is_fresh(
        self, *, now: datetime | None = None, max_age_seconds: int = 300,
    ) -> bool:
        """Only a timestamped quota observation can authorize a live request."""
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        if not self.quota_checked_at or not self.quota_source.strip():
            return False
        try:
            checked = datetime.fromisoformat(self.quota_checked_at)
        except ValueError:
            return False
        if checked.tzinfo is None:
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        age = current.astimezone(timezone.utc) - checked.astimezone(timezone.utc)
        return timedelta(0) <= age <= timedelta(seconds=max_age_seconds)


class FreeOnlyRouter:
    """Select only enabled providers with fresh, verified free quota.

    Candidate order is an explicit preference order. Unknown, missing, stale,
    future-dated, or exhausted quota fails closed. No paid fallback is possible.
    """

    def __init__(self, *, max_quota_age_seconds: int = 300) -> None:
        if max_quota_age_seconds <= 0:
            raise ValueError("max_quota_age_seconds must be positive")
        self.max_quota_age_seconds = max_quota_age_seconds

    def select(
        self, candidates: Iterable[ProviderBudget], *, now: datetime | None = None,
    ) -> ProviderBudget:
        for candidate in candidates:
            if candidate.eligible and candidate.is_fresh(
                now=now, max_age_seconds=self.max_quota_age_seconds
            ):
                return candidate
        raise NoFreeProviderAvailable(
            "No provider has an enabled, confirmed free tier and fresh, verified remaining quota."
        )

    def consume(
        self, budget: ProviderBudget, units: int = 1, *, now: datetime | None = None,
    ) -> ProviderBudget:
        if units < 1:
            raise ValueError("units must be positive")
        if not budget.eligible or not budget.is_fresh(
            now=now, max_age_seconds=self.max_quota_age_seconds
        ):
            raise NoFreeProviderAvailable("Provider quota is missing, stale or not eligible for free-only routing.")
        if units > budget.remaining_units:
            raise NoFreeProviderAvailable("Insufficient verified free quota; paid fallback is disabled.")
        return replace(budget, remaining_units=budget.remaining_units - units)
