from __future__ import annotations

import json
import hashlib
import io
from contextlib import redirect_stderr
import tempfile
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pathlib import Path

from muse.plugin_platform import (
    PluginError, PluginRegistry, commander_task_queue_adapter, main as plugin_main,
    plugin_signature_payload, sync_plugin_capabilities, validate_approved_plugin_ticket, validate_manifest,
)
from muse.task_queue import TaskQueue
from muse.capabilities import CapabilityRegistry


def manifest(plugin_id: str = "research.cross-check", *, version: str = "1.0.0",
             dependencies: dict[str, str] | None = None) -> dict:
    return {
        "schema_version": 1,
        "id": plugin_id,
        "name": "Research Cross-check",
        "version": version,
        "description": "Collects sources and counter-evidence for Commander review.",
        "publisher": "Muse",
        "dependencies": dependencies or {},
        "capabilities": [{
            "id": "research",
            "description": "Research and cross-check a hypothesis.",
            "risk": "research",
            "permissions": ["research.read", "network.public_research", "commander.enqueue"],
        }],
    }


class PluginManifestTests(unittest.TestCase):
    def test_manifest_is_canonical_and_order_independent(self) -> None:
        first = validate_manifest(manifest())
        second = validate_manifest({**manifest(), "capabilities": list(reversed(manifest()["capabilities"]))})
        self.assertEqual(first, second)

    def test_rejects_unknown_fields_and_non_semver(self) -> None:
        value = manifest()
        value["entrypoint"] = "module:run"
        with self.assertRaises(PluginError):
            validate_manifest(value)
        value = manifest(version="latest")
        with self.assertRaises(PluginError):
            validate_manifest(value)

    def test_rejects_shell_permission_and_capability_without_commander(self) -> None:
        value = manifest()
        value["capabilities"][0]["permissions"] = ["system.shell", "commander.enqueue"]
        with self.assertRaises(PluginError):
            validate_manifest(value)
        value = manifest()
        value["capabilities"][0]["permissions"] = ["research.read"]
        with self.assertRaises(PluginError):
            validate_manifest(value)

    def test_rejects_duplicate_capabilities_and_self_dependency(self) -> None:
        value = manifest()
        value["capabilities"].append(dict(value["capabilities"][0]))
        with self.assertRaises(PluginError):
            validate_manifest(value)
        with self.assertRaises(PluginError):
            validate_manifest(manifest(dependencies={"research.cross-check": "1.0.0"}))

    def test_risk_class_is_a_permission_ceiling(self) -> None:
        value = manifest()
        value["capabilities"][0]["risk"] = "read_only"
        value["capabilities"][0]["permissions"] = ["research.read", "network.public_research", "commander.enqueue"]
        with self.assertRaisesRegex(PluginError, "exceed the declared risk class"):
            validate_manifest(value)
        value["capabilities"][0]["risk"] = "research"
        value["capabilities"][0]["permissions"] = ["research.read", "gpu.request", "commander.enqueue"]
        with self.assertRaisesRegex(PluginError, "exceed the declared risk class"):
            validate_manifest(value)

class PluginTrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = PluginRegistry(Path(self.temp.name) / "plugins.sqlite3")

    def tearDown(self) -> None:
        self.registry.close()
        self.temp.cleanup()

    def test_signed_bundle_binds_manifest_artifact_and_trusted_publisher_key(self) -> None:
        self.assertTrue(hasattr(self.registry, "trust_publisher_key"))
        self.assertTrue(hasattr(self.registry, "discover_signed"))
        from muse.plugin_platform import plugin_signature_payload

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key(
            "muse-release-2026", "Muse", public_key, trusted_by="owner",
        )
        artifact = Path(self.temp.name) / "research-plugin.whl"
        artifact.write_bytes(b"signed plugin artifact bytes")
        artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        payload = plugin_signature_payload(manifest(), artifact_digest)
        self.assertTrue(payload.startswith(b"MUSE-PLUGIN-BUNDLE-SIGNATURE-V1\x00"))
        self.assertIn(b"\x00artifact-sha256=", payload)
        signature = private_key.sign(payload)
        result = self.registry.discover_signed(
            manifest(), artifact, key_id="muse-release-2026", signature=signature,
        )
        self.assertEqual(result["status"], "discovered")
        self.assertEqual(result["signature"]["status"], "verified")
        self.assertEqual(result["signature"]["artifact_sha256"], artifact_digest)
        self.assertEqual(self.registry.get("research.cross-check").get("signature", {}).get("status"), "verified")
        self.assertEqual(self.registry.list_plugins()[0].get("signature", {}).get("status"), "verified")
        self.assertFalse(result["execution_enabled"])

    def test_signed_bundle_rejects_tampering_unknown_key_and_publisher_mismatch(self) -> None:
        self.assertTrue(hasattr(self.registry, "trust_publisher_key"))
        self.assertTrue(hasattr(self.registry, "discover_signed"))
        from muse.plugin_platform import plugin_signature_payload

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key("muse-release", "Muse", public_key, trusted_by="owner")
        artifact = Path(self.temp.name) / "plugin.whl"
        artifact.write_bytes(b"original artifact")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature = private_key.sign(plugin_signature_payload(manifest(), digest))
        artifact.write_bytes(b"tampered artifact")
        with self.assertRaises(PluginError):
            self.registry.discover_signed(manifest(), artifact, key_id="muse-release", signature=signature)
        artifact.write_bytes(b"original artifact")
        with self.assertRaises(PluginError):
            self.registry.discover_signed(manifest(), artifact, key_id="unknown-key", signature=signature)
        wrong_publisher = {**manifest(), "publisher": "Untrusted Publisher"}
        with self.assertRaises(PluginError):
            self.registry.discover_signed(wrong_publisher, artifact, key_id="muse-release", signature=signature)

    def test_revoking_publisher_key_invalidates_previously_verified_bundle(self) -> None:
        self.assertTrue(hasattr(self.registry, "revoke_publisher_key"))
        self.assertTrue(hasattr(self.registry, "discover_signed"))
        from muse.plugin_platform import plugin_signature_payload

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key("muse-release", "Muse", public_key, trusted_by="owner")
        artifact = Path(self.temp.name) / "plugin.whl"
        artifact.write_bytes(b"signed artifact")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature = private_key.sign(plugin_signature_payload(manifest(), digest))
        self.registry.discover_signed(manifest(), artifact, key_id="muse-release", signature=signature)
        self.registry.revoke_publisher_key("muse-release", revoked_by="owner")
        self.assertEqual(self.registry.get_signature("research.cross-check")["status"], "revoked")
        self.assertEqual(self.registry.get("research.cross-check").get("signature", {}).get("status"), "revoked")

    def test_revoking_publisher_key_disables_all_plugins_signed_by_that_key(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key("muse-release", "Muse", public_key, trusted_by="owner")
        artifact = Path(self.temp.name) / "enabled-plugin.whl"
        artifact.write_bytes(b"signed artifact")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature = private_key.sign(plugin_signature_payload(manifest(), digest))
        self.registry.discover_signed(manifest(), artifact, key_id="muse-release", signature=signature)
        self.registry.approve("research.cross-check", approved_by="owner")
        self.registry.enable("research.cross-check", enabled_by="owner")

        self.registry.revoke_publisher_key("muse-release", revoked_by="owner")

        self.assertEqual(self.registry.get("research.cross-check")["status"], "disabled")
        self.assertEqual(self.registry.get_signature("research.cross-check")["status"], "revoked")

    def test_revoked_publisher_cannot_submit_new_commander_capability_requests(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key("muse-release", "Muse", public_key, trusted_by="owner")
        artifact = Path(self.temp.name) / "revoked-plugin.whl"
        artifact.write_bytes(b"signed artifact")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature = private_key.sign(plugin_signature_payload(manifest(), digest))
        self.registry.discover_signed(manifest(), artifact, key_id="muse-release", signature=signature)
        self.registry.approve("research.cross-check", approved_by="owner")
        self.registry.enable("research.cross-check", enabled_by="owner")
        self.registry.revoke_publisher_key("muse-release", revoked_by="owner")

        enqueued = []
        with self.assertRaisesRegex(PluginError, "publisher signing key has been revoked"):
            self.registry.commander_request(
                "research.cross-check", "research", {"topic": "must-not-queue"},
                enqueue=lambda request, idempotency_key: enqueued.append((request, idempotency_key)),
                requested_by="owner",
            )
        self.assertEqual(enqueued, [])

    def test_trusted_key_id_is_immutable_and_same_version_artifact_cannot_be_replaced(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key("muse-release", "Muse", public_key, trusted_by="owner")
        with self.assertRaises(PluginError):
            self.registry.trust_publisher_key(
                "muse-release", "Muse", Ed25519PrivateKey.generate().public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw,
                ), trusted_by="owner",
            )

        from muse.plugin_platform import plugin_signature_payload
        artifact = Path(self.temp.name) / "plugin.whl"
        artifact.write_bytes(b"release one")
        first_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        first_signature = private_key.sign(plugin_signature_payload(manifest(), first_digest))
        self.registry.discover_signed(manifest(), artifact, key_id="muse-release", signature=first_signature)
        artifact.write_bytes(b"release two")
        second_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        second_signature = private_key.sign(plugin_signature_payload(manifest(), second_digest))
        with self.assertRaisesRegex(PluginError, "artifact changed without a plugin version"):
            self.registry.discover_signed(manifest(), artifact, key_id="muse-release", signature=second_signature)

    def test_cli_pins_publisher_key_discovers_signed_bundle_and_revokes_key(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        database = Path(self.temp.name) / "cli-plugins.sqlite3"
        capability_database = Path(self.temp.name) / "cli-capabilities.sqlite3"
        public_key_file = Path(self.temp.name) / "publisher.pub"
        public_key_file.write_bytes(public_key)
        manifest_file = Path(self.temp.name) / "manifest.json"
        manifest_file.write_text(json.dumps(manifest()), encoding="utf-8")
        artifact = Path(self.temp.name) / "cli-plugin.whl"
        artifact.write_bytes(b"cli signed plugin")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature_file = Path(self.temp.name) / "plugin.sig"
        signature_file.write_bytes(private_key.sign(plugin_signature_payload(manifest(), digest)))

        def invoke(args: list[str]) -> int:
            try:
                return plugin_main(args)
            except SystemExit as exc:
                return int(exc.code)

        self.assertEqual(invoke([
            "trust-key", "muse-cli-release", "--publisher", "Muse",
            "--public-key-file", str(public_key_file), "--database", str(database),
            "--trusted-by", "owner",
        ]), 0)
        self.assertEqual(invoke([
            "discover-signed", str(manifest_file), str(artifact), "--key-id", "muse-cli-release",
            "--signature-file", str(signature_file), "--database", str(database),
        ]), 0)
        with PluginRegistry(database) as registry, CapabilityRegistry(capability_database) as catalog:
            sync_plugin_capabilities(registry, catalog)

        self.assertEqual(invoke([
            "revoke-key", "muse-cli-release", "--database", str(database),
            "--capability-db", str(capability_database), "--revoked-by", "owner",
        ]), 0)
        with PluginRegistry(database) as registry:
            self.assertEqual(registry.get_signature("research.cross-check")["status"], "revoked")
        with CapabilityRegistry(capability_database) as catalog:
            self.assertEqual(catalog.get("plugin.research.cross-check.research")["status"], "disabled")

    def test_cli_revoke_key_synchronizes_shared_capability_catalog(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key("muse-release-sync", "Muse", public_key, trusted_by="owner")
        artifact = Path(self.temp.name) / "sync-plugin.whl"
        artifact.write_bytes(b"signed sync artifact")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature = private_key.sign(plugin_signature_payload(manifest(), digest))
        self.registry.discover_signed(manifest(), artifact, key_id="muse-release-sync", signature=signature)
        self.registry.approve("research.cross-check", approved_by="owner")
        self.registry.enable("research.cross-check", enabled_by="owner")

        capability_database = Path(self.temp.name) / "capabilities.sqlite3"
        with CapabilityRegistry(capability_database) as catalog:
            sync_plugin_capabilities(self.registry, catalog)
            self.assertEqual(catalog.get("plugin.research.cross-check.research")["status"], "needs_approval")

        result = plugin_main([
            "revoke-key", "muse-release-sync", "--database", str(Path(self.temp.name) / "plugins.sqlite3"),
            "--capability-db", str(capability_database), "--revoked-by", "owner",
        ])
        self.assertEqual(result, 0)
        with CapabilityRegistry(capability_database) as catalog:
            item = catalog.get("plugin.research.cross-check.research")
            self.assertEqual(item["status"], "disabled")
            self.assertEqual(item["metadata"]["plugin_status"], "disabled")
            self.assertFalse(item["metadata"]["execution_enabled"])
            self.assertIsNone(item["executable"])

    def test_cli_sync_capabilities_repairs_catalog_after_separate_revocation(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key("muse-release-repair", "Muse", public_key, trusted_by="owner")
        artifact = Path(self.temp.name) / "repair-plugin.whl"
        artifact.write_bytes(b"signed repair artifact")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature = private_key.sign(plugin_signature_payload(manifest(), digest))
        self.registry.discover_signed(manifest(), artifact, key_id="muse-release-repair", signature=signature)
        self.registry.approve("research.cross-check", approved_by="owner")
        self.registry.enable("research.cross-check", enabled_by="owner")

        capability_database = Path(self.temp.name) / "repair-capabilities.sqlite3"
        with CapabilityRegistry(capability_database) as catalog:
            sync_plugin_capabilities(self.registry, catalog)
        self.registry.revoke_publisher_key("muse-release-repair", revoked_by="owner")
        with CapabilityRegistry(capability_database) as catalog:
            self.assertEqual(catalog.get("plugin.research.cross-check.research")["status"], "needs_approval")

        result = plugin_main([
            "sync-capabilities", "--database", str(Path(self.temp.name) / "plugins.sqlite3"),
            "--capability-db", str(capability_database),
        ])
        self.assertEqual(result, 0)
        with CapabilityRegistry(capability_database) as catalog:
            item = catalog.get("plugin.research.cross-check.research")
            self.assertEqual(item["status"], "disabled")
            self.assertEqual(item["metadata"]["plugin_status"], "disabled")


    def test_cli_reports_committed_revocation_when_catalog_sync_fails_and_recovers(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        self.registry.trust_publisher_key("muse-release-failure", "Muse", public_key, trusted_by="owner")
        artifact = Path(self.temp.name) / "failure-plugin.whl"
        artifact.write_bytes(b"signed failure artifact")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature = private_key.sign(plugin_signature_payload(manifest(), digest))
        self.registry.discover_signed(manifest(), artifact, key_id="muse-release-failure", signature=signature)
        self.registry.approve("research.cross-check", approved_by="owner")
        self.registry.enable("research.cross-check", enabled_by="owner")

        capability_database = Path(self.temp.name) / "failure-capabilities.sqlite3"
        with CapabilityRegistry(capability_database) as catalog:
            sync_plugin_capabilities(self.registry, catalog)

        arguments = [
            "revoke-key", "muse-release-failure", "--database", str(Path(self.temp.name) / "plugins.sqlite3"),
            "--capability-db", str(capability_database), "--revoked-by", "owner",
        ]
        error_output = io.StringIO()
        with patch("muse.plugin_platform.sync_plugin_capabilities", side_effect=RuntimeError("simulated sync failure")):
            with redirect_stderr(error_output):
                with self.assertRaises(SystemExit) as raised:
                    plugin_main(arguments)
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("publisher key is revoked", error_output.getvalue())
        self.assertEqual(self.registry.get_signature("research.cross-check")["status"], "revoked")
        with CapabilityRegistry(capability_database) as catalog:
            self.assertEqual(catalog.get("plugin.research.cross-check.research")["status"], "needs_approval")

        self.assertEqual(plugin_main([
            "sync-capabilities", "--database", str(Path(self.temp.name) / "plugins.sqlite3"),
            "--capability-db", str(capability_database),
        ]), 0)
        with CapabilityRegistry(capability_database) as catalog:
            item = catalog.get("plugin.research.cross-check.research")
            self.assertEqual(item["status"], "disabled")
            self.assertFalse(item["metadata"]["execution_enabled"])


class PluginRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.registry = PluginRegistry(Path(self.temp.name) / "plugins.sqlite3")

    def tearDown(self) -> None:
        self.registry.close()
        self.temp.cleanup()

    def approve_enable(self, value: dict) -> None:
        self.registry.discover(value)
        self.registry.approve(value["id"], approved_by="owner")
        self.registry.enable(value["id"], enabled_by="owner")

    def approve_enable_signed(self, value: dict, *, enable: bool = True) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )
        key_id = f"test-{value['id']}"
        self.registry.trust_publisher_key(key_id, value["publisher"], public_key, trusted_by="owner")
        artifact = Path(self.temp.name) / f"{value['id'].replace('.', '-')}.bundle"
        artifact.write_bytes(b"test plugin bundle bytes")
        artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        signature = private_key.sign(plugin_signature_payload(value, artifact_digest))
        self.registry.discover_signed(value, artifact, key_id=key_id, signature=signature)
        self.registry.approve(value["id"], approved_by="owner")
        if enable:
            self.registry.enable(value["id"], enabled_by="owner")

    def test_discovery_requires_approval_and_manifest_change_resets_approval(self) -> None:
        record = self.registry.discover(manifest())
        self.assertEqual(record["status"], "discovered")
        self.assertEqual(record.get("signature", {}).get("status"), "unsigned")
        with self.assertRaises(PluginError):
            self.registry.enable("research.cross-check", enabled_by="owner")
        self.registry.approve("research.cross-check", approved_by="owner")
        changed = manifest(version="1.0.1")
        record = self.registry.discover(changed)
        self.assertEqual(record["status"], "discovered")
        self.assertIsNone(record["approved_by"])

    def test_dependency_must_be_enabled_at_exact_version(self) -> None:
        dependent = manifest("research.pipeline", dependencies={"research.core": "1.2.0"})
        self.registry.discover(dependent)
        self.registry.approve("research.pipeline", approved_by="owner")
        with self.assertRaises(PluginError):
            self.registry.enable("research.pipeline", enabled_by="owner")
        self.registry.discover(manifest("research.core", version="1.1.0"))
        self.registry.approve("research.core", approved_by="owner")
        self.registry.enable("research.core", enabled_by="owner")
        with self.assertRaises(PluginError):
            self.registry.enable("research.pipeline", enabled_by="owner")
        self.registry.discover(manifest("research.core", version="1.2.0"))
        self.registry.approve("research.core", approved_by="owner")
        self.registry.enable("research.core", enabled_by="owner")
        self.registry.enable("research.pipeline", enabled_by="owner")

    def test_capability_is_queued_to_commander_and_never_executed_locally(self) -> None:
        self.approve_enable_signed(manifest())
        calls = []
        result = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "test"},
            enqueue=lambda request, idempotency_key: calls.append((request, idempotency_key)) or {"task_id": "a" * 32},
            requested_by="owner",
        )
        self.assertEqual(result["status"], "queued_for_commander_approval")
        self.assertEqual(len(calls), 1)
        request = calls[0][0]
        self.assertTrue(request["approval_required"])
        self.assertEqual(request["action_class"], "research")
        self.assertEqual(request["publisher_signature"]["status"], "verified")
        self.assertEqual(len(request["publisher_signature"]["artifact_sha256"]), 64)
        self.assertEqual(len(request["publisher_signature"]["signature_sha256"]), 64)
        self.assertEqual(request["payload"], {"topic": "test"})
        budget = request["proposed_resource_budget"]
        self.assertFalse(budget["runtime_enforced"])
        self.assertEqual(budget["limits"]["max_network_requests"], 16)
        self.assertFalse(budget["limits"]["gpu_allowed"])
        self.assertFalse(request["execution_enabled"])

    def test_trusted_adapter_creates_commander_approval_ticket_idempotently(self) -> None:
        self.approve_enable_signed(manifest())
        task_db = Path(self.temp.name) / "commander.sqlite3"
        enqueue = commander_task_queue_adapter(task_db)
        first = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "alpha"},
            enqueue=enqueue, requested_by="owner",
        )
        repeated = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "alpha"},
            enqueue=enqueue, requested_by="owner",
        )
        distinct = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "beta"},
            enqueue=enqueue, requested_by="owner",
        )
        first_id = first["commander_result"]["task_id"]
        self.assertLess(len(first["idempotency_key"]), 200)
        self.assertEqual(first_id, repeated["commander_result"]["task_id"])
        self.assertNotEqual(first_id, distinct["commander_result"]["task_id"])
        with TaskQueue(task_db) as queue:
            task = queue.get(first_id)
        self.assertEqual(task["status"], "waiting_approval")
        self.assertEqual(task["payload"]["kind"], "plugin_capability_request")
        self.assertFalse(task["payload"]["execution_enabled"])
        self.assertTrue(task["payload"]["execution_adapter_required"])
        signed_request = task["payload"]["plugin_capability_request"]
        self.assertEqual(signed_request["publisher_signature"]["artifact_sha256"], first["request"]["publisher_signature"]["artifact_sha256"])

    def test_approved_ticket_is_revalidated_against_human_event_and_current_plugin_state(self) -> None:
        self.approve_enable_signed(manifest())
        task_db = Path(self.temp.name) / "commander.sqlite3"
        enqueue = commander_task_queue_adapter(task_db)
        request = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "approval gate"},
            enqueue=enqueue, requested_by="owner",
        )
        task_id = request["commander_result"]["task_id"]
        with self.assertRaisesRegex(PluginError, "not been explicitly approved"):
            validate_approved_plugin_ticket(self.registry.database, task_db, task_id)

        with TaskQueue(task_db) as queue:
            queue.approve(task_id, approved_by="reviewer")
        validated = validate_approved_plugin_ticket(self.registry.database, task_db, task_id)
        self.assertEqual(validated["status"], "approved_for_worker_review")
        self.assertEqual(validated["approval"]["approved_by"], "reviewer")
        self.assertFalse(validated["execution_enabled"])
        self.assertFalse(validated["runtime_adapter_registered"])

        self.registry.disable("research.cross-check", disabled_by="owner")
        with self.assertRaisesRegex(PluginError, "no longer enabled"):
            validate_approved_plugin_ticket(self.registry.database, task_db, task_id)

    def test_running_plugin_ticket_requires_matching_live_worker_lease_and_claim_audit(self) -> None:
        self.approve_enable_signed(manifest())
        task_db = Path(self.temp.name) / "commander.sqlite3"
        request = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "worker lease binding"},
            enqueue=commander_task_queue_adapter(task_db), requested_by="owner",
        )
        task_id = request["commander_result"]["task_id"]
        with TaskQueue(task_db) as queue:
            queue.approve(task_id, approved_by="reviewer")
            claimed = queue.claim_next(worker_id="plugin-worker", lease_seconds=300, task_id=task_id)
            self.assertEqual(claimed["status"], "running")
        with self.assertRaisesRegex(PluginError, "current Worker lease owner"):
            validate_approved_plugin_ticket(self.registry.database, task_db, task_id)
        with self.assertRaisesRegex(PluginError, "current Worker lease owner"):
            validate_approved_plugin_ticket(self.registry.database, task_db, task_id, worker_id="other-worker")
        validated = validate_approved_plugin_ticket(
            self.registry.database, task_db, task_id, worker_id="plugin-worker",
        )
        self.assertEqual(validated["status"], "approved_for_worker_review")
        self.assertFalse(validated["execution_enabled"])

        with TaskQueue(task_db) as queue:
            queue._connection.execute(
                "DELETE FROM task_events WHERE task_id = ? AND event_type = ?",
                (task_id, "assignment.claimed"),
            )
            queue._connection.commit()
        with self.assertRaisesRegex(PluginError, "matching Worker claim audit event"):
            validate_approved_plugin_ticket(
                self.registry.database, task_db, task_id, worker_id="plugin-worker",
            )

    def test_running_plugin_ticket_rejects_lease_extension_without_matching_heartbeat_audit(self) -> None:
        self.approve_enable_signed(manifest())
        task_db = Path(self.temp.name) / "commander.sqlite3"
        request = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "lease audit binding"},
            enqueue=commander_task_queue_adapter(task_db), requested_by="owner",
        )
        task_id = request["commander_result"]["task_id"]
        with TaskQueue(task_db) as queue:
            queue.approve(task_id, approved_by="reviewer")
            queue.claim_next(worker_id="plugin-worker", lease_seconds=300, task_id=task_id)
            renewed = queue.heartbeat(task_id, worker_id="plugin-worker", lease_seconds=600)
            self.assertIsNotNone(renewed)
        validated = validate_approved_plugin_ticket(
            self.registry.database, task_db, task_id, worker_id="plugin-worker",
        )
        self.assertFalse(validated["execution_enabled"])

        with TaskQueue(task_db) as queue:
            queue._connection.execute(
                "UPDATE tasks SET lease_expires_at = ? WHERE id = ?",
                ("2999-01-01T00:00:00+00:00", task_id),
            )
            queue._connection.commit()
        with self.assertRaisesRegex(PluginError, "lease expiry does not match the latest lease audit"):
            validate_approved_plugin_ticket(
                self.registry.database, task_db, task_id, worker_id="plugin-worker",
            )

    def test_running_plugin_ticket_rejects_expired_worker_lease(self) -> None:
        self.approve_enable_signed(manifest())
        task_db = Path(self.temp.name) / "commander.sqlite3"
        request = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "expired worker lease"},
            enqueue=commander_task_queue_adapter(task_db), requested_by="owner",
        )
        task_id = request["commander_result"]["task_id"]
        with TaskQueue(task_db) as queue:
            queue.approve(task_id, approved_by="reviewer")
            queue.claim_next(worker_id="plugin-worker", lease_seconds=300, task_id=task_id)
            queue._connection.execute(
                "UPDATE tasks SET lease_expires_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", task_id),
            )
            queue._connection.commit()
        with self.assertRaisesRegex(PluginError, "lease has expired"):
            validate_approved_plugin_ticket(
                self.registry.database, task_db, task_id, worker_id="plugin-worker",
            )

    def test_approved_ticket_rejects_unbounded_integer_timestamp_as_plugin_error(self) -> None:
        self.approve_enable_signed(manifest())
        task_db = Path(self.temp.name) / "commander.sqlite3"
        request = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "timestamp bounds"},
            enqueue=commander_task_queue_adapter(task_db), requested_by="owner",
        )
        task_id = request["commander_result"]["task_id"]
        with TaskQueue(task_db) as queue:
            queue.approve(task_id, approved_by="reviewer")
            task = queue.get(task_id)
            task["payload"]["_approval"]["approved_at"] = 10 ** 400
            queue._connection.execute(
                "UPDATE tasks SET payload_json = ? WHERE id = ?",
                (json.dumps(task["payload"]), task_id),
            )
            queue._connection.commit()
        with self.assertRaisesRegex(PluginError, "approval timestamp does not match its audit event"):
            validate_approved_plugin_ticket(self.registry.database, task_db, task_id)

    def test_approved_ticket_rejects_timestamp_not_bound_to_audit_event(self) -> None:
        self.approve_enable_signed(manifest())
        task_db = Path(self.temp.name) / "commander.sqlite3"
        request = self.registry.commander_request(
            "research.cross-check", "research", {"topic": "approval timestamp binding"},
            enqueue=commander_task_queue_adapter(task_db), requested_by="owner",
        )
        task_id = request["commander_result"]["task_id"]
        with TaskQueue(task_db) as queue:
            queue.approve(task_id, approved_by="reviewer")
            task = queue.get(task_id)
            task["payload"]["_approval"]["approved_at"] = "2000-01-01T00:00:00+00:00"
            queue._connection.execute(
                "UPDATE tasks SET payload_json = ? WHERE id = ?",
                (json.dumps(task["payload"]), task_id),
            )
            queue._connection.commit()
        with self.assertRaisesRegex(PluginError, "approval timestamp does not match its audit event"):
            validate_approved_plugin_ticket(self.registry.database, task_db, task_id)

    def test_unsigned_enabled_plugin_cannot_request_commander_approval(self) -> None:
        self.approve_enable(manifest())
        with self.assertRaisesRegex(PluginError, "verified publisher signature"):
            self.registry.commander_request(
                "research.cross-check", "research", {"topic": "unsigned"},
                enqueue=lambda *_args, **_kw: {}, requested_by="owner",
            )

    def test_unlisted_capability_disabled_plugin_and_oversized_payload_are_rejected(self) -> None:
        self.approve_enable_signed(manifest(), enable=False)
        with self.assertRaises(PluginError):
            self.registry.commander_request("research.cross-check", "research", {}, enqueue=lambda *_args, **_kw: {}, requested_by="owner")
        self.registry.enable("research.cross-check", enabled_by="owner")
        with self.assertRaises(PluginError):
            self.registry.commander_request("research.cross-check", "arbitrary", {}, enqueue=lambda *_args, **_kw: {}, requested_by="owner")
        with self.assertRaises(PluginError):
            self.registry.commander_request("research.cross-check", "research", {"blob": "x" * 70000}, enqueue=lambda *_args, **_kw: {}, requested_by="owner")
    def test_enabled_dependents_block_dependency_disable(self) -> None:
        self.approve_enable(manifest("research.core", version="1.2.0"))
        self.approve_enable(manifest("research.pipeline", dependencies={"research.core": "1.2.0"}))
        with self.assertRaises(PluginError):
            self.registry.disable("research.core", disabled_by="owner")
        self.registry.disable("research.pipeline", disabled_by="owner")
        self.registry.disable("research.core", disabled_by="owner")

    def test_audit_contains_digest_but_not_manifest_payload(self) -> None:
        self.approve_enable(manifest())
        events = self.registry.connection.execute("SELECT * FROM plugin_audit ORDER BY event_id").fetchall()
        self.assertEqual([row["event_type"] for row in events], [
            "plugin.discovered", "plugin.approved", "plugin.enabled",
        ])
        self.assertTrue(all(len(row["manifest_sha256"]) == 64 for row in events))

    def test_plugin_capabilities_sync_into_shared_catalog_without_becoming_executable(self) -> None:
        self.registry.discover(manifest())
        with CapabilityRegistry(Path(self.temp.name) / "capabilities.sqlite3") as catalog:
            first = sync_plugin_capabilities(self.registry, catalog)
            item = catalog.get("plugin.research.cross-check.research")
            self.assertEqual(first, {"registered": 1, "updated": 0, "disabled": 0, "total": 1})
            self.assertEqual(item["status"], "needs_approval")
            self.assertIsNone(item["executable"])
            self.assertFalse(item["metadata"]["execution_enabled"])
            self.assertFalse(item["metadata"]["runtime_adapter_registered"])
            self.approve_enable(manifest())
            second = sync_plugin_capabilities(self.registry, catalog)
            self.assertEqual(second["updated"], 1)
            self.assertEqual(catalog.get(item["capability_id"])["status"], "needs_approval")
            self.registry.disable("research.cross-check", disabled_by="owner")
            third = sync_plugin_capabilities(self.registry, catalog)
            self.assertEqual(third["disabled"], 1)
            self.assertEqual(catalog.get(item["capability_id"])["status"], "disabled")

    def test_plugin_capability_catalog_collision_fails_closed(self) -> None:
        self.registry.discover(manifest())
        with CapabilityRegistry(Path(self.temp.name) / "capabilities.sqlite3") as catalog:
            catalog.register(
                capability_id="plugin.research.cross-check.research", domain="plugin",
                name="Existing capability", description="Owned by another source.",
                status="ready", capabilities=["unrelated"], trust="local", license="unknown",
            )
            with self.assertRaises(PluginError):
                sync_plugin_capabilities(self.registry, catalog)

    def test_incomplete_plugin_snapshot_does_not_disable_unseen_capabilities(self) -> None:
        self.registry.discover(manifest())
        with CapabilityRegistry(Path(self.temp.name) / "capabilities.sqlite3") as catalog:
            sync_plugin_capabilities(self.registry, catalog)
            catalog.register(
                capability_id="plugin.unseen.future.capability", domain="plugin",
                name="Unseen capability", description="Outside the current registry page.",
                status="needs_approval", executable=None,
                capabilities=["plugin.unseen.future.capability"],
                trust="declarative_manifest_untrusted", license="unknown",
                metadata={"source": "muse_plugin_registry", "plugin_id": "unseen.future",
                          "source_capability_id": "capability", "execution_enabled": False},
            )
            self.registry.count_plugins = lambda: 501
            sync_plugin_capabilities(self.registry, catalog)
            unseen = catalog.get("plugin.unseen.future.capability")
            self.assertEqual(unseen["status"], "needs_approval")

    def test_removed_plugin_capability_is_retained_but_disabled(self) -> None:
        self.registry.discover(manifest())
        with CapabilityRegistry(Path(self.temp.name) / "capabilities.sqlite3") as catalog:
            sync_plugin_capabilities(self.registry, catalog)
            changed = manifest()
            changed["capabilities"] = [{
                "id": "research-v2", "description": "Replacement capability.",
                "risk": "research", "permissions": ["commander.enqueue", "research.read"],
            }]
            self.registry.discover(changed)
            result = sync_plugin_capabilities(self.registry, catalog)
            self.assertEqual(result["total"], 1)
            stale = catalog.get("plugin.research.cross-check.research")
            self.assertEqual(stale["status"], "disabled")
            self.assertEqual(stale["metadata"]["catalog_state"], "removed_from_manifest")

if __name__ == "__main__":
    unittest.main()
