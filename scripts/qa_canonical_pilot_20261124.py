"""Extended QA and versioned packaging for the canonical Muse monetization pilot."""
from __future__ import annotations
import hashlib, json, subprocess, sys, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/"src") not in sys.path: sys.path.insert(0,str(ROOT/"src"))
PILOT=ROOT/"data"/"monetization"/"first-cantonese-pilot-2026-11-24"
OUT=ROOT/"outputs"/"creator_pilot_20261124"
QA_PATH=PILOT/"video-qa-extended.json"
HANDOFF=OUT/"canonical-pilot-handoff.json"

def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def main()->int:
    for p in (QA_PATH,HANDOFF):
        if p.exists() or p.is_symlink(): raise FileExistsError(p)
    manifest=json.loads((PILOT/"manifest.json").read_text(encoding="utf-8"))
    qa=json.loads((PILOT/"qa-report.json").read_text(encoding="utf-8"))
    rights=json.loads((PILOT/"rights-manifest.json").read_text(encoding="utf-8"))
    costs=json.loads((PILOT/"cost-ledger.json").read_text(encoding="utf-8"))
    asr=json.loads((PILOT/"asr-result.json").read_text(encoding="utf-8"))
    review=json.loads((PILOT/"subtitle-review.json").read_text(encoding="utf-8"))
    video=PILOT/manifest["video"]["file"]
    package=PILOT/"delivery-package.zip"
    if not video.is_file() or not package.is_file(): raise FileNotFoundError("canonical video/package missing")
    if sha256(video)!=manifest["video"]["sha256"]: raise ValueError("canonical video SHA mismatch")
    if manifest.get("release_state")!="draft_only" or manifest.get("publish_api_called") is not False:
        raise ValueError("canonical release/publish gate changed unexpectedly")
    if manifest.get("auto_publish") is not False or manifest.get("account_authorization")!="not_connected":
        raise ValueError("canonical account/auto-publish gate changed unexpectedly")
    if rights.get("publishable") is not False or any(a.get("commercial_use_allowed") for a in rights.get("assets",[])):
        raise ValueError("rights gate unexpectedly opened")
    if review.get("human_review_required") is not True or review.get("publish_blocked") is not True:
        raise ValueError("subtitle human review gate missing")
    if float(manifest["voiceover"]["asr_cer"])>0.10 or manifest["voiceover"]["asr_gate"]!="passed":
        raise ValueError("pinned ASR threshold not passed")
    with zipfile.ZipFile(package) as z:
        bad=z.testzip()
        if bad: raise RuntimeError(f"canonical package CRC failure: {bad}")

    from muse.media import discover_media_tools
    from muse.video_qa import inspect_video_file
    from muse.video_motion_diagnostics import inspect_video_motion
    tools={x.tool_id:x.executable for x in discover_media_tools().tools}
    technical=inspect_video_file(
        video,ffprobe_path=tools["ffprobe"],ffmpeg_path=tools["ffmpeg"],
        expected_width=1080,expected_height=1920,expected_fps=24,
        min_duration_seconds=20,max_duration_seconds=30,require_audio=True)
    try:
        motion=inspect_video_motion(video,max_frames=180)
    except Exception as exc:
        motion={"status":"unavailable","error":f"{type(exc).__name__}: {str(exc)[:300]}"}
    extended={"schema":"muse.video-qa-extended.v1","technical":technical,
      "motion_diagnostics":motion,"human_listening_review_required":True,
      "human_visual_review_required":True,"rights_review_required":True,
      "publishable":False,"commercial_use_allowed":False}
    QA_PATH.write_text(json.dumps(extended,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    package_script=ROOT/"scripts"/"package_creator_kit_v011_20261124.py"
    proc=subprocess.run([sys.executable,str(package_script)],cwd=ROOT,capture_output=True,
      text=True,encoding="utf-8",errors="replace",timeout=300)
    if proc.returncode: raise RuntimeError((proc.stderr or proc.stdout)[-1500:])
    product_manifest=json.loads((OUT/"AI-Verification-Kit-v0.1.1.manifest.json").read_text(encoding="utf-8"))
    if product_manifest.get("zip_crc")!="passed" or product_manifest.get("published") is not False:
        raise RuntimeError("digital product v0.1.1 package validation failed")

    handoff={
      "schema":"muse.canonical-pilot-handoff.v1","status":"draft_only_review_required",
      "pilot_dir":"data/monetization/first-cantonese-pilot-2026-11-24",
      "video_file":manifest["video"]["file"],"video_sha256":sha256(video),
      "video_bytes":video.stat().st_size,"duration_seconds":manifest["video"]["duration_seconds"],
      "resolution":"1080x1920","fps":24,"codec":"H.264/AAC",
      "asr_gate":manifest["voiceover"]["asr_gate"],"asr_cer":manifest["voiceover"]["asr_cer"],
      "voice_model":manifest["voiceover"]["model"],"voice_model_license":manifest["voiceover"]["license"],
      "human_listening_review_required":True,"subtitle_review_required":True,
      "technical_qa_status":qa.get("status"),"extended_qa_status":technical.get("status"),
      "extended_qa_warnings":technical.get("warnings",[]),"motion_summary":motion.get("summary"),
      "rights_review_required":True,"commercial_use_allowed":False,"publishable":False,
      "account_authorization":"not_connected","publish_api_called":False,"auto_publish":False,
      "paid_api_cost_usd":costs.get("paid_api_cost_usd",0),
      "paid_asset_cost_usd":costs.get("paid_asset_cost_usd",0),
      "cloud_compute_cost_usd":costs.get("cloud_compute_cost_usd",0),
      "local_compute_estimate_usd":costs.get("local_compute_estimate_usd"),
      "orders":0,"settled_revenue":costs.get("settled_revenue",0),
      "digital_product_file":"AI-Verification-Kit-v0.1.1.zip",
      "digital_product_sha256":product_manifest["sha256"],
      "digital_product_version":product_manifest["version"],
      "price_hypothesis_usd":product_manifest["price_hypothesis"]["amount"],
      "price_validated":False,
      "production_database_touched":False
    }
    HANDOFF.write_text(json.dumps(handoff,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(handoff,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
