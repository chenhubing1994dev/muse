from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from muse.process_utils import no_console_window_kwargs


class NoConsoleWindowTests(unittest.TestCase):
    def test_posix_does_not_receive_windows_creation_flags(self):
        with patch("muse.process_utils.os.name", "posix"):
            self.assertEqual(no_console_window_kwargs(), {})

    def test_windows_uses_create_no_window_flag(self):
        with patch("muse.process_utils.os.name", "nt"), patch.object(
            subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True
        ):
            self.assertEqual(no_console_window_kwargs(), {"creationflags": 0x08000000})


if __name__ == "__main__":
    unittest.main()
