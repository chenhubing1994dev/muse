"""Correct v3 provenance: the MP4 hash matches v2 and does not contain voiceover."""
from __future__ import annotations
import hashlib,json,shutil,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
V2=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24-v2"
V3=ROOT/"data/monetization/first-cantonese-pilot-2026-11-24-v3"
HANDOFF=ROOT/"outputs/creator_pilot_20261124/canonical-pilot-handoff-v3.json"

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()

def load(path): return json.loads(path.read_text(encoding="utf-8"))
def save(path,obj): path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def main():
    if not V3.is_dir(): raise FileNotFoundError(V3)
    manifest=load(V3/"manifest.json")
    v2_hash=sha(V2/"video.mp4")
    v3_hash=sha(V3/"video.mp4")
    if v2_hash!=v3_hash: raise RuntimeError("v3 is not byte-identical to v2; manual review required")
    if HANDOFF.exists(): raise FileExistsError(HANDOFF)
    for name in ("delivery-package.zip","package.sha256"):
        src=V3/name
        if src.exists():
            dst=V3/("delivery-package.before-provenance-correction.zip" if name.endswith(".zip") else "package.before-provenance-correction.sha256")
            if not dst.exists(): shutil.copy2(src,dst)

    manifest.setdefault("voiceover",{})
    manifest["voiceover"]["status"]="not_used_in_video"
    manifest["voiceover"]["muxed_into_video"]=False
    manifest["voiceover"]["asr_scope"]="standalone voiceover.wav only"
    manifest.setdefault("video",{})
    manifest["video"]["voiceover_used"]=False
    manifest["video"]["subtitle_track_embedded"]=False
    manifest["subtitles"]={"file":"subtitles.srt","status":"sidecar_only","embedded":False,"human_review_required":True}
    manifest["release_state"]="draft_only"
    manifest["auto_publish"]=False
    manifest["publish_api_called"]=False

    readme="""# AI 講得快，不代表答得啱｜三步查證法 — v3 provenance correction

This folder is a packaging snapshot, not a new video cut. Its video.mp4 SHA-256 is byte-identical to v2, so it does NOT contain the separate voiceover.wav and does NOT embed subtitles. The MP4 retains the original synthesized ambient bed only.

voiceover.wav is a standalone local Cantonese voice asset. Its ASR CER (6.25%) applies only to that WAV, not to the MP4 audio. subtitles.srt is a sidecar generated from the standalone voiceover; it is not embedded in the MP4 and contains a known ASR mistranscription of 「搵反證」.

Do not describe this MP4 as a narrated or subtitled video. Use v4 or later only after checking the actual MP4 hash, audio streams, subtitle streams and QA. This snapshot remains draft-only, unpublished, and not approved for commercial reuse.
"""
    (V3/"README.md").write_text(readme,encoding="utf-8")
    qa=load(V3/"qa-report.json")
    qa["voiceover_status"]="not_used_in_video"
    qa["voiceover_muxed"]=False
    qa["voiceover_asr_scope"]="standalone voiceover.wav only"
    qa["subtitle_track_embedded"]=False
    qa["subtitle_review_status"]="human_review_required"
    qa["ready_to_publish"]=False
    save(V3/"qa-report.json",qa)

    excluded_hash={"manifest.json","delivery-package.zip","package.sha256","delivery-package.before-provenance-correction.zip","package.before-provenance-correction.sha256"}
    manifest["files"]={}
    for p in sorted(V3.iterdir()):
        if p.is_file() and p.name not in excluded_hash:
            manifest["files"][p.name]=sha(p)
    save(V3/"manifest.json",manifest)

    package=V3/"delivery-package.zip"
    with zipfile.ZipFile(package,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(V3.rglob("*")):
            if p.is_file() and p.name not in {"delivery-package.zip","package.sha256","delivery-package.before-provenance-correction.zip","package.before-provenance-correction.sha256"}:
                z.write(p,arcname=p.relative_to(V3).as_posix())
    with zipfile.ZipFile(package) as z:
        bad=z.testzip()
        if bad: raise RuntimeError(f"ZIP CRC failed: {bad}")
    package_hash=sha(package)
    (V3/"package.sha256").write_text(f"{package_hash}  delivery-package.zip\n",encoding="ascii")

    handoff={
      "schema":"muse.canonical-pilot-handoff.v3-corrected",
      "status":"superseded_snapshot_voiceover_not_muxed",
      "pilot_dir":"data/monetization/first-cantonese-pilot-2026-11-24-v3",
      "video_sha256":v3_hash,"same_video_as_v2":True,
      "voiceover_status":"not_used_in_video","voiceover_asset":"voiceover.wav",
      "asr_scope":"standalone voiceover.wav only","asr_cer":manifest["voiceover"]["asr_cer"],
      "subtitles_embedded":False,"subtitle_sidecar":"subtitles.srt",
      "release_state":"draft_only","commercial_use_allowed":False,
      "publish_api_called":False,"account_authorization":"not_connected",
      "delivery_package_sha256":package_hash,"delivery_package_zip_crc":"passed",
      "orders":0,"settled_revenue":0
    }
    save(HANDOFF,handoff)
    print(json.dumps(handoff,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
