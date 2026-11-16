from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from threading import Barrier, Lock
from unittest.mock import patch

from muse.orchestration import AgentSpec, MultiAgentOrchestrator, OrchestrationStore

from muse.resource_governor import HostResourceSnapshot, ResourcePolicy


class ResourceAwareOrchestrationTests(unittest.TestCase):
    def test_orchestrator_recalculates_worker_slots_from_host_snapshot(self) -> None:
        snapshot = HostResourceSnapshot(
            logical_cpu_count=4,
            available_memory_bytes=3 * 1024**3,
            total_memory_bytes=16 * 1024**3,
        )
        policy = ResourcePolicy(
            maximum_workers=16,
            reserve_cpu_cores=1,
            reserve_memory_bytes=1 * 1024**3,
            estimated_memory_per_worker_bytes=1 * 1024**3,
        )
        with tempfile.TemporaryDirectory() as directory:
            with OrchestrationStore(Path(directory) / "runs.sqlite3") as store:
                orchestrator = MultiAgentOrchestrator(
                    store, max_workers=16, resource_policy=policy
                )
                with patch("muse.orchestration.probe_host_resources", return_value=snapshot):
                    self.assertEqual(orchestrator._worker_slots(12), 2)

    def test_default_orchestrator_enables_host_aware_budgeting(self) -> None:
        snapshot = HostResourceSnapshot(
            logical_cpu_count=4,
            available_memory_bytes=3 * 1024**3,
            total_memory_bytes=16 * 1024**3,
        )
        with tempfile.TemporaryDirectory() as directory:
            with OrchestrationStore(Path(directory) / "runs.sqlite3") as store:
                orchestrator = MultiAgentOrchestrator(store, max_workers=16)
                with patch("muse.orchestration.probe_host_resources", return_value=snapshot):
                    self.assertEqual(orchestrator._worker_slots(12), 2)

    def test_resource_aware_can_be_disabled_for_explicit_stress_tests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with OrchestrationStore(Path(directory) / "runs.sqlite3") as store:
                orchestrator = MultiAgentOrchestrator(
                    store, max_workers=16, resource_aware=False
                )
                self.assertIsNone(orchestrator.resource_policy)
                self.assertEqual(orchestrator._worker_slots(12), 12)

    def test_orchestrator_fails_closed_when_memory_reserve_cannot_be_kept(self) -> None:
        snapshot = HostResourceSnapshot(
            logical_cpu_count=16,
            available_memory_bytes=512 * 1024**2,
            total_memory_bytes=16 * 1024**3,
        )
        policy = ResourcePolicy(maximum_workers=16)
        with tempfile.TemporaryDirectory() as directory:
            with OrchestrationStore(Path(directory) / "runs.sqlite3") as store:
                orchestrator = MultiAgentOrchestrator(
                    store, max_workers=16, resource_policy=policy
                )
                with patch("muse.orchestration.probe_host_resources", return_value=snapshot):
                    with self.assertRaisesRegex(RuntimeError, "resource governor"):
                        orchestrator._worker_slots(12)


    def test_32_requested_agents_are_limited_by_live_host_budget(self) -> None:
        snapshot = HostResourceSnapshot(
            logical_cpu_count=4,
            available_memory_bytes=3 * 1024**3,
            total_memory_bytes=16 * 1024**3,
        )
        policy = ResourcePolicy(
            maximum_workers=32,
            reserve_cpu_cores=1,
            reserve_memory_bytes=1 * 1024**3,
            estimated_memory_per_worker_bytes=1 * 1024**3,
        )
        active = 0
        peak = 0
        lock = Lock()
        barrier = Barrier(2)

        def meet(prompt: str) -> str:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=2)
                return prompt
            finally:
                with lock:
                    active -= 1

        with tempfile.TemporaryDirectory() as directory:
            with OrchestrationStore(Path(directory) / "runs.sqlite3") as store:
                orchestrator = MultiAgentOrchestrator(
                    store, max_workers=32, resource_policy=policy
                )
                agents = [
                    AgentSpec(f"worker-{index:02d}", "specialist", meet)
                    for index in range(32)
                ]
                with patch("muse.orchestration.probe_host_resources", return_value=snapshot):
                    result = orchestrator.run("resource-governed 32-way fanout", agents)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(result.outcomes), 32)
        self.assertEqual(peak, 2)

if __name__ == "__main__":
    unittest.main()
