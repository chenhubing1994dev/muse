from pathlib import Path
import argparse
import json
from muse.kinetic_explainer import build

parser = argparse.ArgumentParser(description="Build an original silent 64-second Muse kinetic typography video.")
parser.add_argument("--output", default="outputs/kinetic_ai_fact_check_20261124_v4_noto_ofl")
args = parser.parse_args()
result = build(Path(args.output), Path.cwd())
print(json.dumps(result, ensure_ascii=False, indent=2))
