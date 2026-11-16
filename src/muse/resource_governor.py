"""Conservative host-resource probing and bounded worker concurrency decisions.

GPU memory is deliberately not guessed or inferred from CPU/RAM. GPU-bound
work must supply a separately verified GPU-capability policy before dispatch.
"""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HostResourceSnapshot:
    logical_cpu_count: int | None = None
    available_memory_bytes: int | None = None
    total_memory_bytes: int | None = None
    gpu_available_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        for name in ("logical_cpu_count", "total_memory_bytes"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(f"{name} must be a positive integer or None")
        for name in ("available_memory_bytes", "gpu_available_memory_bytes"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if (self.available_memory_bytes is not None and self.total_memory_bytes is not None
                and self.available_memory_bytes > self.total_memory_bytes):
            raise ValueError("available memory cannot exceed total memory")


@dataclass(frozen=True)
class ResourceDecision:
    recommended_workers: int
    maximum_workers: int
    cpu_cap: int
    memory_cap: int
    status: str
    logical_cpu_count: int | None
    available_memory_bytes: int | None
    gpu_available_memory_bytes: int | None
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommended_workers": self.recommended_workers,
            "maximum_workers": self.maximum_workers,
            "cpu_cap": self.cpu_cap,
            "memory_cap": self.memory_cap,
            "status": self.status,
            "logical_cpu_count": self.logical_cpu_count,
            "available_memory_bytes": self.available_memory_bytes,
            "gpu_available_memory_bytes": self.gpu_available_memory_bytes,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ResourcePolicy:
    """A conservative concurrency ceiling; it never promises full machine saturation."""

    maximum_workers: int = 32
    reserve_cpu_cores: int = 1
    reserve_memory_bytes: int = 2 * 1024**3
    estimated_memory_per_worker_bytes: int = 512 * 1024**2

    def __post_init__(self) -> None:
        if (isinstance(self.maximum_workers, bool) or not isinstance(self.maximum_workers, int)
                or not 1 <= self.maximum_workers <= 32):
            raise ValueError("maximum_workers must be between 1 and 32")
        if (isinstance(self.reserve_cpu_cores, bool) or not isinstance(self.reserve_cpu_cores, int)
                or not 0 <= self.reserve_cpu_cores <= 32):
            raise ValueError("reserve_cpu_cores must be between 0 and 32")
        if (isinstance(self.reserve_memory_bytes, bool) or not isinstance(self.reserve_memory_bytes, int)
                or self.reserve_memory_bytes < 0):
            raise ValueError("reserve_memory_bytes must be a non-negative integer")
        if (isinstance(self.estimated_memory_per_worker_bytes, bool)
                or not isinstance(self.estimated_memory_per_worker_bytes, int)
                or self.estimated_memory_per_worker_bytes < 1):
            raise ValueError("estimated_memory_per_worker_bytes must be positive")

    def decide(self, snapshot: HostResourceSnapshot) -> ResourceDecision:
        if not isinstance(snapshot, HostResourceSnapshot):
            raise TypeError("snapshot must be a HostResourceSnapshot")

        reasons: list[str] = []
        if snapshot.logical_cpu_count is None:
            cpu_cap = 1
            reasons.append("cpu_unknown_conservative")
        else:
            cpu_cap = max(0, snapshot.logical_cpu_count - self.reserve_cpu_cores)
            if cpu_cap == 0:
                reasons.append("cpu_reserve_protected")

        if snapshot.available_memory_bytes is None:
            memory_cap = 1
            reasons.append("memory_unknown_conservative")
        else:
            usable_memory = snapshot.available_memory_bytes - self.reserve_memory_bytes
            memory_cap = max(0, usable_memory // self.estimated_memory_per_worker_bytes)
            if memory_cap == 0:
                reasons.append("memory_reserve_protected")

        recommended = min(self.maximum_workers, cpu_cap, memory_cap)
        status = ("cpu_pressure" if cpu_cap == 0 else "memory_pressure") if recommended == 0 else "ready"
        if snapshot.gpu_available_memory_bytes is None:
            reasons.append("gpu_memory_unverified")

        return ResourceDecision(
            recommended_workers=recommended,
            maximum_workers=self.maximum_workers,
            cpu_cap=cpu_cap,
            memory_cap=memory_cap,
            status=status,
            logical_cpu_count=snapshot.logical_cpu_count,
            available_memory_bytes=snapshot.available_memory_bytes,
            gpu_available_memory_bytes=snapshot.gpu_available_memory_bytes,
            reasons=tuple(reasons),
        )


def _probe_memory() -> tuple[int | None, int | None]:
    """Return available/total physical memory without third-party packages."""
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        try:
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            function = kernel32.GlobalMemoryStatusEx
            function.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
            function.restype = ctypes.c_int
            if function(ctypes.byref(status)):
                return int(status.ullAvailPhys), int(status.ullTotalPhys)
        except (AttributeError, OSError, TypeError, ValueError):
            return None, None
        return None, None

    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        if page_size > 0 and available_pages >= 0 and total_pages > 0:
            return available_pages * page_size, total_pages * page_size
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return None, None


def probe_host_resources() -> HostResourceSnapshot:
    """Take one best-effort local snapshot; GPU memory remains unknown by default."""
    cpu_count = os.cpu_count()
    if isinstance(cpu_count, bool) or not isinstance(cpu_count, int) or cpu_count < 1:
        cpu_count = None
    available, total = _probe_memory()
    return HostResourceSnapshot(
        logical_cpu_count=cpu_count,
        available_memory_bytes=available,
        total_memory_bytes=total,
        gpu_available_memory_bytes=None,
    )
