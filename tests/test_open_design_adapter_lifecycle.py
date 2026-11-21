from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from muse.open_design import OpenDesignAdapter


class CloseableClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.error is not None:
            raise self.error


class OpenDesignAdapterLifecycleTests(unittest.TestCase):
    def _adapter(self, read_client: CloseableClient) -> OpenDesignAdapter:
        root = Path(tempfile.mkdtemp())
        return OpenDesignAdapter(
            workspace=root,
            database=root / "data" / "tasks.sqlite3",
            command=("python", "fake-mcp.py"),
            daemon_url="http://127.0.0.1:7456",
            allowed_agents=(),
            client_factory=lambda **_kwargs: read_client,
        )

    def test_close_is_idempotent_and_closes_each_owned_client_once(self) -> None:
        read_client = CloseableClient()
        write_client = CloseableClient()
        adapter = self._adapter(read_client)
        adapter._write_client = write_client

        adapter.close()
        adapter.close()

        self.assertEqual(read_client.close_calls, 1)
        self.assertEqual(write_client.close_calls, 1)
        self.assertIsNone(adapter._write_client)

    def test_context_manager_preserves_primary_exception_when_cleanup_fails(self) -> None:
        read_client = CloseableClient(RuntimeError("cleanup failure"))
        adapter = self._adapter(read_client)

        with self.assertRaisesRegex(ValueError, "primary operation failure"):
            with adapter:
                raise ValueError("primary operation failure")

        self.assertEqual(read_client.close_calls, 1)

    def test_context_manager_propagates_cleanup_error_without_primary_error(self) -> None:
        read_client = CloseableClient(RuntimeError("cleanup failure"))
        adapter = self._adapter(read_client)

        with self.assertRaisesRegex(RuntimeError, "cleanup failure"):
            with adapter:
                pass

    def test_closed_adapter_rejects_new_daemon_operations(self) -> None:
        adapter = self._adapter(CloseableClient())
        adapter.close()
        with self.assertRaisesRegex(RuntimeError, "adapter is closed"):
            adapter._ensure_daemon()


if __name__ == "__main__":
    unittest.main()
