import unittest

from muse.workflow_profiles import VerificationPolicy
from muse.verification import (
    BoundedVerificationLoop, VerificationBlocked, VerificationResult,
)


class BoundedVerificationLoopTests(unittest.TestCase):
    def test_retryable_idempotent_repair_reverifies_and_returns_approved_artifact(self):
        seen = []
        def verify(artifact, attempt):
            seen.append((artifact, attempt))
            if attempt == 1:
                return VerificationResult(False, True, ("missing acceptance evidence",))
            return VerificationResult(True, False, ())
        outcome = BoundedVerificationLoop(VerificationPolicy(max_attempts=2)).run(
            "draft", verify=verify,
            fix=lambda artifact, findings, attempt: artifact + "-revised",
            idempotent=True,
        )
        self.assertEqual(outcome.artifact, "draft-revised")
        self.assertEqual(outcome.attempts, 2)
        self.assertEqual(seen, [("draft", 1), ("draft-revised", 2)])

    def test_non_idempotent_work_never_enters_automatic_fix(self):
        fixed = []
        with self.assertRaisesRegex(VerificationBlocked, "non-idempotent"):
            BoundedVerificationLoop(VerificationPolicy()).run(
                "artifact",
                verify=lambda _artifact, _attempt: VerificationResult(False, True, ("failed",)),
                fix=lambda *args: fixed.append(args),
                idempotent=False,
            )
        self.assertEqual(fixed, [])

    def test_non_retryable_review_stops_without_repair(self):
        with self.assertRaisesRegex(VerificationBlocked, "non-retryable"):
            BoundedVerificationLoop(VerificationPolicy()).run(
                "artifact",
                verify=lambda _artifact, _attempt: VerificationResult(False, False, ("permission denied",)),
                fix=lambda *_args: self.fail("fix must not run"),
                idempotent=True,
            )

    def test_attempt_ceiling_is_enforced(self):
        with self.assertRaisesRegex(VerificationBlocked, "attempt ceiling"):
            BoundedVerificationLoop(VerificationPolicy(max_attempts=1)).run(
                "artifact",
                verify=lambda _artifact, _attempt: VerificationResult(False, True, ("needs work",)),
                fix=lambda *_args: self.fail("fix must not run after final attempt"),
                idempotent=True,
            )

    def test_runtime_ceiling_is_enforced_before_next_verification(self):
        ticks = iter((0.0, 0.0, 2.0))
        loop = BoundedVerificationLoop(VerificationPolicy(max_attempts=2, max_runtime_seconds=1), clock=lambda: next(ticks))
        with self.assertRaisesRegex(VerificationBlocked, "runtime ceiling"):
            loop.run(
                "artifact",
                verify=lambda _artifact, _attempt: VerificationResult(False, True, ("retry",)),
                fix=lambda *_args: "repaired",
                idempotent=True,
            )


if __name__ == "__main__":
    unittest.main()
