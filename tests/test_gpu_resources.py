from __future__ import annotations

import multiprocessing
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from muse.gpu_resources import GpuLeaseSession, GpuResourceBusy, GpuResourceCoordinator, probe_gpu_workloads


def _claim_gpu_in_child(database: str, result_queue) -> None:
    coordinator = GpuResourceCoordinator(database)
    try:
        lease_id = coordinator.acquire("video_generation", "child-video-job")
    except GpuResourceBusy:
        result_queue.put("blocked")
    else:
        result_queue.put("acquired")
        coordinator.release(lease_id)



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


    def test_video_encoding_and_comfyui_share_the_exclusive_gpu_slot(self) -> None:
        encoding = self.coordinator.acquire("video_encoding", "encode-job")
        try:
            with self.assertRaises(GpuResourceBusy):
                self.coordinator.acquire("video_generation", "wan-job")
            with self.assertRaises(GpuResourceBusy):
                self.coordinator.acquire("comfyui_generation", "comfy-job")
        finally:
            self.coordinator.release(encoding)
        generation = self.coordinator.acquire("comfyui_generation", "comfy-job")
        try:
            with self.assertRaises(GpuResourceBusy):
                self.coordinator.acquire("video_encoding", "encode-job")
        finally:
            self.coordinator.release(generation)

    def test_non_generation_gpu_session_rejects_unknown_or_active_model_state(self) -> None:
        for status in ("healthy", "unknown"):
            with self.subTest(status=status):
                with self.assertRaises(GpuResourceBusy):
                    with GpuLeaseSession(
                        self.coordinator, "encode-job", owner_kind="video_encoding",
                        model_probe=lambda value=status: {"status": value},
                    ):
                        self.fail("unsafe encoder lease must not enter")
        self.assertIsNone(self.coordinator.active_lease())

    def test_gpu_lease_is_exclusive_across_processes(self) -> None:
        parent_lease = self.coordinator.acquire("video_encoding", "parent-amf-encode")
        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        process = context.Process(target=_claim_gpu_in_child, args=(str(self.db), results))
        try:
            process.start()
            self.assertEqual(results.get(timeout=15), "blocked")
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            results.close()
            self.coordinator.release(parent_lease)

class GpuWorkloadProbeTests(unittest.TestCase):
    def _probe(self, *, running=0, pending=0, free_ratio=0.95, total=12 * 1024**3, free=None):
        free_bytes = int(total * free_ratio) if free is None else free

        class FakeComfyUIClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def health(self):
                return {"status": "ready"}

            def queue_status(self):
                return {"running_jobs": running, "pending_jobs": pending}

            def gpu_memory(self):
                return {
                    "vram_total_bytes": total,
                    "vram_free_bytes": free_bytes,
                    "vram_free_ratio": free_ratio,
                }

        with patch("muse.video_engines.ComfyUIClient", FakeComfyUIClient):
            with patch("muse.model_service.probe_configured_model_service") as model_probe:
                result = probe_gpu_workloads(Path("."))
        model_probe.assert_not_called()
        return result

    def test_healthy_but_idle_comfyui_with_released_vram_allows_amf(self) -> None:
        result = self._probe()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["service_health"], "healthy")
        self.assertEqual(result["gpu_workload"], "idle")
        self.assertEqual(result["active_service"], "comfyui_idle")

    def test_active_comfyui_queue_blocks_amf_even_when_memory_is_free(self) -> None:
        result = self._probe(running=1)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["gpu_workload"], "active")

    def test_resident_comfyui_models_block_amf_until_vram_is_released(self) -> None:
        result = self._probe(free_ratio=0.55)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["gpu_workload"], "models_resident_or_memory_busy")

    def test_unknown_queue_state_fails_closed(self) -> None:
        class UnknownQueueClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def health(self):
                return {"status": "ready"}

            def queue_status(self):
                return {"running_jobs": None, "pending_jobs": 0}

        with patch("muse.video_engines.ComfyUIClient", UnknownQueueClient):
            result = probe_gpu_workloads(Path("."))
        self.assertEqual(result["status"], "unknown")

    def test_idle_comfyui_probe_allows_shared_encoding_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coordinator = GpuResourceCoordinator(Path(temp) / "gpu.sqlite3")
            lease_id = coordinator.acquire("video_encoding", "amf-encode")
            try:
                with patch("muse.video_engines.ComfyUIClient") as client_type:
                    client = client_type.return_value
                    client.health.return_value = {"status": "ready"}
                    client.queue_status.return_value = {"running_jobs": 0, "pending_jobs": 0}
                    client.gpu_memory.return_value = {
                        "vram_total_bytes": 12 * 1024**3,
                        "vram_free_bytes": 11 * 1024**3,
                        "vram_free_ratio": 11 / 12,
                    }
                    result = coordinator.wait_for_video_access(
                        lease_id, lambda: probe_gpu_workloads(temp), timeout_seconds=0,
                    )
                self.assertEqual(result["mode"], "model_offline_supervisor_not_running")
            finally:
                coordinator.release(lease_id)

    def test_busy_comfyui_probe_blocks_shared_encoding_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            coordinator = GpuResourceCoordinator(Path(temp) / "gpu.sqlite3")
            lease_id = coordinator.acquire("video_encoding", "amf-encode")
            try:
                with patch("muse.video_engines.ComfyUIClient") as client_type:
                    client = client_type.return_value
                    client.health.return_value = {"status": "ready"}
                    client.queue_status.return_value = {"running_jobs": 1, "pending_jobs": 0}
                    with self.assertRaises(GpuResourceBusy):
                        coordinator.wait_for_video_access(
                            lease_id, lambda: probe_gpu_workloads(temp), timeout_seconds=0,
                        )
            finally:
                coordinator.release(lease_id)


if __name__ == "__main__":
    unittest.main()
