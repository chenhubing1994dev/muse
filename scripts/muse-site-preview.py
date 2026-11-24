from pathlib import Path
import argparse
import json
from muse.site_preview import build_preview

parser = argparse.ArgumentParser(description="Build an isolated static Muse site preview bundle.")
parser.add_argument("--output", default="outputs/site_launch_preview_20261124_v3")
args = parser.parse_args()
print(json.dumps(build_preview(Path(args.output), Path.cwd()), ensure_ascii=False, indent=2))
