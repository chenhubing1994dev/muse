from __future__ import annotations
"""Loopback-only B160 human annotation UI; standard library only."""
import argparse
import csv
import json
import math
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "annotation" / "b160"
FIELDS = ["sample_id","source_video","timestamp_seconds","frame_path","presence","subject_type","x_min","y_min","x_max","y_max","camera_motion","annotator","notes","pred_presence","pred_x_min","pred_y_min","pred_x_max","pred_y_max","pred_confidence","pred_source","pred_camera_motion"]
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
SAVE_LOCK = threading.RLock()

def load_samples(dataset=None):
    dataset = DATASET if dataset is None else dataset
    with (dataset / "annotations.csv").open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        frame = Path(row.get("frame_path", ""))
        if (
            not ID_RE.fullmatch(row.get("sample_id", ""))
            or frame.is_absolute()
            or ".." in frame.parts
            or not frame.parts
            or frame.parts[0] != "frames"
        ):
            raise ValueError("unsafe sample id or frame path")
        if not (dataset / frame).is_file():
            raise FileNotFoundError("missing frame: " + row["sample_id"])
    return rows
def validate_annotation(p):
    presence = str(p.get("presence","")).strip().lower()
    motion = str(p.get("camera_motion","")).strip().lower()
    annotator = str(p.get("annotator","")).strip()[:80]
    if presence not in {"present","absent","ambiguous"}: raise ValueError("invalid presence")
    if motion not in {"static","low","medium","high"}: raise ValueError("invalid camera_motion")
    if not annotator: raise ValueError("annotator is required")
    out = {"presence":presence,"subject_type":str(p.get("subject_type","")).strip()[:80],"camera_motion":motion,"annotator":annotator,"notes":str(p.get("notes","")).strip()[:1000]}
    keys=("x_min","y_min","x_max","y_max"); raw=[p.get(k,"") for k in keys]
    if any(str(v).strip() for v in raw):
        try: box=[float(v) for v in raw]
        except (TypeError,ValueError) as e: raise ValueError("bbox must be numeric") from e
        if not all(math.isfinite(v) and 0<=v<=1 for v in box) or box[2]<=box[0] or box[3]<=box[1]: raise ValueError("invalid normalized bbox")
        out.update({k:f"{v:.6f}" for k,v in zip(keys,box)})
    else: out.update({k:"" for k in keys})
    return out

def save_annotation(sample_id,payload,dataset=None):
    dataset = DATASET if dataset is None else dataset
    if not ID_RE.fullmatch(sample_id):
        raise ValueError("invalid sample id")
    rows = load_samples(dataset)
    matches = [r for r in rows if r["sample_id"] == sample_id]
    if len(matches) != 1:
        raise KeyError("unknown sample")
    matches[0].update(validate_annotation(payload))
    path = dataset / "annotations.csv"
    fd, tmp = tempfile.mkstemp(prefix=".annotations-", suffix=".tmp", dir=dataset)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

PAGE = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Muse B160 标注台</title><style>
:root{color-scheme:dark;font:15px system-ui;background:#080d19;color:#e7efff}body{margin:0;background:radial-gradient(ellipse at 20% 0%,#17264b,#080d19 55%)}header{padding:18px 22px;border-bottom:1px solid #31466c}h1{font-size:20px;color:#a9d7ff}main{max-width:1180px;margin:20px auto;padding:0 16px;display:grid;grid-template-columns:1.5fr 1fr;gap:18px}.panel{background:#101a2b;border:1px solid #2b4167;border-radius:12px;padding:16px}.stage{position:relative;background:#050811;display:grid;place-items:center;min-height:280px;overflow:hidden}.stage img{max-width:100%;max-height:65vh;object-fit:contain}.stage canvas{position:absolute;touch-action:none;cursor:crosshair}.muted{color:#91a5c5;font-size:13px}label{display:block;color:#a9bddb;font-size:13px;margin:10px 0 5px}input,select,textarea{width:100%;box-sizing:border-box;background:#081221;color:#e7efff;border:1px solid #344c70;border-radius:6px;padding:9px}textarea{min-height:70px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.controls{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}button{background:#173c67;color:white;border:1px solid #4b79ad;padding:9px 13px;border-radius:7px}.primary{background:#146b83}.status{color:#72e1c1}@media(max-width:780px){main{grid-template-columns:1fr}}
</style><header><h1>✦ MUSE / PROMETHEUS · B160 人工视觉标注台</h1><span class="muted">仅本机访问 · 人工标签与模型预测分离</span></header><main><section class="panel"><div id="meta" class="muted">载入中…</div><div class="stage" id="stage"><img id="frame"><canvas id="cv"></canvas></div><p class="muted">在图像上拖动框选主体；难以判断时选择 ambiguous。</p><p id="prediction" class="muted" aria-live="polite" style="margin-top:12px;padding:10px;border:1px solid #344c70;border-radius:8px;background:#081221">模型预测载入中…</p><div class="controls"><button id="prev">← 上一帧</button><button id="next">下一帧 →</button><button id="clear">清除框</button><span id="count" class="muted"></span></div></section><section class="panel"><label>主体状态 *</label><select id="presence"><option value="">请选择</option><option value="present">present · 有主体</option><option value="absent">absent · 无主体</option><option value="ambiguous">ambiguous · 无法确定</option></select><label>主体类型</label><input id="subject_type" placeholder="人物、建筑、车辆、动物…"><label>镜头运动 *</label><select id="camera_motion"><option value="">请选择</option><option>static</option><option>low</option><option>medium</option><option>high</option></select><label>标注者 *</label><input id="annotator" placeholder="填写标注代号"><label>备注</label><textarea id="notes"></textarea><div class="grid"><div><label>x_min</label><input id="x_min" readonly></div><div><label>y_min</label><input id="y_min" readonly></div><div><label>x_max</label><input id="x_max" readonly></div><div><label>y_max</label><input id="y_max" readonly></div></div><div class="controls"><button class="primary" id="save">保存此帧</button><span id="status" class="status"></span></div><p class="muted">只更新人工标签列，不改写 pred_* 预测列。</p></section></main><script>
let rows=[],i=0,box=null,drag=null;const $=x=>document.getElementById(x),keys=['presence','subject_type','camera_motion','annotator','notes'];
async function api(url,opt){let r=await fetch(url,opt),j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);return j}
function setbox(b){box=b;['x_min','y_min','x_max','y_max'].forEach((k,n)=>$(k).value=b?b[n].toFixed(6):'');draw()}
function draw(){let c=$('cv'),r=c.getBoundingClientRect(),d=devicePixelRatio||1;c.width=Math.max(1,r.width*d);c.height=Math.max(1,r.height*d);let x=c.getContext('2d');x.scale(d,d);if(box){x.strokeStyle='#45f0d0';x.lineWidth=2;x.strokeRect(box[0]*r.width,box[1]*r.height,(box[2]-box[0])*r.width,(box[3]-box[1])*r.height)}}
function renderPrediction(s){const fields=[['主体预测','pred_presence'],['镜头运动预测','pred_camera_motion'],['预测框','pred_x_min'],['置信度','pred_confidence'],['来源','pred_source']];const value=k=>{if(k==='pred_x_min'){const b=['pred_x_min','pred_y_min','pred_x_max','pred_y_max'].map(x=>s[x]||'');return b.every(Boolean)?b.join(', '):'未提供'}return s[k]||'未提供'};$('prediction').textContent='模型预测（只读，不作为人工真值）： '+fields.map(([label,key])=>label+'：'+value(key)).join(' ｜ ')}
function load(){let s=rows[i];renderPrediction(s);keys.forEach(k=>$(k).value=s[k]||'');setbox(['x_min','y_min','x_max','y_max'].every(k=>s[k])?[+s.x_min,+s.y_min,+s.x_max,+s.y_max]:null);$('meta').textContent=s.sample_id+' · '+s.source_video+' · '+s.timestamp_seconds+'s';$('frame').src='/frame?sample='+encodeURIComponent(s.sample_id);$('count').textContent=(i+1)+' / '+rows.length;$('prev').disabled=i===0;$('next').disabled=i===rows.length-1;$('status').textContent=''}
$('frame').onload=()=>{let im=$('frame'),stage=$('stage'),c=$('cv'),a=im.getBoundingClientRect(),b=stage.getBoundingClientRect();c.style.left=(a.left-b.left)+'px';c.style.top=(a.top-b.top)+'px';c.style.width=a.width+'px';c.style.height=a.height+'px';draw()};
function pos(e){let r=$('cv').getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))]}
$('cv').onpointerdown=e=>{let p=pos(e);drag=[...p,...p];$('cv').setPointerCapture(e.pointerId);setbox(drag)};
$('cv').onpointermove=e=>{if(!drag)return;let p=pos(e);drag[2]=p[0];drag[3]=p[1];setbox([Math.min(drag[0],drag[2]),Math.min(drag[1],drag[3]),Math.max(drag[0],drag[2]),Math.max(drag[1],drag[3])])};$('cv').onpointerup=()=>drag=null;
$('prev').onclick=()=>{if(i){i--;load()}};$('next').onclick=()=>{if(i<rows.length-1){i++;load()}};$('clear').onclick=()=>setbox(null);
$('save').onclick=async()=>{try{let p={};keys.forEach(k=>p[k]=$(k).value);['x_min','y_min','x_max','y_max'].forEach(k=>p[k]=$(k).value);let d=await api('/api/annotation/'+encodeURIComponent(rows[i].sample_id),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});rows[i]=d.sample;$('status').textContent='✓ 已保存'}catch(e){$('status').textContent='保存失败：'+e.message}};
(async()=>{try{rows=(await api('/api/samples')).samples;load()}catch(e){$('meta').textContent='载入失败：'+e.message}})();window.onresize=draw;
</script></html>"""

class Handler(BaseHTTPRequestHandler):
    def send_json(self,status,obj):
        b=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(b))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u=urlparse(self.path)
        if u.path=="/":
            b=PAGE.encode(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        elif u.path=="/api/samples":
            try:self.send_json(200,{"samples":load_samples()})
            except Exception as e:self.send_json(500,{"error":str(e)})
        elif u.path=="/frame":
            try:
                sid=parse_qs(u.query).get("sample",[""])[0]; row=next(r for r in load_samples() if r["sample_id"]==sid); b=(DATASET/row["frame_path"]).read_bytes()
                self.send_response(200); self.send_header("Content-Type","image/jpeg"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
            except Exception:self.send_json(404,{"error":"frame not found"})
        else:self.send_json(404,{"error":"not found"})
    def do_POST(self):
        m=re.fullmatch(r"/api/annotation/([A-Za-z0-9_-]{1,80})",urlparse(self.path).path)
        if not m:return self.send_json(404,{"error":"not found"})
        try:
            n=int(self.headers.get("Content-Length","0"))
            if n<=0 or n>16384:raise ValueError("invalid request size")
            p=json.loads(self.rfile.read(n))
            if not isinstance(p,dict):raise ValueError("JSON object required")
            with SAVE_LOCK:
                save_annotation(m.group(1), p)
                row = next(r for r in load_samples() if r["sample_id"] == m.group(1))
            self.send_json(200, {"sample": row})
        except KeyError as exc:
            self.send_json(404, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def log_message(self, fmt, *args):
        print("[annotation] " + fmt % args)
def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument("--host",choices=["127.0.0.1","localhost"],default="127.0.0.1"); ap.add_argument("--port",type=int,default=8765); a=ap.parse_args()
    if not 1024<=a.port<=65535:ap.error("port must be 1024..65535")
    load_samples(); server=ThreadingHTTPServer((a.host,a.port),Handler); print(f"Open http://127.0.0.1:{a.port}/ (Ctrl+C to stop)")
    try:server.serve_forever()
    except KeyboardInterrupt:print("Stopped")
    finally:server.server_close()
if __name__=="__main__":main()
