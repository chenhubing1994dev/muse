from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parent
OUT=ROOT/"ember-fire-project.zip"
INCLUDE=["TREATMENT.md","STYLE.md","CREDITS","README.md","build.ps1","build.sh","index.html","mix.py","qa.py","package.py","火种不灭.mp4","火种不灭.srt","poster.jpg","contact-sheet.jpg","QA_REPORT.md","voice/lines.json","voice/dur.json","voice/v01.wav","voice/v02.wav","voice/v03.wav","voice/v04.wav","voice/v05.wav","voice/v06.wav","voice/v07.wav","out/mix.wav","out/voice_timing.json","stills"]with zipfile.ZipFile(OUT,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for item in INCLUDE:
  p=ROOT/item
  if p.is_dir():
   for f in p.rglob("*"):
    if f.is_file() and ".cache" not in f.parts: z.write(f,f.relative_to(ROOT).as_posix())
  elif p.is_file(): z.write(p,p.relative_to(ROOT).as_posix())
print(f"{OUT} {OUT.stat().st_size/1024/1024:.2f} MiB")
