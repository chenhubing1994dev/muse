"""Build versioned AI Verification Kit v0.1.1 without overwriting v0.1."""
from __future__ import annotations
import hashlib, json, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"outputs"/"creator_pilot_20261124"
KIT=OUT/"digital-product"
ZIP=OUT/"AI-Verification-Kit-v0.1.1.zip"
MANIFEST=OUT/"AI-Verification-Kit-v0.1.1.manifest.json"

def digest(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def main()->int:
    if ZIP.exists() or MANIFEST.exists(): raise FileExistsError("refusing to overwrite v0.1.1 package")
    product=json.loads((KIT/"product-manifest.json").read_text(encoding="utf-8"))
    if product.get("version")!="0.1.1" or product.get("status")!="draft_not_published":
        raise ValueError("product manifest version/status mismatch")
    files=sorted(p for p in KIT.rglob("*") if p.is_file())
    with zipfile.ZipFile(ZIP,"x",zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for p in files: archive.write(p,p.relative_to(KIT).as_posix())
    with zipfile.ZipFile(ZIP) as archive:
        bad=archive.testzip()
        if bad: raise RuntimeError(f"ZIP CRC failure: {bad}")
        entries=archive.namelist()
    payload={
      "schema":"muse.digital-product-package.v1",
      "product_id":product["product_id"],"version":product["version"],
      "status":"draft_not_published","file":ZIP.name,"sha256":digest(ZIP),
      "bytes":ZIP.stat().st_size,"entry_count":len(entries),"entries":entries,
      "price_hypothesis":product["price_hypothesis"],
      "market_validation":product["market_validation"],
      "published":False,"orders":0,"settled_revenue":0,
      "commercial_use_allowed":False,"zip_crc":"passed"
    }
    MANIFEST.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"packaged","zip":str(ZIP.relative_to(ROOT)).replace("\\","/"),
      "bytes":ZIP.stat().st_size,"sha256":payload["sha256"],"files":len(entries),
      "version":product["version"],"published":False,"settled_revenue":0},ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
