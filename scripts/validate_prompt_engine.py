from __future__ import annotations

import compileall
import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def main() -> int:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    assert project["project"]["scripts"]["muse-prompt"] == "muse.prompt_engine:main"

    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for pattern in ("test_prompt_engine.py", "test_specialist_router.py"):
        suite.addTests(loader.discover(
            str(ROOT / "tests"), pattern=pattern, top_level_dir=str(ROOT / "tests")
        ))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    completed = subprocess.run(
        [sys.executable, "-m", "muse.prompt_engine", "adapt", "--model", "Claude",
         "--task", "总结公开研究资料", "--constraint", "不上传私人资料",
         "--success", "列出来源", "--evidence"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    if completed.returncode != 0:
        print(completed.stderr)
        return completed.returncode
    payload = json.loads(completed.stdout)
    assert payload["profile"] == "anthropic"
    assert payload["quality"]["checks"]["safety_boundary_present"] is True
    assert compileall.compile_file(str(ROOT / "src" / "muse" / "prompt_engine.py"), quiet=1)
    assert compileall.compile_file(str(ROOT / "tests" / "test_prompt_engine.py"), quiet=1)
    print("MUSE_PROMPT_ENGINE_VALIDATION_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
