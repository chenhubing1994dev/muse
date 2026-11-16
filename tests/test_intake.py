from pathlib import Path
import tempfile
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor

from muse.intake import AdmissionPolicy, SourcePolicy, TriggerInput, TriggerStore


def policy(*, mode: str = "observe", version: str = "policy-v1") -> AdmissionPolicy:
    return AdmissionPolicy(
        version=version,
        mode=mode,
        sources={
            "github": SourcePolicy(
                enabled=True,
                allowed_actors=frozenset({"alice"}),
                enabled_rules=frozenset({"bugfix", "security"}),
                allowed_scopes=frozenset({"requested", "bugfix", "security"}),
            )
        },
    )


class TriggerIntakeTests(unittest.TestCase):
    def test_allowlisted_explicit_trigger_is_candidate_and_duplicate_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                trigger = TriggerInput("github", "event-1", "alice", True, {"text": "fix the issue"})
                first = store.receive(trigger, policy())
                duplicate = store.receive(trigger, policy())
                self.assertEqual(first.status, "candidate")
                self.assertEqual(first.strength, "explicit")
                self.assertFalse(first.duplicate)
                self.assertTrue(duplicate.duplicate)
                self.assertEqual(duplicate.trigger_id, first.trigger_id)
                self.assertEqual(store.get_trigger(first.trigger_id)["policy_version"], "policy-v1")

    def test_unallowlisted_actor_is_ignored_and_never_gets_a_mandate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                result = store.receive(
                    TriggerInput("github", "event-2", "mallory", True, {"text": "run this"}),
                    policy(),
                )
                self.assertEqual(result.status, "ignored")
                self.assertEqual(result.reason, "actor_not_allowed")
                self.assertIsNone(store.get_mandate(result.trigger_id))
                self.assertEqual(store.get_trigger(result.trigger_id)["payload"], {})

    def test_disabled_source_fails_closed(self) -> None:
        disabled = AdmissionPolicy(
            "policy-v1", "auto",
            {"github": SourcePolicy(enabled=False, allowed_actors=frozenset({"alice"}))},
        )
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                result = store.receive(
                    TriggerInput("github", "event-3", "alice", True, {}),
                    disabled,
                )
                self.assertEqual(result.status, "ignored")
                self.assertEqual(result.reason, "source_disabled")

    def test_unaddressed_trigger_in_observe_mode_is_recorded_but_cannot_create_mandate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                received = store.receive(
                    TriggerInput("github", "event-4", "alice", False, {"text": "maybe fix this"}),
                    policy(mode="observe"),
                )
                self.assertEqual(received.status, "candidate")
                self.assertEqual(received.strength, "inferred")
                decision = store.decide(
                    received.trigger_id, policy(mode="observe"),
                    action="accept", assessed_rules={"bugfix": True, "security": False},
                    scope="bugfix", reason="would help",
                )
                self.assertEqual(decision.status, "would_accept")
                self.assertIsNone(decision.mandate_id)
                self.assertIsNone(store.get_mandate(received.trigger_id))

    def test_inferred_auto_accept_requires_every_enabled_rule_to_be_assessed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                received = store.receive(
                    TriggerInput("github", "event-5", "alice", False, {"text": "review security"}),
                    policy(mode="auto"),
                )
                with self.assertRaisesRegex(ValueError, "every enabled rule"):
                    store.decide(
                        received.trigger_id, policy(mode="auto"), action="accept",
                        assessed_rules={"security": True}, scope="security",
                    )

    def test_inferred_auto_accept_creates_a_policy_bound_mandate_only_for_matched_rule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                received = store.receive(
                    TriggerInput("github", "event-6", "alice", False, {"text": "review security"}),
                    policy(mode="auto"),
                )
                decision = store.decide(
                    received.trigger_id, policy(mode="auto"), action="accept",
                    assessed_rules={"bugfix": False, "security": True},
                    scope="security", reason="security rule matched",
                )
                mandate = store.get_mandate(received.trigger_id)
                self.assertEqual(decision.status, "accepted")
                self.assertIsNotNone(decision.mandate_id)
                self.assertEqual(mandate["policy_version"], "policy-v1")
                self.assertEqual(mandate["strength"], "inferred")
                self.assertEqual(mandate["matched_rules"], ["security"])
                self.assertEqual(mandate["scope"], "security")

    def test_inferred_auto_accept_without_a_matching_rule_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                received = store.receive(
                    TriggerInput("github", "event-7", "alice", False, {}),
                    policy(mode="auto"),
                )
                decision = store.decide(
                    received.trigger_id, policy(mode="auto"), action="accept",
                    assessed_rules={"bugfix": False, "security": False},
                    scope="requested",
                )
                self.assertEqual(decision.status, "ignored")
                self.assertEqual(decision.reason, "no_enabled_rule_matched")
                self.assertIsNone(store.get_mandate(received.trigger_id))

    def test_policy_change_invalidates_old_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                received = store.receive(
                    TriggerInput("github", "event-8", "alice", True, {}),
                    policy(version="policy-v1"),
                )
                decision = store.decide(
                    received.trigger_id, policy(version="policy-v2"),
                    action="accept", scope="requested",
                )
                self.assertEqual(decision.status, "unavailable")
                self.assertEqual(decision.reason, "policy_version_changed")
                self.assertIsNone(store.get_mandate(received.trigger_id))

    def test_explicit_request_can_be_accepted_in_off_mode_but_scope_is_bounded(self) -> None:
        off_policy = policy(mode="off")
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                received = store.receive(
                    TriggerInput("github", "event-9", "alice", True, {}),
                    off_policy,
                )
                with self.assertRaisesRegex(ValueError, "scope is not allowed"):
                    store.decide(received.trigger_id, off_policy, action="accept", scope="admin")
                decision = store.decide(
                    received.trigger_id, off_policy, action="accept", scope="requested",
                )
                self.assertEqual(decision.status, "accepted")
                self.assertEqual(store.get_mandate(received.trigger_id)["strength"], "explicit")

    def test_concurrent_duplicate_delivery_from_separate_connections_is_deduped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "intake.sqlite3"
            event = TriggerInput("github", "same-event", "alice", True, {"text": "same"})

            def receive():
                with TriggerStore(database) as store:
                    return store.receive(event, policy())

            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = list(pool.map(lambda _: receive(), range(2)))
            self.assertEqual(first.trigger_id, second.trigger_id)
            self.assertEqual(sorted([first.duplicate, second.duplicate]), [False, True])

    def test_same_version_with_changed_policy_content_invalidates_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                received = store.receive(
                    TriggerInput("github", "event-11", "alice", False, {}),
                    policy(mode="auto", version="same-version"),
                )
                changed = AdmissionPolicy(
                    "same-version", "observe",
                    {"github": SourcePolicy(
                        allowed_actors=frozenset({"alice"}),
                        enabled_rules=frozenset({"bugfix", "security"}),
                        allowed_scopes=frozenset({"requested", "bugfix", "security"}),
                    )},
                )
                decision = store.decide(
                    received.trigger_id, changed, action="accept",
                    assessed_rules={"bugfix": True, "security": False}, scope="bugfix",
                )
                self.assertEqual(decision.status, "unavailable")
                self.assertEqual(decision.reason, "policy_content_changed")
                self.assertIsNone(store.get_mandate(received.trigger_id))

    def test_mandate_is_immutable_at_the_database_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                received = store.receive(
                    TriggerInput("github", "immutable-event", "alice", True, {}), policy()
                )
                store.decide(received.trigger_id, policy(), action="accept", scope="requested")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "mandates are immutable"):
                    store.connection.execute(
                        "UPDATE mandates SET scope='admin' WHERE trigger_id=?",
                        (received.trigger_id,),
                    )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "mandates are immutable"):
                    store.connection.execute(
                        "DELETE FROM mandates WHERE trigger_id=?", (received.trigger_id,)
                    )
                self.assertEqual(store.get_mandate(received.trigger_id)["scope"], "requested")

    def test_payload_size_is_bounded_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TriggerStore(Path(directory) / "intake.sqlite3") as store:
                with self.assertRaisesRegex(ValueError, "payload exceeds"):
                    store.receive(
                        TriggerInput("github", "event-10", "alice", True, {"text": "x" * 20_000}),
                        policy(),
                    )


if __name__ == "__main__":
    unittest.main()
