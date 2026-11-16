from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from muse.provider_policy import FreeOnlyRouter, NoFreeProviderAvailable, ProviderBudget


def fresh_budget(provider_id: str, remaining: int | None, *, free: bool = True) -> ProviderBudget:
    return ProviderBudget(
        provider_id, True, free, remaining is not None, remaining,
        "requests", datetime.now(timezone.utc).isoformat(), "https://quota.example/status",
    )


class FreeOnlyRouterTests(unittest.TestCase):
    def test_skips_unknown_and_exhausted_quota(self) -> None:
        router = FreeOnlyRouter()
        unknown = ProviderBudget("unknown", True, True, False, None)
        exhausted = fresh_budget("empty", 0)
        free = fresh_budget("free", 12)
        self.assertEqual(router.select([unknown, exhausted, free]).provider_id, "free")

    def test_refuses_paid_only_or_unverified_provider(self) -> None:
        router = FreeOnlyRouter()
        paid = fresh_budget("paid", 100, free=False)
        unknown = ProviderBudget("unknown", True, True, False, 100)
        with self.assertRaises(NoFreeProviderAvailable):
            router.select([paid, unknown])

    def test_stale_missing_future_and_naive_quota_evidence_fail_closed(self) -> None:
        router = FreeOnlyRouter(max_quota_age_seconds=60)
        stale = ProviderBudget("stale", True, True, True, 5, "requests", (datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat(), "https://quota.example")
        future = ProviderBudget("future", True, True, True, 5, "requests", (datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat(), "https://quota.example")
        naive = ProviderBudget("naive", True, True, True, 5, "requests", datetime.now().replace(tzinfo=None).isoformat(), "https://quota.example")
        missing = fresh_budget("missing", 5)
        missing = ProviderBudget("missing", True, True, True, 5)
        for candidate in (stale, future, naive, missing):
            with self.subTest(candidate=candidate.provider_id):
                with self.assertRaises(NoFreeProviderAvailable):
                    router.select([candidate])

    def test_consumption_never_goes_below_zero_or_falls_back(self) -> None:
        budget = fresh_budget("free", 2)
        remaining = FreeOnlyRouter().consume(budget, 2)
        self.assertEqual(remaining.remaining_units, 0)
        with self.assertRaises(NoFreeProviderAvailable):
            FreeOnlyRouter().consume(remaining)
        with self.assertRaises(NoFreeProviderAvailable):
            FreeOnlyRouter().consume(budget, 3)

    def test_invalid_consumption_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FreeOnlyRouter().consume(fresh_budget("free", 2), 0)


if __name__ == "__main__":
    unittest.main()
