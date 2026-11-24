"""Run the shared local video builder into a fresh v4 output path."""
from pathlib import Path
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"scripts"/"build_cantonese_pilot_v3.py"
spec=importlib.util.spec_from_file_location("muse_build_cantonese_pilot_v3",SOURCE)
if spec is None or spec.loader is None: raise RuntimeError("cannot load shared v3 builder")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.OUT=ROOT/"data"/"monetization"/"first-cantonese-pilot-2026-11-24-v4"
module.HANDOFF=ROOT/"outputs"/"creator_pilot_20261124"/"canonical-pilot-handoff-v4.json"
result=module.main()
if result: raise SystemExit(result)
new_hash=module.sha256(module.OUT/"video.mp4")
source_hash=module.sha256(module.V2/"video.mp4")
if new_hash==source_hash:
    raise RuntimeError("v4 MP4 is byte-identical to v2; voiceover mux was not proven")
print({"v4_video_sha256":new_hash,"v2_video_sha256":source_hash,"different":True,"output":str(module.OUT.relative_to(ROOT))})
