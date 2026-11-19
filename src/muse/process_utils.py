"""Small cross-platform helpers for quiet, explicitly managed child processes."""

from __future__ import annotations

import os
import subprocess


def no_console_window_kwargs() -> dict[str, int]:
    """Hide a Windows console child without changing its pipes or process lifecycle.

    GUI-launched Muse components can call console CLIs while still capturing
    stdin/stdout/stderr synchronously. CREATE_NO_WINDOW avoids a transient
    desktop console; on POSIX the helper adds no platform-specific arguments.
    """
    if os.name != "nt":
        return {}
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flag} if flag else {}
