"""Run a real local Wan 2.1 GPU clip and verify its Muse video product."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(workspace / "src"))
    from muse.video_product_builder import VideoProductBuilder

    builder = VideoProductBuilder(workspace)
    result = builder.build_from_prompt(
        "Muse Wan 2.1 RX 6750 GRE 实机验收",
        "A cinematic close-up of a tiny paper boat floating through a moonlit stream, gentle ripples, soft volumetric light, realistic film texture. Smooth natural camera movement, coherent details, no text.",
        negative_prompt="text, watermark, logo, flicker, distorted geometry, low quality",
        aspect_ratio="16:9", duration_seconds=1.0, steps=8,
        seed=20261114, timeout_seconds=1800,
        motion_profile="brand_intro_outro",
    )
    print("BUILD_RESULT", json.dumps(result, ensure_ascii=False, default=str))
    verified = builder.verify_path(
        workspace / "data" / "products", result["initiative_id"], result["version_id"]
    )
    print("VERIFIED", json.dumps(verified, ensure_ascii=False, default=str))
    if verified.get("validated") is not True:
        raise RuntimeError("Wan GPU product failed Muse product verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
