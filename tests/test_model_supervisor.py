from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse import model_supervisor


class ModelSupervisorTests(unittest.TestCase):
    def _workspace(self, root: Path) -> Path:
        config = root / "config"
        config.mkdir()
        exe = root / "llama-server.exe"
        exe.write_bytes(b"test executable")
        model = root / "model.gguf"
        model.write_bytes(b"model")
        mmproj = root / "mmproj.gguf"
        mmproj.write_bytes(b"mmproj")
        (config / "model-runtime.json").write_text(json.dumps({
            "executable": str(exe),
            "cwd": str(root),
            "args": ["-m", str(model), "--mmproj", str(mmproj), "--host", "127.0.0.1", "--port", "1235", "--alias", "test-model"],
            "health_url": "http://127.0.0.1:1235/v1",
            "log_dir": str(root / "logs"),
            "startup_timeout_seconds": 5,
            "health_interval_seconds": 1,
            "max_restarts_per_hour": 2,
        }), encoding="utf-8")
        (config / "muse.toml").write_text(
            '[models]\nprovider = "llama_cpp"\nbase_url = "http://127.0.0.1:1235/v1"\nmodel = "test-model"\n',
            encoding="utf-8",
        )
        return root

    def test_runtime_config_requires_existing_assets_and_loopback_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            config, _ = model_supervisor._load_runtime(root)
            self.assertEqual(config["health_url"], "http://127.0.0.1:1235/v1")
            payload = json.loads((root / "config" / "model-runtime.json").read_text())
            payload["health_url"] = "http://example.com:1235/v1"
            (root / "config" / "model-runtime.json").write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "loopback"):
                model_supervisor._load_runtime(root)

    def test_start_skips_launch_when_configured_model_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            with patch.object(model_supervisor, "_probe", return_value={"status": "healthy"}), \
                 patch.object(model_supervisor.subprocess, "Popen") as popen:
                result = model_supervisor.start_if_needed(root)
            self.assertEqual(result["action"], "already_healthy")
            popen.assert_not_called()

    def test_start_uses_literal_argv_without_shell_and_waits_for_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            process = type("Process", (), {"pid": 4321, "poll": lambda self: None})()
            with patch.object(model_supervisor, "_probe", side_effect=[
                {"status": "unavailable"}, {"status": "unavailable"}, {"status": "healthy"}
            ]), patch.object(model_supervisor, "_comfyui_gpu_memory", return_value={"status": "unavailable"}), \
                 patch.object(model_supervisor.subprocess, "Popen", return_value=process) as popen, \
                 patch.object(model_supervisor.time, "sleep"):
                result = model_supervisor.start_if_needed(root, startup_timeout=5)
            self.assertEqual(result["action"], "started")
            self.assertEqual(result["pid"], 4321)
            args, kwargs = popen.call_args
            self.assertEqual(args[0][0], str(root / "llama-server.exe"))
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["cwd"], str(root))
            self.assertEqual(args[0][args[0].index("--port") + 1], "1235")

    def test_external_lm_studio_endpoint_blocks_muse_model_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            with patch.object(model_supervisor, "_probe", return_value={"status": "unavailable"}):
                with patch.object(model_supervisor, "_comfyui_gpu_memory", return_value={"status": "external_model_active"}):
                    with patch.object(model_supervisor.subprocess, "Popen") as popen:
                        result = model_supervisor.start_if_needed(root)
            self.assertEqual(result["action"], "external_model_active")
            popen.assert_not_called()

    def test_comfyui_stats_timeout_is_unknown_not_proof_of_free_gpu(self) -> None:
        with patch.object(model_supervisor, "_loopback_port_status", side_effect=["closed", "listening"]):
            with patch.object(model_supervisor.ComfyUIClient, "gpu_memory", side_effect=RuntimeError("timeout")):
                result = model_supervisor._comfyui_gpu_memory()
        self.assertEqual(result, {"status": "unknown"})

    def test_live_lm_studio_port_is_detected_before_comfyui_probe(self) -> None:
        with patch.object(model_supervisor, "_loopback_port_status", return_value="listening") as probe:
            with patch.object(model_supervisor.ComfyUIClient, "gpu_memory") as memory:
                result = model_supervisor._comfyui_gpu_memory()
        self.assertEqual(result["status"], "external_model_active")
        self.assertEqual(probe.call_count, 1)
        memory.assert_not_called()

    def test_supervisor_records_external_model_active_without_launching_muse_model(self) -> None:
        from muse.gpu_resources import GpuResourceCoordinator
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            with patch.object(model_supervisor, "_probe", return_value={"status": "unavailable"}):
                with patch.object(model_supervisor, "start_if_needed", return_value={"action": "external_model_active"}):
                    result = model_supervisor.run_supervisor(root, once=True)
            state = GpuResourceCoordinator(root / "data" / "gpu-resources.sqlite3").supervisor_state()
            self.assertEqual(result, 1)
            self.assertEqual(state["action"], "external_model_active")

    def test_start_waits_when_comfyui_has_not_released_enough_vram(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            low_memory = {
                "status": "available", "vram_total_bytes": 12_000,
                "vram_free_bytes": 2_000, "vram_free_ratio": 2_000 / 12_000,
                "torch_vram_total_bytes": 10_000, "torch_vram_free_bytes": 1_000,
            }
            with patch.object(model_supervisor, "_probe", return_value={"status": "unavailable"}), \
                 patch.object(model_supervisor, "_comfyui_gpu_memory", return_value=low_memory), \
                 patch.object(model_supervisor.subprocess, "Popen") as popen:
                result = model_supervisor.start_if_needed(root)
            self.assertEqual(result["action"], "gpu_memory_insufficient")
            popen.assert_not_called()

    def test_start_allows_spawn_after_verified_comfyui_vram_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            ready_memory = {
                "status": "available", "vram_total_bytes": 12_000,
                "vram_free_bytes": 10_000, "vram_free_ratio": 10_000 / 12_000,
                "torch_vram_total_bytes": 10_000, "torch_vram_free_bytes": 9_000,
            }
            process = type("Process", (), {"pid": 9876, "poll": lambda self: None})()
            with patch.object(model_supervisor, "_probe", side_effect=[
                {"status": "unavailable"}, {"status": "healthy"}
            ]), patch.object(model_supervisor, "_comfyui_gpu_memory", return_value=ready_memory), \
                 patch.object(model_supervisor.subprocess, "Popen", return_value=process), \
                 patch.object(model_supervisor.time, "sleep"):
                result = model_supervisor.start_if_needed(root, startup_timeout=5)
            self.assertEqual(result["action"], "started")

    def test_supervisor_invokes_recovery_when_model_endpoint_goes_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            with patch.object(model_supervisor, "_probe", return_value={"status": "unavailable"}), \
                 patch.object(model_supervisor, "start_if_needed", return_value={"action": "started"}) as starter:
                result = model_supervisor.run_supervisor(root, once=True)
            self.assertEqual(result, 0)
            starter.assert_called_once()
            self.assertTrue(starter.call_args.kwargs["retain_process"])
            self.assertEqual(starter.call_args.args[0], root.resolve())

    def test_does_not_start_on_model_mismatch_or_other_non_unavailable_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            with patch.object(model_supervisor, "_probe", return_value={"status": "model_mismatch"}), \
                 patch.object(model_supervisor.subprocess, "Popen") as popen:
                result = model_supervisor.start_if_needed(root)
            self.assertEqual(result["action"], "not_started")
            popen.assert_not_called()

    def test_start_if_needed_does_not_launch_during_video_gpu_lease(self) -> None:
        from muse.gpu_resources import GpuResourceCoordinator
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            resources = GpuResourceCoordinator(root / "data" / "gpu-resources.sqlite3")
            lease_id = resources.acquire("video_generation", "video-job")
            try:
                with patch.object(model_supervisor, "_probe", return_value={"status": "unavailable"}), \
                     patch.object(model_supervisor.subprocess, "Popen") as popen:
                    result = model_supervisor.start_if_needed(root)
                self.assertEqual(result["action"], "gpu_reserved_for_video")
                popen.assert_not_called()
            finally:
                resources.release(lease_id)

    def test_supervisor_acknowledges_video_lease_when_model_is_already_offline(self) -> None:
        from muse.gpu_resources import GpuResourceCoordinator
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            resources = GpuResourceCoordinator(root / "data" / "gpu-resources.sqlite3")
            lease_id = resources.acquire("video_generation", "video-job")
            with patch.object(model_supervisor, "_probe", return_value={"status": "unavailable"}):
                result = model_supervisor.run_supervisor(root, once=True)
            state = resources.supervisor_state()
            self.assertEqual(result, 0)
            self.assertEqual(state["action"], "paused_for_video")
            self.assertEqual(state["acknowledged_lease_id"], lease_id)
            resources.release(lease_id)

    def test_config_rejects_mismatched_host_or_port(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(Path(directory))
            payload = json.loads((root / "config" / "model-runtime.json").read_text())
            payload["args"][-1] = "1234"
            (root / "config" / "model-runtime.json").write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "does not match"):
                model_supervisor._load_runtime(root)


if __name__ == "__main__":
    unittest.main()
