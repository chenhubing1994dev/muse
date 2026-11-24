"""Finalize the v2 Cantonese pilot with a corrected human-review subtitle draft.

Preserves raw ASR output and the original package, writes a corrected draft SRT,
repackages the deliverables, runs extended video QA, and creates the v0.1.1
digital-product ZIP. Never publishes or touches production databases.
"""
from __future__ import annotations
import hashlib, json, shutil, subprocess, sys, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
PILOT=ROOT/"data"/"monetization"/"first-cantonese-pilot-2026-11-24-v2"
V1=ROOT/"data"/"monetization"/"first-cantonese-pilot-2026-11-24"
OUT=ROOT/"outputs"/"creator_pilot_20261124"
HANDOFF=OUT/"canonical-pilot-handoff.json"
MANUAL_SRT="""1
00:00:00,000 --> 00:00:04,400
人工智能可以答得好快，但未必答得啱。

2
00:00:04,400 --> 00:00:08,820
先查來源，再搵反證。

3
00:00:08,820 --> 00:00:11,920
唔肯定，就先唔好信。
"""

def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def read_json(path:Path)->dict:
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path:Path,value:dict)->None:
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def main()->int:
    for required in ("manifest.json","asr-result.json","subtitle-review.json","subtitles.srt","qa-report.json","release-checklist.json","video.mp4","voiceover.wav","delivery-package.zip"):
        if not (PILOT/required).is_file(): raise FileNotFoundError(PILOT/required)
    if HANDOFF.exists() or HANDOFF.is_symlink(): raise FileExistsError(HANDOFF)

    manifest=read_json(PILOT/"manifest.json")
    asr=read_json(PILOT/"asr-result.json")
    review=read_json(PILOT/"subtitle-review.json")
    qa=read_json(PILOT/"qa-report.json")
    release=read_json(PILOT/"release-checklist.json")
    transcript=str(asr.get("transcript",""))
    recognized=str(review.get("recognized_text",""))
    old_srt=(PILOT/"subtitles.srt").read_text(encoding="utf-8-sig")
    if any("\ufffd" in x for x in (transcript,recognized,old_srt)):
        raise RuntimeError("v2 still contains UTF-8 replacement-character mojibake")
    cer=float(review.get("character_error_rate",1.0))
    if cer>0.10 or manifest.get("quality_gate",{}).get("human_subtitle_review_required") is not True:
        raise RuntimeError("ASR threshold or human-review gate is invalid")
    if manifest.get("release_state")!="draft_only" or manifest.get("quality_gate",{}).get("ready_to_publish") is not False:
        raise RuntimeError("release gate unexpectedly opened")

    # Preserve original ASR SRT and ZIP before replacing the default subtitle draft.
    asr_srt=PILOT/"subtitles.asr.srt"
    if not asr_srt.exists(): shutil.copy2(PILOT/"subtitles.srt",asr_srt)
    old_zip=PILOT/"delivery-package.asr-v2.zip"
    if not old_zip.exists(): shutil.copy2(PILOT/"delivery-package.zip",old_zip)
    old_hash=PILOT/"package.asr-v2.sha256"
    if (PILOT/"package.sha256").is_file() and not old_hash.exists():
        shutil.copy2(PILOT/"package.sha256",old_hash)

    # ASR mishears 搵反證 as 揾反正 and splits phrases across cue boundaries.
    (PILOT/"manual-subtitles-draft.srt").write_text(MANUAL_SRT,encoding="utf-8")
    (PILOT/"subtitles.srt").write_text(MANUAL_SRT,encoding="utf-8")
    manual_review={
      "schema_version":1,
      "status":"manual_draft_needs_human_review",
      "source":"narration.txt",
      "asr_transcript_trusted_utf8":True,
      "asr_cer":cer,
      "known_asr_mistranscription":"揾反正 -> 搵反證",
      "known_segment_boundary_issues":["答得啱 split across ASR subtitle cues","唔肯定 split across ASR subtitle cues"],
      "manual_draft_applied_to_subtitles_srt":True,
      "original_asr_subtitles_preserved_as":"subtitles.asr.srt",
      "human_review_required":True,
      "publish_blocked":True
    }
    write_json(PILOT/"manual-subtitle-review.json",manual_review)
    incident_note="""# Cantonese pilot v2 — ASR and subtitle review

The UTF-8 handoff issue is resolved in this v2 output: the transcript is readable Unicode and contains no U+FFFD replacement characters. The measured CER is 6.25%, below the unchanged 10% gate.

The recognizer still mishears 「搵反證」 as 「揾反正」, and its automatic SRT splits phrases across cue boundaries. The raw ASR SRT is preserved as subtitles.asr.srt. The default subtitles.srt is now a corrected editorial draft aligned to the approved narration; it still requires a human listen/watch pass.

This is a draft-only deliverable. Human voice listening, subtitle review, visual review, source/fact review, rights review, AI disclosure review and account authorization remain pending. No publication API was called.
"""
    (PILOT/"ASR-UTF8-REBUILD-REPORT.md").write_text(incident_note,encoding="utf-8")
    readme="""# Canonical Cantonese Pilot v2

- Video: video.mp4 — 1080×1920, H.264/AAC, 24.512 seconds; full decode passed.
- Local Cantonese TTS and local Whisper ASR; transcript is valid UTF-8, reported CER 6.25%.
- Raw ASR captions are preserved in subtitles.asr.srt.
- subtitles.srt is the corrected editorial draft from the approved narration; human listen/watch review is still required.
- Rights, account authorization, human review and publication gates remain closed. This package is not published and is not yet approved for commercial reuse.
"""
    (PILOT/"README.md").write_text(readme,encoding="utf-8")

    # Copy restrictive rights/cost sidecars from v1, marking inherited estimates.
    for name in ("rights-manifest.json","cost-ledger.json"):
        src=V1/name
        dst=PILOT/name
        if src.is_file() and not dst.exists(): shutil.copy2(src,dst)
    rights_path=PILOT/"rights-manifest.json"
    if rights_path.is_file():
        rights=read_json(rights_path)
        rights["derived_from"]="v1_same_source_assets"
        rights["review_required"]=True
        rights["commercial_use_allowed"]=False
        rights["publishable"]=False
        write_json(rights_path,rights)
    cost_path=PILOT/"cost-ledger.json"
    if cost_path.is_file():
        costs=read_json(cost_path)
        costs["build_id"]="first-cantonese-pilot-2026-11-24-v2"
        costs["derived_from"]="v1_estimate_only"
        costs["actual_metered"]=False
        costs["note"]="Inherited estimate only; v2 electricity/GPU time was not independently metered."
        write_json(cost_path,costs)

    # Extended technical and motion QA; motion sampling is deliberately bounded.
    from muse.media import discover_media_tools
    from muse.video_qa import inspect_video_file
    from muse.video_motion_diagnostics import inspect_video_motion
    tool_map={x.tool_id:x.executable for x in discover_media_tools().tools}
    technical=inspect_video_file(
      PILOT/"video.mp4",ffprobe_path=tool_map["ffprobe"],ffmpeg_path=tool_map["ffmpeg"],
      expected_width=1080,expected_height=1920,expected_fps=24,
      min_duration_seconds=20,max_duration_seconds=30,require_audio=True)
    try: motion=inspect_video_motion(PILOT/"video.mp4",max_frames=24)
    except Exception as exc: motion={"status":"unavailable","error":f"{type(exc).__name__}: {str(exc)[:250]}"}
    extended={"schema":"muse.video-qa-extended.v1","technical":technical,"motion_diagnostics":motion,
      "human_visual_review_required":True,"human_listening_review_required":True,
      "rights_review_required":True,"publishable":False,"commercial_use_allowed":False}
    write_json(PILOT/"video-qa-extended.json",extended)

    qa["subtitle_review_status"]="manual_draft_needs_human_review"
    qa["ready_to_publish"]=False
    write_json(PILOT/"qa-report.json",qa)
    release["release_state"]="draft_only"
    release["can_publish"]=False
    release["human_editorial_review"]="pending"
    release["human_voice_listening_review"]="pending"
    release["visual_and_model_rights_review"]="pending"
    write_json(PILOT/"release-checklist.json",release)

    # Update manifest hashes; exclude ZIP and its hash file to avoid circular hashing.
    excluded_for_hash={"manifest.json","delivery-package.zip","package.sha256","delivery-package.asr-v2.zip","package.asr-v2.sha256"}
    excluded_for_zip={"delivery-package.zip","package.sha256","delivery-package.asr-v2.zip","package.asr-v2.sha256"}
    manifest["files"]={}
    for path in sorted(PILOT.iterdir()):
        if path.is_file() and path.name not in excluded_for_hash:
            manifest["files"][path.name]=sha256(path)
    manifest["quality_gate"]["human_subtitle_review_required"]=True
    manifest["quality_gate"]["ready_to_publish"]=False
    manifest["release_state"]="draft_only"
    manifest["auto_publish"]=False
    manifest["publish_api_called"]=False
    manifest["subtitle_policy"]={
      "default_srt":"manual editorial draft; human review required",
      "raw_asr_srt":"subtitles.asr.srt",
      "asr_cer":cer,
      "known_mistranscription":"揾反正 -> 搵反證"
    }
    write_json(PILOT/"manifest.json",manifest)

    package=PILOT/"delivery-package.zip"
    with zipfile.ZipFile(package,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(PILOT.rglob("*")):
            if path.is_file() and path.name not in excluded_for_zip and path != package:
                archive.write(path,arcname=path.relative_to(PILOT).as_posix())
    with zipfile.ZipFile(package) as archive:
        bad=archive.testzip()
        if bad: raise RuntimeError(f"delivery ZIP CRC failure: {bad}")
    package_hash=sha256(package)
    (PILOT/"package.sha256").write_text(f"{package_hash}  delivery-package.zip\n",encoding="ascii")

    product_script=ROOT/"scripts"/"package_creator_kit_v011_20261124.py"
    proc=subprocess.run([sys.executable,str(product_script)],cwd=ROOT,capture_output=True,
      text=True,encoding="utf-8",errors="replace",timeout=300)
    if proc.returncode: raise RuntimeError((proc.stderr or proc.stdout)[-1500:])
    product_manifest=read_json(OUT/"AI-Verification-Kit-v0.1.1.manifest.json")
    product_zip=OUT/"AI-Verification-Kit-v0.1.1.zip"
    with zipfile.ZipFile(product_zip) as archive:
        bad=archive.testzip()
        if bad: raise RuntimeError(f"digital product ZIP CRC failure: {bad}")

    handoff={
      "schema":"muse.canonical-pilot-handoff.v2",
      "status":"draft_only_human_review_required",
      "pilot_dir":"data/monetization/first-cantonese-pilot-2026-11-24-v2",
      "video_file":"video.mp4","video_sha256":sha256(PILOT/"video.mp4"),
      "video_bytes":(PILOT/"video.mp4").stat().st_size,
      "duration_seconds":manifest["video"]["duration_seconds"],
      "resolution":"1080x1920","codec":"H.264/AAC",
      "asr_utf8_valid":True,"asr_cer":cer,
      "asr_known_mistranscription":"揾反正 -> 搵反證",
      "manual_subtitles_applied":True,"raw_asr_subtitles_preserved":"subtitles.asr.srt",
      "human_subtitle_review_required":True,"human_voice_listening_review_required":True,
      "extended_video_qa_status":technical.get("status"),
      "extended_video_qa_warnings":technical.get("warnings",[]),
      "motion_summary":motion.get("summary"),
      "rights_review_required":True,"commercial_use_allowed":False,
      "release_state":"draft_only","account_authorization":"not_connected",
      "publish_api_called":False,"auto_publish":False,
      "delivery_package_sha256":package_hash,"delivery_package_zip_crc":"passed",
      "digital_product_file":"AI-Verification-Kit-v0.1.1.zip",
      "digital_product_sha256":sha256(product_zip),
      "digital_product_version":product_manifest["version"],
      "price_hypothesis_usd":product_manifest["price_hypothesis"]["amount"],
      "price_validated":False,"orders":0,"settled_revenue":0,
      "paid_api_cost_usd":0,"paid_asset_cost_usd":0,"cloud_compute_cost_usd":0,
      "local_compute_estimate_usd":0.02,"actual_metered":False,
      "production_database_touched":False
    }
    write_json(HANDOFF,handoff)
    print(json.dumps(handoff,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
