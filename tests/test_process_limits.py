from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from muse.process_limits import ProcessLimitError, WindowsJobLimits


class ProcessLimitTests(unittest.TestCase):
    def test_rejects_invalid_memory_and_process_limits(self):
        with patch("muse.process_limits.os.name", "nt"):
            with self.assertRaises(ValueError):
                WindowsJobLimits(object(), memory_limit_mb=64)
            with self.assertRaises(ValueError):
                WindowsJobLimits(object(), memory_limit_mb=512, max_processes=0)

    def test_windows_job_object_configures_limits_and_attaches_child(self):
        class FakeKernel:
            def __init__(self):
                self.calls = []
            def CreateJobObjectW(self, *_args):
                self.calls.append("create")
                return 77
            def SetInformationJobObject(self, handle, info_class, _info, _size):
                self.calls.append(("configure", handle, info_class))
                return 1
            def AssignProcessToJobObject(self, handle, process_handle):
                self.calls.append(("assign", handle, process_handle))
                return 1
            def CloseHandle(self, handle):
                self.calls.append(("close", handle))
                return 1
        kernel = FakeKernel()
        process = type("Process", (), {"_handle": 123})()
        with patch("muse.process_limits.os.name", "nt"), patch("muse.process_limits.ctypes.WinDLL", return_value=kernel, create=True):
            limits = WindowsJobLimits(process, memory_limit_mb=512, max_processes=4)
            self.assertIn(("configure", 77, 9), kernel.calls)
            self.assertTrue(any(call[0] == "assign" for call in kernel.calls if isinstance(call, tuple)))
            limits.close()
        self.assertIn(("close", 77), kernel.calls)

    def test_closes_job_handle_when_limit_configuration_fails(self):
        class FakeKernel:
            def __init__(self): self.closed = []
            def CreateJobObjectW(self, *_args): return 88
            def SetInformationJobObject(self, *_args): return 0
            def CloseHandle(self, handle): self.closed.append(handle); return 1
        kernel = FakeKernel()
        process = type("Process", (), {"_handle": 123})()
        with patch("muse.process_limits.os.name", "nt"), patch("muse.process_limits.ctypes.WinDLL", return_value=kernel, create=True):
            with self.assertRaisesRegex(ProcessLimitError, "SetInformationJobObject"):
                WindowsJobLimits(process, memory_limit_mb=512)
        self.assertEqual(kernel.closed, [88])

    def test_closes_job_handle_when_process_assignment_fails(self):
        class FakeKernel:
            def __init__(self): self.closed = []
            def CreateJobObjectW(self, *_args): return 89
            def SetInformationJobObject(self, *_args): return 1
            def AssignProcessToJobObject(self, *_args): return 0
            def CloseHandle(self, handle): self.closed.append(handle); return 1
        kernel = FakeKernel()
        process = type("Process", (), {"_handle": 123})()
        with patch("muse.process_limits.os.name", "nt"), patch("muse.process_limits.ctypes.WinDLL", return_value=kernel, create=True):
            with self.assertRaisesRegex(ProcessLimitError, "AssignProcessToJobObject"):
                WindowsJobLimits(process, memory_limit_mb=512)
        self.assertEqual(kernel.closed, [89])

    def test_non_windows_fails_closed(self):
        with patch("muse.process_limits.os.name", "posix"):
            with self.assertRaisesRegex(ProcessLimitError, "unavailable"):
                WindowsJobLimits(object(), memory_limit_mb=512)


if __name__ == "__main__":
    unittest.main()