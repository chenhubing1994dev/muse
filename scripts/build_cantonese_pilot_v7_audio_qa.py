"""Create a v7 audio-normalized review build from v6; preserve draft and rights gates."""
from __future__ import annotations
import hashlib,json,shutil,subprocess,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24-v6"
OUT=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24-v7"
HANDOFF=ROOT/"outputs/creator_pilot_20261124/canonical-pilot-handoff-v7.json"

def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()
def read(p):return json.loads(p.read_text(encoding="utf-8"))
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def run(cmd,timeout=900):
    r=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=timeout)
    if r.returncode:raise RuntimeError((r.stderr or r.stdout)[-2500:])
def probe(path,ffprobe):
    r=subprocess.run([ffprobe,"-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=60)
    if r.returncode:raise RuntimeError("ffprobe failed: "+r.stderr[-1000:])
    return json.loads(r.stdout)

def _qa_warning_counts(technical):
    """Count authoritative video_qa warnings, not optical-flow observations."""
    all_warnings = []
    for report in technical.values():
        if not isinstance(report, dict):
            continue
        warnings = report.get("warnings", [])
        if isinstance(warnings, list):
            all_warnings.extend(item for item in warnings if isinstance(item, dict) and isinstance(item.get("code"), str) and item.get("code"))
    frozen_count = sum(1 for warning in all_warnings if warning.get("code") == "frozen_frames")
    return len(all_warnings), frozen_count



def main():
    if OUT.exists() or OUT.is_symlink() or HANDOFF.exists() or HANDOFF.is_symlink():
        raise FileExistsError("v7 output/handoff already exists; refusing overwrite")
    from muse.media import discover_media_tools
    from muse.video_qa import inspect_video_file
    from muse.video_motion_diagnostics import inspect_video_motion
    tools={x.tool_id:x.executable for x in discover_media_tools().tools}
    ffmpeg,ffprobe=tools["ffmpeg"],tools["ffprobe"]
    base=read(SRC/"manifest.json")
    for name in ("video-9x16.mp4","video-16x9.mp4","voiceover-preview-edited.srt","voiceover-preview.wav"):
        if not (SRC/name).is_file():raise FileNotFoundError(SRC/name)
    OUT.mkdir(parents=True)
    excluded={"video-9x16.mp4","video-16x9.mp4","manifest.json","video-qa.json","delivery-package.zip","package.sha256"}
    for p in SRC.iterdir():
        if p.is_file() and p.name not in excluded:shutil.copy2(p,OUT/p.name)
    srt=OUT/"voiceover-preview-edited.srt"
    outputs={}
    for name in ("video-9x16.mp4","video-16x9.mp4"):
        source=SRC/name; target=OUT/name
        # Keep encoded picture bit-for-bit; normalize/re-encode AAC audio to -1.5 dBTP ceiling.
        run([ffmpeg,"-hide_banner","-loglevel","error","-n","-i",str(source),"-i",str(srt),
          "-map","0:v:0","-map","0:a:0","-map","1:0",
          "-c:v","copy","-af","loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary",
          "-c:a","aac","-b:a","192k","-ar","48000","-c:s","mov_text",
          "-metadata:s:s:0","language=chi","-movflags","+faststart",str(target)],timeout=900)
        meta=probe(target,ffprobe)
        streams=meta.get("streams",[])
        if not any(x.get("codec_type")=="video" for x in streams):raise RuntimeError(f"{name}: no video")
        if not any(x.get("codec_type")=="audio" for x in streams):raise RuntimeError(f"{name}: no audio")
        if not any(x.get("codec_type")=="subtitle" and x.get("codec_name")=="mov_text" for x in streams):
            raise RuntimeError(f"{name}: missing soft subtitle")
        outputs[name]=meta
    technical={}
    motion={}
    for name,wh in (("video-9x16.mp4",(1080,1920)),("video-16x9.mp4",(1920,1080))):
        technical[name]=inspect_video_file(OUT/name,ffprobe_path=ffprobe,ffmpeg_path=ffmpeg,
            expected_width=wh[0],expected_height=wh[1],expected_fps=24,
            min_duration_seconds=24,max_duration_seconds=25,require_audio=True)
        motion[name]=inspect_video_motion(OUT/name,max_frames=24)
    vertical_audio=next(x for x in outputs["video-9x16.mp4"]["streams"] if x.get("codec_type")=="audio")
    # Re-run loudness scan on final audio.
    scan=subprocess.run([ffmpeg,"-hide_banner","-nostats","-i",str(OUT/"video-9x16.mp4"),"-vn","-af","ebur128=peak=true","-f","null","-"],
      capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=120)
    log=scan.stderr or scan.stdout
    import re
    peaks=re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS",log)
    true_peak=float(peaks[-1]) if peaks else None
    technical_warning_count,frozen_frame_warning_count=_qa_warning_counts(technical)
    qa={"schema":"muse.cantonese-pilot-v7-qa.v1",
      "technical_decode":{"status":"passed" if all(v.get("status")=="passed" for v in technical.values()) else "needs_review","by_variant":technical},
      "motion_diagnostics":motion,
      "audio_normalization":{"method":"FFmpeg loudnorm","target_integrated_lufs":-16.0,"target_true_peak_dbfs":-1.5,
        "measured_peak_dbfs":true_peak,"measurement_tool":"ebur128","audio_stream_codec":vertical_audio.get("codec_name")},
      "visual_review_required":True,"technical_warning_count":technical_warning_count,
      "motion_warning_count":frozen_frame_warning_count,
      "voiceover_standalone_asr_cer":base["voiceover"]["asr_cer"],
      "mixed_audio_asr":"not_run","human_listening_review_required":True,
      "human_subtitle_review_required":True,"rights_review_required":True,
      "commercial_use_allowed":False,"publishable":False,
      "status":"needs_review" if technical_warning_count or true_peak is None or true_peak>-1.0 else "technical_pass_human_review_pending"}
    write(OUT/"video-qa.json",qa)
    manifest=base.copy()
    manifest["schema"]="muse.canonical-cantonese-pilot.v7"
    manifest["status"]="completed_audio_normalized_review_only"
    manifest["videos"]={}
    for name,wh,key in (("video-9x16.mp4",(1080,1920),"vertical"),("video-16x9.mp4",(1920,1080),"horizontal")):
        meta=outputs[name]; fmt=meta.get("format",{})
        manifest["videos"][key]={"file":name,"sha256":sha(OUT/name),"bytes":(OUT/name).stat().st_size,
            "width":wh[0],"height":wh[1],"duration_seconds":float(fmt.get("duration",0)),
            "subtitle_track":"mov_text","audio_normalized":True,"technical_qa":technical[name].get("status")}
    manifest["audio_normalization"]=qa["audio_normalization"]
    manifest["quality_gate"]={"status":qa["status"],"visual_review_required":True,"human_listening_review_required":True}
    manifest["release_state"]="draft_only";manifest["rights_review_required"]=True
    manifest["commercial_use_allowed"]=False;manifest["publishable"]=False
    manifest["publish_api_called"]=False;manifest["account_authorization"]="not_connected"
    manifest["orders"]=0;manifest["settled_revenue"]=0
    manifest["files"]={}
    for p in sorted(OUT.iterdir()):
        if p.is_file() and p.name not in {"manifest.json","delivery-package.zip","package.sha256"}:
            manifest["files"][p.name]=sha(p)
    write(OUT/"manifest.json",manifest)
    package=OUT/"delivery-package.zip"
    with zipfile.ZipFile(package,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(OUT.iterdir()):
            if p.is_file() and p.name not in {"delivery-package.zip","package.sha256"}:z.write(p,p.name)
    with zipfile.ZipFile(package) as z:
        if z.testzip():raise RuntimeError("ZIP CRC failed")
        members=set(z.namelist())
        expected=set(manifest["files"])|{"manifest.json"}
        if members!=expected:raise RuntimeError("ZIP member set mismatch")
        for n,d in manifest["files"].items():
            if hashlib.sha256(z.read(n)).hexdigest()!=d:raise RuntimeError("ZIP member hash mismatch: "+n)
        if json.loads(z.read("manifest.json"))!=manifest:raise RuntimeError("embedded manifest mismatch")
    pkg_hash=sha(package)
    (OUT/"package.sha256").write_text(f"{pkg_hash}  delivery-package.zip\n",encoding="ascii")
    readme=f"""# Cantonese Pilot v7 — audio-normalized review build

Derived from v6. Video streams are copied without re-encoding; AAC audio is normalized using FFmpeg loudnorm (I=-16 LUFS, TP=-1.5 dBTP), and corrected Chinese soft subtitles remain embedded as mov_text.
Motion QA summary: {frozen_frame_warning_count} potential frozen-frame warnings across both variants; technical warning count: {technical_warning_count}. Heuristic signals require editorial review and are not proof of an actual freeze.\n\nDefault neutral local Cantonese voice is not approved as a mature female voice. Rights, model terms, background audio, source/fact, privacy, disclosure, subtitle and human listening reviews remain pending. Draft-only, unpublished, commercial use not cleared. No orders or settled revenue."""
    (OUT/"README.md").write_text(readme,encoding="utf-8")
    handoff={"schema":"muse.canonical-pilot-handoff.v7","status":"draft_only_human_review_required",
      "pilot_dir":str(OUT.relative_to(ROOT)).replace("\\","/"),
      "videos":manifest["videos"],"audio_normalization":qa["audio_normalization"],
      "technical_warning_count":technical_warning_count,
      "motion_warning_count":frozen_frame_warning_count,"technical_decode":qa["technical_decode"]["status"],
      "standalone_voiceover_asr_cer":base["voiceover"]["asr_cer"],"mixed_audio_asr":"not_run",
      "rights_review_required":True,"commercial_use_allowed":False,"publishable":False,
      "release_state":"draft_only","account_authorization":"not_connected","publish_api_called":False,
      "delivery_package_sha256":pkg_hash,"delivery_package_zip_crc":"passed",
      "paid_api_cost_usd":0,"paid_asset_cost_usd":0,"local_compute_cost":"not_metered",
      "orders":0,"settled_revenue":0,"production_database_touched":False}
    write(HANDOFF,handoff)
    print(json.dumps(handoff,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
