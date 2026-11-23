from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse.gpu_resources import GpuResourceBusy

from muse.wan_video import (
    WAN21_NODE_ALLOWLIST,
    WanVideoError,
    _video_output,
    build_wan21_t2v_workflow,
    generate_wan21_t2v,
)


class WanWorkflowTests(unittest.TestCase):
    def test_builds_fixed_allowlisted_graph_for_landscape_portrait_and_square(self):
        for ratio, size in (("16:9", (512, 288)), ("9:16", (288, 512)), ("1:1", (384, 384))):
            graph = build_wan21_t2v_workflow(
                "A red fox in a bamboo forest", aspect_ratio=ratio,
                duration_seconds=2.0, filename_prefix="muse_wan21_0123456789abcdef",
            )
            self.assertEqual({node["class_type"] for node in graph.values()}, WAN21_NODE_ALLOWLIST)
            self.assertEqual((graph["7"]["inputs"]["width"], graph["7"]["inputs"]["height"]), size)
            self.assertEqual(graph["7"]["inputs"]["length"] % 4, 1)
            self.assertEqual(graph["9"]["class_type"], "VAEDecodeTiled")
            self.assertEqual(graph["9"]["inputs"]["temporal_size"], 8)
            self.assertEqual(graph["9"]["inputs"]["temporal_overlap"], 4)
            self.assertEqual(graph["11"]["inputs"]["format"], "mp4")

    def test_rejects_invalid_prompts_and_generation_settings(self):
        invalid = (
            {"prompt": ""},
            {"prompt": "ok", "aspect_ratio": "4:3"},
            {"prompt": "ok", "duration_seconds": 6},
            {"prompt": "ok", "steps": 7},
            {"prompt": "ok", "seed": -1},
            {"prompt": "ok", "filename_prefix": "../escape"},
        )
        for args in invalid:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    build_wan21_t2v_workflow(**args)

    def test_video_output_only_accepts_plain_mp4_in_output_root(self):
        good = {"outputs": {"11": {"gifs": [
            {"filename": "muse_wan21_00001.mp4", "subfolder": "", "type": "output"}
        ]}}}
        self.assertEqual(_video_output(good), {
            "filename": "muse_wan21_00001.mp4", "subfolder": "", "type": "output"
        })
        bad = {"outputs": {"11": {"gifs": [
            {"filename": "../outside.mp4", "subfolder": "", "type": "output"}
        ]}}}
        self.assertIsNone(_video_output(bad))

    def test_video_output_accepts_comfyui_savevideo_images_metadata(self):
        entry = {"outputs": {"11": {"images": [
            {"filename": "muse_wan21_real.mp4", "subfolder": "", "type": "output"}
        ]}}}
        self.assertEqual(_video_output(entry), {
            "filename": "muse_wan21_real.mp4", "subfolder": "", "type": "output"
        })

    def test_generate_submits_fixed_graph_and_downloads_only_reported_output(self):
        calls = []

        class FakeClient:
            def __init__(self, endpoint, **kwargs):
                calls.append(("init", endpoint))
            def gpu_memory(self):
                return {"vram_total_bytes": 1000, "vram_free_bytes": 900, "vram_free_ratio": 0.9}
            def submit_workflow(self, workflow, *, node_allowlist, approved):
                calls.append(("submit", set(node_allowlist), approved, workflow["11"]["inputs"]["format"]))
                return {"prompt_id": "prompt-123"}
            def history(self, prompt_id):
                calls.append(("history", prompt_id))
                return {"prompt-123": {"status": {"status_str": "success"}, "outputs": {
                    "11": {"gifs": [{"filename": "muse_wan21_test.mp4", "subfolder": "", "type": "output"}]}
                }}}

        def fake_download(_client, _metadata, target):
            target.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return target.stat().st_size

        with tempfile.TemporaryDirectory() as temp:
            with patch("muse.wan_video.ComfyUIClient", FakeClient), patch(
                "muse.wan_video._download_local_output", side_effect=fake_download
            ), patch("muse.wan_video.time.sleep"):
                target = Path(temp) / "video.mp4"
                result = generate_wan21_t2v("A red fox", target, timeout_seconds=30)
                self.assertTrue(target.is_file())
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["prompt_id"], "prompt-123")
        self.assertIn(("submit", set(WAN21_NODE_ALLOWLIST), True, "mp4"), calls)
    def test_generate_retries_temporary_comfyui_history_transport_failure(self):
        calls = []

        class FlakyClient:
            def __init__(self, endpoint, **kwargs):
                pass
            def gpu_memory(self):
                return {"vram_total_bytes": 1000, "vram_free_bytes": 900, "vram_free_ratio": 0.9}
            def submit_workflow(self, workflow, *, node_allowlist, approved):
                return {"prompt_id": "prompt-flaky"}
            def history(self, prompt_id):
                calls.append(prompt_id)
                if len(calls) == 1:
                    raise RuntimeError("local video engine is unavailable")
                return {prompt_id: {"status": {"status_str": "success"}, "outputs": {
                    "11": {"gifs": [{"filename": "muse_wan21_retry.mp4", "subfolder": "", "type": "output"}]}
                }}}

        def fake_download(_client, _metadata, target):
            target.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return target.stat().st_size

        with tempfile.TemporaryDirectory() as temp:
            with patch("muse.wan_video.ComfyUIClient", FlakyClient), patch(
                "muse.wan_video._download_local_output", side_effect=fake_download
            ), patch("muse.wan_video.time.sleep"):
                result = generate_wan21_t2v("A red fox", Path(temp) / "video.mp4", timeout_seconds=30)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls, ["prompt-flaky", "prompt-flaky"])

    def test_generate_uses_exclusive_gpu_lease_when_resource_database_is_supplied(self):
        from muse.gpu_resources import GpuResourceCoordinator
        calls = []

        class FakeClient:
            def __init__(self, endpoint, **kwargs):
                calls.append(("init", endpoint))
            def gpu_memory(self):
                return {"vram_total_bytes": 1000, "vram_free_bytes": 900, "vram_free_ratio": 0.9}
            def submit_workflow(self, workflow, *, node_allowlist, approved):
                return {"prompt_id": "leased-prompt"}
            def history(self, prompt_id):
                return {prompt_id: {"status": {"status_str": "success"}, "outputs": {
                    "11": {"gifs": [{"filename": "muse_wan21_leased.mp4", "subfolder": "", "type": "output"}]}
                }}}

        def fake_download(_client, _metadata, target):
            target.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return target.stat().st_size

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "data" / "gpu-resources.sqlite3"
            coordinator = GpuResourceCoordinator(database)
            with patch("muse.model_service.probe_configured_model_service", return_value={"status": "unavailable"}), \
                 patch("muse.wan_video.ComfyUIClient", FakeClient), \
                 patch("muse.wan_video._download_local_output", side_effect=fake_download), \
                 patch("muse.wan_video._request_comfyui_model_release", return_value={"status": "released", "vram_free_ratio": 0.8}), \
                 patch("muse.wan_video.time.sleep"):
                result = generate_wan21_t2v(
                    "A red fox", root / "video.mp4", timeout_seconds=30,
                    resource_db=database, workspace=root,
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["gpu_resource_coordination"]["mode"], "model_offline_supervisor_not_running")
            self.assertEqual(result["gpu_memory_release"]["status"], "released")
            self.assertEqual(coordinator.status_snapshot()["comfyui_memory_release"]["status"], "released")
            self.assertIsNone(coordinator.active_lease())

    def test_comfyui_release_is_deferred_when_user_queue_is_not_empty(self):
        from muse.wan_video import _request_comfyui_model_release
        class FakeClient:
            def __init__(self, endpoint, **kwargs): pass
            def queue_status(self): return {"running_jobs": 1, "pending_jobs": 0}
            def free_models(self): raise AssertionError("must never unload a busy queue")
        with patch("muse.wan_video.ComfyUIClient", FakeClient):
            result = _request_comfyui_model_release("http://127.0.0.1:8188")
        self.assertEqual(result, {"status": "deferred_queue_busy", "queue_running": 1, "queue_pending": 0})

    def test_comfyui_release_waits_for_measured_free_vram_after_unload(self):
        from muse.wan_video import _request_comfyui_model_release
        calls = []
        class FakeClient:
            def __init__(self, endpoint, **kwargs): pass
            def queue_status(self): return {"running_jobs": 0, "pending_jobs": 0}
            def free_models(self): calls.append("free")
            def gpu_memory(self):
                calls.append("stats")
                return {"vram_total_bytes": 1000, "vram_free_bytes": 900,
                        "vram_free_ratio": 0.9, "torch_vram_total_bytes": 800,
                        "torch_vram_free_bytes": 750}
        with patch("muse.wan_video.ComfyUIClient", FakeClient):
            result = _request_comfyui_model_release("http://127.0.0.1:8188", timeout_seconds=0)
        self.assertEqual(result["status"], "released")
        self.assertEqual(calls, ["free", "stats"])

    def test_small_rocm_allocator_pool_does_not_override_device_headroom(self):
        from muse.wan_video import _require_gpu_headroom

        class TinyAllocatorPool:
            def gpu_memory(self):
                return {
                    "vram_total_bytes": 12 * 1024**3,
                    "vram_free_bytes": 11 * 1024**3,
                    "vram_free_ratio": 11 / 12,
                    "torch_vram_total_bytes": 64 * 1024**2,
                    "torch_vram_free_bytes": 32 * 1024**2,
                }

        result = _require_gpu_headroom(TinyAllocatorPool())
        self.assertEqual(result["torch_vram_total_bytes"], 64 * 1024**2)

    def test_large_torch_allocator_pool_still_enforces_free_memory_threshold(self):
        from muse.wan_video import _require_gpu_headroom

        class LargeAllocatorPool:
            def gpu_memory(self):
                return {
                    "vram_total_bytes": 12 * 1024**3,
                    "vram_free_bytes": 11 * 1024**3,
                    "vram_free_ratio": 11 / 12,
                    "torch_vram_total_bytes": 8 * 1024**3,
                    "torch_vram_free_bytes": 5 * 1024**3,
                }

        with self.assertRaisesRegex(WanVideoError, "PyTorch GPU memory"):
            _require_gpu_headroom(LargeAllocatorPool())

    def test_generate_blocks_when_measured_gpu_headroom_is_below_threshold(self):
        class LowMemoryClient:
            def __init__(self, endpoint, **kwargs): pass
            def gpu_memory(self):
                return {"vram_total_bytes": 1000, "vram_free_bytes": 600, "vram_free_ratio": 0.6}
            def submit_workflow(self, *args, **kwargs):
                raise AssertionError("a low-memory GPU must not receive a workflow")

        with patch("muse.wan_video.ComfyUIClient", LowMemoryClient):
            with self.assertRaisesRegex(WanVideoError, "70% safety threshold"):
                generate_wan21_t2v("A red fox", Path("unused.mp4"), timeout_seconds=30)

    def test_gpu_lease_block_is_reported_as_wan_video_error(self):
        class BusyLease:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self):
                raise GpuResourceBusy("external model has not yielded GPU")
            def __exit__(self, exc_type, exc, traceback): return False

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("muse.gpu_resources.GpuLeaseSession", BusyLease):
                with self.assertRaisesRegex(WanVideoError, "GPU resource coordination blocked"):
                    generate_wan21_t2v(
                        "A red fox", root / "video.mp4", timeout_seconds=30,
                        resource_db=root / "gpu-resources.sqlite3", workspace=root,
                    )

    def test_generate_rejects_non_loopback_endpoint(self):
        with self.assertRaises(ValueError):
            generate_wan21_t2v("A red fox", Path("unused.mp4"), endpoint="http://192.168.1.10:8188")

    def test_generate_calls_admission_guard_with_live_lease_after_vram_preflight_before_submit(self):
        events = []
        leases = []

        class FakeLeaseSession:
            def __init__(self, _coordinator, owner_id, **_kwargs):
                self.lease_id = "lease-guard-test"
                self.owner_id = owner_id
                self.coordination = {"mode": "model_offline_supervisor_not_running"}
                self.active = False

            def __enter__(self):
                self.active = True
                leases.append(self)
                events.append("lease_enter")
                return self

            def assert_valid(self):
                if not self.active:
                    raise GpuResourceBusy("lease is not active")
                events.append("lease_assert")

            def __exit__(self, exc_type, exc, traceback):
                self.active = False
                events.append("lease_exit")
                return False

        class FakeClient:
            def __init__(self, _endpoint, **_kwargs):
                events.append("client_init")

            def gpu_memory(self):
                events.append("gpu_memory")
                return {"vram_total_bytes": 1000, "vram_free_bytes": 900, "vram_free_ratio": 0.9}

            def submit_workflow(self, _workflow, *, node_allowlist, approved):
                events.append("submit")
                return {"prompt_id": "guarded-prompt"}

            def history(self, prompt_id):
                return {prompt_id: {"status": {"status_str": "success"}, "outputs": {
                    "11": {"gifs": [{"filename": "muse_wan21_guarded.mp4", "subfolder": "", "type": "output"}]}
                }}}

        def guard(received_lease):
            events.append("guard")
            self.assertIs(received_lease, leases[0])
            self.assertTrue(received_lease.active)

        def fake_download(_client, _metadata, target):
            target.write_bytes(bytes.fromhex("000000186674797069736f6d66697874757265"))
            return target.stat().st_size

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("muse.gpu_resources.GpuLeaseSession", FakeLeaseSession), \
                 patch("muse.model_service.probe_configured_model_service", return_value={"status": "unavailable"}), \
                 patch("muse.wan_video.ComfyUIClient", FakeClient), \
                 patch("muse.wan_video._download_local_output", side_effect=fake_download), \
                 patch("muse.wan_video._request_comfyui_model_release", return_value={"status": "released", "vram_free_ratio": 0.8}), \
                 patch("muse.wan_video.time.sleep"):
                result = generate_wan21_t2v(
                    "A red fox", root / "video.mp4", timeout_seconds=30,
                    resource_db=root / "data" / "gpu-resources.sqlite3", workspace=root,
                    admission_guard=guard,
                )

        self.assertEqual(result["status"], "completed")
        self.assertLess(events.index("gpu_memory"), events.index("guard"))
        self.assertLess(events.index("guard"), events.index("submit"))
        self.assertIn("lease_exit", events)

    def test_generate_propagates_admission_rejection_without_submitting_workflow(self):
        events = []
        denied = PermissionError("Commander admission was revoked")

        class FakeLeaseSession:
            def __init__(self, _coordinator, owner_id, **_kwargs):
                self.lease_id = "lease-denied-test"
                self.owner_id = owner_id
                self.coordination = {"mode": "model_offline_supervisor_not_running"}
                self.active = False

            def __enter__(self):
                self.active = True
                return self

            def assert_valid(self):
                if not self.active:
                    raise GpuResourceBusy("lease is not active")

            def __exit__(self, exc_type, exc, traceback):
                self.active = False
                return False

        class FakeClient:
            def __init__(self, _endpoint, **_kwargs):
                pass

            def gpu_memory(self):
                return {"vram_total_bytes": 1000, "vram_free_bytes": 900, "vram_free_ratio": 0.9}

            def submit_workflow(self, *_args, **_kwargs):
                events.append("submit")
                raise AssertionError("a rejected admission must not submit a workflow")

        def guard(_lease):
            events.append("guard")
            raise denied

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("muse.gpu_resources.GpuLeaseSession", FakeLeaseSession), \
                 patch("muse.model_service.probe_configured_model_service", return_value={"status": "unavailable"}), \
                 patch("muse.wan_video.ComfyUIClient", FakeClient):
                with self.assertRaises(PermissionError) as caught:
                    generate_wan21_t2v(
                        "A red fox", root / "video.mp4", timeout_seconds=30,
                        resource_db=root / "data" / "gpu-resources.sqlite3", workspace=root,
                        admission_guard=guard,
                    )

        self.assertIs(caught.exception, denied)
        self.assertEqual(events, ["guard"])

    def test_generate_rejects_admission_guard_without_gpu_lease_database(self):
        with patch("muse.wan_video.ComfyUIClient") as client:
            with self.assertRaisesRegex(WanVideoError, "resource_db"):
                generate_wan21_t2v(
                    "A red fox", Path("unused.mp4"), admission_guard=lambda _lease: None,
                )
        client.assert_not_called()

    def test_gpu_memory_transport_error_remains_wan_video_error(self):
        class BrokenTelemetryClient:
            def __init__(self, _endpoint, **_kwargs):
                pass

            def gpu_memory(self):
                raise OSError("local GPU telemetry unavailable")

        with patch("muse.wan_video.ComfyUIClient", BrokenTelemetryClient):
            with self.assertRaisesRegex(WanVideoError, "reliable GPU memory telemetry is unavailable"):
                generate_wan21_t2v("A red fox", Path("unused.mp4"), timeout_seconds=30)

if __name__ == "__main__":
    unittest.main()
