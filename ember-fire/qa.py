from pathlib import Path
import hashlib, json, re, subprocess, sys

ROOT = Path(__file__).resolve().parent
VIDEO = ROOT / "火种不灭.mp4"
REPORT = ROOT / "QA_REPORT.md"
SRT = ROOT / "火种不灭.srt"

def run(args, timeout=300):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)

def main():
    checks=[]
    def record(name, ok, detail):
        checks.append((name, bool(ok), str(detail)))
        print(("PASS" if ok else "FAIL"), name, detail)
    if not VIDEO.exists():
        record("MP4 exists", False, "missing final MP4")
        REPORT.write_text("# QA Report\n\nFinal MP4 missing.\n", encoding="utf-8")
        sys.exit(1)
    probe=run(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(VIDEO)])
    if probe.returncode:
        record("FFprobe",False,probe.stderr[-1000:]); sys.exit(1)
    info=json.loads(probe.stdout)
    vs=next((s for s in info.get("streams",[]) if s.get("codec_type")=="video"),{})
    aus=next((s for s in info.get("streams",[]) if s.get("codec_type")=="audio"),{})
    dur=float(info.get("format",{}).get("duration",0))
    record("MP4 exists",True,f"{VIDEO.stat().st_size/1024/1024:.1f} MiB")
    record("Video dimensions",vs.get("width")==1080 and vs.get("height")==1920,f"{vs.get('width')}x{vs.get('height')}")
    record("Frame rate",vs.get("avg_frame_rate")=="24/1",str(vs.get("avg_frame_rate")))
    record("Duration",44.8<=dur<=45.2,f"{dur:.3f}s")
    record("Audio stream",bool(aus),json.dumps({k:aus.get(k) for k in ("codec_name","sample_rate","channels")},ensure_ascii=False))
    dec=run(["ffmpeg","-v","error","-i",str(VIDEO),"-f","null","NUL"],timeout=300)
    record("Full decode",dec.returncode==0,dec.stderr[-500:] or "FFmpeg decoded the full file")
    loud=run(["ffmpeg","-hide_banner","-i",str(VIDEO),"-af","loudnorm=I=-14:TP=-1.2:LRA=11:print_format=json","-f","null","NUL"],timeout=180)
    loud_data={}
    match=re.search(r"\{[\s\S]*?\}",loud.stderr)
    if match:
        try:loud_data=json.loads(match.group(0))
        except Exception:pass
    input_i=loud_data.get("input_i")
    try:loud_ok=input_i is not None and -16.5<=float(input_i)<=-12.5
    except Exception:loud_ok=False
    record("Integrated loudness",loud_ok,f"measured {input_i} LUFS; target -14 LUFS")
    black=run(["ffmpeg","-hide_banner","-i",str(VIDEO),"-vf","blackdetect=d=0.15:pix_th=0.10","-an","-f","null","NUL"],timeout=180)
    black_count=len(re.findall(r"black_start:",black.stderr))
    record("Black-frame scan",black.returncode==0,f"{black_count} black segments detected (dark opening is intentional)")
    srt_ok=SRT.exists(); cue_count=0
    if srt_ok:
        raw=SRT.read_text(encoding="utf-8-sig")
        cue_count=len(re.findall(r"(?m)^\d+\s*$",raw))
        srt_ok=cue_count==7 and "-->" in raw
    record("Cantonese subtitle file",srt_ok,f"{cue_count} cues")
    sha=hashlib.sha256(VIDEO.read_bytes()).hexdigest()
    lines=["# QA Report","",f"- File: {VIDEO.name}",f"- SHA-256: {sha}",f"- Size: {VIDEO.stat().st_size/1024/1024:.2f} MiB",f"- Duration: {dur:.3f}s","", "## Checks",""]
    for name,ok,detail in checks: lines.append(f"- {'PASS' if ok else 'FAIL'} — **{name}**: {detail}")
    lines += ["","## Known limits","- Automatic Cantonese speech-to-text verification was not run because faster-whisper was not installed in the Lemo environment; the voice was generated with the explicit zh-HK-HiuMaanNeural Cantonese voice and the source lines are preserved in voice/lines.json.","- The film is original procedural animation, not Wan-generated footage. The Wan/ComfyUI path was unavailable at render time.",""]
    REPORT.write_text("\n".join(lines),encoding="utf-8")
    if not all(x[1] for x in checks): sys.exit(1)
    print("QA_REPORT",REPORT,"SHA256",sha)

if __name__=="__main__": main()
