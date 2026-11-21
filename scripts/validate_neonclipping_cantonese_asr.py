from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / ".muse-canto-tts-py314"
SRC = ROOT / "src"
VIDEO = ROOT / "data" / "handoff" / "prosodic-boundary-audit-20261119" / "wan21_cantonese_narrated_demo_v2.mp4"
MODEL = ROOT / "models" / "whisper-small-cantonese"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))



def main() -> int:
    if not VIDEO.is_file() or VIDEO.is_symlink():
        raise FileNotFoundError(f"existing real Cantonese-narrated video not found: {VIDEO}")
    if not MODEL.is_dir() or MODEL.is_symlink():
        raise FileNotFoundError(f"pinned local Cantonese model directory not found: {MODEL}")
    from muse.cantonese_asr import _verified_model
    _, model_digest = _verified_model(MODEL)

    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(SRC), str(PACKAGES), existing) if item
    )
    with tempfile.TemporaryDirectory(prefix="muse-neon-rank-cantonese-") as temporary:
        output = Path(temporary) / "analysis.json"
        completed = subprocess.run([
            "py", "-3.14", "-m", "muse.video_highlights", "rank-cantonese",
            str(VIDEO), str(output), "--model-dir", str(MODEL),
            "--device", "cpu", "--compute-type", "int8", "--spectral-2-4khz",
        ], cwd=ROOT, env=environment, capture_output=True, text=True, timeout=600, check=False)
        if completed.returncode != 0 or not output.is_file():
            raise RuntimeError(
                "rank-cantonese CLI failed: " + (completed.stderr or completed.stdout)[-3000:]
            )
        result = json.loads(output.read_text(encoding="utf-8"))
    words = result.get("words", [])
    candidate_signals = [
        candidate.get("signals", {}) for candidate in result.get("candidates", [])
    ]
    spectral_signal_count = sum(
        "band_energy_2_4khz_db" in signals for signals in candidate_signals
    )
    if not words or not result.get("spectral_feature_2_4khz") or spectral_signal_count < 1:
        raise RuntimeError("rank-cantonese CLI did not return word timestamps and spectral candidate signals")
    print(json.dumps({
        "status": "passed",
        "python_runtime": "3.14",
        "model_sha256": model_digest,
        "analysis_mode": result.get("analysis_mode"),
        "duration_seconds": result.get("duration_seconds"),
        "transcript_engine": result.get("transcript_engine"),
        "transcript": result.get("transcript"),
        "word_count": len(words),
        "candidate_count": result.get("candidate_count"),
        "spectral_feature_2_4khz": result.get("spectral_feature_2_4khz"),
        "spectral_candidate_signal_count": spectral_signal_count,
        "human_listening_review_required": result.get("human_listening_review_required"),
    }, ensure_ascii=False, indent=2))
    print("MUSE_NEONCLIPPING_RANK_CANTONESE_CLI_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
