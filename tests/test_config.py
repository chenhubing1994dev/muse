from pathlib import Path
import unittest

from muse.config import load_config


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "muse.example.toml")
        self.assertEqual(config["app"]["name"], "Muse")
        self.assertFalse(config["scheduler"]["enabled"])


if __name__ == "__main__":
    unittest.main()
