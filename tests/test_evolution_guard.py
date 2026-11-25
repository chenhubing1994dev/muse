"""B394: signed self-evolution gate, protected surfaces, versioned rollback, supervisor safety, plugin broker."""
from __future__ import annotations

import hashlib
import json
import secrets
import tempfile
import unittest
from pathlib import Path

try:  # gc.collect() releases SQLite handles before Windows temp cleanup
    import gc
except ImportError:  # restricted sandboxes
    gc = None

from muse.autonomy_safety import KILL_RESET_PHRASE, ResourceLimits, SafetyController, guarded_tick
from muse.evolution_executor import ApprovedCodeChangeExecutor
from muse.evolution_guard import (
    EvolutionVersionLedger,
    PatchSignatureError,
    PatchSignatureGate,
    ProtectedSurfaceViolation,
    build_patch_manifest,
    enforce_protected_surfaces,
    protected_surface_for,
    sign_patch_manifest,
)
from muse.plugin_capability_broker import CapabilityBroker, CapabilityDenied, verify_supply_chain
from muse.plugin_platform import PluginError, PluginRegistry, plugin_signature_payload
from muse.reviewer_attestation import public_key_from_seed, sign_bytes

REVIEWER_SEED = bytes(range(1, 33))     # labelled test seeds only
TEST_SEED = bytes(range(33, 65))
BUILDER_SEED = bytes(range(65, 97))


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Store:
    def __init__(self):
        self.status = "approved"

    def get(self, proposal_id):
        return {"id": proposal_id, "status": self.status}

    def validate_and_promote(self, proposal_id, **_):
        self.status = "implemented"
        return {"status": "implemented"}

    def transition(self, proposal_id, status, **_):
        self.status = status
        return {"status": status}


def _keys():
    return {
        "rk-prod": {"reviewer_id": "creator", "public_key": public_key_from_seed(REVIEWER_SEED),
                    "purpose": "production", "status": "trusted"},
        "rk-test": {"reviewer_id": "sandbox", "public_key": public_key_from_seed(TEST_SEED),
                    "purpose": "ephemeral_test", "status": "trusted"},
        "rk-builder": {"reviewer_id": "builder-1", "public_key": public_key_from_seed(BUILDER_SEED),
                       "purpose": "production", "status": "trusted"},
    }


class EvolutionGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.target = self.root / "src" / "example.py"
        self.target.parent.mkdir()
        self.target.write_text("VALUE = 1\n", encoding="utf-8")
        self.clock = Clock()
        self.closers = []

    def tearDown(self):
        for item in self.closers:
            item.close()
        if gc:
            gc.collect()
        self.tmp.cleanup()

    def _payload(self, *, path="src/example.py", new="VALUE = 2", old="VALUE = 1"):
        file = self.root / path
        return {"kind": "approved_code_change", "_approved": True, "_approval": {"approved_by": "owner"},
                "proposal_id": "p-1", "patches": [{"path": path, "expected_sha256": hashlib.sha256(
                    file.read_bytes()).hexdigest() if file.exists() else "0" * 64, "old_text": old, "new_text": new}]}

    def _sign(self, payload, *, seed=REVIEWER_SEED, key_id="rk-prod", ticket="t-1", issued=None):
        manifest = build_patch_manifest(ticket_id=ticket, proposal_id=payload["proposal_id"], patches=payload["patches"],
                                        nonce=secrets.token_hex(16), issued_at=int(issued or self.clock.now))
        payload["_patch_signature"] = sign_patch_manifest(seed, key_id=key_id, manifest=manifest)
        return payload

    def _gate(self):
        gate = PatchSignatureGate(_keys(), self.root / "receipts.sqlite3", clock=self.clock)
        self.closers.append(gate)
        return gate

    def _executor(self, **kwargs):
        ok = lambda *, phase, **_: {"run_ref": f"run:{phase}", "status": "succeeded"}
        return ApprovedCodeChangeExecutor(self.root, improvement_store=Store(), experiment_bridge=object(),
                                          review_gate=object(), run_tests=kwargs.pop("run_tests", ok),
                                          implementer_id="builder-1", **kwargs)

    # ---------------------------------------------------------------- protected surfaces
    def test_protected_surfaces_are_hard_blocked(self):
        for path in ("docs/AGENT_BUILD_CHARTER.md", "src/muse/review_gate.py", "SRC/MUSE/TASK_QUEUE.PY",
                     "config/reviewer-trust-store.json", "src/muse/autonomy_safety.py", "src/muse/resource_governor.py",
                     "data/progress_audit.log", ".env", "src/muse/evolution_guard.py", "src\\muse\\worker.py"):
            self.assertIsNotNone(protected_surface_for(path), path)
        self.assertIsNone(protected_surface_for("src/muse/video_recipe.py"))
        for bad in ("../outside.py", "/etc/passwd", "C:/x.py", "src/./a.py"):
            with self.assertRaises(ProtectedSurfaceViolation):
                enforce_protected_surfaces([{"path": bad, "old_text": "a", "new_text": "b"}])

    def test_new_text_cannot_introduce_gate_disabling_tokens(self):
        with self.assertRaisesRegex(ProtectedSurfaceViolation, "kill_switch"):
            enforce_protected_surfaces([{"path": "src/a.py", "old_text": "x = 1", "new_text": "kill_switch = None"}])
        with self.assertRaisesRegex(ProtectedSurfaceViolation, "subprocess"):
            enforce_protected_surfaces([{"path": "src/a.py", "old_text": "x", "new_text": "subprocess.run(['rm'])"}])
        # word-boundary aware: retrieval( is not eval(
        enforce_protected_surfaces([{"path": "src/a.py", "old_text": "x", "new_text": "retrieval(1)"}])

    def test_executor_refuses_protected_target_before_any_write(self):
        protected = self.root / "src" / "muse" / "review_gate.py"
        protected.parent.mkdir(parents=True)
        protected.write_text("GATE = True\n", encoding="utf-8")
        executor = self._executor()
        with self.assertRaises(ProtectedSurfaceViolation):
            executor.execute(self._payload(path="src/muse/review_gate.py", old="GATE = True", new="GATE = False"),
                             task_id="t-1")
        self.assertEqual(protected.read_text(encoding="utf-8"), "GATE = True\n")

    # ---------------------------------------------------------------- signature gate
    def test_signed_patch_applies_and_records_version_then_atomic_rollback(self):
        ledger = EvolutionVersionLedger(self.root / "versions.sqlite3", self.root, clock=self.clock)
        self.closers.append(ledger)
        executor = self._executor(patch_guard=self._gate(), version_ledger=ledger, require_signed_patches=True)
        result = executor.execute(self._sign(self._payload()), task_id="t-1")
        self.assertEqual(result["status"], "implemented")
        self.assertEqual(result["patch_signature"]["reviewer_id"], "creator")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(ledger.list_versions()[0]["status"], "applied")
        rolled = ledger.rollback(result["version"], actor="creator")
        self.assertEqual(rolled["status"], "rolled_back")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "VALUE = 1\n")
        with self.assertRaisesRegex(ValueError, "already rolled back"):
            ledger.rollback(result["version"], actor="creator")

    def test_rollback_refuses_concurrent_edit_and_restores_nothing(self):
        ledger = EvolutionVersionLedger(self.root / "versions.sqlite3", self.root, clock=self.clock)
        self.closers.append(ledger)
        other = self.root / "src" / "other.py"
        other.write_text("B = 1\n", encoding="utf-8")
        version = ledger.record(task_id="t", proposal_id="p", files=[
            ("src/example.py", b"VALUE = 0\n", hashlib.sha256(self.target.read_bytes()).hexdigest()),
            ("src/other.py", b"B = 0\n", hashlib.sha256(b"B = 1\n").hexdigest())])
        other.write_text("B = 999  # user edit\n", encoding="utf-8")
        self.assertEqual(ledger.rollback(version, actor="c")["status"], "blocked_concurrent_edit")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "VALUE = 1\n")  # untouched: all-or-nothing

    def test_unsigned_self_signed_test_key_tampered_replayed_expired_are_refused(self):
        gate = self._gate()
        executor = self._executor(patch_guard=gate, require_signed_patches=True)
        with self.assertRaisesRegex(PatchSignatureError, "_patch_signature"):
            executor.execute(self._payload(), task_id="t-1")
        with self.assertRaisesRegex(PatchSignatureError, "own patch"):
            executor.execute(self._sign(self._payload(), seed=BUILDER_SEED, key_id="rk-builder"), task_id="t-1")
        with self.assertRaisesRegex(PatchSignatureError, "ephemeral"):
            executor.execute(self._sign(self._payload(), seed=TEST_SEED, key_id="rk-test"), task_id="t-1")
        tampered = self._sign(self._payload())
        tampered["patches"][0]["new_text"] = "VALUE = 666"
        with self.assertRaisesRegex(PatchSignatureError, "verification failed"):
            executor.execute(tampered, task_id="t-1")
        other_ticket = self._sign(self._payload(), ticket="t-other")
        with self.assertRaisesRegex(PatchSignatureError, "verification failed"):
            executor.execute(other_ticket, task_id="t-1")
        expired = self._sign(self._payload(), issued=self.clock.now - 3 * 86400)
        with self.assertRaisesRegex(PatchSignatureError, "expired"):
            executor.execute(expired, task_id="t-1")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "VALUE = 1\n")
        signed = self._sign(self._payload())
        replay = json.loads(json.dumps(signed))
        self.assertEqual(executor.execute(signed, task_id="t-1")["status"], "implemented")
        self.target.write_text("VALUE = 1\n", encoding="utf-8")
        executor.improvement_store.status = "approved"
        replay["patches"][0]["expected_sha256"] = hashlib.sha256(self.target.read_bytes()).hexdigest()
        with self.assertRaisesRegex(PatchSignatureError, "replay|verification"):
            executor.execute(replay, task_id="t-1")

    def test_require_signed_without_gate_fails_closed_and_empty_trust_store_refused(self):
        with self.assertRaises(ValueError):
            self._executor(require_signed_patches=True)
        with self.assertRaises(PatchSignatureError):
            PatchSignatureGate({}, self.root / "r.sqlite3")


class SupervisorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.clock = Clock(1000.0)
        self.sample = {"cpu_percent": 10, "memory_percent": 10, "gpu_free_mib": 9000, "queue_depth": 0}
        self.controller = SafetyController(self.root / "safety.sqlite3", clock=self.clock,
                                           resource_probe=lambda: dict(self.sample),
                                           limits=ResourceLimits(min_free_gpu_mib=7884, base_backoff_seconds=10,
                                                                 max_backoff_seconds=40),
                                           breaker_threshold=2, breaker_cooldown_seconds=60,
                                           kill_switch_file=self.root / "KILL_SWITCH")
        self.ticks = 0

    def tearDown(self):
        self.controller.close()
        if gc:
            gc.collect()
        self.tmp.cleanup()

    def tick(self):
        self.ticks += 1
        return {"ok": True}

    def test_pause_and_resume(self):
        self.controller.pause(actor="creator", reason="night")
        self.assertFalse(guarded_tick(self.tick, self.controller)["ran"])
        self.controller.resume(actor="creator")
        self.assertTrue(guarded_tick(self.tick, self.controller)["ok"])
        self.assertEqual(self.ticks, 1)

    def test_backpressure_exponential_backoff_and_release(self):
        self.sample["gpu_free_mib"] = 2000
        first = self.controller.check()
        self.assertEqual((first["reason"], first["retry_at"]), ("backpressure", 1010.0))
        self.assertEqual(self.controller.check()["reason"], "backpressure_backoff")
        self.clock.now = 1010.0
        self.assertEqual(self.controller.check()["retry_at"], 1030.0)   # doubled
        self.clock.now = 1030.0
        self.assertEqual(self.controller.check()["retry_at"], 1070.0)   # capped at 40
        self.clock.now = 1070.0
        self.sample["gpu_free_mib"] = 9000
        self.assertTrue(guarded_tick(self.tick, self.controller)["ok"])
        self.sample["cpu_percent"] = 99
        self.assertEqual(self.controller.check()["breaches"], ["cpu"])

    def test_failing_probe_is_backpressure(self):
        self.controller.resource_probe = lambda: 1 / 0
        self.assertEqual(self.controller.check()["reason"], "backpressure")

    def test_circuit_breaker_open_half_open_close_and_doubling(self):
        def boom():
            raise RuntimeError("model down")
        guarded_tick(boom, self.controller)
        guarded_tick(boom, self.controller)
        self.assertEqual(self.controller.check()["reason"], "circuit_open")
        self.clock.now += 60
        self.assertEqual(self.controller.check()["reason"], "half_open_trial")
        self.assertEqual(self.controller.check()["reason"], "half_open_trial_in_progress")
        self.controller.record_result(False, error="still down")
        self.assertEqual(self.controller.status()["breaker_cooldown"], 120)
        self.clock.now += 120
        self.assertTrue(guarded_tick(self.tick, self.controller)["ok"])
        self.assertEqual(self.controller.status()["consecutive_failures"], 0)

    def test_kill_switch_latch_file_and_explicit_reset(self):
        self.controller.engage_kill_switch(actor="creator", reason="panic")
        self.assertEqual(self.controller.check()["reason"], "kill_switch")
        with self.assertRaises(PermissionError):
            self.controller.resume(actor="creator")
        with self.assertRaises(PermissionError):
            self.controller.reset_kill_switch(actor="creator", confirmation="yes")
        self.controller.reset_kill_switch(actor="creator", confirmation=KILL_RESET_PHRASE)
        self.assertEqual(self.controller.check()["reason"], "paused")   # reset never auto-resumes
        self.controller.resume(actor="creator")
        (self.root / "KILL_SWITCH").write_text("stop", encoding="utf-8")
        self.assertEqual(self.controller.check()["reason"], "kill_switch")
        with self.assertRaises(PermissionError):
            self.controller.reset_kill_switch(actor="creator", confirmation=KILL_RESET_PHRASE)
        self.assertEqual(self.ticks, 0)
        events = [e["event"] for e in self.controller.events()]
        self.assertIn("kill_switch_engaged", events)


class CapabilityBrokerTests(unittest.TestCase):
    MANIFEST = {"schema_version": 1, "id": "muse.subtitle-helper", "name": "Subtitle helper", "version": "1.0.0",
                "description": "Aligns subtitles", "publisher": "acme", "dependencies": {},
                "capabilities": [{"id": "subtitle.align", "description": "align", "risk": "research",
                                  "permissions": ["commander.enqueue", "research.read", "network.public_research"]}]}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.registry = PluginRegistry(self.root / "plugins.sqlite3")
        self.clock = Clock(5000.0)
        self.broker = CapabilityBroker(self.registry, self.root / "broker.sqlite3", clock=self.clock, ttl_seconds=60)
        self.artifact = self.root / "bundle.zip"
        self.artifact.write_bytes(b"PK-fake-bundle")

    def tearDown(self):
        self.broker.close()
        self.registry.close()
        if gc:
            gc.collect()
        self.tmp.cleanup()

    def _install(self, *, sign=True, enable=True):
        self.registry.trust_publisher_key("pk-acme", "acme", public_key_from_seed(REVIEWER_SEED), trusted_by="creator")
        digest = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        if sign:
            signature = sign_bytes(REVIEWER_SEED, plugin_signature_payload(self.MANIFEST, digest))
            self.registry.discover_signed(self.MANIFEST, self.artifact, key_id="pk-acme", signature=signature)
        else:
            self.registry.discover(self.MANIFEST)
        self.registry.approve("muse.subtitle-helper", approved_by="creator")
        if enable:
            self.registry.enable("muse.subtitle-helper", enabled_by="creator")

    def test_forged_publisher_signature_rejected(self):
        self.registry.trust_publisher_key("pk-acme", "acme", public_key_from_seed(REVIEWER_SEED), trusted_by="creator")
        bad = sign_bytes(TEST_SEED, plugin_signature_payload(self.MANIFEST, "0" * 64))
        with self.assertRaises(PluginError):
            self.registry.discover_signed(self.MANIFEST, self.artifact, key_id="pk-acme", signature=bad)

    def test_default_deny_until_signed_approved_enabled(self):
        with self.assertRaises(CapabilityDenied):
            self.broker.issue_grant("muse.subtitle-helper", "subtitle.align", ["research.read"], issued_by="c")
        self._install(sign=False)
        with self.assertRaisesRegex(CapabilityDenied, "signature"):
            self.broker.issue_grant("muse.subtitle-helper", "subtitle.align", ["research.read"], issued_by="c")

    def test_grant_scope_budget_expiry_revocation(self):
        self._install()
        with self.assertRaisesRegex(CapabilityDenied, "not declared"):
            self.broker.issue_grant("muse.subtitle-helper", "subtitle.align", ["filesystem.project_write"], issued_by="c")
        grant = self.broker.issue_grant("muse.subtitle-helper", "subtitle.align", ["research.read",
                                        "network.public_research"], issued_by="c")
        self.assertTrue(self.broker.authorize(grant["token"], "research.read", usage={"max_network_requests": 10})["allowed"])
        with self.assertRaisesRegex(CapabilityDenied, "budget"):
            self.broker.authorize(grant["token"], "network.public_research", usage={"max_network_requests": 7})
        with self.assertRaisesRegex(CapabilityDenied, "not granted"):
            self.broker.authorize(grant["token"], "commander.enqueue")
        with self.assertRaisesRegex(CapabilityDenied, "invalid usage"):
            self.broker.authorize(grant["token"], "research.read", usage={"memory_mb": 1})
        self.registry.disable("muse.subtitle-helper", disabled_by="c")
        with self.assertRaisesRegex(CapabilityDenied, "enabled"):
            self.broker.authorize(grant["token"], "research.read")
        self.registry.enable("muse.subtitle-helper", enabled_by="c")
        self.clock.now += 61
        with self.assertRaisesRegex(CapabilityDenied, "expired"):
            self.broker.authorize(grant["token"], "research.read")
        second = self.broker.issue_grant("muse.subtitle-helper", "subtitle.align", ["research.read"], issued_by="c")
        self.assertEqual(self.broker.revoke_plugin("muse.subtitle-helper", actor="c"), 2)
        with self.assertRaisesRegex(CapabilityDenied, "revoked"):
            self.broker.authorize(second["token"], "research.read")
        with self.assertRaises(CapabilityDenied):
            self.broker.authorize("x" * 43, "research.read")

    def test_supply_chain_lock(self):
        bundle = self.root / "bundle"
        (bundle / "lib").mkdir(parents=True)
        (bundle / "lib" / "a.txt").write_bytes(b"alpha")
        entry = {"path": "lib/a.txt", "sha256": hashlib.sha256(b"alpha").hexdigest(), "size": 5}
        lock = {"schema": "muse.plugin.lock.v1", "artifacts": [entry]}
        self.assertEqual(verify_supply_chain(lock, bundle)["artifacts"], 1)
        (bundle / "lib" / "a.txt").write_bytes(b"alphX")
        with self.assertRaisesRegex(PluginError, "SHA-256"):
            verify_supply_chain(lock, bundle)
        (bundle / "lib" / "a.txt").write_bytes(b"alpha")
        (bundle / "evil.py").write_bytes(b"print(1)")
        with self.assertRaisesRegex(PluginError, "not pinned"):
            verify_supply_chain(lock, bundle)
        for bad in ("../x", "/abs", "C:/x"):
            with self.assertRaisesRegex(PluginError, "traversal"):
                verify_supply_chain({"schema": "muse.plugin.lock.v1",
                                     "artifacts": [dict(entry, path=bad)]}, bundle)


if __name__ == "__main__":
    unittest.main()
