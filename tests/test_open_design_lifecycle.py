from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from muse.open_design import OpenDesignAdapter, OpenDesignTaskHandler


class CloseableClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.error is not None:
            raise self.error


class OpenDesignLifecycleTests(unittest.TestCase):
    def make_adapter(self, client_factory):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        adapter = OpenDesignAdapter(
            workspace=root,
            database=root / "tasks.sqlite3",
            command=("node", "daemon-cli.mjs", "mcp"),
            daemon_url="http://127.0.0.1:7456",
            client_factory=client_factory,
        )
        self.addCleanup(self.temp.cleanup)
        return adapter

    def test_close_is_idempotent_and_deduplicates_shared_client(self) -> None:
        client = CloseableClient()
        adapter = self.make_adapter(lambda **_kwargs: client)
        adapter._write_client = client
        adapter.close()
        adapter.close()
        self.assertEqual(client.close_calls, 1)
        self.assertIsNone(adapter._write_client)
        self.assertIsNone(adapter._read_client)

    def test_close_attempts_both_clients_even_when_first_close_fails(self) -> None:
        first = CloseableClient(RuntimeError("first close failed"))
        second = CloseableClient()
        adapter = self.make_adapter(lambda **_kwargs: first)
        adapter._write_client = second
        with self.assertRaisesRegex(RuntimeError, "first close failed"):
            adapter.close()
        self.assertEqual(first.close_calls, 1)
        self.assertEqual(second.close_calls, 1)

    def test_context_manager_closes_client(self) -> None:
        client = CloseableClient()
        adapter = self.make_adapter(lambda **_kwargs: client)
        with adapter as entered:
            self.assertIs(entered, adapter)
        self.assertEqual(client.close_calls, 1)
        with self.assertRaisesRegex(RuntimeError, "adapter is closed"):
            adapter.__enter__()

    def test_cleanup_failure_does_not_mask_original_operation_error(self) -> None:
        client = CloseableClient(RuntimeError("cleanup failed"))
        adapter = self.make_adapter(lambda **_kwargs: client)
        with self.assertRaisesRegex(ValueError, "operation failed"):
            with adapter:
                raise ValueError("operation failed")
        self.assertEqual(client.close_calls, 1)

    def test_cleanup_failure_is_reported_when_no_primary_error_exists(self) -> None:
        client = CloseableClient(RuntimeError("cleanup failed"))
        adapter = self.make_adapter(lambda **_kwargs: client)
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            with adapter:
                pass
        self.assertEqual(client.close_calls, 1)

    def test_clients_without_close_method_remain_supported(self) -> None:
        class LegacyClient:
            pass
        adapter = self.make_adapter(lambda **_kwargs: LegacyClient())
        adapter.close()
        adapter.close()

    def test_closed_adapter_rejects_new_daemon_operations(self) -> None:
        adapter = self.make_adapter(lambda **_kwargs: CloseableClient())
        adapter.close()
        with self.assertRaisesRegex(RuntimeError, "adapter is closed"):
            adapter._ensure_daemon()

    def test_task_handler_closes_adapter_when_execution_raises(self) -> None:
        client = CloseableClient()
        adapter = self.make_adapter(lambda **_kwargs: client)
        handler = OpenDesignTaskHandler(
            workspace=Path(self.temp.name),
            database=Path(self.temp.name) / "tasks.sqlite3",
        )
        with patch("muse.open_design.OpenDesignAdapter.from_installed", return_value=adapter):
            with patch.object(adapter, "execute_task", side_effect=ValueError("task failed")):
                with self.assertRaisesRegex(ValueError, "task failed"):
                    handler.execute_task({}, task_id="task-1")
        self.assertEqual(client.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
