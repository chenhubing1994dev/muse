from __future__ import annotations

import unittest

from muse.resource_governor import HostResourceSnapshot, ResourcePolicy


class ResourcePolicyTests(unittest.TestCase):
    def test_worker_limit_uses_cpu_and_memory_caps(self) -> None:

        snapshot = HostResourceSnapshot(
            logical_cpu_count=8,
            available_memory_bytes=8 * 1024**3,
            total_memory_bytes=16 * 1024**3,
        )
        policy = ResourcePolicy(
            maximum_workers=12,
            reserve_cpu_cores=1,
            reserve_memory_bytes=2 * 1024**3,
            estimated_memory_per_worker_bytes=1 * 1024**3,
        )

        decision = policy.decide(snapshot)

        self.assertEqual(decision.recommended_workers, 6)
        self.assertEqual(decision.cpu_cap, 7)
        self.assertEqual(decision.memory_cap, 6)
        self.assertEqual(decision.maximum_workers, 12)
        self.assertEqual(decision.status, "ready")

    def test_low_memory_pauses_auto_dispatch_instead_of_violating_reserve(self) -> None:
        snapshot = HostResourceSnapshot(
            logical_cpu_count=16,
            available_memory_bytes=2 * 1024**3,
            total_memory_bytes=16 * 1024**3,
        )
        policy = ResourcePolicy(
            maximum_workers=32,
            reserve_cpu_cores=1,
            reserve_memory_bytes=2 * 1024**3,
            estimated_memory_per_worker_bytes=512 * 1024**2,
        )

        decision = policy.decide(snapshot)

        self.assertEqual(decision.recommended_workers, 0)
        self.assertEqual(decision.memory_cap, 0)
        self.assertEqual(decision.status, "memory_pressure")

    def test_unknown_memory_fails_conservatively_to_one_worker(self) -> None:
        snapshot = HostResourceSnapshot(logical_cpu_count=32)

        decision = ResourcePolicy(maximum_workers=32).decide(snapshot)

        self.assertEqual(decision.recommended_workers, 1)
        self.assertEqual(decision.memory_cap, 1)
        self.assertIn("memory_unknown_conservative", decision.reasons)
        self.assertIsNone(snapshot.gpu_available_memory_bytes)

    def test_cpu_reserve_pauses_when_only_one_core_is_available(self) -> None:
        snapshot = HostResourceSnapshot(
            logical_cpu_count=1,
            available_memory_bytes=16 * 1024**3,
            total_memory_bytes=16 * 1024**3,
        )
        decision = ResourcePolicy(reserve_cpu_cores=1).decide(snapshot)
        self.assertEqual(decision.recommended_workers, 0)
        self.assertEqual(decision.status, "cpu_pressure")
        self.assertIn("cpu_reserve_protected", decision.reasons)

    def test_unknown_cpu_also_uses_a_single_worker(self) -> None:
        snapshot = HostResourceSnapshot(
            logical_cpu_count=None,
            available_memory_bytes=64 * 1024**3,
            total_memory_bytes=64 * 1024**3,
        )

        decision = ResourcePolicy(maximum_workers=32).decide(snapshot)

        self.assertEqual(decision.recommended_workers, 1)
        self.assertIn("cpu_unknown_conservative", decision.reasons)

    def test_hard_maximum_is_never_exceeded(self) -> None:
        snapshot = HostResourceSnapshot(
            logical_cpu_count=128,
            available_memory_bytes=256 * 1024**3,
            total_memory_bytes=256 * 1024**3,
        )

        decision = ResourcePolicy(maximum_workers=5).decide(snapshot)

        self.assertEqual(decision.recommended_workers, 5)

    def test_invalid_snapshots_and_policy_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HostResourceSnapshot(logical_cpu_count=0)
        with self.assertRaises(ValueError):
            HostResourceSnapshot(available_memory_bytes=-1)
        with self.assertRaises(ValueError):
            ResourcePolicy(maximum_workers=33)
        with self.assertRaises(ValueError):
            ResourcePolicy(reserve_cpu_cores=-1)
        with self.assertRaises(ValueError):
            ResourcePolicy(estimated_memory_per_worker_bytes=0)


class WorkerAutoConcurrencyTests(unittest.TestCase):
    def test_worker_cli_uses_resource_recommendation(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from muse.worker import main

        snapshot = HostResourceSnapshot(
            logical_cpu_count=8,
            available_memory_bytes=8 * 1024**3,
            total_memory_bytes=16 * 1024**3,
        )
        with TemporaryDirectory() as directory:
            with patch("muse.worker.probe_host_resources", return_value=snapshot):
                with patch("muse.worker.run_parallel_once", return_value=[None] * 6) as run:
                    with redirect_stdout(StringIO()):
                        result = main([
                            "--db", str(Path(directory) / "tasks.sqlite3"),
                            "--auto-workers", "--max-workers", "6",
                        ])
            self.assertEqual(result, 0)
            self.assertEqual(run.call_args.kwargs["max_workers"], 6)

    def test_worker_cli_does_not_dispatch_under_resource_pressure(self) -> None:
        from contextlib import redirect_stderr
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from muse.worker import main

        snapshot = HostResourceSnapshot(
            logical_cpu_count=8,
            available_memory_bytes=1024**3,
            total_memory_bytes=16 * 1024**3,
        )
        with TemporaryDirectory() as directory:
            with patch("muse.worker.probe_host_resources", return_value=snapshot):
                with patch("muse.worker.run_parallel_once") as run:
                    with redirect_stderr(StringIO()):
                        with self.assertRaises(SystemExit):
                            main(["--db", str(Path(directory) / "tasks.sqlite3"),
                                  "--auto-workers"])
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
