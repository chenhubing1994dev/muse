from __future__ import annotations

import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from muse.gpu_resources import GpuLeaseSession, GpuResourceBusy, GpuResourceCoordinator


class GpuResourceCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "gpu-resources.sqlite3"
        self.coordinator = GpuResourceCoordinator(self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exclusive_lease_rejects_second_owner(self) -> None:
        first = self.coordinator.acquire("video_generation", "job-a", now=100)
        with self.assertRaises(GpuResourceBusy):
            self.coordinator.acquire("model_inference", "model-a", now=101)
        self.assertEqual(self.coordinator.active_lease(now=101)["lease_id"], first)

    def test_parallel_claims_only_grant_one_lease(self) -> None:
        barrier = threading.Barrier(8)
        results: list[str] = []
        rejected: list[str] = []
        lock = threading.Lock()

        def claim(index: int) -> None:
            barrier.wait()
            try:
                value = self.coordinator.acquire("video_generation", f"job-{index}")
                with lock:
                    results.append(value)
            except GpuResourceBusy:
                with lock:
                    rejected.append(str(index))

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(rejected), 7)

    def test_heartbeat_extends_lease_and_release_is_idempotent(self) -> None:
        lease_id = self.coordinator.acquire("video_generation", "job-a", ttl_seconds=30, now=100)
        self.assertTrue(self.coordinator.renew(lease_id, ttl_seconds=60, now=110))
        current = self.coordinator.active_lease(now=111)
        self.assertEqual(current["expires_at"], 170)
        self.assertTrue(self.coordinator.release(lease_id, now=112))
        self.assertFalse(self.coordinator.release(lease_id, now=113))
        self.assertIsNone(self.coordinator.active_lease(now=113))

    def test_expired_lease_is_reaped_and_next_owner_can_acquire(self) -> None:
        old = self.coordinator.acquire("video_generation", "stalled-job", ttl_seconds=15, now=100)
        self.assertIsNotNone(self.coordinator.active_lease(now=114))
        self.assertIsNone(self.coordinator.active_lease(now=115))
        new = self.coordinator.acquire("model_inference", "model-job", now=116)
        self.assertNotEqual(old, new)

    def test_invalid_owner_and_ttl_fail_closed(self) -> None:
        for owner, identifier, ttl in (
            ("unknown", "x", 120),
            ("video_generation", "", 120),
            ("video_generation", "x", 14),
            ("video_generation", "x", 3601),
            ("video_generation", "x", True),
        ):
            with self.subTest(owner=owner, identifier=identifier, ttl=ttl):
                with self.assertRaises(ValueError):
                    self.coordinator.acquire(owner, identifier, ttl_seconds=ttl)

    def test_external_healthy_model_without_live_supervisor_blocks_video(self) -> None:
        lease_id = self.coordinator.acquire("video_generation", "job-a")
        try:
            with self.assertRaisesRegex(GpuResourceBusy, "unowned process"):
                self.coordinator.wait_for_video_access(
                    lease_id, lambda: {"status": "healthy"}, timeout_seconds=0,
                )
        finally:
            self.coordinator.release(lease_id)

    def test_offline_model_allows_video_when_supervisor_is_not_running(self) -> None:
        lease_id = self.coordinator.acquire("video_generation", "job-a")
        try:
            result = self.coordinator.wait_for_video_access(
                lease_id, lambda: {"status": "unavailable"}, timeout_seconds=0,
            )
            self.assertEqual(result["mode"], "model_offline_supervisor_not_running")
        finally:
            self.coordinator.release(lease_id)

    def test_fresh_supervisor_must_acknowledge_the_exact_lease(self) -> None:
        lease_id = self.coordinator.acquire("video_generation", "job-a")
        self.coordinator.record_supervisor(
            supervisor_id="supervisor-a", pid=123, action="paused_for_video",
            acknowledged_lease_id=lease_id, restore_pending=True, now=100,
        )
        try:
            result = self.coordinator.wait_for_video_access(
                lease_id, lambda: {"status": "unavailable"}, timeout_seconds=0, now_fn=lambda: 100,
            )
            self.assertEqual(result["mode"], "supervisor_paused_owned_model")
        finally:
            self.coordinator.release(lease_id)
        snapshot = self.coordinator.status_snapshot(now=101)
        self.assertEqual(snapshot["status"], "idle")
        self.assertTrue(snapshot["model_supervisor"]["restore_pending"])

    def test_stale_external_model_observation_waits_for_current_video_lease_ack(self) -> None:
        lease_id = self.coordinator.acquire("video_generation", "job-a")
        external_state = {
            "heartbeat_at": 100, "action": "external_model_active",
            "acknowledged_lease_id": None, "supervisor_id": "supervisor-a",
        }
        acknowledged_state = {
            "heartbeat_at": 100, "action": "paused_for_video",
            "acknowledged_lease_id": lease_id, "supervisor_id": "supervisor-a",
        }
        try:
            with patch.object(self.coordinator, "supervisor_state", side_effect=[external_state, acknowledged_state]):
                with patch("muse.gpu_resources.time.sleep"):
                    result = self.coordinator.wait_for_video_access(
                        lease_id, lambda: {"status": "unavailable"},
                        timeout_seconds=1, now_fn=lambda: 100,
                    )
            self.assertEqual(result["mode"], "supervisor_paused_owned_model")
        finally:
            self.coordinator.release(lease_id)
    def test_lease_context_releases_when_body_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "failure"):
            with GpuLeaseSession(
                self.coordinator, "job-a", model_probe=lambda: {"status": "unavailable"},
                wait_timeout_seconds=0,
            ):
                raise RuntimeError("failure")
        self.assertIsNone(self.coordinator.active_lease())


if __name__ == "__main__":
    unittest.main()
