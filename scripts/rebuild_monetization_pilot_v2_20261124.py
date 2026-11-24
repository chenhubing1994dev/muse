"""Rebuild the canonical Cantonese pilot after the UTF-8 ASR handoff fix.

Creates a new v2 output directory; preserves v1 as incident evidence. Local-only,
draft-only, no production DB writes, no publishing.
"""
from __future__ import annotations
import hashlib, json, os, shutil, sys, unittest, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"src"))
SOURCE_RELATIVE="data/prometheus/high-value-short-b133"
OUTPUT_RELATIVE="data/monetization/first-cantonese-pilot-2026-11-24-v2"
SOURCE=ROOT/SOURCE_RELATIVE
OUTPUT=ROOT/OUTPUT_RELATIVE
V1=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24"
PRODUCT_OUT=ROOT/"outputs"/"creator_pilot_20261124"

def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def run_tests()->dict:
    suite=unittest.defaultTestLoader.loadTestsFromNames(["tests.test_monetization_pilot"])
    result=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError(f"monetization pilot tests failed: {result.testsRun} tests")
    return {"tests_run":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"status":"passed"}

def main()->int:
    os.environ["HF_HUB_OFFLINE"]="1"
    os.environ["TRANSFORMERS_OFFLINE"]="1"
    if OUTPUT.exists() or OUTPUT.is_symlink(): raise FileExistsError(OUTPUT)
    if not SOURCE.is_dir() or SOURCE.is_symlink(): raise FileNotFoundError(SOURCE)

    tests=run_tests()
    from muse.monetization_pilot import create_cantonese_video_package
    result=create_cantonese_video_package(ROOT,source_relative=SOURCE_RELATIVE,output_relative=OUTPUT_RELATIVE)
    if result.get("status")!="completed" or result.get("release_state")!="draft_only":
        raise RuntimeError(f"pilot build did not complete draft-only: {result}")

    manifest=json.loads((OUTPUT/"manifest.json").read_text(encoding="utf-8"))
    asr=json.loads((OUTPUT/"asr-result.json").read_text(encoding="utf-8"))
    review=json.loads((OUTPUT/"subtitle-review.json").read_text(encoding="utf-8"))
    qa=json.loads((OUTPUT/"qa-report.json").read_text(encoding="utf-8"))
    srt=(OUTPUT/"subtitles.srt").read_text(encoding="utf-8")
    transcript=str(asr.get("transcript",""))
    recognized=str(review.get("recognized_text",""))
    if "\ufffd" in transcript or "\ufffd" in recognized or "\ufffd" in srt:
        raise RuntimeError("UTF-8 rebuild still contains replacement-character mojibake")
    if not transcript.strip() or not recognized.strip() or not srt.strip():
        raise RuntimeError("ASR transcript or SRT is empty")
    for label, text_value in (("transcript", transcript), ("recognized_text", recognized), ("srt", srt)):
        if "人工智能" not in text_value or ("反證" not in text_value and "反证" not in text_value) or "唔肯定" not in text_value:
            raise RuntimeError(f"{label} failed required Cantonese content checks: {text_value[:240]}")
    if manifest.get("quality_gate",{}).get("human_subtitle_review_required") is not True:
        raise RuntimeError("manifest did not preserve mandatory human subtitle review")
    if manifest.get("quality_gate",{}).get("ready_to_publish") is not False:
        raise RuntimeError("release gate unexpectedly opened")
    if manifest.get("release_state")!="draft_only" or manifest.get("publish_api_called") is not False or manifest.get("auto_publish") is not False:
        raise RuntimeError("publication gate unexpectedly opened")
    if review.get("human_review_required") is not True or review.get("publish_blocked") is not True:
        raise RuntimeError("subtitle human-review gate missing")
    cer=float(manifest["voiceover"]["asr_cer"])
    if cer>0.10 or manifest["voiceover"]["asr_gate"]!="passed":
        raise RuntimeError(f"valid UTF-8 ASR did not meet the unchanged 10% threshold: {cer}")
    if qa.get("status")!="passed" or qa.get("full_decode")!="passed" or qa.get("ready_to_publish") is not False:
        raise RuntimeError("technical QA or publication block mismatch")
    video=OUTPUT/manifest["video"]["file"]
    package=OUTPUT/"delivery-package.zip"
    if sha256(video)!=manifest["video"]["sha256"]: raise RuntimeError("video SHA mismatch")
    with zipfile.ZipFile(package) as z:
        bad=z.testzip()
        if bad: raise RuntimeError(f"delivery ZIP CRC failed: {bad}")

    # Preserve the old incident evidence and manual draft as separate sidecars.
    for name in ("ASR-UTF8-INCIDENT.md","manual-subtitles-draft.srt","manual-subtitle-review.json","rights-manifest.json","cost-ledger.json"):
        src=V1/name
        dst=OUTPUT/name
        if src.is_file() and not dst.exists(): shutil.copy2(src,dst)
    # Keep copied rights restrictive and label the inherited cost figure as an estimate.
    rights_path=OUTPUT/"rights-manifest.json"
    if rights_path.is_file():
        rights_data=json.loads(rights_path.read_text(encoding="utf-8"))
        rights_data["derived_from"]="v1_same_source_assets"
        rights_data["review_required"]=True
        rights_data["commercial_use_allowed"]=False
        rights_data["publishable"]=False
        rights_path.write_text(json.dumps(rights_data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    costs_path=OUTPUT/"cost-ledger.json"
    if costs_path.is_file():
        cost_data=json.loads(costs_path.read_text(encoding="utf-8"))
        cost_data["derived_from"]="v1_estimate_only"
        cost_data["build_id"]="first-cantonese-pilot-2026-11-24-v2"
        cost_data["local_compute_estimate_usd"]=cost_data.get("local_compute_estimate_usd",0.02)
        cost_data["actual_metered"]=False
        cost_data["note"]="Inherited estimate only; v2 electricity/GPU time was not independently metered."
        costs_path.write_text(json.dumps(cost_data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (OUTPUT/"README.md").write_text(
        "# Canonical Cantonese Pilot v2\n\n"
        "Rebuilt with the UTF-8 ASR handoff fix. ASR text and SRT passed the content/encoding checks, "
        "but human listening, subtitle correction, visual review, rights review and account authorization remain required. "
        "This package is draft-only and not published.\n",
        encoding="utf-8")
    # Record provenance; do not treat copied estimates as metered costs.
    report={
      "schema":"muse.asr-utf8-rebuild-report.v1",
      "status":"valid_utf8_asr_draft_only",
      "tests":tests,
      "output_dir":OUTPUT_RELATIVE,
      "video_file":manifest["video"]["file"],
      "video_sha256":sha256(video),
      "package_sha256":sha256(package),
      "package_zip_crc":"passed",
      "asr_cer":cer,
      "asr_gate":"passed",
      "transcript_has_replacement_characters":False,
      "recognized_text_has_replacement_characters":False,
      "subtitle_has_replacement_characters":False,
      "human_subtitle_review_required":True,
      "rights_review_required":True,
      "commercial_use_allowed":False,
      "release_state":"draft_only",
      "publish_api_called":False,
      "auto_publish":False,
      "production_database_touched":False,
      "source_v1_preserved":True
    }
    (OUTPUT/"ASR-UTF8-REBUILD-REPORT.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    # Build the versioned digital product ZIP from the current v0.1.1 source tree.
    package_script=ROOT/"scripts"/"package_creator_kit_v011_20261124.py"
    import subprocess
    proc=subprocess.run([sys.executable,str(package_script)],cwd=ROOT,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=300)
    if proc.returncode: raise RuntimeError((proc.stderr or proc.stdout)[-1500:])
    product_manifest=json.loads((PRODUCT_OUT/"AI-Verification-Kit-v0.1.1.manifest.json").read_text(encoding="utf-8"))
    product_zip=PRODUCT_OUT/"AI-Verification-Kit-v0.1.1.zip"
    with zipfile.ZipFile(product_zip) as z:
        bad=z.testzip()
        if bad: raise RuntimeError(f"digital product ZIP CRC failed: {bad}")
    report["digital_product"]={"file":str(product_zip.relative_to(ROOT)).replace("\\","/"),"version":product_manifest["version"],"sha256":sha256(product_zip),"bytes":product_zip.stat().st_size,"zip_crc":"passed","price_hypothesis_usd":product_manifest["price_hypothesis"]["amount"],"price_validated":False,"published":False}
    (OUTPUT/"ASR-UTF8-REBUILD-REPORT.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
