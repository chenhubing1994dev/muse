"""Best-effort-independent OS process-tree limits for isolated experiments.

Windows Job Objects enforce per-process memory, aggregate job memory, active
process count, and kill-on-close. This is not a network sandbox.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class ProcessLimitError(RuntimeError):
    """Requested OS resource limits could not be applied safely."""


class WindowsJobLimits:
    """Own a Windows Job Object attached to one child process tree."""

    def __init__(self, process, *, memory_limit_mb: int, max_processes: int = 16):
        if os.name != "nt":
            raise ProcessLimitError("Windows Job Objects are unavailable on this platform")
        if isinstance(memory_limit_mb, bool) or not isinstance(memory_limit_mb, int) or not 128 <= memory_limit_mb <= 65536:
            raise ValueError("memory_limit_mb must be between 128 and 65536")
        if isinstance(max_processes, bool) or not isinstance(max_processes, int) or not 1 <= max_processes <= 128:
            raise ValueError("max_processes must be between 1 and 128")
        try:
            self._handle = None
            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            signatures = (
                ("CreateJobObjectW", wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
                ("SetInformationJobObject", wintypes.BOOL, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]),
                ("AssignProcessToJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
                ("TerminateJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
                ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE]),
            )
            for function_name, restype, argtypes in signatures:
                try:
                    function = getattr(self._kernel, function_name)
                    function.restype = restype
                    function.argtypes = argtypes
                except AttributeError:
                    pass
            self._handle = self._kernel.CreateJobObjectW(None, None)
            if not self._handle:
                raise ProcessLimitError("CreateJobObjectW failed")
            self._configure(memory_limit_mb, max_processes)
            process_handle = wintypes.HANDLE(int(process._handle))
            if not self._kernel.AssignProcessToJobObject(self._handle, process_handle):
                raise ProcessLimitError("AssignProcessToJobObject failed")
        except Exception as exc:
            self.close()
            if isinstance(exc, (ValueError, ProcessLimitError)):
                raise
            raise ProcessLimitError("Windows Job Object setup failed") from exc

    def _configure(self, memory_limit_mb: int, max_processes: int) -> None:
        class IO_COUNTERS(ctypes.Structure):            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        limit = EXTENDED_LIMIT()
        limit.BasicLimitInformation.LimitFlags = 0x00000100 | 0x00000200 | 0x00002000 | 0x00000008
        limit.BasicLimitInformation.ActiveProcessLimit = max_processes
        byte_limit = memory_limit_mb * 1024 * 1024
        limit.ProcessMemoryLimit = byte_limit
        limit.JobMemoryLimit = byte_limit
        if not self._kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)):
            raise ProcessLimitError("SetInformationJobObject failed")

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle and not self._kernel.TerminateJobObject(self._handle, exit_code):
            raise ProcessLimitError("TerminateJobObject failed")

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            self._handle = None
            self._kernel.CloseHandle(handle)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
