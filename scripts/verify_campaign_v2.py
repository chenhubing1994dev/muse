from __future__ import annotations
import hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def crc(p:Path)->None:
 with zipfile.ZipFile(p) as z:
  bad=z.testzip()
  if bad:raise RuntimeError(f'CRC failed: {p}: {bad}')
def main()->None:
 base=ROOT/'outputs/campaign_videos_20261124_v2'
 m=json.loads((base/'campaign-video-manifest.json').read_text(encoding='utf-8'))
 if len(m['episodes'])!=3:raise RuntimeError('expected three episodes')
 results=[]
 for e in m['episodes']:
  v=ROOT/e['video'].replace(chr(92),'/')
  p=ROOT/e['package'].replace(chr(92),'/')
  if not v.is_file() or sha(v)!=e['sha256']:raise RuntimeError(f"video hash mismatch: {e['title']}")
  if v.stat().st_size!=e['bytes'] or e['duration_seconds']!=55.0 or e['full_decode']!='passed':raise RuntimeError(f"video metadata mismatch: {e['title']}")
  crc(p)
  if sha(p)!=e['package_sha256']:raise RuntimeError(f"package hash mismatch: {e['title']}")
  if e['ready_to_publish'] is not False:raise RuntimeError(f"release gate open: {e['title']}")
  results.append({'title':e['title'],'bytes':e['bytes'],'sha256':e['sha256'],'package_sha256':e['package_sha256'],'decode':'passed','package_crc':'passed'})
 for key in ('public_posting_authorized','public_deployment_authorized','payments_enabled'):
  if m[key] is not False:raise RuntimeError(f'campaign gate unexpectedly open: {key}')
 if m['sales']!=0 or m['settled_revenue_usd']!='0.00':raise RuntimeError('campaign revenue state mismatch')
 html=(base/'campaign-landing-preview.html').read_text(encoding='utf-8')
 plan=(base/'LAUNCH-EXPERIMENT-PLAN.md').read_text(encoding='utf-8')
 readme=(base/'LANDING-PREVIEW-README.md').read_text(encoding='utf-8')
 if any('US$9' not in x for x in (html,plan,readme)):raise RuntimeError('campaign price copy mismatch')
 if any('US$7' in x for x in (html,plan,readme)):raise RuntimeError('stale campaign price copy')
 bundle=base/'campaign-v2-delivery-v2.zip'
 crc(bundle)
 with zipfile.ZipFile(bundle) as z:
  names=z.namelist()
  if len(names)!=len(set(names)):raise RuntimeError('duplicate campaign bundle member names')
  manifest=json.loads(z.read('campaign/delivery-manifest.json'))
  for name,digest in manifest['files'].items():
   if hashlib.sha256(z.read(name)).hexdigest()!=digest:raise RuntimeError('campaign bundle SHA mismatch: '+name)
  if set(names)!=set(manifest['files'])|{'campaign/delivery-manifest.json'}:raise RuntimeError('campaign bundle member set mismatch')
 if manifest['public_posting_authorized'] or manifest['public_deployment_authorized'] or manifest['payments_enabled']:raise RuntimeError('campaign bundle release gate mismatch')
 print(json.dumps({'status':'passed','episodes':results,'campaign_bundle_sha256':sha(bundle),'campaign_bundle_members':len(names),'campaign_bundle_crc':'passed','duplicate_member_names':False,'release_gates':'closed','price_hypothesis':'US$9, unvalidated','sales':0,'settled_revenue_usd':'0.00','public_deployment':False},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
