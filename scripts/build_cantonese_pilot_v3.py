"""Build a v3 Cantonese promo cut: remove black leader/tail, mux local voiceover,
add soft subtitles, and apply subtle pan motion. Local-only, draft-only.
"""
from __future__ import annotations
import hashlib, json, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
V2=ROOT/"data"/"monetization"/"first-cantonese-pilot-2026-11-24-v2"
OUT=ROOT/"data"/"monetization"/"first-cantonese-pilot-2026-11-24-v3"
HANDOFF=ROOT/"outputs"/"creator_pilot_20261124"/"canonical-pilot-handoff-v3.json"

def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def read_json(path:Path)->dict:
    return json.loads(path.read_text(encoding="utf-8"))

def write_json(path:Path,value:dict)->None:
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def run(cmd:list[str],timeout:int=600)->None:
    p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=timeout)
    if p.returncode:
        raise RuntimeError("Command failed: "+ " ".join(cmd[:8]) + "\n"+(p.stderr or p.stdout)[-2200:])

def main()->int:
    if OUT.exists() or OUT.is_symlink(): raise FileExistsError(OUT)
    if HANDOFF.exists() or HANDOFF.is_symlink(): raise FileExistsError(HANDOFF)
    for name in ("video.mp4","voiceover.wav","cover.jpg","manual-subtitles-draft.srt","asr-result.json","subtitle-review.json","manifest.json","rights-manifest.json","cost-ledger.json"):
        if not (V2/name).is_file(): raise FileNotFoundError(V2/name)
    manifest=read_json(V2/"manifest.json")
    review=read_json(V2/"subtitle-review.json")
    asr=read_json(V2/"asr-result.json")
    if "\ufffd" in str(asr.get("transcript","")) or "\ufffd" in str(review.get("recognized_text","")):
        raise RuntimeError("v2 voiceover ASR contains invalid UTF-8 replacement characters")
    if float(review.get("character_error_rate",1.0))>0.10:
        raise RuntimeError("standalone voiceover ASR did not meet the 10% gate")

    from muse.media import discover_media_tools
    from muse.video_qa import inspect_video_file
    from muse.video_motion_diagnostics import inspect_video_motion
    tools={x.tool_id:x.executable for x in discover_media_tools().tools}
    ffmpeg,ffprobe=tools["ffmpeg"],tools["ffprobe"]
    enc=subprocess.run([ffmpeg,"-hide_banner","-encoders"],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=60).stdout
    encoder="h264_amf" if "h264_amf" in enc else "libx264"
    if encoder=="h264_amf":
        enc_args=["-c:v","h264_amf","-quality","quality","-b:v","12M","-maxrate","16M","-bufsize","24M"]
    else:
        enc_args=["-c:v","libx264","-preset","medium","-crf","18"]
    OUT.mkdir(parents=True)
    # Preserve all source provenance and review artifacts without modifying v2.
    copy_names=("voiceover.wav","cover.jpg","manual-subtitles-draft.srt","subtitles.asr.srt",
      "manual-subtitle-review.json","asr-result.json","subtitle-review.json","narration.txt",
      "source-notes.json","storyboard.json","platform-drafts.json","release-checklist.json",
      "rights-manifest.json","cost-ledger.json","ASR-UTF8-REBUILD-REPORT.md")
    for name in copy_names:
        src=V2/name
        if src.is_file(): shutil.copy2(src,OUT/name)
    if (ROOT/"outputs"/"creator_pilot_20261124"/"social-content-kit.md").is_file():
        shutil.copy2(ROOT/"outputs"/"creator_pilot_20261124"/"social-content-kit.md",OUT/"social-content-kit.md")

    with tempfile.TemporaryDirectory(prefix="muse-v3-") as td:
        td=Path(td)
        intro,scene,outro,video_only,ambient= (td/x for x in ("intro.mp4","scene.mp4","outro.mp4","video-only.mp4","ambient.wav"))
        cover=V2/"cover.jpg"
        base=V2/"video.mp4"
        card_filter="scale=1166:2073:flags=lanczos,crop=1080:1920:x='(iw-ow)/2+12*sin(t*0.8)':y='(ih-oh)/2+8*cos(t*0.5)',fps=24,format=yuv420p"
        scene_filter="trim=start=3:end=21.5,setpts=PTS-STARTPTS,scale=1166:2073:flags=lanczos,crop=1080:1920:x='(iw-ow)/2+18*sin(t*0.6)':y='(ih-oh)/2+10*cos(t*0.3)',fps=24,format=yuv420p"
        for target in (intro,outro):
            run([ffmpeg,"-y","-loop","1","-framerate","24","-i",str(cover),"-t","1.5",
              "-vf",card_filter,"-an",*enc_args,"-r","24","-pix_fmt","yuv420p","-movflags","+faststart",str(target)])
        run([ffmpeg,"-y","-i",str(base),"-vf",scene_filter,"-an",*enc_args,
          "-r","24","-pix_fmt","yuv420p","-movflags","+faststart",str(scene)])
        concat_filter="[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]"
        run([ffmpeg,"-y","-i",str(intro),"-i",str(scene),"-i",str(outro),
          "-filter_complex",concat_filter,"-map","[v]",*enc_args,"-r","24","-pix_fmt","yuv420p","-an",str(video_only)])
        ambient_filter="atrim=start=3:end=21.5,asetpts=PTS-STARTPTS,volume=0.18,adelay=1500|1500,apad=pad_dur=1.5,afade=t=out:st=20:d=1.5"
        run([ffmpeg,"-y","-i",str(base),"-vn","-af",ambient_filter,"-ac","2","-ar","48000","-c:a","pcm_s16le",str(ambient)])
        final=OUT/"video.mp4"
        mix="[1:a]aresample=48000,pan=stereo|c0=c0|c1=c0,volume=1.0[vo];[2:a]aresample=48000[amb];[vo][amb]amix=inputs=2:duration=longest:dropout_transition=1:normalize=0,alimiter=limit=0.95,loudnorm=I=-16:TP=-1.5:LRA=11[a]"
        run([ffmpeg,"-y","-i",str(video_only),"-i",str(V2/"voiceover.wav"),"-i",str(ambient),"-i",str(V2/"manual-subtitles-draft.srt"),
          "-filter_complex",mix,"-map","0:v:0","-map","[a]","-map","3:s:0",
          "-c:v","copy","-c:a","aac","-b:a","192k","-c:s","mov_text",
          "-metadata:s:s:0","language=chi","-metadata","title=AI Verification — Fluent does not mean true",
          "-movflags","+faststart",str(final)])

    # Copy the current manifest, then explicitly record that the standalone voiceover
    # is now muxed into v3; the default voice is not yet user-approved.
    manifest["video"]["file"]="video.mp4"
    manifest["video"]["sha256"]=sha256(OUT/"video.mp4")
    manifest["video"]["bytes"]=(OUT/"video.mp4").stat().st_size
    manifest["video"]["duration_seconds"]=21.5
    manifest["video"]["width"]=1080
    manifest["video"]["height"]=1920
    manifest["video"]["fps"]=24
    manifest["video"]["encoder"]=encoder
    manifest["voiceover"]["status"]="used_in_video"
    manifest["voiceover"]["muxed_into_video"]=True
    manifest["voiceover"]["asr_scope"]="standalone voiceover.wav, same asset muxed into v3"
    manifest["voiceover"]["voice_profile_approved"]=False
    manifest["voiceover"]["human_listening_review_required"]=True
    manifest["audio"]={"mix":"local Cantonese voiceover + original synthesized ambient bed","voiceover_start_seconds":0,"ambient_start_seconds":1.5,"target_loudness_lufs":-16,"human_listening_review_required":True}
    manifest["subtitles"]={"file":"manual-subtitles-draft.srt","embedded_track":"mov_text","burned_in":False,"human_review_required":True}
    manifest["release_state"]="draft_only"
    manifest["publish_api_called"]=False
    manifest["auto_publish"]=False
    manifest["quality_gate"]["human_subtitle_review_required"]=True
    manifest["quality_gate"]["ready_to_publish"]=False
    manifest["rights_review_required"]=True
    manifest["commercial_use_allowed"]=False

    storyboard=read_json(OUT/"storyboard.json") if (OUT/"storyboard.json").is_file() else read_json(V2/"storyboard.json")
    storyboard["voiceover"]={"status":"used_in_video","file":"voiceover.wav","muxed_into_video":True,"profile":manifest["voiceover"].get("voice_profile","default_local_voice"),"human_review_required":True}
    storyboard["subtitles"]={"file":"manual-subtitles-draft.srt","embedded_track":"mov_text","burned_in":False,"human_review_required":True}
    storyboard["visual_edit"]={"trimmed_source_range_seconds":[3.0,21.5],"intro_card_seconds":1.5,"outro_card_seconds":1.5,"subtle_pan_crop":True}
    write_json(OUT/"storyboard.json",storyboard)

    rights=read_json(OUT/"rights-manifest.json")
    rights["derived_from"]="v2_same_source_assets"
    rights["review_required"]=True
    rights["commercial_use_allowed"]=False
    rights["publishable"]=False
    write_json(OUT/"rights-manifest.json",rights)
    costs=read_json(OUT/"cost-ledger.json")
    costs["build_id"]="first-cantonese-pilot-2026-11-24-v3"
    costs["derived_from"]="v2_estimate_only"
    costs["actual_metered"]=False
    costs["note"]="Inherited local compute estimate only; v3 hardware encoding and audio mix were not independently metered."
    write_json(OUT/"cost-ledger.json",costs)

    technical=inspect_video_file(final,ffprobe_path=ffprobe,ffmpeg_path=ffmpeg,
      expected_width=1080,expected_height=1920,expected_fps=24,
      min_duration_seconds=20,max_duration_seconds=22,require_audio=True)
    motion=inspect_video_motion(final,max_frames=24)
    extended={"schema":"muse.video-qa-extended.v1","technical":technical,"motion_diagnostics":motion,
      "voiceover_muxed":True,"soft_subtitle_track_embedded":True,
      "voice_profile_approved":False,"human_visual_review_required":True,
      "human_listening_review_required":True,"rights_review_required":True,
      "publishable":False,"commercial_use_allowed":False}
    write_json(OUT/"video-qa-extended.json",extended)

    qa={"schema":"muse.video-qa.v3","status":"passed" if technical.get("status")=="passed" else "needs_review",
      "full_decode":"passed" if technical.get("status")=="passed" else "failed",
      "voiceover_muxed":True,"subtitle_track":"mov_text","subtitle_review_status":"manual_draft_needs_human_review",
      "visual_motion_warnings":motion.get("warnings",[]),"ready_to_publish":False}
    write_json(OUT/"qa-report.json",qa)
    release=read_json(OUT/"release-checklist.json")
    release["release_state"]="draft_only"
    release["can_publish"]=False
    release["voiceover_review"]="pending"
    release["human_editorial_review"]="pending"
    release["visual_review"]="pending"
    release["rights_review"]="pending"
    release["account_authorization"]="not_connected"
    write_json(OUT/"release-checklist.json",release)
    readme="""# Cantonese Pilot v3 — voiceover muxed draft

The v3 MP4 trims the black head/tail from v2, adds a subtle pan crop and title cards, mixes the local Cantonese voiceover with the original synthesized ambient bed, and embeds the corrected SRT as a selectable MP4 subtitle track.

Voice profile is the model's default local voice, not yet approved as the user's preferred mature female Cantonese voice. Human listening, subtitle, visual, source/fact, rights and account review remain mandatory. Commercial reuse and publishing remain blocked.
"""
    (OUT/"README.md").write_text(readme,encoding="utf-8")

    excluded_hash={"manifest.json","delivery-package.zip","package.sha256"}
    manifest["files"]={}
    for path in sorted(OUT.iterdir()):
        if path.is_file() and path.name not in excluded_hash:
            manifest["files"][path.name]=sha256(path)
    write_json(OUT/"manifest.json",manifest)

    package=OUT/"delivery-package.zip"
    with zipfile.ZipFile(package,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(OUT.rglob("*")):
            if path.is_file() and path.name not in {"delivery-package.zip","package.sha256"} and path!=package:
                archive.write(path,arcname=path.relative_to(OUT).as_posix())
    with zipfile.ZipFile(package) as archive:
        bad=archive.testzip()
        if bad: raise RuntimeError(f"v3 delivery ZIP CRC failure: {bad}")
    package_hash=sha256(package)
    (OUT/"package.sha256").write_text(f"{package_hash}  delivery-package.zip\n",encoding="ascii")

    handoff={
      "schema":"muse.canonical-pilot-handoff.v3","status":"draft_only_human_review_required",
      "pilot_dir":"data/monetization/first-cantonese-pilot-2026-11-24-v3",
      "video_file":"video.mp4","video_sha256":sha256(final),"video_bytes":final.stat().st_size,
      "duration_seconds":21.5,"resolution":"1080x1920","fps":24,"encoder":encoder,
      "voiceover_status":"muxed_into_video","voiceover_file":"voiceover.wav",
      "voice_profile":manifest["voiceover"].get("voice_profile","default_local_voice"),
      "voice_profile_approved":False,"asr_scope":"standalone voiceover asset also used in final mix",
      "asr_utf8_valid":True,"asr_cer":float(review.get("character_error_rate",0.0625)),
      "subtitle_track":"mov_text","manual_subtitles":True,"human_subtitle_review_required":True,
      "extended_video_qa_status":technical.get("status"),
      "extended_video_qa_warnings":technical.get("warnings",[]),
      "motion_warnings":motion.get("warnings",[]),"motion_summary":motion.get("summary"),
      "rights_review_required":True,"commercial_use_allowed":False,
      "release_state":"draft_only","account_authorization":"not_connected",
      "publish_api_called":False,"auto_publish":False,
      "delivery_package_sha256":package_hash,"delivery_package_zip_crc":"passed",
      "digital_product_file":"AI-Verification-Kit-v0.1.1.zip",
      "digital_product_price_hypothesis_usd":9,"price_validated":False,
      "orders":0,"settled_revenue":0,"production_database_touched":False
    }
    write_json(HANDOFF,handoff)
    print(json.dumps(handoff,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
