"""Create v5: replace black leader/tail, keep the already-mixed v4 audio,
and embed a corrected manual Cantonese subtitle track. Draft-only.
"""
from __future__ import annotations
import hashlib,json,shutil,subprocess,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
V4=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24-v4"
V2=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24-v2"
OUT=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24-v5"
HANDOFF=ROOT/"outputs/creator_pilot_20261124/canonical-pilot-handoff-v5.json"
MANUAL_SRT="""1
00:00:00,000 --> 00:00:03,820
你敢信人工智能每句話？

2
00:00:04,400 --> 00:00:07,940
流暢唔等於真實。

3
00:00:07,940 --> 00:00:12,160
拆開人名、日期、數字逐條核驗；

4
00:00:12,160 --> 00:00:16,580
查原始來源，確認原文支持；

5
00:00:16,580 --> 00:00:21,040
主動搵反證。搵唔到來源，

6
00:00:21,040 --> 00:00:22,100
就標記未證實。
"""

def sha(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()

def load(path:Path)->dict:return json.loads(path.read_text(encoding="utf-8"))
def save(path:Path,obj:dict)->None:path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def run(cmd:list[str],timeout:int=600)->None:
    p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=timeout)
    if p.returncode:raise RuntimeError("command failed: "+" ".join(cmd[:8])+"\n"+(p.stderr or p.stdout)[-2000:])

def main()->int:
    if OUT.exists() or OUT.is_symlink():raise FileExistsError(OUT)
    if HANDOFF.exists() or HANDOFF.is_symlink():raise FileExistsError(HANDOFF)
    for name in ("video.mp4","voiceover.wav","cover.jpg","subtitles.srt","manifest.json","asr-result.json","subtitle-review.json","storyboard.json","narration.txt"):
        if not (V4/name).is_file():raise FileNotFoundError(V4/name)
    manifest=load(V4/"manifest.json")
    review=load(V4/"subtitle-review.json")
    asr=load(V4/"asr-result.json")
    cer=float(review.get("character_error_rate",1.0))
    if cer<=0 or cer>1:raise RuntimeError("invalid ASR CER")
    if sha(V4/"voiceover.wav")!=manifest["voiceover"]["sha256"]:
        raise RuntimeError("voiceover asset SHA does not match manifest")
    if sha(V4/"video.mp4")==sha(V2/"video.mp4"):
        raise RuntimeError("v4 video is identical to v2; refusing to assume voiceover mux")

    from muse.media import discover_media_tools
    from muse.video_qa import inspect_video_file
    from muse.video_motion_diagnostics import inspect_video_motion
    tools={x.tool_id:x.executable for x in discover_media_tools().tools}
    ffmpeg,ffprobe=tools["ffmpeg"],tools["ffprobe"]
    encoders=subprocess.run([ffmpeg,"-hide_banner","-encoders"],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=60).stdout
    encoder="h264_amf" if "h264_amf" in encoders else "libx264"
    enc_args=(["-c:v","h264_amf","-quality","quality","-b:v","12M","-maxrate","16M","-bufsize","24M"]
      if encoder=="h264_amf" else ["-c:v","libx264","-preset","medium","-crf","18"])

    OUT.mkdir(parents=True)
    copy_names=("voiceover.wav","cover.jpg","asr-result.json","subtitle-review.json","narration.txt",
      "source-notes.json","platform-drafts.json","release-checklist.json")
    for name in copy_names:shutil.copy2(V4/name,OUT/name)
    shutil.copy2(V4/"subtitles.srt",OUT/"subtitles.asr.srt")
    shutil.copy2(V4/"storyboard.json",OUT/"storyboard.source.json")
    for name in ("rights-manifest.json","cost-ledger.json","manual-subtitle-review.json"):
        src=V2/name
        if src.is_file():shutil.copy2(src,OUT/name)
    social=ROOT/"outputs/creator_pilot_20261124/social-content-kit.md"
    if social.is_file():shutil.copy2(social,OUT/"social-content-kit.md")
    (OUT/"manual-subtitles-draft.srt").write_text(MANUAL_SRT,encoding="utf-8")
    (OUT/"subtitles.srt").write_text(MANUAL_SRT,encoding="utf-8")
    manual_review={
      "schema_version":1,"status":"manual_draft_needs_human_review",
      "source":"narration.txt","asr_cer":cer,"asr_gate":"needs_review",
      "known_asr_issues":["多处粤语词识别错误","ASR 将多个词组切分错误"],
      "manual_draft_applied_to_subtitles_srt":True,
      "original_asr_srt_preserved":"subtitles.asr.srt",
      "subtitle_track_embedded_as":"mov_text",
      "human_review_required":True,"publish_blocked":True
    }
    save(OUT/"manual-subtitle-review.json",manual_review)

    with __import__("tempfile").TemporaryDirectory(prefix="muse-v5-") as td:
        td=Path(td)
        intro,scene,outro,video_only=(td/x for x in ("intro.mp4","scene.mp4","outro.mp4","video-only.mp4"))
        cover=V4/"cover.jpg"
        base=V4/"video.mp4"
        card_filter="scale=1166:2073:flags=lanczos,crop=1080:1920:x='(iw-ow)/2+12*sin(t*0.7)':y='(ih-oh)/2+8*cos(t*0.5)',fps=24,format=yuv420p"
        scene_filter="trim=start=3:end=21.5,setpts=PTS-STARTPTS,scale=1166:2073:flags=lanczos,crop=1080:1920:x='(iw-ow)/2+18*sin(t*0.6)':y='(ih-oh)/2+10*cos(t*0.3)',fps=24,format=yuv420p"
        for target in (intro,outro):
            run([ffmpeg,"-y","-loop","1","-framerate","24","-i",str(cover),"-t","3",
              "-vf",card_filter,"-an",*enc_args,"-r","24","-pix_fmt","yuv420p","-movflags","+faststart",str(target)])
        run([ffmpeg,"-y","-i",str(base),"-vf",scene_filter,"-an",*enc_args,
          "-r","24","-pix_fmt","yuv420p","-movflags","+faststart",str(scene)])
        run([ffmpeg,"-y","-i",str(intro),"-i",str(scene),"-i",str(outro),
          "-filter_complex","[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
          "-map","[v]",*enc_args,"-r","24","-pix_fmt","yuv420p","-an",str(video_only)])
        final=OUT/"video.mp4"
        # v4's audio already contains the voiceover + ambient bed; preserve it exactly.
        run([ffmpeg,"-y","-i",str(video_only),"-i",str(base),"-i",str(OUT/"subtitles.srt"),
          "-map","0:v:0","-map","1:a:0","-map","2:s:0",
          "-c:v","copy","-c:a","copy","-c:s","mov_text","-metadata:s:s:0","language=chi",
          "-movflags","+faststart","-shortest",str(final)])

    # Verify video/audio/subtitle streams and the visual timeline.
    probe=subprocess.run([ffprobe,"-v","error","-show_streams","-show_format","-of","json",str(OUT/"video.mp4")],
      capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=60)
    if probe.returncode:raise RuntimeError("ffprobe failed for v5")
    meta=json.loads(probe.stdout)
    streams=meta.get("streams",[])
    video_stream=next((x for x in streams if x.get("codec_type")=="video"),None)
    audio_stream=next((x for x in streams if x.get("codec_type")=="audio"),None)
    subtitle_stream=next((x for x in streams if x.get("codec_type")=="subtitle"),None)
    if not video_stream or not audio_stream or not subtitle_stream or subtitle_stream.get("codec_name")!="mov_text":
        raise RuntimeError("v5 must contain video, audio and mov_text subtitle streams")
    if video_stream.get("width")!=1080 or video_stream.get("height")!=1920:
        raise RuntimeError("v5 resolution mismatch")
    technical=inspect_video_file(OUT/"video.mp4",ffprobe_path=ffprobe,ffmpeg_path=ffmpeg,
      expected_width=1080,expected_height=1920,expected_fps=24,
      min_duration_seconds=24,max_duration_seconds=25,require_audio=True)
    motion=inspect_video_motion(OUT/"video.mp4",max_frames=24)
    extended={"schema":"muse.video-qa-extended.v1","technical":technical,"motion_diagnostics":motion,
      "voiceover_muxed":True,"voiceover_asr_cer":cer,"voiceover_asr_gate":"needs_review",
      "soft_subtitle_track_embedded":True,"human_visual_review_required":True,
      "human_listening_review_required":True,"human_subtitle_review_required":True,
      "rights_review_required":True,"publishable":False,"commercial_use_allowed":False}
    save(OUT/"video-qa-extended.json",extended)

    storyboard=load(V4/"storyboard.json")
    storyboard["voiceover"]={"status":"used_in_video","file":"voiceover.wav","muxed_into_video":True,
      "model":manifest["voiceover"]["model_repo"],"profile":manifest["voiceover"]["profile"],
      "human_review_required":True,"asr_gate":"needs_review"}
    storyboard["audio"]="original synthesized ambient bed mixed with local Cantonese TTS voiceover"
    storyboard["subtitles"]={"file":"subtitles.srt","embedded_track":"mov_text","burned_in":False,"human_review_required":True}
    storyboard["visual_edit"]={"replaced_black_leader_and_tail":True,"intro_card_seconds":3.0,
      "scene_source_range_seconds":[3.0,21.5],"outro_card_seconds":3.0,"subtle_pan_crop":True}
    save(OUT/"storyboard.json",storyboard)

    manifest["video"]["path"]="video.mp4"
    manifest["video"]["sha256"]=sha(OUT/"video.mp4")
    manifest["video"]["bytes"]=(OUT/"video.mp4").stat().st_size
    manifest["video"]["duration_seconds"]=float(meta.get("format",{}).get("duration",24.5))
    manifest["video"]["encoder"]=encoder
    manifest["video"]["voiceover_used"]=True
    manifest["video"]["subtitle_track_embedded"]="mov_text"
    manifest["voiceover"]["status"]="used_in_video"
    manifest["voiceover"]["muxed_into_video"]=True
    manifest["voiceover"]["asr_scope"]="standalone voiceover.wav; same asset mixed into v4/v5"
    manifest["voiceover"]["voice_profile_approved"]=False
    manifest["voiceover"]["asr_gate"]="needs_review"
    manifest["subtitles"]={"file":"subtitles.srt","raw_asr_file":"subtitles.asr.srt",
      "embedded_track":"mov_text","burned_in":False,"human_review_required":True}
    manifest["quality_gate"]={"status":"needs_review","asr_status":"needs_review",
      "character_error_rate":cer,"human_subtitle_review_required":True,"ready_to_publish":False}
    manifest["release_state"]="draft_only"
    manifest["auto_publish"]=False
    manifest["publish_api_called"]=False
    manifest["rights_review_required"]=True
    manifest["commercial_use_allowed"]=False

    rights=load(OUT/"rights-manifest.json")
    rights["review_required"]=True
    rights["commercial_use_allowed"]=False
    rights["publishable"]=False
    save(OUT/"rights-manifest.json",rights)
    costs=load(OUT/"cost-ledger.json")
    costs["build_id"]="first-cantonese-pilot-2026-11-24-v5"
    costs["actual_metered"]=False
    costs["note"]="Inherited local estimate only; v5 encode/mux time not independently metered."
    save(OUT/"cost-ledger.json",costs)

    qa={"schema":"muse.video-qa.v5","status":"needs_review" if cer>0.10 else "passed",
      "full_decode":technical.get("full_decode","passed"),"voiceover_muxed":True,
      "voiceover_asr_cer":cer,"voiceover_asr_gate":"needs_review",
      "subtitle_track":"mov_text","subtitle_review_status":"manual_draft_needs_human_review",
      "motion_warnings":motion.get("warnings",[]),"ready_to_publish":False}
    save(OUT/"qa-report.json",qa)
    release=load(V4/"release-checklist.json")
    release["release_state"]="draft_only"
    release["can_publish"]=False
    release["human_editorial_review"]="pending"
    release["human_voice_listening_review"]="pending"
    release["visual_and_model_rights_review"]="pending"
    release["source_and_fact_review"]="pending"
    release["account_authorization"]="not_connected"
    save(OUT/"release-checklist.json",release)

    readme="""# Cantonese Pilot v5 — narrated draft with corrected subtitle track

The v5 MP4 preserves the voiceover + ambient mix from v4, replaces the black 3-second leader and tail with moving cover cards, applies subtle pan/crop to the scene section, and embeds a corrected manual SRT as a selectable mov_text subtitle track.

The local Canto-TTS voice profile is the default local voice, not yet approved as the user's preferred mature female Cantonese voice. Whisper CER is 32.14% and remains needs_review; the raw ASR SRT is preserved separately, while subtitles.srt is an editorial draft. Human listening, subtitle, visual, source/fact, rights and account review remain mandatory. Draft-only; not published.
"""
    (OUT/"README.md").write_text(readme,encoding="utf-8")

    excluded_hash={"manifest.json","delivery-package.zip","package.sha256"}
    manifest["files"]={}
    for p in sorted(OUT.iterdir()):
        if p.is_file() and p.name not in excluded_hash:manifest["files"][p.name]=sha(p)
    save(OUT/"manifest.json",manifest)
    package=OUT/"delivery-package.zip"
    with zipfile.ZipFile(package,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and p.name not in {"delivery-package.zip","package.sha256"}:
                z.write(p,arcname=p.relative_to(OUT).as_posix())
    with zipfile.ZipFile(package) as z:
        bad=z.testzip()
        if bad:raise RuntimeError(f"v5 ZIP CRC failure: {bad}")
    package_hash=sha(package)
    (OUT/"package.sha256").write_text(f"{package_hash}  delivery-package.zip\n",encoding="ascii")

    handoff={"schema":"muse.canonical-pilot-handoff.v5","status":"draft_only_human_review_required",
      "pilot_dir":"data/monetization/first-cantonese-pilot-2026-11-24-v5",
      "video_sha256":sha(OUT/"video.mp4"),"video_bytes":(OUT/"video.mp4").stat().st_size,
      "duration_seconds":manifest["video"]["duration_seconds"],"resolution":"1080x1920","fps":24,
      "encoder":encoder,"voiceover_status":"used_in_video","voice_profile":manifest["voiceover"]["profile"],
      "voice_profile_approved":False,"asr_cer":cer,"asr_gate":"needs_review",
      "manual_subtitles":True,"soft_subtitle_track":"mov_text","raw_asr_preserved":"subtitles.asr.srt",
      "technical_qa":technical.get("status"),"motion_warnings":motion.get("warnings",[]),
      "rights_review_required":True,"commercial_use_allowed":False,"release_state":"draft_only",
      "account_authorization":"not_connected","publish_api_called":False,"auto_publish":False,
      "delivery_package_sha256":package_hash,"delivery_package_zip_crc":"passed",
      "digital_product_file":"AI-Verification-Kit-v0.1.1.zip","price_hypothesis_usd":9,
      "price_validated":False,"orders":0,"settled_revenue":0,"production_database_touched":False}
    save(HANDOFF,handoff)
    print(json.dumps(handoff,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
